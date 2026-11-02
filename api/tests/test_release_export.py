import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ytu_scheduler.config import Settings
from ytu_scheduler.database import Base, get_session
from ytu_scheduler.main import create_app
from ytu_scheduler.models import (
    AcademicTerm,
    Course,
    CurriculumSnapshot,
    EligibilityRule,
    Program,
    ProgramRequirement,
    RequirementKind,
    RuleType,
    Season,
)
from ytu_scheduler.schedule_document import ScheduleDocument
from ytu_scheduler.services.releases import ExportSelection, ReleaseStore, build_export, digest

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/examples"


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    store = ReleaseStore(tmp_path / "drafts.db")
    settings = Settings(draft_database=store.path)
    monkeypatch.setattr("ytu_scheduler.routers.releases.get_settings", lambda: settings)
    with Session(engine) as session:
        session.add(
            AcademicTerm(
                code="2026-2027-fall",
                academic_year="2026-2027",
                season=Season.FALL,
                timezone="Europe/Istanbul",
                starts_on=date(2026, 9, 21),
                ends_on=date(2027, 1, 10),
            )
        )
        session.add_all(
            [
                Course(id=1, code="FIZ1111", normalized_code="FIZ1111", name="Fizik 1"),
                Course(id=2, code="IST1000", normalized_code="IST1000", name="Statistics"),
            ]
        )
        for pid, bid in [(1, 25), (2, 9)]:
            session.add(
                Program(
                    id=pid,
                    bologna_program_id=bid,
                    name=f"Program {bid}",
                    department="Test",
                    academic_unit_id=1,
                    source_url="test",
                )
            )
            session.flush()
            session.add(
                CurriculumSnapshot(
                    id=pid,
                    program_id=pid,
                    source_hash=str(pid) * 64,
                    fetched_at=datetime(2026, 9, 1, tzinfo=UTC),
                )
            )
            session.flush()
            session.add(
                ProgramRequirement(
                    snapshot_id=pid,
                    course_id=pid,
                    kind=RequirementKind.COMPULSORY,
                    year=1,
                    season=Season.FALL,
                    semester_number=1,
                    source_order=1,
                )
            )
        session.commit()
        app = create_app(local_owner=True)
        app.dependency_overrides[get_session] = lambda: session
        client = TestClient(app, base_url="http://127.0.0.1:8001", client=("127.0.0.1", 1234))
        yield session, store, client
    engine.dispose()


def save(store, *, key="physics", revision="1", baseline=None, edit=None, synthetic=False):
    document = json.loads((EXAMPLES / "schedule-v1.json").read_text())
    source = (EXAMPLES / "schedule-source.txt").read_bytes() if synthetic else b"Fixture source"
    if not synthetic:
        document["source"].update(
            key=key,
            publisher="Fixture Department",
            revision=revision,
            sha256=hashlib.sha256(source).hexdigest(),
        )
    if baseline:
        document["source"]["replaces_revision"] = store.get(baseline)["document"].source.revision
    if edit:
        edit(document)
    return store.save(ScheduleDocument.model_validate(document), source, baseline)


def selection(*ids, baseline=None, purpose="release"):
    return ExportSelection(
        term_code="2026-2027-fall",
        purpose=purpose,
        draft_ids=list(ids),
        baseline_export_id=baseline,
    )


def commit(client, selected):
    payload = selected.model_dump()
    preview = client.post("/owner/exports/preview", json=payload)
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["report"]["ready_to_export"], data
    result = client.post(
        "/owner/exports",
        json={**payload, "expected_digest": data["digest"], "owner_confirmed": True},
    )
    assert result.status_code == 201, result.text
    return result.json()["id"]


def test_deterministic_export_preserves_data_and_student_tables(env):
    session, store, client = env
    did = save(store)
    before = {
        t.name: session.scalar(select(func.count()).select_from(t))
        for t in Base.metadata.sorted_tables
    }
    chosen = selection(did)
    first = build_export(session, store, chosen)
    assert digest(first) == digest(build_export(session, store, chosen))
    eid = commit(client, chosen)
    package = client.get(f"/owner/exports/{eid}/student-data").json()
    report = client.get(f"/owner/exports/{eid}/owner-report").json()
    group = package["groups"][0]
    assert group["eligible_bologna_program_ids"] == [25]
    assert group["meetings"][0]["starts_on"] is None
    assert group["meetings"][0]["effective_starts_on"] == "2026-09-21"
    assert group["meetings"][0]["evidence"]["quote"]
    assert report["documents"][0]["document"] == store.get(did)["document"].model_dump(mode="json")
    assert all(p["timetable_completeness"] == "not_established" for p in report["coverage"])
    assert before == {
        t.name: session.scalar(select(func.count()).select_from(t))
        for t in Base.metadata.sorted_tables
    }
    assert ReleaseStore(store.path).get_export(eid)["package"] == first["package"]
    assert client.get("/owner/exports").json()["heads"][0]["export_id"] == eid


def test_double_major_intersection_precedes_union(env):
    session, store, _ = env

    def restrict(doc):
        group = doc["courses"][0]["groups"][0]
        group["permissions"].update(allowed_bologna_program_ids=[9], evidence=group["evidence"])

    chosen = selection(save(store, edit=restrict))

    def eligible():
        return build_export(session, store, chosen)["package"]["groups"][0][
            "eligible_bologna_program_ids"
        ]

    assert eligible() == []
    session.add(
        EligibilityRule(
            program_id=2, course_id=1, rule_type=RuleType.EXCEPTION, reason="Verified", active=True
        )
    )
    session.flush()
    assert eligible() == [9]
    session.add(
        EligibilityRule(
            program_id=2, course_id=1, rule_type=RuleType.EXCLUSION, reason="Excluded", active=True
        )
    )
    session.flush()
    assert eligible() == []


def test_replacement_withdrawal_and_retained_tombstone(env):
    session, store, client = env
    first = save(store)
    e1 = commit(client, selection(first))

    def change(doc):
        doc["courses"][0]["groups"][0]["meetings"][0]["room"] = "D-102"

    second = save(store, revision="2", baseline=first, edit=change)
    assert not build_export(session, store, selection(second))["report"]["ready_to_export"]
    e2 = commit(client, selection(second, baseline=e1))
    assert store.get_export(e1)["package"]["groups"][0]["meetings"][0]["room"] == "D-101"
    assert store.get_export(e2)["package"]["groups"][0]["meetings"][0]["room"] == "D-102"

    def withdraw(doc):
        doc.update(courses=[], withdraw_all=True)

    withdrawn = save(store, revision="3", baseline=second, edit=withdraw)
    e3 = commit(client, selection(withdrawn, baseline=e2))
    assert store.get_export(e3)["package"]["groups"] == []
    assert store.get_export(e3)["package"]["sources"][0]["withdrawn"]
    commit(client, selection(withdrawn, baseline=e3))


def test_omission_conflicting_revisions_and_overlapping_groups_block(env):
    session, store, client = env
    first = save(store)
    e1 = commit(client, selection(first))
    another = save(store, key="other")

    def blockers(*ids):
        return str(build_export(session, store, selection(*ids, baseline=e1))["report"]["blockers"])

    assert "omitted source" in blockers(another)
    second = save(store, revision="2", baseline=first)
    assert "exactly one revision" in blockers(first, second)
    assert "overlapping group identity" in blockers(first, another)


def test_source_order_and_group_exclusions(env):
    session, store, _ = env
    first = save(store)

    def second_course(doc):
        doc["courses"][0].update(code="IST1000", name="Statistics")
        group = doc["courses"][0]["groups"][0]
        group["permissions"].update(excluded_bologna_program_ids=[9], evidence=group["evidence"])

    second = save(store, key="statistics", edit=second_course)
    a = build_export(session, store, selection(first, second))
    b = build_export(session, store, selection(second, first))
    assert a["report"]["ready_to_export"]
    assert digest(a) == digest(b)
    assert (
        next(g for g in a["package"]["groups"] if g["catalog_course_id"] == 2)[
            "eligible_bologna_program_ids"
        ]
        == []
    )


@pytest.mark.parametrize("kind", ["ambiguous", "restriction", "dates", "source", "term"])
def test_export_revalidates_blockers(env, kind):
    session, store, client = env

    def edit(doc):
        if kind == "restriction":
            group = doc["courses"][0]["groups"][0]
            group["permissions"].update(unresolved=True, evidence=group["evidence"])
        if kind == "dates":
            doc["courses"][0]["groups"][0]["meetings"][0].update(
                starts_on="2025-09-01", ends_on="2025-12-01"
            )
        if kind == "source":
            doc["source"]["sha256"] = "0" * 64
        if kind == "term":
            doc["term"]["code"] = "other-term"

    did = save(store, edit=edit)
    if kind == "ambiguous":
        session.add(Course(code="FIZ1111", normalized_code="FIZ1111", name="Different"))
        session.flush()
    result = client.post("/owner/exports/preview", json=selection(did).model_dump()).json()
    assert result["report"]["blockers"]
    response = client.post(
        "/owner/exports",
        json={
            **selection(did).model_dump(),
            "expected_digest": result["digest"],
            "owner_confirmed": True,
        },
    )
    assert response.status_code == 409
    assert store.list_exports()["items"] == []


def test_synthetic_exports_are_separate(env):
    session, store, client = env
    did = save(store, synthetic=True)
    assert "synthetic" in str(build_export(session, store, selection(did))["report"]["blockers"])
    eid = commit(client, selection(did, purpose="test"))
    assert store.head("2026-2027-fall", "release") is None
    assert store.get_export(eid)["package"]["purpose"] == "test"
    real = save(store)
    assert not build_export(session, store, selection(real, baseline=eid))["report"][
        "ready_to_export"
    ]


def test_stale_preview_and_head_are_rejected(env):
    session, store, client = env
    chosen = selection(save(store))
    preview = client.post("/owner/exports/preview", json=chosen.model_dump()).json()
    session.get(Course, 1).name = "Updated catalog name"
    session.flush()
    response = client.post(
        "/owner/exports",
        json={**chosen.model_dump(), "expected_digest": preview["digest"], "owner_confirmed": True},
    )
    assert response.status_code == 409 and "changed since preview" in response.text
    bundle = build_export(session, store, chosen)
    commit(client, chosen)
    with pytest.raises(ValueError, match="baseline changed"):
        store.save_export(chosen, bundle)


def test_corruption_fails_closed_download_is_immutable(env):
    _, store, client = env
    did = save(store)
    eid = commit(client, selection(did))
    with store.connection() as db:
        db.execute("UPDATE drafts SET source=? WHERE id=?", (b"corrupt", did))
    assert (
        client.post(
            "/owner/exports/preview", json=selection(did, baseline=eid).model_dump()
        ).status_code
        == 409
    )
    assert client.get(f"/owner/exports/{eid}/student-data").status_code == 200
    with store.connection() as db:
        db.execute("UPDATE exports SET bundle=? WHERE id=?", ("{}", eid))
    assert client.get(f"/owner/exports/{eid}/student-data").status_code == 409


def test_explicit_selection_confirmation_and_teaching_dates(env):
    session, store, client = env
    did = save(store)
    chosen = selection(did).model_dump()
    assert (
        client.post("/owner/exports/preview", json={**chosen, "draft_ids": []}).status_code == 422
    )
    assert (
        client.post("/owner/exports/preview", json={**chosen, "draft_ids": [did, did]}).status_code
        == 422
    )
    assert (
        client.post("/owner/exports", json={**chosen, "expected_digest": "0" * 64}).status_code
        == 422
    )
    session.scalar(select(AcademicTerm)).starts_on = None
    session.flush()
    assert "teaching start" in str(
        build_export(session, store, selection(did))["report"]["blockers"]
    )


def test_same_revision_and_wrong_baseline_block(env):
    session, store, client = env
    first = save(store)
    e1 = commit(client, selection(first))

    def change(doc):
        doc["courses"][0]["name"] = "Different name"

    changed = save(store, edit=change)
    assert "new revision" in str(
        build_export(session, store, selection(changed, baseline=e1))["report"]["blockers"]
    )
    unrelated = save(store, key="another")
    replacement = save(store, revision="2", baseline=unrelated)
    assert "last exported draft" in str(
        build_export(session, store, selection(replacement, baseline=e1))["report"]["blockers"]
    )


def test_routes_are_local_only(env):
    _, _, client = env
    assert "/owner/exports" not in create_app().openapi()["paths"]
    assert (
        client.get("/owner/exports", headers={"Origin": "https://foreign.test"}).status_code == 403
    )


def test_historical_revision_identifier_cannot_be_reused(env):
    session, store, client = env
    first = save(store)
    e1 = commit(client, selection(first))
    second = save(store, revision="2", baseline=first)
    e2 = commit(client, selection(second, baseline=e1))
    reused = save(store, revision="1", baseline=second)
    result = build_export(session, store, selection(reused, baseline=e2))
    assert "already exported" in str(result["report"]["blockers"])


def test_latest_curriculum_and_teaching_date_changes_invalidate_preview(env):
    session, store, client = env
    chosen = selection(save(store))
    preview = client.post("/owner/exports/preview", json=chosen.model_dump()).json()
    session.add(
        CurriculumSnapshot(
            program_id=1, source_hash="3" * 64, fetched_at=datetime(2026, 9, 2, tzinfo=UTC)
        )
    )
    session.flush()
    fresh = build_export(session, store, chosen)
    assert fresh["package"]["groups"][0]["eligible_bologna_program_ids"] == []
    assert digest(fresh) != preview["digest"]
    session.scalar(select(AcademicTerm)).ends_on = date(2027, 1, 17)
    session.flush()
    assert digest(build_export(session, store, chosen)) != digest(fresh)
    response = client.post(
        "/owner/exports",
        json={**chosen.model_dump(), "expected_digest": preview["digest"], "owner_confirmed": True},
    )
    assert response.status_code == 409


def test_export_classifications_follow_latest_snapshot_and_permissions(env):
    from ytu_scheduler.models import ElectivePool, PoolCourse

    session, store, _ = env
    did = save(store)
    package = build_export(session, store, selection(did))["package"]
    physics = next(p for p in package["programs"] if p["bologna_program_id"] == 25)
    assert physics["course_permissions"][0]["classifications"] == [
        {"category": "compulsory", "year": 1, "season": "fall"}
    ]
    session.add(
        CurriculumSnapshot(
            id=3, program_id=1, source_hash="a" * 64, fetched_at=datetime(2027, 1, 1, tzinfo=UTC)
        )
    )
    session.flush()
    pool = ElectivePool(snapshot_id=3, source_pool_id="test", name="Mesleki Seçmeli")
    session.add(pool)
    session.flush()
    session.add(PoolCourse(pool_id=pool.id, course_id=1))
    session.add(
        ProgramRequirement(
            snapshot_id=3,
            elective_pool_id=pool.id,
            kind=RequirementKind.ELECTIVE_SLOT,
            year=3,
            season=Season.SPRING,
            semester_number=6,
            source_order=1,
        )
    )
    session.commit()
    package = build_export(session, store, selection(did))["package"]
    physics = next(p for p in package["programs"] if p["bologna_program_id"] == 25)
    assert physics["course_permissions"][0]["classifications"] == [
        {"category": "professional_elective", "year": 3, "season": "spring"}
    ]
    session.add(
        EligibilityRule(
            course_id=1, program_id=1, rule_type=RuleType.EXCLUSION, reason="test", active=True
        )
    )
    session.commit()
    package = build_export(session, store, selection(did))["package"]
    assert (
        next(p for p in package["programs"] if p["bologna_program_id"] == 25)["course_permissions"]
        == []
    )
