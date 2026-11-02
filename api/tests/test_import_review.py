from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ytu_scheduler.database import Base
from ytu_scheduler.extraction_schemas import ExtractedSchedule
from ytu_scheduler.models import (
    AcademicTerm,
    ImportStatus,
    Offering,
    OfferingStatus,
    ScheduleImport,
    Season,
)
from ytu_scheduler.routers import imports as import_routes
from ytu_scheduler.routers.imports import approve_schedule, get_import_source, save_reviewed_json


def test_reviewed_json_can_approve_a_provisional_course() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        term = AcademicTerm(
            code="2025-2026-fall",
            academic_year="2025-2026",
            season=Season.FALL,
            timezone="Europe/Istanbul",
        )
        session.add(term)
        session.flush()
        schedule_import = ScheduleImport(
            term_id=term.id,
            department="Outside Department",
            original_filename="outside.pdf",
            object_key="outside.pdf",
            file_hash="c" * 64,
            status=ImportStatus.NEEDS_REVIEW,
            extraction_payload={"deterministic": {"kind": "pdf"}},
        )
        session.add(schedule_import)
        session.commit()

        reviewed = ExtractedSchedule(
            department="Outside Department",
            courses=[
                {
                    "code": "EXT4001",
                    "name": "Approved External Topic",
                    "groups": [
                        {
                            "label": "Gr.1",
                            "time_status": "tba",
                            "confidence": 0.8,
                            "evidence": {"page": 1, "raw_text": "EXT4001 Gr.1"},
                        }
                    ],
                }
            ],
        )

        saved = save_reviewed_json(schedule_import.id, reviewed, session)
        result = approve_schedule(schedule_import.id, session, reviewed=None)

        assert saved.extraction_payload["structured_source"] == "reviewed_json"
        assert result.offerings_created == 1
        assert result.ineligible == 1
        offering = session.scalar(select(Offering))
        assert offering.status == OfferingStatus.INELIGIBLE
        assert offering.course.is_provisional is True


def test_original_import_source_is_available_for_visual_review(tmp_path, monkeypatch) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    source = tmp_path / "term" / "schedule.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF-1.4 source preview")
    monkeypatch.setattr(
        import_routes,
        "get_settings",
        lambda: SimpleNamespace(upload_dir=tmp_path),
    )

    with Session(engine) as session:
        term = AcademicTerm(
            code="2025-2026-fall",
            academic_year="2025-2026",
            season=Season.FALL,
            timezone="Europe/Istanbul",
        )
        session.add(term)
        session.flush()
        schedule_import = ScheduleImport(
            term_id=term.id,
            department="Physics",
            original_filename="schedule.pdf",
            object_key="term/schedule.pdf",
            file_hash="d" * 64,
            status=ImportStatus.NEEDS_REVIEW,
        )
        session.add(schedule_import)
        session.commit()

        response = get_import_source(schedule_import.id, session)

        assert Path(response.path) == source
        assert response.headers["content-disposition"].startswith("inline;")
