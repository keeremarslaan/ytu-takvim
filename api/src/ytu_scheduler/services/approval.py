from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ytu_scheduler.extraction_schemas import ExtractedSchedule
from ytu_scheduler.models import (
    Course,
    CourseAlias,
    ElectivePool,
    EligibilityRule,
    EnrollmentOption,
    ImportStatus,
    Meeting,
    Offering,
    OfferingAlias,
    OfferingStatus,
    PoolCourse,
    ProgramRequirement,
    RuleType,
    ScheduleImport,
)
from ytu_scheduler.services.bologna import normalize_course_code
from ytu_scheduler.services.snapshots import latest_snapshot_ids


def find_course(session: Session, codes: list[str]) -> Course | None:
    normalized = [normalize_course_code(code) for code in codes if code.strip()]
    matches = list(session.scalars(
        select(Course).where(Course.normalized_code.in_(normalized)).order_by(Course.id)
    ))
    if len(matches) > 1:
        raise ValueError(f"Ambiguous course codes require review: {', '.join(codes)}")
    if matches:
        return matches[0]
    return session.scalar(
        select(Course)
        .join(CourseAlias)
        .where(CourseAlias.normalized_code.in_(normalized))
        .order_by(Course.id)
    )


def _offering_status(session: Session, course: Course) -> OfferingStatus:
    active_rules = list(
        session.scalars(
            select(EligibilityRule).where(
                EligibilityRule.course_id == course.id,
                EligibilityRule.active.is_(True),
            )
        )
    )
    if any(rule.rule_type == RuleType.EXCEPTION for rule in active_rules):
        return OfferingStatus.APPROVED_EXCEPTION
    if any(rule.rule_type == RuleType.EXCLUSION for rule in active_rules):
        return OfferingStatus.EXCLUDED

    snapshot_ids = latest_snapshot_ids(session)
    compulsory = session.scalar(
        select(ProgramRequirement.id).where(
            ProgramRequirement.snapshot_id.in_(snapshot_ids),
            ProgramRequirement.course_id == course.id,
        )
    )
    elective = session.scalar(
        select(PoolCourse.id)
        .join(ElectivePool, ElectivePool.id == PoolCourse.pool_id)
        .where(
            ElectivePool.snapshot_id.in_(snapshot_ids),
            PoolCourse.course_id == course.id,
        )
    )
    return OfferingStatus.ELIGIBLE if compulsory or elective else OfferingStatus.INELIGIBLE


def approve_extraction(
    session: Session,
    schedule_import: ScheduleImport,
    extraction: ExtractedSchedule,
) -> list[Offering]:
    existing_offerings = list(
        session.scalars(
            select(Offering)
            .options(
                selectinload(Offering.aliases),
                selectinload(Offering.options).selectinload(EnrollmentOption.meetings),
            )
            .where(Offering.import_id == schedule_import.id)
        )
    )
    if existing_offerings:
        for existing_offering in existing_offerings:
            session.delete(existing_offering)
        session.flush()

    for exclusion in extraction.eligibility_exclusions:
        course = find_course(session, [exclusion.course_code])
        if course is None or not exclusion.applies_globally_to_profile:
            continue
        existing_rule = session.scalar(
            select(EligibilityRule).where(
                EligibilityRule.course_id == course.id,
                EligibilityRule.rule_type == RuleType.EXCLUSION,
                EligibilityRule.reason == exclusion.reason,
                EligibilityRule.active.is_(True),
            )
        )
        if existing_rule is None:
            session.add(
                EligibilityRule(
                    course_id=course.id,
                    rule_type=RuleType.EXCLUSION,
                    reason=exclusion.reason,
                    source=schedule_import.original_filename,
                    active=True,
                )
            )
    session.flush()

    offerings: list[Offering] = []
    for extracted_course in extraction.courses:
        codes = [extracted_course.code, *extracted_course.aliases]
        course = find_course(session, codes)
        if course is None:
            course = Course(
                code=extracted_course.code,
                normalized_code=normalize_course_code(extracted_course.code),
                name=extracted_course.name,
                owner_department=extraction.department,
                is_provisional=True,
            )
            session.add(course)
            session.flush()
        offering = Offering(
            term_id=schedule_import.term_id,
            import_id=schedule_import.id,
            course_id=course.id,
            raw_code=extracted_course.code,
            raw_name=extracted_course.name,
            department=extraction.department,
            status=_offering_status(session, course),
            approved=True,
        )
        session.add(offering)
        session.flush()
        offerings.append(offering)

        normalized_aliases = {
            normalize_course_code(alias): alias for alias in reversed(codes) if alias.strip()
        }
        for normalized_code, alias in normalized_aliases.items():
            session.add(
                OfferingAlias(
                    offering_id=offering.id,
                    code=alias,
                    normalized_code=normalized_code,
                )
            )

        for extracted_group in extracted_course.groups:
            option = EnrollmentOption(
                offering_id=offering.id,
                group_label=extracted_group.label,
                language=extracted_group.language,
                instructor=extracted_group.instructor,
                time_status=extracted_group.time_status,
                confidence=extracted_group.confidence,
                source_page=extracted_group.evidence.page,
                raw_text=extracted_group.evidence.raw_text,
            )
            session.add(option)
            session.flush()
            for extracted_meeting in extracted_group.meetings:
                bbox = extracted_meeting.evidence.bbox
                session.add(
                    Meeting(
                        option_id=option.id,
                        weekday=extracted_meeting.weekday,
                        starts_at=extracted_meeting.starts_at,
                        ends_at=extracted_meeting.ends_at,
                        room=extracted_meeting.room,
                        component=extracted_meeting.component,
                        delivery_mode=extracted_meeting.delivery_mode,
                        confidence=extracted_meeting.confidence,
                        source_page=extracted_meeting.evidence.page,
                        source_bbox=(
                            {"x0": bbox[0], "y0": bbox[1], "x1": bbox[2], "y1": bbox[3]}
                            if bbox and len(bbox) == 4
                            else None
                        ),
                        raw_text=extracted_meeting.evidence.raw_text,
                    )
                )

    schedule_import.status = ImportStatus.APPROVED
    session.commit()
    return offerings
