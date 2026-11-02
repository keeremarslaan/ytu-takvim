"""Deterministic local exports. Export history is not deployment history."""

import hashlib
import json
import re
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ytu_scheduler.models import AcademicTerm, Course, CurriculumSnapshot, Program
from ytu_scheduler.services.coverage import catalog_coverage
from ytu_scheduler.services.curriculum_metadata import curriculum_roles
from ytu_scheduler.services.document_review import review_document
from ytu_scheduler.services.drafts import DraftStore
from ytu_scheduler.services.eligibility import program_permissions
from ytu_scheduler.services.program_sources import STUDENT_MANIFEST, STUDENT_PROGRAM_IDS
from ytu_scheduler.services.snapshots import latest_snapshot_ids

Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


def canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: dict) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class ExportSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term_code: str = Field(min_length=1, max_length=100)
    purpose: Literal["test", "release"]
    draft_ids: list[Digest] = Field(min_length=1, max_length=100)
    baseline_export_id: Digest | None = None

    @model_validator(mode="after")
    def distinct(self):
        if len(self.draft_ids) != len(set(self.draft_ids)):
            raise ValueError("Select each draft only once")
        return self


class ExportCommit(ExportSelection):
    expected_digest: Digest
    owner_confirmed: Literal[True]


class ReleaseStore(DraftStore):
    def setup(self, db):
        db.execute("""CREATE TABLE IF NOT EXISTS exports (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, term_code TEXT NOT NULL,
            purpose TEXT NOT NULL, baseline_id TEXT, bundle TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS export_heads (
            term_code TEXT NOT NULL, purpose TEXT NOT NULL,
            export_id TEXT NOT NULL REFERENCES exports(id),
            PRIMARY KEY (term_code, purpose)
        )""")

    def head(self, term_code: str, purpose: str) -> str | None:
        with self.connection() as db:
            self.setup(db)
            row = db.execute(
                "SELECT export_id FROM export_heads WHERE term_code=? AND purpose=?",
                (term_code, purpose),
            ).fetchone()
        return row[0] if row else None

    def get_export(self, export_id: str) -> dict:
        if not re.fullmatch(r"[a-f0-9]{64}", export_id):
            raise KeyError("Export not found")
        with self.connection() as db:
            self.setup(db)
            row = db.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
        if row is None:
            raise KeyError("Export not found")
        bundle = json.loads(row["bundle"])
        if digest(bundle) != export_id:
            raise ValueError("Saved export integrity check failed")
        selection = bundle["report"]["selection"]
        if (row["term_code"], row["purpose"], row["baseline_id"]) != (
            selection["term_code"],
            selection["purpose"],
            selection["baseline_export_id"],
        ):
            raise ValueError("Saved export metadata integrity check failed")
        return {"id": export_id, "created_at": row["created_at"], **bundle}

    def save_export(self, selection: ExportSelection, bundle: dict) -> str:
        export_id = digest(bundle)
        with self.connection() as db:
            self.setup(db)
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT export_id FROM export_heads WHERE term_code=? AND purpose=?",
                (selection.term_code, selection.purpose),
            ).fetchone()
            if (row[0] if row else None) != selection.baseline_export_id:
                raise ValueError("Export baseline changed; review the selection again")
            db.execute(
                "INSERT INTO exports VALUES (?, ?, ?, ?, ?, ?)",
                (
                    export_id,
                    datetime.now(UTC).isoformat(),
                    selection.term_code,
                    selection.purpose,
                    selection.baseline_export_id,
                    canonical(bundle).decode(),
                ),
            )
            db.execute(
                "INSERT OR REPLACE INTO export_heads VALUES (?, ?, ?)",
                (selection.term_code, selection.purpose, export_id),
            )
        return export_id

    def list_exports(self, limit: int = 50, offset: int = 0) -> dict:
        with self.connection() as db:
            self.setup(db)
            rows = db.execute(
                """SELECT id, created_at, term_code, purpose, baseline_id FROM exports
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
                (limit + 1, offset),
            ).fetchall()
            heads = [dict(r) for r in db.execute("SELECT * FROM export_heads")]
        return {
            "items": [dict(r) for r in rows[:limit]],
            "heads": heads,
            "next_offset": offset + limit if len(rows) > limit else None,
        }


def source_scope(document) -> tuple[str, str]:
    return document.source.publisher, document.source.key


def build_export(session: Session, store: ReleaseStore, selection: ExportSelection) -> dict:
    # pysqlite's legacy transaction mode otherwise does not start a read snapshot on SELECT.
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
    blockers: list[str] = []
    warnings: list[str] = []
    previous_sources = {}
    revision_hashes = {}
    head = store.head(selection.term_code, selection.purpose)
    if head != selection.baseline_export_id:
        blockers.append("Select the latest local export as baseline; export history changed")
    if selection.baseline_export_id:
        previous = store.get_export(selection.baseline_export_id)
        old_selection = previous["report"]["selection"]
        if (old_selection["term_code"], old_selection["purpose"]) != (
            selection.term_code,
            selection.purpose,
        ):
            blockers.append("Export baseline belongs to a different term or purpose")
        else:
            previous_sources = {
                (s["publisher"], s["key"]): s for s in previous["package"]["sources"]
            }
            ancestor = previous
            visited = set()
            while ancestor:
                if ancestor["id"] in visited:
                    raise ValueError("Cyclic export history")
                visited.add(ancestor["id"])
                for source in ancestor["package"]["sources"]:
                    revision_hashes[(source["publisher"], source["key"], source["revision"])] = (
                        source["document_sha256"]
                    )
                parent = ancestor["report"]["selection"]["baseline_export_id"]
                ancestor = store.get_export(parent) if parent else None

    term = session.scalar(select(AcademicTerm).where(AcademicTerm.code == selection.term_code))
    term_data = None
    if term is None:
        blockers.append("Registered academic term not found")
    else:
        term_data = {
            "code": term.code,
            "academic_year": term.academic_year,
            "season": term.season.value,
            "timezone": term.timezone,
            "starts_on": term.starts_on.isoformat() if term.starts_on else None,
            "ends_on": term.ends_on.isoformat() if term.ends_on else None,
        }
        if not term.starts_on or not term.ends_on or term.starts_on > term.ends_on:
            blockers.append("Register valid teaching start and end dates before exporting")
        if term.timezone != "Europe/Istanbul":
            blockers.append("Term timezone must be Europe/Istanbul")

    drafts = [store.get(did) for did in sorted(selection.draft_ids)]
    drafts.sort(key=lambda d: (*source_scope(d["document"]), d["id"]))
    scopes = set()
    sources, reviews = [], []
    source_changes = []
    resolved_courses = {}
    groups = {}
    for draft in drafts:
        document = draft["document"]
        scope = source_scope(document)
        context = f"{scope[0]} / {scope[1]} / {document.source.revision}"
        if scope in scopes:
            blockers.append(f"{context}: select exactly one revision per source")
        scopes.add(scope)
        if document.term.code != selection.term_code:
            blockers.append(f"{context}: draft belongs to a different term")
        if selection.purpose == "release" and (
            document.source.key.lower().startswith("example-")
            or document.source.sha256
            == "d67a6fede17f41ea761872bb9ffad940ec69a711c5500694bc35f8925e7cf988"
        ):
            blockers.append(f"{context}: synthetic example is allowed only in test exports")
        baseline = store.get(draft["baseline_id"]) if draft["baseline_id"] else None
        prior = previous_sources.get(scope)
        historical_hash = revision_hashes.get((*scope, document.source.revision))
        if historical_hash and historical_hash != draft["document_sha256"]:
            blockers.append(
                f"{context}: revision identifier was already exported with other content"
            )
        if prior:
            if draft["id"] != prior["draft_id"]:
                if document.source.revision == prior["revision"]:
                    blockers.append(f"{context}: changed content must use a new revision")
                if document.source.replaces_revision != prior["revision"]:
                    blockers.append(f"{context}: replacement must name the last exported revision")
                if draft["baseline_id"] != prior["draft_id"]:
                    blockers.append(f"{context}: replacement must compare the last exported draft")
        elif document.source.replaces_revision or document.withdraw_all:
            blockers.append(f"{context}: cannot replace or withdraw a source never exported here")
        source_changes.append(
            {
                "publisher": scope[0],
                "key": scope[1],
                "before_revision": prior["revision"] if prior else None,
                "after_revision": document.source.revision,
                "action": "retained"
                if prior and prior["draft_id"] == draft["id"]
                else ("withdrawn" if document.withdraw_all else "replaced" if prior else "added"),
            }
        )
        report = review_document(
            session, document, baseline["document"] if baseline else None, draft["source_sha256"]
        )
        blockers.extend(f"{context}: {message}" for message in report["blockers"])
        warnings.extend(f"{context}: {message}" for message in report["warnings"])
        reviews.append(
            {
                "draft_id": draft["id"],
                "document": document.model_dump(mode="json"),
                "review": report,
            }
        )
        sources.append(
            {
                **document.source.model_dump(mode="json"),
                "draft_id": draft["id"],
                "document_sha256": draft["document_sha256"],
                "withdrawn": document.withdraw_all,
            }
        )
        for course, match in zip(document.courses, report["resolved_courses"], strict=True):
            cid = match["catalog_course_id"]
            if cid is None:
                continue
            resolved_courses[cid] = session.get(Course, cid)
            for group in course.groups:
                key = (cid, group.label.casefold())
                if key in groups:
                    blockers.append(
                        f"{course.code} / {group.label}: overlapping group identity across sources;"
                        "reconcile explicitly before export"
                    )
                value = group.model_dump(mode="json")
                value.update(
                    {
                        "id": digest(
                            {
                                "term": selection.term_code,
                                "source": scope,
                                "course_id": cid,
                                "group": group.label.casefold(),
                            }
                        ),
                        "catalog_course_id": cid,
                        "draft_id": draft["id"],
                        "source_key": document.source.key,
                        "publisher": scope[0],
                    }
                )
                # Keep source dates verbatim and additionally resolve the actual teaching interval.
                for meeting in value["meetings"]:
                    meeting["effective_starts_on"] = meeting["starts_on"] or (
                        term_data["starts_on"] if term_data else None
                    )
                    meeting["effective_ends_on"] = meeting["ends_on"] or (
                        term_data["ends_on"] if term_data else None
                    )
                    if term and term.starts_on and term.ends_on:
                        start = date.fromisoformat(meeting["effective_starts_on"])
                        end = date.fromisoformat(meeting["effective_ends_on"])
                        if (meeting["weekday"] - start.isoweekday()) % 7 > (end - start).days:
                            blockers.append(f"{context}: effective dates have no meeting weekday")
                groups[key] = value
    for scope in sorted(previous_sources.keys() - scopes):
        blockers.append(
            f"{scope[0]} / {scope[1]}: omitted source; retain it or select a withdrawal"
        )

    programs = list(
        session.scalars(
            select(Program)
            .where(Program.bologna_program_id.in_(STUDENT_PROGRAM_IDS))
            .order_by(Program.bologna_program_id)
        )
    )
    snapshots = {
        s.program_id: s
        for s in session.scalars(
            select(CurriculumSnapshot).where(
                CurriculumSnapshot.id.in_(latest_snapshot_ids(session, [p.id for p in programs]))
            )
        )
    }
    program_data = []
    for program in programs:
        permissions = program_permissions(session, [program.id])[program.id]
        bid = program.bologna_program_id
        snapshot = snapshots.get(program.id)
        roles = curriculum_roles(session, snapshot.id if snapshot else None)
        course_permissions = [
            {
                "catalog_course_id": cid,
                "status": status.value,
                "classifications": roles.get(cid, []),
            }
            for cid, status in sorted(permissions.items())
            if cid in resolved_courses
        ]
        program_data.append(
            {
                "bologna_program_id": bid,
                "name": program.name,
                "curriculum": {
                    "id": snapshot.id,
                    "sha256": snapshot.source_hash,
                    "verified_at": snapshot.fetched_at.isoformat(),
                }
                if snapshot
                else None,
                "course_permissions": course_permissions,
            }
        )
        for group in groups.values():
            restriction = group["permissions"]
            allowed = restriction["allowed_bologna_program_ids"]
            if (
                group["catalog_course_id"] in permissions
                and (allowed is None or bid in allowed)
                and bid not in restriction["excluded_bologna_program_ids"]
            ):
                group.setdefault("eligible_bologna_program_ids", []).append(bid)
    for group in groups.values():
        group.setdefault("eligible_bologna_program_ids", [])
        if not group["eligible_bologna_program_ids"]:
            warnings.append(
                f"{group['source_key']} / {group['label']}: no supported major is eligible"
            )
    coverage = [
        p
        for p in catalog_coverage(session)["programs"]
        if p["bologna_program_id"] in STUDENT_PROGRAM_IDS
    ]
    for entry in coverage:
        bid = entry["bologna_program_id"]
        entry["exported_eligible_courses"] = len(
            {
                g["catalog_course_id"]
                for g in groups.values()
                if bid in g["eligible_bologna_program_ids"]
            }
        )
        entry["timetable_completeness"] = "not_established"
    warnings.append("Timetable completeness is not established; inspect source and curriculum gaps")
    package = {
        "schema_version": "1.0",
        "purpose": selection.purpose,
        "baseline_export_id": selection.baseline_export_id,
        "term": term_data,
        "student_directory": STUDENT_MANIFEST,
        "sources": sources,
        "programs": program_data,
        "coverage": coverage,
        "courses": [
            {
                "catalog_course_id": cid,
                "code": c.code,
                "name": c.name,
                "ects": c.ects,
                "prerequisite_text": c.prerequisite_text,
            }
            for cid, c in sorted(resolved_courses.items())
        ],
        "groups": sorted(groups.values(), key=lambda g: g["id"]),
        "eligibility_rule": "group eligible IDs already intersect course and group permissions; "
        "allow when any selected major is in that group's eligible IDs",
        "holiday_exceptions": None,
    }
    normalized_selection = selection.model_dump(include=set(ExportSelection.model_fields))
    normalized_selection["draft_ids"] = sorted(normalized_selection["draft_ids"])
    report = {
        "report_version": "1.0",
        "term": term_data,
        "source_changes": source_changes,
        "selection": normalized_selection,
        "ready_to_export": not blockers,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "documents": reviews,
        "coverage": coverage,
        "summary": {
            "sources": len(sources),
            "courses": len(resolved_courses),
            "groups": len(groups),
            "withdrawals": sum(s["withdrawn"] for s in sources),
        },
        "publication": "Local export only; not deployed or integrated with the student app",
    }
    return {"package": package, "report": report}
