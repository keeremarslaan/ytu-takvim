from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ytu_scheduler.models import CurriculumSnapshot


def latest_snapshot_ids(session: Session, program_ids: list[int] | None = None) -> list[int]:
    """Most recently verified content, including a source reverting to a known version."""
    statement = select(
        CurriculumSnapshot.id,
        func.row_number()
        .over(
            partition_by=CurriculumSnapshot.program_id,
            order_by=(CurriculumSnapshot.fetched_at.desc(), CurriculumSnapshot.id.desc()),
        )
        .label("position"),
    )
    if program_ids is not None:
        statement = statement.where(CurriculumSnapshot.program_id.in_(program_ids))
    ranked = statement.subquery()
    return list(session.scalars(select(ranked.c.id).where(ranked.c.position == 1)))
