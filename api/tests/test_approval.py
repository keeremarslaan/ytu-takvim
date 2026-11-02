from datetime import UTC, datetime, time

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ytu_scheduler.database import Base
from ytu_scheduler.extraction_schemas import ExtractedSchedule
from ytu_scheduler.models import (
    AcademicTerm,
    Course,
    CurriculumSnapshot,
    EligibilityRule,
    ImportStatus,
    OfferingStatus,
    Program,
    ProgramRequirement,
    RequirementKind,
    RuleType,
    ScheduleImport,
    Season,
)
from ytu_scheduler.services.approval import approve_extraction


def test_approval_creates_meetings_and_applies_global_exclusion() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        program = Program(
            name="Statistics",
            department="İstatistik",
            bologna_program_id=9,
            academic_unit_id=11,
            source_url="https://example.test/statistics",
        )
        course = Course(
            bologna_course_id=12264,
            code="IST3557",
            normalized_code="IST3557",
            name="İstatistik ve Bilimsel Düşünme",
        )
        term = AcademicTerm(
            code="2025-2026-fall",
            academic_year="2025-2026",
            season=Season.FALL,
            timezone="Europe/Istanbul",
        )
        session.add_all([program, course, term])
        session.flush()
        snapshot = CurriculumSnapshot(
            program_id=program.id,
            source_hash="a" * 64,
            fetched_at=datetime.now(UTC),
        )
        session.add(snapshot)
        session.flush()
        session.add(
            ProgramRequirement(
                snapshot_id=snapshot.id,
                course_id=course.id,
                kind=RequirementKind.COMPULSORY,
                year=3,
                season=Season.FALL,
                semester_number=5,
                source_order=1,
            )
        )
        schedule_import = ScheduleImport(
            term_id=term.id,
            department="İstatistik",
            original_filename="statistics.pdf",
            object_key="statistics.pdf",
            file_hash="b" * 64,
            status=ImportStatus.NEEDS_REVIEW,
        )
        session.add(schedule_import)
        session.commit()

        extraction = ExtractedSchedule(
            department="İstatistik",
            academic_year="2025-2026",
            season=Season.FALL,
            eligibility_exclusions=[
                {
                    "course_code": "IST3557",
                    "reason": "İstatistik Bölümü öğrencileri bu dersi alamazlar.",
                    "applies_globally_to_profile": True,
                    "evidence": {"page": 1, "raw_text": "IST3557 dersini alamazlar"},
                }
            ],
            courses=[
                {
                    "code": "IST3557",
                    "name": "İstatistik ve Bilimsel Düşünme",
                    "curriculum_year": 3,
                    "groups": [
                        {
                            "label": "Gr.1",
                            "time_status": "scheduled",
                            "confidence": 0.98,
                            "evidence": {"page": 2, "raw_text": "IST3557 Gr.1"},
                            "meetings": [
                                {
                                    "weekday": 4,
                                    "starts_at": time(13, 0),
                                    "ends_at": time(14, 50),
                                    "room": "BZ-D05",
                                    "delivery_mode": "in_person",
                                    "confidence": 0.98,
                                    "evidence": {"page": 2, "raw_text": "13.00-14.50"},
                                }
                            ],
                        }
                    ],
                }
            ],
        )

        offerings = approve_extraction(session, schedule_import, extraction)

        assert offerings[0].status == OfferingStatus.EXCLUDED
        assert len(offerings[0].options) == 1
        assert offerings[0].options[0].meetings[0].starts_at == time(13, 0)
        assert session.scalar(select(EligibilityRule)).reason.startswith("İstatistik")
        assert schedule_import.status == ImportStatus.APPROVED

        session.add(
            EligibilityRule(
                course_id=course.id,
                rule_type=RuleType.EXCEPTION,
                reason="Department approval recorded by the student.",
                active=True,
            )
        )
        session.commit()

        overridden = approve_extraction(session, schedule_import, extraction)

        assert overridden[0].status == OfferingStatus.APPROVED_EXCEPTION
