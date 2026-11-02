from datetime import UTC, datetime, time

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ytu_scheduler.database import Base
from ytu_scheduler.models import (
    AcademicTerm,
    Course,
    CurriculumSnapshot,
    DeliveryMode,
    ElectivePool,
    EnrollmentOption,
    Meeting,
    Offering,
    OfferingStatus,
    PoolCourse,
    Program,
    ProgramRequirement,
    RequirementKind,
    Season,
    TimeStatus,
)
from ytu_scheduler.routers.schedules import (
    create_manual_option,
    generate_from_offerings,
    list_selectable_offerings,
)
from ytu_scheduler.schemas import (
    GenerateFromOfferingsRequest,
    ManualEnrollmentOptionCreate,
    MeetingInput,
)


def add_option(
    session: Session,
    offering: Offering,
    label: str,
    weekday: int,
) -> EnrollmentOption:
    option = EnrollmentOption(
        offering_id=offering.id,
        group_label=label,
        time_status=TimeStatus.SCHEDULED,
    )
    session.add(option)
    session.flush()
    session.add(
        Meeting(
            option_id=option.id,
            weekday=weekday,
            starts_at=time(9),
            ends_at=time(10, 50),
            delivery_mode=DeliveryMode.IN_PERSON,
        )
    )
    return option


def test_same_course_from_two_departments_is_one_choice_with_all_groups() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        term = AcademicTerm(
            code="2025-2026-fall",
            academic_year="2025-2026",
            season=Season.FALL,
            timezone="Europe/Istanbul",
        )
        course = Course(
            code="FIZ1111",
            normalized_code="FIZ1111",
            name="Fizik 1",
        )
        session.add_all([term, course])
        session.flush()
        program = Program(
            name="Physics",
            department="Physics",
            bologna_program_id=25,
            academic_unit_id=1,
            source_url="https://example.test/physics",
        )
        session.add(program)
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
                year=1,
                season=Season.FALL,
                semester_number=1,
                source_order=1,
            )
        )
        professional_pool = ElectivePool(
            snapshot_id=snapshot.id,
            source_pool_id="professional",
            name="Mesleki Seçmeli Dersleri",
        )
        social_pool = ElectivePool(
            snapshot_id=snapshot.id,
            source_pool_id="social",
            name="Üniversite Sosyal Seçmeli Dersleri",
        )
        session.add_all([professional_pool, social_pool])
        session.flush()
        session.add_all(
            [
                PoolCourse(pool_id=professional_pool.id, course_id=course.id),
                PoolCourse(pool_id=social_pool.id, course_id=course.id),
            ]
        )
        physics = Offering(
            term_id=term.id,
            course_id=course.id,
            raw_code=course.code,
            raw_name=course.name,
            department="Physics",
            status=OfferingStatus.ELIGIBLE,
            approved=True,
        )
        statistics = Offering(
            term_id=term.id,
            course_id=course.id,
            raw_code=course.code,
            raw_name=course.name,
            department="Statistics",
            status=OfferingStatus.ELIGIBLE,
            approved=True,
        )
        session.add_all([physics, statistics])
        session.flush()
        add_option(session, physics, "Gr.1", 1)
        add_option(session, statistics, "Gr.1", 1)
        add_option(session, statistics, "Gr.2", 2)
        session.commit()

        choices = list_selectable_offerings(session, term.id, [program.id])
        result = generate_from_offerings(
            GenerateFromOfferingsRequest(offering_ids=[physics.id], program_ids=[program.id]),
            session,
        )

        assert len(choices) == 1
        assert choices[0].departments == ["Physics", "Statistics"]
        assert choices[0].majors == ["Physics"]
        assert choices[0].offering_ids == [physics.id, statistics.id]
        assert choices[0].categories == [
            "compulsory",
            "professional_elective",
            "social_elective",
        ]
        assert len(choices[0].compulsory_placements) == 1
        assert choices[0].compulsory_placements[0].year == 1
        assert choices[0].compulsory_placements[0].season == Season.FALL
        assert [option.group_label for option in choices[0].options] == ["Gr.1", "Gr.2"]
        assert result.explored_solutions == 2
        assert {
            schedule.selections[0].option_label for schedule in result.schedules
        } == {"Gr.1", "Gr.2"}

        created = create_manual_option(
            physics.id,
            ManualEnrollmentOptionCreate(
                group_label="Gr.3",
                meetings=[
                    MeetingInput(
                        weekday=3,
                        starts_at=time(13),
                        ends_at=time(14, 50),
                        room="D-101",
                        delivery_mode=DeliveryMode.IN_PERSON,
                    )
                ],
            ),
            session,
        )
        refreshed = list_selectable_offerings(session, term.id, [program.id])

        assert created.group_label == "Gr.3"
        assert created.meetings[0].room == "D-101"
        assert [option.group_label for option in refreshed[0].options] == [
            "Gr.1",
            "Gr.2",
            "Gr.3",
        ]
