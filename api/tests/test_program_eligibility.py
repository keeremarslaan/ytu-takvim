from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ytu_scheduler.database import Base, get_session
from ytu_scheduler.models import (
    AcademicTerm,
    Course,
    CurriculumSnapshot,
    ElectivePool,
    EligibilityRule,
    EnrollmentOption,
    Offering,
    OfferingStatus,
    PoolCourse,
    Program,
    ProgramRequirement,
    RequirementKind,
    RuleType,
    Season,
    TimeStatus,
)
from ytu_scheduler.routers.schedules import (
    generate_from_offerings,
    list_selectable_offerings,
    router,
)
from ytu_scheduler.schemas import GenerateFromOfferingsRequest


@pytest.fixture
def catalog():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        programs = [
            Program(
                name=f"Major {i}",
                department=f"Dept {i}",
                bologna_program_id={1: 25, 2: 9}[i],
                academic_unit_id=i,
                source_url="test",
            )
            for i in (1, 2)
        ]
        term = AcademicTerm(code="test", academic_year="2026-2027", season=Season.FALL)
        courses = [
            Course(code=code, normalized_code=code, name=code)
            for code in ("OLD", "FIRST", "SECOND", "SHARED", "ELECTIVE", "EXTERNAL")
        ]
        session.add_all([*programs, term, *courses])
        session.flush()
        snapshots = [
            CurriculumSnapshot(
                program_id=programs[index].id, source_hash=str(i), fetched_at=datetime.now(UTC)
            )
            for i, index in enumerate((0, 0, 1))
        ]
        session.add_all(snapshots)
        session.flush()
        for snapshot, indexes in (
            (snapshots[0], [0]),
            (snapshots[1], [1, 3]),
            (snapshots[2], [2, 3]),
        ):
            for index in indexes:
                session.add(
                    ProgramRequirement(
                        snapshot_id=snapshot.id,
                        course_id=courses[index].id,
                        kind=RequirementKind.COMPULSORY,
                        year=1,
                        season=Season.FALL,
                        semester_number=1,
                        source_order=index,
                    )
                )
        pool = ElectivePool(
            snapshot_id=snapshots[1].id, source_pool_id="elective", name="Mesleki Seçmeli"
        )
        session.add(pool)
        session.flush()
        session.add(PoolCourse(pool_id=pool.id, course_id=courses[4].id))
        offerings = [
            Offering(
                term_id=term.id,
                course_id=course.id,
                raw_code=course.code,
                raw_name=course.name,
                department="Unrelated publishing department",
                approved=True,
                status=OfferingStatus.INELIGIBLE,
            )
            for course in courses
        ]
        session.add_all(offerings)
        session.flush()
        session.add_all(
            [
                EnrollmentOption(
                    offering_id=offering.id, group_label="1", time_status=TimeStatus.TBA
                )
                for offering in offerings
            ]
        )
        session.commit()
        yield session, programs, term, courses, offerings
    engine.dispose()


def codes(session, term, programs):
    return {item.course_code for item in list_selectable_offerings(session, term.id, programs)}


def test_latest_curriculum_electives_and_double_major_union(catalog):
    session, programs, term, _, _ = catalog
    first, second = [p.id for p in programs]
    assert codes(session, term, [first]) == {"FIRST", "SHARED", "ELECTIVE"}
    assert codes(session, term, [second]) == {"SECOND", "SHARED"}
    combined = list_selectable_offerings(session, term.id, [first, second])
    assert len(combined) == 4
    assert next(item for item in combined if item.course_code == "SHARED").majors == [
        "Dept 1",
        "Dept 2",
    ]
    assert codes(session, term, [first, second]) == {"FIRST", "SECOND", "SHARED", "ELECTIVE"}


def test_scoped_rules_apply_immediately_and_do_not_leak_between_majors(catalog):
    session, programs, term, courses, _ = catalog
    first, second = [p.id for p in programs]
    session.add_all(
        [
            EligibilityRule(
                course_id=courses[5].id,
                program_id=first,
                rule_type=RuleType.EXCEPTION,
                reason="allowed",
                active=True,
            ),
            EligibilityRule(
                course_id=courses[3].id,
                program_id=first,
                rule_type=RuleType.EXCLUSION,
                reason="excluded",
                active=True,
            ),
            EligibilityRule(
                course_id=courses[3].id,
                program_id=first,
                rule_type=RuleType.EXCEPTION,
                reason="conflicting rule",
                active=True,
            ),
            EligibilityRule(
                course_id=courses[2].id,
                program_id=second,
                rule_type=RuleType.EXCLUSION,
                reason="inactive",
                active=False,
            ),
        ]
    )
    session.commit()
    assert codes(session, term, [first]) == {"FIRST", "ELECTIVE", "EXTERNAL"}
    assert codes(session, term, [second]) == {"SECOND", "SHARED"}
    both = list_selectable_offerings(session, term.id, [first, second])
    assert next(item for item in both if item.course_code == "SHARED").majors == ["Dept 2"]
    session.add(
        EligibilityRule(
            course_id=courses[3].id, rule_type=RuleType.EXCLUSION, reason="global", active=True
        )
    )
    session.commit()
    assert "SHARED" not in codes(session, term, [first, second])


def test_generation_rechecks_permissions_and_approval(catalog):
    session, programs, _, _, offerings = catalog
    first = programs[0].id
    # The import's stale INELIGIBLE status must not override the current curriculum.
    allowed = GenerateFromOfferingsRequest(program_ids=[first], offering_ids=[offerings[1].id])
    assert generate_from_offerings(allowed, session).schedules
    for offering in (offerings[0], offerings[2], offerings[5]):
        with pytest.raises(HTTPException) as error:
            generate_from_offerings(
                GenerateFromOfferingsRequest(program_ids=[first], offering_ids=[offering.id]),
                session,
            )
        assert error.value.status_code == 422
    offerings[1].approved = False
    session.commit()
    with pytest.raises(HTTPException) as error:
        generate_from_offerings(allowed, session)
    assert error.value.status_code == 422


def test_http_contract_requires_valid_program_selection(catalog):
    session, programs, term, _, offerings = catalog
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        path = f"/schedules/offerings?term_id={term.id}"
        for suffix in (
            "",
            "&program_ids=999",
            "&program_ids=1&program_ids=1",
            "&program_ids=1&program_ids=2&program_ids=3",
        ):
            assert client.get(path + suffix).status_code == 422
        response = client.get(path + f"&program_ids={programs[0].id}")
        assert response.status_code == 200
        assert {item["course_code"] for item in response.json()} == {"FIRST", "SHARED", "ELECTIVE"}
        assert (
            client.post(
                "/schedules/generate-from-offerings",
                json={
                    "offering_ids": [offerings[1].id],
                },
            ).status_code
            == 422
        )
