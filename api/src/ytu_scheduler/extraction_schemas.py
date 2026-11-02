from datetime import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ytu_scheduler.models import DeliveryMode, Season, TimeStatus


class ExtractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceEvidence(ExtractionModel):
    page: int | None = None
    sheet: str | None = None
    raw_text: str
    bbox: list[float] | None = None


class ExtractedMeeting(ExtractionModel):
    weekday: int = Field(ge=1, le=7, description="Monday is 1 and Sunday is 7")
    starts_at: time
    ends_at: time
    room: str | None = None
    component: str | None = None
    delivery_mode: DeliveryMode = DeliveryMode.UNKNOWN
    confidence: float = Field(ge=0, le=1)
    evidence: SourceEvidence

    @model_validator(mode="after")
    def validate_interval(self) -> "ExtractedMeeting":
        if self.ends_at <= self.starts_at:
            raise ValueError("Meeting end time must be after start time")
        return self


class ExtractedGroup(ExtractionModel):
    label: str
    language: str | None = None
    instructor: str | None = None
    time_status: TimeStatus
    confidence: float = Field(ge=0, le=1)
    evidence: SourceEvidence
    meetings: list[ExtractedMeeting] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_time_status(self) -> "ExtractedGroup":
        if self.time_status == TimeStatus.SCHEDULED and not self.meetings:
            raise ValueError("A scheduled group needs at least one meeting")
        return self


class ExtractedCourse(ExtractionModel):
    code: str
    aliases: list[str] = Field(default_factory=list)
    name: str
    curriculum_year: int | None = Field(default=None, ge=1, le=4)
    curriculum_semester: int | None = Field(default=None, ge=1, le=8)
    source_classification: Literal[
        "compulsory", "professional_elective", "university_elective", "unknown"
    ] = "unknown"
    groups: list[ExtractedGroup] = Field(min_length=1)


class ExtractedEligibilityExclusion(ExtractionModel):
    course_code: str
    reason: str
    applies_globally_to_profile: bool
    evidence: SourceEvidence


class IgnoredEvent(ExtractionModel):
    name: str
    reason: str
    evidence: SourceEvidence


class ExtractedSchedule(ExtractionModel):
    department: str
    academic_year: str | None = None
    season: Season | None = None
    courses: list[ExtractedCourse]
    eligibility_exclusions: list[ExtractedEligibilityExclusion] = Field(default_factory=list)
    ignored_events: list[IgnoredEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
