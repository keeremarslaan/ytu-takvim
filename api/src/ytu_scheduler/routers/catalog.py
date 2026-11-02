from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ytu_scheduler.auth import require_owner
from ytu_scheduler.config import get_settings
from ytu_scheduler.database import get_session
from ytu_scheduler.models import (
    Course,
    CurriculumSnapshot,
    ElectivePool,
    EligibilityRule,
    PoolCourse,
    Program,
    ProgramRequirement,
    RequirementKind,
)
from ytu_scheduler.schemas import (
    CatalogSummary,
    CourseCatalogItem,
    CourseEligibilityOut,
    CourseOut,
    EligibilityRuleCreate,
    EligibilityRuleOut,
    ProgramOut,
    SyncResult,
)
from ytu_scheduler.services.bologna import BolognaService
from ytu_scheduler.services.coverage import catalog_coverage
from ytu_scheduler.services.program_sources import STUDENT_PROGRAM_IDS
from ytu_scheduler.services.snapshots import latest_snapshot_ids

router = APIRouter(prefix="/catalog", tags=["catalog"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("/programs", response_model=list[ProgramOut])
def list_programs(session: SessionDependency) -> list[ProgramOut]:
    coverage = catalog_coverage(session)
    readiness = {p["program_id"]: p for p in coverage["programs"]}
    return [ProgramOut.model_validate(program).model_copy(update={
        "curriculum_ready": readiness[program.id]["status"] == "ready",
        "curriculum_available": readiness[program.id]["snapshot_id"] is not None,
    }) for program in session.scalars(select(Program).order_by(Program.name))
        if program.id in readiness and program.bologna_program_id in STUDENT_PROGRAM_IDS]


@router.get("/coverage")
def coverage(session: SessionDependency, term_id: int | None = None) -> dict:
    return catalog_coverage(session, term_id)


@router.get("/summary", response_model=CatalogSummary)
def catalog_summary(session: SessionDependency) -> CatalogSummary:
    snapshot_ids = latest_snapshot_ids(session)
    requirement_filter = ProgramRequirement.snapshot_id.in_(snapshot_ids)
    return CatalogSummary(
        programs=session.scalar(select(func.count()).select_from(Program)) or 0,
        courses=session.scalar(select(func.count()).select_from(Course)) or 0,
        compulsory_requirements=(
            session.scalar(
                select(func.count()).select_from(ProgramRequirement).where(
                    requirement_filter,
                    ProgramRequirement.kind == RequirementKind.COMPULSORY,
                )
            )
            or 0
        ),
        elective_slots=(
            session.scalar(
                select(func.count()).select_from(ProgramRequirement).where(
                    requirement_filter,
                    ProgramRequirement.kind == RequirementKind.ELECTIVE_SLOT,
                )
            )
            or 0
        ),
        elective_pools=(
            session.scalar(
                select(func.count()).select_from(ElectivePool).where(
                    ElectivePool.snapshot_id.in_(snapshot_ids)
                )
            )
            or 0
        ),
        last_synced_at=session.scalar(select(func.max(CurriculumSnapshot.fetched_at))),
    )


@router.get("/courses", response_model=list[CourseCatalogItem])
def list_courses(
    session: SessionDependency,
    query: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[CourseCatalogItem]:
    statement = select(Course).order_by(Course.code).limit(limit)
    if query:
        value = f"%{query.strip()}%"
        statement = statement.where(or_(Course.code.ilike(value), Course.name.ilike(value)))
    courses = list(session.scalars(statement))
    if not courses:
        return []

    course_ids = [course.id for course in courses]
    snapshot_ids = latest_snapshot_ids(session)
    eligibility: dict[int, list[CourseEligibilityOut]] = {course.id: [] for course in courses}
    compulsory_rows = session.execute(
        select(ProgramRequirement, Program)
        .join(CurriculumSnapshot, CurriculumSnapshot.id == ProgramRequirement.snapshot_id)
        .join(Program, Program.id == CurriculumSnapshot.program_id)
        .where(
            ProgramRequirement.snapshot_id.in_(snapshot_ids),
            ProgramRequirement.course_id.in_(course_ids),
        )
    )
    for requirement, program in compulsory_rows:
        eligibility[requirement.course_id].append(
            CourseEligibilityOut(
                program=program.department,
                kind=requirement.kind,
                year=requirement.year,
                season=requirement.season,
            )
        )

    pool_rows = session.execute(
        select(PoolCourse.course_id, ElectivePool.name, Program.department)
        .join(ElectivePool, ElectivePool.id == PoolCourse.pool_id)
        .join(CurriculumSnapshot, CurriculumSnapshot.id == ElectivePool.snapshot_id)
        .join(Program, Program.id == CurriculumSnapshot.program_id)
        .where(
            ElectivePool.snapshot_id.in_(snapshot_ids),
            PoolCourse.course_id.in_(course_ids),
        )
    )
    for course_id, pool_name, department in pool_rows:
        eligibility[course_id].append(
            CourseEligibilityOut(
                program=department,
                kind=RequirementKind.ELECTIVE_SLOT,
                pool=pool_name,
            )
        )

    return [
        CourseCatalogItem(
            **CourseOut.model_validate(course).model_dump(),
            eligibility=eligibility[course.id],
        )
        for course in courses
    ]


@router.post("/sync", response_model=SyncResult, dependencies=[Depends(require_owner)])
def sync_catalog(session: SessionDependency) -> SyncResult:
    service = BolognaService(get_settings())
    try:
        return SyncResult(programs=service.sync_all(session))
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/courses/{course_id}/rules", response_model=list[EligibilityRuleOut])
def list_eligibility_rules(
    course_id: int,
    session: SessionDependency,
) -> list[EligibilityRule]:
    return list(
        session.scalars(
            select(EligibilityRule)
            .where(EligibilityRule.course_id == course_id)
            .order_by(EligibilityRule.created_at.desc())
        )
    )


@router.post(
    "/courses/{course_id}/rules",
    dependencies=[Depends(require_owner)],
    response_model=EligibilityRuleOut,
    status_code=status.HTTP_201_CREATED,
)
def create_eligibility_rule(
    course_id: int,
    payload: EligibilityRuleCreate,
    session: SessionDependency,
) -> EligibilityRule:
    if session.get(Course, course_id) is None:
        raise HTTPException(status_code=404, detail="Course not found")
    if payload.program_id is not None and session.get(Program, payload.program_id) is None:
        raise HTTPException(status_code=404, detail="Program not found")
    rule = EligibilityRule(course_id=course_id, active=True, **payload.model_dump())
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule
