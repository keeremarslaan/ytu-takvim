"""Read-only owner review; publishing must separately recheck the reviewed hashes."""

import hashlib
import json

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ytu_scheduler.models import AcademicTerm, Course, CourseAlias, Program
from ytu_scheduler.schedule_document import ScheduleDocument
from ytu_scheduler.services.bologna import course_name_key, normalize_course_code


def document_digest(document: ScheduleDocument) -> str:
    payload = json.dumps(
        document.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _course_content(course) -> dict:
    value = course.model_dump(mode="json")
    value["aliases"] = sorted(value["aliases"])
    value["groups"] = sorted(value["groups"], key=lambda group: group["label"].casefold())
    for group in value["groups"]:
        group.pop("evidence")
        group["permissions"].pop("evidence")
        for key in ("allowed_bologna_program_ids", "excluded_bologna_program_ids"):
            ids = group["permissions"][key]
            if ids is not None:
                group["permissions"][key] = sorted(ids)
        for meeting in group["meetings"]:
            meeting.pop("evidence")
        group["meetings"].sort(key=lambda meeting: json.dumps(meeting, sort_keys=True))
    return value


def document_diff(document: ScheduleDocument, baseline: ScheduleDocument | None) -> dict:
    old = (
        {normalize_course_code(c.code): _course_content(c) for c in baseline.courses}
        if baseline
        else {}
    )
    new = {normalize_course_code(c.code): _course_content(c) for c in document.courses}
    return {
        "added": [{"code": code, "after": new[code]} for code in sorted(new.keys() - old.keys())],
        "removed": [
            {"code": code, "before": old[code]} for code in sorted(old.keys() - new.keys())
        ],
        "changed": [
            {"code": code, "before": old[code], "after": new[code]}
            for code in sorted(old.keys() & new.keys())
            if old[code] != new[code]
        ],
        "unchanged": [code for code in sorted(old.keys() & new.keys()) if old[code] == new[code]],
    }


def review_document(
    session: Session,
    document: ScheduleDocument,
    baseline: ScheduleDocument | None = None,
    source_file_sha256: str | None = None,
) -> dict:
    blockers = []
    warnings = []
    if source_file_sha256 is None:
        blockers.append("Supply the original source file to verify its SHA-256")
    elif document.source.sha256 != source_file_sha256:
        blockers.append("Source SHA-256 is missing or does not match the supplied file")
    if baseline:
        if (
            document.source.key != baseline.source.key
            or document.source.publisher != baseline.source.publisher
            or document.term != baseline.term
        ):
            blockers.append("Baseline belongs to a different publisher, source key, or term")
        if document.source.replaces_revision != baseline.source.revision:
            blockers.append("replaces_revision must match the baseline revision")
    elif document.source.replaces_revision is not None or document.withdraw_all:
        blockers.append("Replacement or withdrawal requires a baseline document")

    term = session.scalar(select(AcademicTerm).where(AcademicTerm.code == document.term.code))
    if term is None:
        blockers.append(f"Academic term not found: {document.term.code}")
    elif (
        term.academic_year != document.term.academic_year
        or term.season != document.term.season
        or term.timezone != document.term.timezone
    ):
        blockers.append("Academic year, season, or timezone differs from the registered term")

    known_programs = set(session.scalars(select(Program.bologna_program_id)))
    resolved = []
    seen_ids = set()
    for course in document.courses:
        codes = [normalize_course_code(code) for code in [course.code, *course.aliases]]
        alias_ids = select(CourseAlias.course_id).where(CourseAlias.normalized_code.in_(codes))
        candidates = list(
            session.scalars(
                select(Course)
                .where(or_(Course.normalized_code.in_(codes), Course.id.in_(alias_ids)))
                .order_by(Course.id)
            )
        )
        match = None
        if course.catalog_course_id is not None:
            match = next((c for c in candidates if c.id == course.catalog_course_id), None)
            if match is None:
                blockers.append(
                    f"{course.code}: selected catalog ID does not match its code/aliases"
                )
        elif len(candidates) == 1:
            match = candidates[0]
        elif not candidates:
            blockers.append(f"{course.code}: no catalog match; owner reconciliation required")
        else:
            blockers.append(
                f"{course.code}: ambiguous code; select a catalog_course_id after review"
            )
        if match:
            if match.id in seen_ids:
                blockers.append(f"{course.code}: catalog course is selected more than once")
            seen_ids.add(match.id)
            if course_name_key(match.name) != course_name_key(course.name):
                warnings.append(
                    f"{course.code}: source name differs from catalog name: {match.name}"
                )
            if match.is_provisional:
                blockers.append(
                    f"{course.code}: provisional catalog record requires reconciliation"
                )
        resolved.append(
            {
                "code": course.code,
                "catalog_course_id": match.id if match else None,
                "candidates": [{"id": c.id, "code": c.code, "name": c.name} for c in candidates],
            }
        )
        for group in course.groups:
            context = f"{course.code} / {group.label}"
            permissions = group.permissions
            mentioned = set(permissions.allowed_bologna_program_ids or []) | set(
                permissions.excluded_bologna_program_ids
            )
            if unknown := mentioned - known_programs:
                blockers.append(f"{context}: unknown Bologna program IDs: {sorted(unknown)}")
            if permissions.unresolved:
                blockers.append(f"{context}: unresolved registration restrictions")
            if group.time_status == "tba":
                warnings.append(f"{context}: time is unknown; conflicts cannot be verified")
            for meeting in group.meetings:
                if meeting.starts_on and meeting.ends_on:
                    offset = (meeting.weekday - meeting.starts_on.isoweekday()) % 7
                    if offset > (meeting.ends_on - meeting.starts_on).days:
                        blockers.append(f"{context}: date range has no matching weekday")
                    if term and (
                        (term.starts_on and meeting.starts_on < term.starts_on)
                        or (term.ends_on and meeting.ends_on > term.ends_on)
                    ):
                        blockers.append(
                            f"{context}: meeting dates fall outside the registered term"
                        )
    for issue in document.issues:
        (blockers if issue.severity == "blocking" else warnings).append(issue.message)
    changes = document_diff(document, baseline)
    return {
        "review_version": "1.0",
        "read_only": True,
        "ready_for_owner_review": not blockers,
        "document_sha256": document_digest(document),
        "baseline_sha256": document_digest(baseline) if baseline else None,
        "verified_source_sha256": source_file_sha256,
        "source": document.source.model_dump(mode="json"),
        "term": document.term.model_dump(mode="json"),
        "blockers": blockers,
        "warnings": warnings,
        "resolved_courses": resolved,
        "summary": {key: len(value) for key, value in changes.items()},
        "changes": changes,
        "ignored_events": [event.model_dump(mode="json") for event in document.ignored_events],
        "publication": "Not published. This report is a review artifact, not approval.",
    }
