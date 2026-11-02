import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from ytu_scheduler.database import Base
from ytu_scheduler.models import AcademicTerm, Course, Program, Season
from ytu_scheduler.schedule_document import ScheduleDocument
from ytu_scheduler.schedule_document_cli import read_document
from ytu_scheduler.services.document_review import document_digest, review_document

EXAMPLE = Path(__file__).parents[2] / "docs/examples/schedule-v1.json"


@pytest.fixture
def payload():
    return json.loads(EXAMPLE.read_text())


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                AcademicTerm(
                    code="2026-2027-fall",
                    academic_year="2026-2027",
                    season=Season.FALL,
                    timezone="Europe/Istanbul",
                ),
                Course(code="FIZ1111", normalized_code="FIZ1111", name="Fizik 1"),
                Program(
                    name="Physics",
                    department="Physics",
                    bologna_program_id=25,
                    academic_unit_id=10,
                    source_url="https://example.test/physics",
                ),
            ]
        )
        session.commit()
        yield session
    engine.dispose()


def document_and_review(session, payload, baseline=None):
    document = ScheduleDocument.model_validate(payload)
    return review_document(session, document, baseline, payload["source"]["sha256"])


def test_example_review_is_read_only(session, payload):
    before = {
        table.name: session.scalar(select(func.count()).select_from(table))
        for table in Base.metadata.sorted_tables
    }
    report = document_and_review(session, payload)
    assert report["ready_for_owner_review"]
    assert report["read_only"]
    assert report["summary"] == {"added": 1, "removed": 0, "changed": 0, "unchanged": 0}
    assert {
        table.name: session.scalar(select(func.count()).select_from(table))
        for table in Base.metadata.sorted_tables
    } == before
    assert not session.new and not session.dirty and not session.deleted


@pytest.mark.parametrize(
    "change",
    [
        "version",
        "unknown_field",
        "duplicate_course",
        "duplicate_group",
        "duplicate_alias",
        "reverse_time",
        "tba_with_meetings",
        "scheduled_without_meetings",
        "evidence_missing",
        "date_pair",
        "overlap",
        "unknown_season",
        "empty_document",
    ],
)
def test_rejects_unsafe_extraction_shapes(payload, change):
    course = payload["courses"][0]
    group = course["groups"][0]
    meeting = group["meetings"][0]
    if change == "version":
        payload["schema_version"] = "2.0"
    elif change == "unknown_field":
        meeting["guess"] = True
    elif change == "duplicate_course":
        payload["courses"].append(copy.deepcopy(course))
    elif change == "duplicate_group":
        course["groups"].append(copy.deepcopy(group))
    elif change == "duplicate_alias":
        course["aliases"] = ["FIZ 1111"]
    elif change == "reverse_time":
        meeting["ends_at"] = "08:00:00"
    elif change == "tba_with_meetings":
        group["time_status"] = "tba"
    elif change == "scheduled_without_meetings":
        group["meetings"] = []
    elif change == "evidence_missing":
        meeting["evidence"].pop("page")
    elif change == "date_pair":
        meeting["starts_on"] = "2026-09-21"
    elif change == "overlap":
        other = copy.deepcopy(meeting)
        other["starts_at"] = "10:00:00"
        group["meetings"].append(other)
    elif change == "unknown_season":
        payload["term"]["season"] = "winter"
    elif change == "empty_document":
        payload["courses"] = []
    with pytest.raises(ValidationError):
        ScheduleDocument.model_validate(payload)


def test_review_checks_source_hash_term_and_unresolved_material(session, payload):
    document = ScheduleDocument.model_validate(payload)
    assert review_document(session, document)["blockers"]
    assert review_document(session, document, source_file_sha256="0" * 64)["blockers"]
    payload["term"]["academic_year"] = "2027-2028"
    payload["issues"] = [
        {
            "severity": "blocking",
            "message": "Unreadable continuation page",
            "evidence": {"page": 2, "quote": "Continuation"},
        }
    ]
    report = document_and_review(session, payload)
    assert len(report["blockers"]) == 2


def test_group_restrictions_need_known_ids_and_resolution(session, payload):
    permissions = payload["courses"][0]["groups"][0]["permissions"]
    permissions.update(
        allowed_bologna_program_ids=[999],
        unresolved=True,
        evidence={"page": 1, "quote": "Only specified majors may take group 1"},
    )
    report = document_and_review(session, payload)
    assert len(report["blockers"]) == 2
    permissions.update(allowed_bologna_program_ids=[25], unresolved=False)
    assert document_and_review(session, payload)["ready_for_owner_review"]
    permissions["excluded_bologna_program_ids"] = [25]
    with pytest.raises(ValidationError):
        ScheduleDocument.model_validate(payload)


def test_ambiguous_code_requires_explicit_matching_owner_id(session, payload):
    other = Course(code="FIZ1111", normalized_code="FIZ1111", name="Different subject")
    session.add(other)
    session.commit()
    report = document_and_review(session, payload)
    assert not report["ready_for_owner_review"]
    assert len(report["resolved_courses"][0]["candidates"]) == 2
    payload["courses"][0]["catalog_course_id"] = other.id
    report = document_and_review(session, payload)
    assert report["ready_for_owner_review"]
    assert report["warnings"]  # Different source/catalog name remains visible for review.
    payload["courses"][0]["catalog_course_id"] = 999
    assert document_and_review(session, payload)["blockers"]


def test_revision_diff_reports_changes_removals_and_exact_baseline_hash(session, payload):
    baseline = ScheduleDocument.model_validate(payload)
    payload["source"].update(revision="example-2", replaces_revision="example-1")
    payload["courses"][0]["groups"][0]["meetings"][0]["room"] = "D-102"
    report = document_and_review(session, payload, baseline)
    assert report["ready_for_owner_review"]
    assert report["summary"]["changed"] == 1
    assert report["baseline_sha256"] == document_digest(baseline)
    assert report["changes"]["changed"][0]["before"]["groups"][0]["meetings"][0]["room"] == "D-101"
    payload["courses"] = []
    payload["withdraw_all"] = True
    report = document_and_review(session, payload, baseline)
    assert report["summary"]["removed"] == 1
    assert not document_and_review(session, payload)["ready_for_owner_review"]


def test_wrong_revision_scope_and_stale_parent_are_blocked(session, payload):
    baseline = ScheduleDocument.model_validate(payload)
    payload["source"].update(revision="example-3", replaces_revision="example-2", key="other")
    report = document_and_review(session, payload, baseline)
    assert len(report["blockers"]) == 2


def test_evidence_only_change_changes_hash_but_not_timetable(session, payload):
    baseline = ScheduleDocument.model_validate(payload)
    payload["source"].update(revision="example-2", replaces_revision="example-1")
    payload["courses"][0]["groups"][0]["evidence"]["page"] = 2
    report = document_and_review(session, payload, baseline)
    assert report["summary"]["unchanged"] == 1
    assert report["document_sha256"] != report["baseline_sha256"]


def test_duplicate_json_keys_are_not_silently_overwritten(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"courses": [], "courses": [{"code": "hidden"}]}')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        read_document(path)
