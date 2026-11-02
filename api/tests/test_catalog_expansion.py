from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from ytu_scheduler.config import Settings
from ytu_scheduler.database import Base
from ytu_scheduler.models import Course, CurriculumSnapshot, Program
from ytu_scheduler.services import bologna
from ytu_scheduler.services.approval import find_course
from ytu_scheduler.services.bologna import (
    BolognaService,
    curriculum_issues,
    parse_curriculum,
    validate_curriculum,
)
from ytu_scheduler.services.coverage import catalog_coverage
from ytu_scheduler.services.program_sources import (
    PROGRAM_SOURCES,
    ProgramSource,
    parse_program_directory,
)
from ytu_scheduler.services.snapshots import latest_snapshot_ids


def complete_html(course_id=1, code="ABC1001", name="Test Course"):
    rows = []
    for year in range(1, 5):
        for season in ("Güz", "Bahar"):
            rows.append(f"""<tr class="table_semester_heading"><th>{year}.Yıl - {season}</th></tr>
                <tr><td><a href="?r=course/view&amp;id={course_id}">{code}</a></td>
                <td></td><td>{name}</td><td>3</td><td>0</td><td>0</td><td>3</td><td>5</td></tr>""")
    return '<div id="curriculum"><table>' + "".join(rows) + "</table></div>"


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_reviewed_manifest_matches_real_bachelor_directory():
    html = (Path(__file__).parent / "fixtures/bologna-bachelors.html").read_text()
    sources = parse_program_directory(html)
    assert set(sources) == set(PROGRAM_SOURCES)
    assert len(sources) == 63
    assert 136 not in {s.program_id for s in sources}  # Scientific preparation, not a degree.
    assert {29, 403} <= {s.program_id for s in sources}  # Distinct language tracks retained.
    with pytest.raises(ValueError):
        parse_program_directory('<div id="content">Lisans Programları</div>')


def test_invalid_curriculum_does_not_replace_existing_snapshot(session):
    service = BolognaService(Settings())
    source = PROGRAM_SOURCES[0]
    first = service.sync_html(session, source, complete_html())
    with pytest.raises(ValueError, match="eight"):
        service.sync_html(session, source, '<div id="curriculum"><table></table></div>')
    assert latest_snapshot_ids(session) == [first.snapshot_id]
    assert session.scalar(select(func.count()).select_from(CurriculumSnapshot)) == 1


def test_empty_elective_pool_is_reported_and_unknown_semester_is_rejected():
    html = complete_html().replace(
        'href="?r=course/view&amp;id=1"', 'class="elective" href="?id="', 1
    )
    parsed = parse_curriculum(html)
    validate_curriculum(parsed)
    assert "missing or empty" in curriculum_issues(parsed)[0]
    with pytest.raises(ValueError, match="heading"):
        parse_curriculum(complete_html().replace("1.Yıl - Güz", "Unknown semester"))


def test_unlinked_elective_slot_is_preserved_without_guessing_permissions(session):
    html = complete_html().replace('href="?r=course/view&amp;id=1"', 'onclick="return false;"', 1)
    html = html.replace("Test Course", "Mesleki Secmeli", 1)
    parsed = parse_curriculum(html)
    assert parsed.requirements[0].pool_source_id is None
    assert parsed.requirements[0].course_source_id is None
    assert len(curriculum_issues(parsed)) == 1
    BolognaService(Settings()).sync_html(session, PROGRAM_SOURCES[0], html)
    coverage = catalog_coverage(session)
    assert coverage["programs"][0]["status"] == "incomplete"
    assert coverage["programs"][0]["requirements"] == 8


def test_shared_codes_unify_only_matching_names_and_ambiguous_codes_need_review(session):
    service = BolognaService(Settings())
    for index, name in enumerate(("Türkçe 2", "Türkçe II", "Different Course"), start=1):
        source = ProgramSource(f"Test {index}", "Test", index, index)
        service.sync_html(session, source, complete_html(index, "TDB1032", name))
    assert session.scalar(select(func.count()).select_from(Course)) == 2
    with pytest.raises(ValueError, match="Ambiguous"):
        find_course(session, ["TDB1032"])


def test_reverted_source_becomes_current_without_duplicate_snapshot(session):
    service = BolognaService(Settings())
    source = PROGRAM_SOURCES[0]
    first = service.sync_html(session, source, complete_html())
    second = service.sync_html(session, source, complete_html(name="Changed Name"))
    assert latest_snapshot_ids(session) == [second.snapshot_id]
    reverted = service.sync_html(session, source, complete_html())
    assert reverted.unchanged
    assert latest_snapshot_ids(session) == [first.snapshot_id]
    assert session.scalar(select(func.count()).select_from(CurriculumSnapshot)) == 2
    assert session.scalar(select(Course.name)) == "Test Course"


def test_coverage_exposes_expected_missing_and_ready_programs(session):
    service = BolognaService(Settings())
    initial = catalog_coverage(session)
    assert initial["expected_programs"] == 63
    assert initial["ready_programs"] == 0
    service.register_programs(session)
    assert session.scalar(select(func.count()).select_from(Program)) == 63
    service.sync_html(session, PROGRAM_SOURCES[0], complete_html())
    coverage = catalog_coverage(session)
    assert coverage["ready_programs"] == 1
    assert coverage["programs"][0]["semesters"] == list(range(1, 9))
    assert coverage["programs"][1]["status"] == "missing"


def test_snapshot_order_uses_verification_time_not_insert_order(session):
    service = BolognaService(Settings())
    source = PROGRAM_SOURCES[0]
    first = service.sync_html(session, source, complete_html())
    second = service.sync_html(session, source, complete_html(name="New Name"))
    session.get(CurriculumSnapshot, first.snapshot_id).fetched_at = datetime.now(UTC) + timedelta(
        days=1
    )
    session.commit()
    assert latest_snapshot_ids(session) == [first.snapshot_id]
    assert first.snapshot_id < second.snapshot_id


def test_bulk_sync_keeps_successes_when_another_program_fails(session, monkeypatch):
    sources = (
        ProgramSource("One Lisans Programı", "One", 1, 1),
        ProgramSource("Two Lisans Programı", "Two", 2, 2),
    )
    directory = (
        '<div id="content">Lisans Programları'
        + "".join(
            f'<a href="?r=program/view&amp;id={s.program_id}&amp;aid={s.academic_unit_id}">'
            f'{s.name}</a>'
            for s in sources
        )
        + "</div>"
    )

    def respond(request):
        if request.url.params.get("r") == "program/bachelor":
            return httpx.Response(200, text=directory)
        if request.url.params.get("id") == "1":
            return httpx.Response(503)
        return httpx.Response(200, text=complete_html())

    client_type = httpx.Client
    monkeypatch.setattr(bologna, "PROGRAM_SOURCES", sources)
    monkeypatch.setattr(
        bologna.httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond)),
    )
    results = BolognaService(Settings(bologna_request_delay_seconds=0)).sync_all(session)
    assert results[0].error is not None
    assert results[1].snapshot_id is not None
    assert session.scalar(select(func.count()).select_from(Program)) == 2
    assert session.scalar(select(func.count()).select_from(CurriculumSnapshot)) == 1


def test_changed_directory_requires_review_before_writing(session, monkeypatch):
    directory = (
        '<div id="content">Lisans Programları'
        '<a href="?r=program/view&amp;id=999&amp;aid=999">New Lisans Programı</a></div>'
    )
    client_type = httpx.Client
    monkeypatch.setattr(
        bologna.httpx,
        "Client",
        lambda **kwargs: client_type(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=directory))
        ),
    )
    with pytest.raises(ValueError, match="directory changed"):
        BolognaService(Settings()).sync_all(session)
    assert session.scalar(select(func.count()).select_from(Program)) == 0
