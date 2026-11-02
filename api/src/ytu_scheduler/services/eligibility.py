"""Student eligibility is evaluated per program, never from cached import status."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ytu_scheduler.models import (
    CurriculumSnapshot,
    ElectivePool,
    EligibilityRule,
    OfferingStatus,
    PoolCourse,
    Program,
    ProgramRequirement,
    RuleType,
)
from ytu_scheduler.services.program_sources import STUDENT_PROGRAM_IDS
from ytu_scheduler.services.snapshots import latest_snapshot_ids


def program_permissions(
    session: Session, program_ids: list[int]
) -> dict[int, dict[int, OfferingStatus]]:
    if not 1 <= len(program_ids) <= 2 or len(set(program_ids)) != len(program_ids):
        raise HTTPException(status_code=422, detail="Select one or two distinct programs")
    known = set(
        session.scalars(
            select(Program.id).where(
                Program.id.in_(program_ids), Program.bologna_program_id.in_(STUDENT_PROGRAM_IDS)
            )
        )
    )
    if known != set(program_ids):
        raise HTTPException(
            status_code=422,
            detail="Selected program is not available in the supported YÖK Atlas list",
        )

    permissions: dict[int, dict[int, OfferingStatus]] = {pid: {} for pid in program_ids}
    latest = latest_snapshot_ids(session, program_ids)
    requirements = session.execute(
        select(CurriculumSnapshot.program_id, ProgramRequirement.course_id)
        .join(ProgramRequirement, ProgramRequirement.snapshot_id == CurriculumSnapshot.id)
        .where(CurriculumSnapshot.id.in_(latest))
    )
    electives = session.execute(
        select(CurriculumSnapshot.program_id, PoolCourse.course_id)
        .join(ElectivePool, ElectivePool.snapshot_id == CurriculumSnapshot.id)
        .join(PoolCourse, PoolCourse.pool_id == ElectivePool.id)
        .where(CurriculumSnapshot.id.in_(latest))
    )
    for pid, course_id in [*requirements, *electives]:
        if course_id is not None:
            permissions[pid][course_id] = OfferingStatus.ELIGIBLE

    rules = list(
        session.scalars(
            select(EligibilityRule).where(
                EligibilityRule.active.is_(True),
                (EligibilityRule.program_id.is_(None))
                | EligibilityRule.program_id.in_(program_ids),
            )
        )
    )
    for pid in program_ids:
        applicable = [rule for rule in rules if rule.program_id in (None, pid)]
        for rule in applicable:
            if rule.rule_type == RuleType.EXCEPTION:
                permissions[pid][rule.course_id] = OfferingStatus.APPROVED_EXCEPTION
        # Explicit exclusions win within this major; double majors union afterwards.
        for rule in applicable:
            if rule.rule_type == RuleType.EXCLUSION:
                permissions[pid].pop(rule.course_id, None)
    return permissions
