from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ytu_scheduler.models import (
    DeliveryMode,
    ImportStatus,
    OfferingStatus,
    RequirementKind,
    RuleType,
    Season,
    TimeStatus,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProgramOut(ORMModel):
    id: int
    name: str
    department: str
    bologna_program_id: int
    academic_unit_id: int
    source_url: str
    curriculum_ready: bool = False
    curriculum_available: bool = False


class CourseOut(ORMModel):
    id: int
    code: str
    name: str
    owner_department: str | None
    prerequisite_text: str | None
    ects: float | None
    is_provisional: bool


class CourseEligibilityOut(BaseModel):
    program: str
    kind: RequirementKind
    year: int | None = None
    season: Season | None = None
    pool: str | None = None


class CourseCatalogItem(CourseOut):
    eligibility: list[CourseEligibilityOut]


class CatalogSummary(BaseModel):
    programs: int
    courses: int
    compulsory_requirements: int
    elective_slots: int
    elective_pools: int
    last_synced_at: datetime | None


class EligibilityRuleCreate(BaseModel):
    rule_type: RuleType
    reason: str = Field(min_length=3, max_length=1000)
    program_id: int | None = None
    source: str | None = Field(default=None, max_length=500)


class EligibilityRuleOut(ORMModel):
    id: int
    course_id: int
    program_id: int | None
    rule_type: RuleType
    reason: str
    source: str | None
    active: bool
    created_at: datetime


class SyncProgramResult(BaseModel):
    warnings: list[str] = Field(default_factory=list)
    program: str
    snapshot_id: int | None = None
    unchanged: bool = False
    courses: int = 0
    compulsory_requirements: int = 0
    elective_slots: int = 0
    elective_pools: int = 0
    error: str | None = None


class SyncResult(BaseModel):
    programs: list[SyncProgramResult]


class TermCreate(BaseModel):
    code: str
    academic_year: str
    season: Season
    starts_on: date | None = None
    ends_on: date | None = None
    timezone: str = "Europe/Istanbul"


class TermOut(ORMModel):
    id: int
    code: str
    academic_year: str
    season: Season
    starts_on: date | None
    ends_on: date | None
    timezone: str


class ImportOut(ORMModel):
    id: int
    term_id: int
    department: str
    original_filename: str
    file_hash: str
    status: ImportStatus
    created_at: datetime


class ImportDetail(ImportOut):
    extraction_payload: dict | None
    error_message: str | None


class ApprovalResult(BaseModel):
    import_id: int
    offerings_created: int
    eligible: int
    excluded: int
    ineligible: int


class StoredMeetingOut(ORMModel):
    id: int
    weekday: int
    starts_at: time
    ends_at: time
    room: str | None
    component: str | None
    delivery_mode: DeliveryMode


class EnrollmentOptionOut(ORMModel):
    id: int
    group_label: str
    language: str | None
    instructor: str | None
    time_status: TimeStatus
    meetings: list[StoredMeetingOut]


class CompulsoryPlacementOut(BaseModel):
    program: str
    year: int
    season: Season


class OfferingOut(BaseModel):
    id: int
    offering_ids: list[int]
    term_id: int
    course_id: int
    course_code: str
    course_name: str
    departments: list[str]
    majors: list[str]
    status: OfferingStatus
    prerequisite_text: str | None
    categories: list[
        Literal["compulsory", "professional_elective", "social_elective"]
    ]
    compulsory_placements: list[CompulsoryPlacementOut]
    options: list[EnrollmentOptionOut]


class MeetingInput(BaseModel):
    weekday: int = Field(ge=1, le=7)
    starts_at: time
    ends_at: time
    starts_on: date | None = None
    ends_on: date | None = None
    room: str | None = None
    delivery_mode: DeliveryMode = DeliveryMode.UNKNOWN

    @model_validator(mode="after")
    def validate_interval(self) -> "MeetingInput":
        if self.ends_at <= self.starts_at:
            raise ValueError("Meeting end time must be after its start time")
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("Meeting date range is reversed")
        return self


class ManualEnrollmentOptionCreate(BaseModel):
    group_label: str = Field(min_length=1, max_length=100)
    instructor: str | None = Field(default=None, max_length=500)
    meetings: list[MeetingInput] = Field(min_length=1)


class OptionInput(BaseModel):
    id: int
    label: str
    meetings: list[MeetingInput] = Field(default_factory=list)
    untimed: bool = False


class CourseSelectionInput(BaseModel):
    course_id: int
    code: str
    name: str
    curriculum_year: int | None = None
    options: list[OptionInput] = Field(min_length=1)


class SchedulePreferences(BaseModel):
    free_day_weight: float = 20.0
    gap_hour_weight: float = 3.0
    early_meeting_weight: float = 2.0
    late_meeting_weight: float = 2.0
    early_before: time = time(9, 0)
    late_after: time = time(18, 0)


class GenerateScheduleRequest(BaseModel):
    courses: list[CourseSelectionInput] = Field(min_length=1)
    preferences: SchedulePreferences = Field(default_factory=SchedulePreferences)
    max_results: int = Field(default=10, ge=1, le=50)
    enumeration_limit: int = Field(default=5000, ge=1, le=50000)


class GenerateFromOfferingsRequest(BaseModel):
    program_ids: list[int] = Field(min_length=1, max_length=2)
    offering_ids: list[int] = Field(min_length=1)
    preferences: SchedulePreferences = Field(default_factory=SchedulePreferences)
    max_results: int = Field(default=10, ge=1, le=50)
    enumeration_limit: int = Field(default=5000, ge=1, le=50000)


class SelectedOptionOut(BaseModel):
    course_id: int
    course_code: str
    course_name: str
    option_id: int
    option_label: str
    meetings: list[MeetingInput]


class ScheduleMetrics(BaseModel):
    days_on_campus: int
    total_gap_minutes: int
    early_meetings: int
    late_meetings: int


class ScheduleCandidateOut(BaseModel):
    rank: int
    score: float
    selections: list[SelectedOptionOut]
    metrics: ScheduleMetrics
    warnings: list[str]


class ScheduleConflictOut(BaseModel):
    left_course_id: int
    left_course_code: str
    left_course_name: str
    left_option_label: str
    right_course_id: int
    right_course_code: str
    right_course_name: str
    right_option_label: str
    weekday: int
    starts_at: time
    ends_at: time


class GenerateScheduleResponse(BaseModel):
    schedules: list[ScheduleCandidateOut]
    explored_solutions: int
    truncated: bool
    message: str | None = None
    conflicts: list[ScheduleConflictOut] = Field(default_factory=list)
