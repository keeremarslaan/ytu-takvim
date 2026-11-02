from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ytu_scheduler.auth import require_owner
from ytu_scheduler.database import get_session
from ytu_scheduler.models import (
    CurriculumSnapshot,
    ElectivePool,
    EnrollmentOption,
    Meeting,
    Offering,
    OfferingStatus,
    PoolCourse,
    Program,
    ProgramRequirement,
    RequirementKind,
    TimeStatus,
)
from ytu_scheduler.schemas import (
    CompulsoryPlacementOut,
    CourseSelectionInput,
    EnrollmentOptionOut,
    GenerateFromOfferingsRequest,
    GenerateScheduleRequest,
    GenerateScheduleResponse,
    ManualEnrollmentOptionCreate,
    MeetingInput,
    OfferingOut,
    OptionInput,
)
from ytu_scheduler.services.curriculum_metadata import pool_category as _pool_category
from ytu_scheduler.services.eligibility import program_permissions
from ytu_scheduler.services.snapshots import latest_snapshot_ids
from ytu_scheduler.services.solver import generate_schedules

router = APIRouter(prefix="/schedules", tags=["schedules"])
SessionDependency = Annotated[Session, Depends(get_session)]
SELECTABLE_STATUSES = {OfferingStatus.ELIGIBLE, OfferingStatus.APPROVED_EXCEPTION}


@router.post(
    "/offerings/{offering_id}/options",
    response_model=EnrollmentOptionOut,
    status_code=201,
    dependencies=[Depends(require_owner)],
)
def create_manual_option(
    offering_id: int,
    payload: ManualEnrollmentOptionCreate,
    session: SessionDependency,
) -> EnrollmentOptionOut:
    offering = session.get(Offering, offering_id)
    if offering is None:
        raise HTTPException(status_code=404, detail="Offering not found")
    if not offering.approved or offering.status not in SELECTABLE_STATUSES:
        raise HTTPException(status_code=422, detail="Only selectable offerings can receive groups")

    option = EnrollmentOption(
        offering_id=offering.id,
        group_label=payload.group_label.strip(),
        instructor=payload.instructor.strip() if payload.instructor else None,
        time_status=TimeStatus.SCHEDULED,
        confidence=1.0,
        raw_text="Manually added",
    )
    session.add(option)
    session.flush()
    for meeting in payload.meetings:
        session.add(
            Meeting(
                option_id=option.id,
                weekday=meeting.weekday,
                starts_at=meeting.starts_at,
                ends_at=meeting.ends_at,
                starts_on=meeting.starts_on,
                ends_on=meeting.ends_on,
                room=meeting.room,
                delivery_mode=meeting.delivery_mode,
                confidence=1.0,
                raw_text="Manually added",
            )
        )
    session.commit()
    session.refresh(option)
    return EnrollmentOptionOut.model_validate(option)


@router.post("/generate", response_model=GenerateScheduleResponse)
def generate(payload: GenerateScheduleRequest) -> GenerateScheduleResponse:
    return generate_schedules(payload)


def _option_signature(option: EnrollmentOption) -> tuple:
    meetings = tuple(
        sorted(
            (
                meeting.weekday,
                meeting.starts_at,
                meeting.ends_at,
                meeting.starts_on,
                meeting.ends_on,
                meeting.room,
                meeting.delivery_mode.value,
            )
            for meeting in option.meetings
        )
    )
    return option.group_label.casefold().strip(), meetings


def _unique_options(offerings: list[Offering]) -> list[EnrollmentOption]:
    options: dict[tuple, EnrollmentOption] = {}
    for offering in offerings:
        for option in offering.options:
            options.setdefault(_option_signature(option), option)
    return sorted(options.values(), key=lambda option: (option.group_label, option.id))



def _curriculum_metadata(
    session: Session, course_ids: list[int], permissions: dict[int, dict[int, OfferingStatus]]
) -> tuple[
    dict[int, set[str]],
    dict[int, list[CompulsoryPlacementOut]],
    dict[int, set[str]],
]:
    categories = {course_id: set() for course_id in course_ids}
    placements: dict[int, list[CompulsoryPlacementOut]] = {
        course_id: [] for course_id in course_ids
    }
    majors = {course_id: set() for course_id in course_ids}
    snapshot_ids = latest_snapshot_ids(session, list(permissions))

    compulsory_rows = session.execute(
        select(
            ProgramRequirement.course_id,
            Program.id,
            Program.department,
            ProgramRequirement.year,
            ProgramRequirement.season,
        )
        .join(CurriculumSnapshot, CurriculumSnapshot.id == ProgramRequirement.snapshot_id)
        .join(Program, Program.id == CurriculumSnapshot.program_id)
        .where(
            ProgramRequirement.snapshot_id.in_(snapshot_ids),
            ProgramRequirement.course_id.in_(course_ids),
            ProgramRequirement.kind == RequirementKind.COMPULSORY,
        )
    )
    seen_placements: dict[int, set[tuple[str, int, object]]] = {
        course_id: set() for course_id in course_ids
    }
    for course_id, program_id, department, year, season in compulsory_rows:
        if course_id not in permissions[program_id]:
            continue
        categories[course_id].add("compulsory")
        majors[course_id].add(department)
        signature = (department, year, season)
        if signature not in seen_placements[course_id]:
            seen_placements[course_id].add(signature)
            placements[course_id].append(
                CompulsoryPlacementOut(program=department, year=year, season=season)
            )

    pool_rows = session.execute(
        select(PoolCourse.course_id, ElectivePool.name, Program.id, Program.department)
        .join(ElectivePool, ElectivePool.id == PoolCourse.pool_id)
        .join(CurriculumSnapshot, CurriculumSnapshot.id == ElectivePool.snapshot_id)
        .join(Program, Program.id == CurriculumSnapshot.program_id)
        .where(
            ElectivePool.snapshot_id.in_(snapshot_ids),
            PoolCourse.course_id.in_(course_ids),
        )
    )
    for course_id, pool_name, program_id, department in pool_rows:
        if course_id not in permissions[program_id]:
            continue
        majors[course_id].add(department)
        if category := _pool_category(pool_name):
            categories[course_id].add(category)

    return categories, placements, majors


def _offering_out(
    offerings: list[Offering],
    categories: set[str],
    compulsory_placements: list[CompulsoryPlacementOut],
    majors: set[str],
    eligibility_status: OfferingStatus,
) -> OfferingOut:
    representative = offerings[0]
    if representative.course is None:
        raise ValueError("Approved offering has no reconciled course")
    return OfferingOut(
        id=representative.id,
        offering_ids=[offering.id for offering in offerings],
        term_id=representative.term_id,
        course_id=representative.course.id,
        course_code=representative.course.code,
        course_name=representative.course.name,
        departments=sorted({offering.department for offering in offerings}),
        majors=sorted(majors),
        status=eligibility_status,
        prerequisite_text=representative.course.prerequisite_text,
        categories=[
            category
            for category in ("compulsory", "professional_elective", "social_elective")
            if category in categories
        ],
        compulsory_placements=sorted(
            compulsory_placements,
            key=lambda placement: (placement.year, placement.season, placement.program),
        ),
        options=[
            EnrollmentOptionOut.model_validate(option) for option in _unique_options(offerings)
        ],
    )


@router.get("/offerings", response_model=list[OfferingOut])
def list_selectable_offerings(
    session: SessionDependency,
    term_id: Annotated[int, Query()],
    program_ids: Annotated[list[int], Query(min_length=1, max_length=2)],
) -> list[OfferingOut]:
    permissions = program_permissions(session, program_ids)
    allowed = {cid: value for courses in permissions.values() for cid, value in courses.items()}
    offerings = list(
        session.scalars(
            select(Offering)
            .options(
                selectinload(Offering.course),
                selectinload(Offering.options).selectinload(EnrollmentOption.meetings),
            )
            .where(
                Offering.term_id == term_id,
                Offering.approved.is_(True),
                Offering.course_id.in_(allowed),
            )
            .order_by(Offering.raw_code)
        )
    )
    by_course: dict[int, list[Offering]] = {}
    for offering in offerings:
        if offering.course_id is not None:
            by_course.setdefault(offering.course_id, []).append(offering)
    categories, placements, majors = _curriculum_metadata(session, list(by_course), permissions)
    return [
        _offering_out(
            course_offerings,
            categories[course_offerings[0].course_id],
            placements[course_offerings[0].course_id],
            majors[course_offerings[0].course_id],
            allowed[course_offerings[0].course_id],
        )
        for course_offerings in sorted(
            by_course.values(), key=lambda items: items[0].course.code if items[0].course else ""
        )
    ]


@router.post("/generate-from-offerings", response_model=GenerateScheduleResponse)
def generate_from_offerings(
    payload: GenerateFromOfferingsRequest,
    session: SessionDependency,
) -> GenerateScheduleResponse:
    permissions = program_permissions(session, payload.program_ids)
    allowed = {cid for courses in permissions.values() for cid in courses}
    offerings = list(
        session.scalars(
            select(Offering)
            .options(
                selectinload(Offering.course),
                selectinload(Offering.options).selectinload(EnrollmentOption.meetings),
            )
            .where(Offering.id.in_(payload.offering_ids))
        )
    )
    by_id = {offering.id: offering for offering in offerings}
    if missing := set(payload.offering_ids) - set(by_id):
        raise HTTPException(status_code=404, detail=f"Offerings not found: {sorted(missing)}")
    if len({offering.term_id for offering in offerings}) != 1:
        raise HTTPException(status_code=422, detail="All offerings must belong to the same term")
    blocked = [
        offering.raw_code for offering in offerings
        if not offering.approved or offering.course_id not in allowed
    ]
    if blocked:
        raise HTTPException(
            status_code=422,
            detail=f"These offerings are not eligible: {', '.join(blocked)}",
        )

    selected_course_ids = [offering.course_id for offering in offerings]
    if None in selected_course_ids:
        raise HTTPException(status_code=422, detail="One or more offerings are unresolved")
    if len(set(selected_course_ids)) != len(selected_course_ids):
        raise HTTPException(status_code=422, detail="A course can only be selected once")

    term_id = offerings[0].term_id
    merged_offerings = list(
        session.scalars(
            select(Offering)
            .options(
                selectinload(Offering.course),
                selectinload(Offering.options).selectinload(EnrollmentOption.meetings),
            )
            .where(
                Offering.term_id == term_id,
                Offering.course_id.in_(selected_course_ids),
                Offering.approved.is_(True),
                Offering.course_id.in_(allowed),
            )
        )
    )
    all_by_course: dict[int, list[Offering]] = {}
    for offering in merged_offerings:
        if offering.course_id is not None:
            all_by_course.setdefault(offering.course_id, []).append(offering)

    courses = []
    for offering_id in payload.offering_ids:
        offering = by_id[offering_id]
        if offering.course is None:
            raise HTTPException(status_code=422, detail=f"{offering.raw_code} is unresolved")
        options = _unique_options(all_by_course[offering.course.id])
        courses.append(
            CourseSelectionInput(
                course_id=offering.course.id,
                code=offering.course.code,
                name=offering.course.name,
                options=[
                    OptionInput(
                        id=option.id,
                        label=option.group_label,
                        untimed=not option.meetings,
                        meetings=[
                            MeetingInput(
                                weekday=meeting.weekday,
                                starts_at=meeting.starts_at,
                                ends_at=meeting.ends_at,
                                starts_on=meeting.starts_on,
                                ends_on=meeting.ends_on,
                                room=meeting.room,
                                delivery_mode=meeting.delivery_mode,
                            )
                            for meeting in option.meetings
                        ],
                    )
                    for option in options
                ],
            )
        )

    return generate_schedules(
        GenerateScheduleRequest(
            courses=courses,
            preferences=payload.preferences,
            max_results=payload.max_results,
            enumeration_limit=payload.enumeration_limit,
        )
    )
