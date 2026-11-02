"""Verified curriculum roles from one latest snapshot; no inferred memberships."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ytu_scheduler.models import ElectivePool, PoolCourse, ProgramRequirement, RequirementKind


def pool_category(name: str) -> str | None:
    normalized = name.casefold().replace("ı", "i")
    if "sosyal" in normalized:
        return "social_elective"
    if "mesleki" in normalized or "mes-" in normalized or "mes1" in normalized:
        return "professional_elective"
    return None


def curriculum_roles(session: Session, snapshot_id: int | None) -> dict[int, list[dict]]:
    if snapshot_id is None:
        return {}
    requirements = list(
        session.scalars(
            select(ProgramRequirement).where(ProgramRequirement.snapshot_id == snapshot_id)
        )
    )
    roles: dict[int, set[tuple]] = {}
    for r in requirements:
        if r.course_id is not None and r.kind == RequirementKind.COMPULSORY:
            roles.setdefault(r.course_id, set()).add(("compulsory", r.year, r.season.value))
    for cid, pool in session.execute(
        select(PoolCourse.course_id, ElectivePool)
        .join(ElectivePool, ElectivePool.id == PoolCourse.pool_id)
        .where(ElectivePool.snapshot_id == snapshot_id)
    ):
        category = pool_category(pool.name)
        slots = [r for r in requirements if r.elective_pool_id == pool.id]
        for r in slots:
            roles.setdefault(cid, set()).add((category, r.year, r.season.value))
        if not slots:
            roles.setdefault(cid, set()).add((category, None, None))
    return {
        cid: [
            {"category": c, "year": y, "season": s}
            for c, y, s in sorted(values, key=lambda v: (v[0] or "", v[1] or 0, v[2] or ""))
        ]
        for cid, values in roles.items()
    }
