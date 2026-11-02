from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ytu_scheduler.models import (
    Course,
    CurriculumSnapshot,
    ElectivePool,
    Offering,
    PoolCourse,
    Program,
    ProgramRequirement,
    RequirementKind,
)
from ytu_scheduler.services.program_sources import MANIFEST, PROGRAM_SOURCES
from ytu_scheduler.services.snapshots import latest_snapshot_ids


def catalog_coverage(session: Session, term_id: int | None = None) -> dict:
    programs = {p.bologna_program_id: p for p in session.scalars(select(Program))}
    snapshots = {
        s.program_id: s
        for s in session.scalars(
            select(CurriculumSnapshot).where(
                CurriculumSnapshot.id.in_(latest_snapshot_ids(session))
            )
        )
    }
    requirements = defaultdict(list)
    for row in session.scalars(
        select(ProgramRequirement).where(
            ProgramRequirement.snapshot_id.in_([s.id for s in snapshots.values()])
        )
    ):
        requirements[row.snapshot_id].append(row)
    pools = {
        p.id: p
        for p in session.scalars(
            select(ElectivePool).where(
                ElectivePool.snapshot_id.in_([s.id for s in snapshots.values()])
            )
        )
    }
    pool_courses = defaultdict(set)
    for pool_id, course_id in session.execute(
        select(PoolCourse.pool_id, PoolCourse.course_id).where(PoolCourse.pool_id.in_(pools))
    ):
        pool_courses[pool_id].add(course_id)
    offered = (
        set(
            session.scalars(
                select(Offering.course_id).where(
                    Offering.term_id == term_id, Offering.approved.is_(True)
                )
            )
        )
        if term_id
        else set()
    )
    course_codes = defaultdict(list)
    for course in session.scalars(select(Course)):
        course_codes[course.normalized_code].append(course.id)
    ambiguous = {code: ids for code, ids in course_codes.items() if len(ids) > 1}
    results = []
    for source in PROGRAM_SOURCES:
        program = programs.get(source.program_id)
        snapshot = snapshots.get(program.id) if program else None
        rows = requirements[snapshot.id] if snapshot else []
        semesters = sorted({r.semester_number for r in rows})
        course_ids = {r.course_id for r in rows if r.course_id is not None}
        for pool in pools.values():
            if snapshot and pool.snapshot_id == snapshot.id:
                course_ids.update(pool_courses[pool.id])
        problems = []
        if snapshot is None:
            problems.append("No validated curriculum imported")
        elif semesters != list(range(1, 9)):
            problems.append("Missing undergraduate semesters")
        if any(
            r.kind == RequirementKind.ELECTIVE_SLOT and not pool_courses[r.elective_pool_id]
            for r in rows
        ):
            problems.append("Missing or empty elective pool")
        results.append(
            {
                "program_id": program.id if program else None,
                "bologna_program_id": source.program_id,
                "name": source.name,
                "source_url": (
                    f"https://bologna.yildiz.edu.tr/index.php?r=program/view"
                    f"&id={source.program_id}&aid={source.academic_unit_id}"
                ),
                "status": "missing" if snapshot is None else "incomplete" if problems else "ready",
                "snapshot_id": snapshot.id if snapshot else None,
                "verified_at": snapshot.fetched_at.isoformat() if snapshot else None,
                "semesters": semesters,
                "courses": len(course_ids),
                "requirements": len(rows),
                "issues": problems,
                "ambiguous_course_codes": sorted(
                    code for code, ids in ambiguous.items() if course_ids.intersection(ids)
                ),
                "offered_curriculum_courses": len(course_ids & offered) if term_id else None,
            }
        )
    return {
        "directory_source": MANIFEST["source_url"],
        "directory_verified_on": MANIFEST["verified_on"],
        "expected_programs": len(PROGRAM_SOURCES),
        "ready_programs": sum(p["status"] == "ready" for p in results),
        "term_id": term_id,
        "programs": results,
    }
