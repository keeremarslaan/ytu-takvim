from __future__ import annotations

import enum
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ytu_scheduler.database import Base


def enum_column(enum_type: type[enum.StrEnum]) -> Enum:
    return Enum(
        enum_type,
        native_enum=False,
        values_callable=lambda values: [item.value for item in values],
    )


class Season(enum.StrEnum):
    FALL = "fall"
    SPRING = "spring"
    SUMMER = "summer"


class RequirementKind(enum.StrEnum):
    COMPULSORY = "compulsory"
    ELECTIVE_SLOT = "elective_slot"


class RuleType(enum.StrEnum):
    EXCLUSION = "exclusion"
    EXCEPTION = "exception"


class ImportStatus(enum.StrEnum):
    UPLOADED = "uploaded"
    EXTRACTING = "extracting"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    FAILED = "failed"


class OfferingStatus(enum.StrEnum):
    UNRESOLVED = "unresolved"
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    EXCLUDED = "excluded"
    APPROVED_EXCEPTION = "approved_exception"


class TimeStatus(enum.StrEnum):
    SCHEDULED = "scheduled"
    TBA = "tba"


class DeliveryMode(enum.StrEnum):
    IN_PERSON = "in_person"
    ONLINE = "online"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Program(TimestampMixin, Base):
    __tablename__ = "programs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    department: Mapped[str] = mapped_column(String(150))
    bologna_program_id: Mapped[int] = mapped_column(unique=True)
    academic_unit_id: Mapped[int]
    source_url: Mapped[str] = mapped_column(String(500))

    snapshots: Mapped[list[CurriculumSnapshot]] = relationship(
        back_populates="program", cascade="all, delete-orphan"
    )


class CurriculumSnapshot(TimestampMixin, Base):
    __tablename__ = "curriculum_snapshots"
    __table_args__ = (UniqueConstraint("program_id", "source_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"))
    source_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    program: Mapped[Program] = relationship(back_populates="snapshots")
    requirements: Mapped[list[ProgramRequirement]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )
    pools: Mapped[list[ElectivePool]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class Course(TimestampMixin, Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    bologna_course_id: Mapped[int | None] = mapped_column(unique=True, nullable=True)
    code: Mapped[str] = mapped_column(String(40), index=True)
    normalized_code: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(300))
    owner_department: Mapped[str | None] = mapped_column(String(200))
    prerequisite_text: Mapped[str | None] = mapped_column(Text)
    lecture_hours: Mapped[int | None]
    practice_hours: Mapped[int | None]
    laboratory_hours: Mapped[int | None]
    local_credits: Mapped[float | None]
    ects: Mapped[float | None]
    source_url: Mapped[str | None] = mapped_column(String(500))
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=False)

    aliases: Mapped[list[CourseAlias]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )


class CourseAlias(Base):
    __tablename__ = "course_aliases"
    __table_args__ = (UniqueConstraint("course_id", "normalized_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(40))
    normalized_code: Mapped[str] = mapped_column(String(40), index=True)
    source: Mapped[str | None] = mapped_column(String(300))

    course: Mapped[Course] = relationship(back_populates="aliases")


class ElectivePool(Base):
    __tablename__ = "elective_pools"
    __table_args__ = (UniqueConstraint("snapshot_id", "source_pool_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("curriculum_snapshots.id", ondelete="CASCADE")
    )
    source_pool_id: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(300))

    snapshot: Mapped[CurriculumSnapshot] = relationship(back_populates="pools")
    courses: Mapped[list[PoolCourse]] = relationship(
        back_populates="pool", cascade="all, delete-orphan"
    )


class PoolCourse(Base):
    __tablename__ = "pool_courses"
    __table_args__ = (UniqueConstraint("pool_id", "course_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("elective_pools.id", ondelete="CASCADE"))
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))

    pool: Mapped[ElectivePool] = relationship(back_populates="courses")
    course: Mapped[Course] = relationship()


class ProgramRequirement(Base):
    __tablename__ = "program_requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("curriculum_snapshots.id", ondelete="CASCADE")
    )
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=True
    )
    elective_pool_id: Mapped[int | None] = mapped_column(
        ForeignKey("elective_pools.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[RequirementKind] = mapped_column(enum_column(RequirementKind))
    year: Mapped[int]
    season: Mapped[Season] = mapped_column(enum_column(Season))
    semester_number: Mapped[int]
    source_order: Mapped[int]
    slot_code: Mapped[str | None] = mapped_column(String(50))
    slot_name: Mapped[str | None] = mapped_column(String(300))
    prerequisite_text: Mapped[str | None] = mapped_column(Text)
    local_credits: Mapped[float | None]
    ects: Mapped[float | None]

    snapshot: Mapped[CurriculumSnapshot] = relationship(back_populates="requirements")
    course: Mapped[Course | None] = relationship()
    elective_pool: Mapped[ElectivePool | None] = relationship()


class EligibilityRule(TimestampMixin, Base):
    __tablename__ = "eligibility_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    program_id: Mapped[int | None] = mapped_column(
        ForeignKey("programs.id", ondelete="CASCADE"), nullable=True
    )
    rule_type: Mapped[RuleType] = mapped_column(enum_column(RuleType))
    reason: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AcademicTerm(TimestampMixin, Base):
    __tablename__ = "academic_terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    academic_year: Mapped[str] = mapped_column(String(20))
    season: Mapped[Season] = mapped_column(enum_column(Season))
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(80), default="Europe/Istanbul")


class ScheduleImport(TimestampMixin, Base):
    __tablename__ = "schedule_imports"
    __table_args__ = (UniqueConstraint("term_id", "file_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("academic_terms.id", ondelete="CASCADE"))
    department: Mapped[str] = mapped_column(String(200))
    original_filename: Mapped[str] = mapped_column(String(300))
    object_key: Mapped[str] = mapped_column(String(500))
    file_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[ImportStatus] = mapped_column(enum_column(ImportStatus))
    extraction_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)

    term: Mapped[AcademicTerm] = relationship()


class Offering(TimestampMixin, Base):
    __tablename__ = "offerings"

    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("academic_terms.id", ondelete="CASCADE"))
    import_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedule_imports.id", ondelete="SET NULL"), nullable=True
    )
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), nullable=True
    )
    raw_code: Mapped[str] = mapped_column(String(100))
    raw_name: Mapped[str] = mapped_column(String(300))
    department: Mapped[str] = mapped_column(String(200))
    status: Mapped[OfferingStatus] = mapped_column(enum_column(OfferingStatus))
    approved: Mapped[bool] = mapped_column(Boolean, default=False)

    course: Mapped[Course | None] = relationship()
    aliases: Mapped[list[OfferingAlias]] = relationship(
        back_populates="offering", cascade="all, delete-orphan"
    )
    options: Mapped[list[EnrollmentOption]] = relationship(
        back_populates="offering", cascade="all, delete-orphan"
    )


class OfferingAlias(Base):
    __tablename__ = "offering_aliases"
    __table_args__ = (UniqueConstraint("offering_id", "normalized_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    offering_id: Mapped[int] = mapped_column(ForeignKey("offerings.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(40))
    normalized_code: Mapped[str] = mapped_column(String(40))

    offering: Mapped[Offering] = relationship(back_populates="aliases")


class EnrollmentOption(TimestampMixin, Base):
    __tablename__ = "enrollment_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    offering_id: Mapped[int] = mapped_column(ForeignKey("offerings.id", ondelete="CASCADE"))
    group_label: Mapped[str] = mapped_column(String(100))
    language: Mapped[str | None] = mapped_column(String(80))
    instructor: Mapped[str | None] = mapped_column(String(500))
    time_status: Mapped[TimeStatus] = mapped_column(enum_column(TimeStatus))
    confidence: Mapped[float | None] = mapped_column(Float)
    source_page: Mapped[int | None]
    raw_text: Mapped[str | None] = mapped_column(Text)

    offering: Mapped[Offering] = relationship(back_populates="options")
    meetings: Mapped[list[Meeting]] = relationship(
        back_populates="option", cascade="all, delete-orphan"
    )


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint("weekday >= 1 AND weekday <= 7", name="valid_weekday"),
        CheckConstraint("ends_at > starts_at", name="valid_meeting_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    option_id: Mapped[int] = mapped_column(
        ForeignKey("enrollment_options.id", ondelete="CASCADE")
    )
    weekday: Mapped[int]
    starts_at: Mapped[time] = mapped_column(Time)
    ends_at: Mapped[time] = mapped_column(Time)
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    room: Mapped[str | None] = mapped_column(String(200))
    component: Mapped[str | None] = mapped_column(String(100))
    delivery_mode: Mapped[DeliveryMode] = mapped_column(enum_column(DeliveryMode))
    confidence: Mapped[float | None] = mapped_column(Float)
    source_page: Mapped[int | None]
    source_bbox: Mapped[dict[str, float] | None] = mapped_column(JSON)
    raw_text: Mapped[str | None] = mapped_column(Text)

    option: Mapped[EnrollmentOption] = relationship(back_populates="meetings")


class SchedulePlan(TimestampMixin, Base):
    __tablename__ = "schedule_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("academic_terms.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    preferences: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    courses: Mapped[list[SchedulePlanCourse]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    schedules: Mapped[list[GeneratedSchedule]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class SchedulePlanCourse(Base):
    __tablename__ = "schedule_plan_courses"
    __table_args__ = (UniqueConstraint("plan_id", "offering_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("schedule_plans.id", ondelete="CASCADE"))
    offering_id: Mapped[int] = mapped_column(ForeignKey("offerings.id", ondelete="CASCADE"))

    plan: Mapped[SchedulePlan] = relationship(back_populates="courses")
    offering: Mapped[Offering] = relationship()


class GeneratedSchedule(TimestampMixin, Base):
    __tablename__ = "generated_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("schedule_plans.id", ondelete="CASCADE"))
    rank: Mapped[int]
    score: Mapped[float]
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    warnings: Mapped[list[str]] = mapped_column(JSON)

    plan: Mapped[SchedulePlan] = relationship(back_populates="schedules")
    options: Mapped[list[GeneratedScheduleOption]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )


class GeneratedScheduleOption(Base):
    __tablename__ = "generated_schedule_options"
    __table_args__ = (UniqueConstraint("schedule_id", "option_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("generated_schedules.id", ondelete="CASCADE")
    )
    option_id: Mapped[int] = mapped_column(
        ForeignKey("enrollment_options.id", ondelete="CASCADE")
    )

    schedule: Mapped[GeneratedSchedule] = relationship(back_populates="options")
    option: Mapped[EnrollmentOption] = relationship()
