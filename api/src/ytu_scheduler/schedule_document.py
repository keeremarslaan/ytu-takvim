"""Versioned owner interchange format, independent of legacy PDF extraction."""

from datetime import date, time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ytu_scheduler.models import DeliveryMode, Season
from ytu_scheduler.services.bologna import normalize_course_code

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$")]


class DocumentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentEvidence(DocumentModel):
    page: int | None = Field(default=None, ge=1)
    sheet: Text | None = None
    rows: Text | None = None
    quote: Text

    @model_validator(mode="after")
    def located(self):
        if self.page is None and self.sheet is None:
            raise ValueError("Evidence needs a page number or sheet name")
        return self


class DocumentSource(DocumentModel):
    key: Identifier
    revision: Identifier
    replaces_revision: Identifier | None = None
    publisher: Text
    filename: Text
    url: Annotated[str, StringConstraints(pattern=r"^https?://", max_length=2000)] | None = None
    sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] | None
    published_on: date | None = None

    @model_validator(mode="after")
    def distinct_revision(self):
        if self.replaces_revision == self.revision:
            raise ValueError("A revision cannot replace itself")
        return self


class DocumentTerm(DocumentModel):
    code: Identifier
    academic_year: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{4}$")]
    season: Season
    timezone: Literal["Europe/Istanbul"] = "Europe/Istanbul"

    @model_validator(mode="after")
    def consecutive_years(self):
        start, end = map(int, self.academic_year.split("-"))
        if end != start + 1:
            raise ValueError("Academic year must contain consecutive years")
        return self


class DocumentMeeting(DocumentModel):
    weekday: int = Field(ge=1, le=7)
    starts_at: time
    ends_at: time
    starts_on: date | None = None
    ends_on: date | None = None
    room: Text | None = None
    component: Text | None = None
    delivery_mode: DeliveryMode = DeliveryMode.UNKNOWN
    evidence: DocumentEvidence

    @model_validator(mode="after")
    def interval(self):
        if self.starts_at.tzinfo or self.ends_at.tzinfo:
            raise ValueError("Use local wall-clock times without UTC offsets")
        if self.starts_at >= self.ends_at:
            raise ValueError("Meeting end must be after start; split overnight sessions")
        if (self.starts_on is None) != (self.ends_on is None):
            raise ValueError("Supply both meeting dates or neither")
        if self.starts_on and self.starts_on > self.ends_on:
            raise ValueError("Meeting date range is reversed")
        return self


class GroupPermissions(DocumentModel):
    allowed_bologna_program_ids: list[int] | None = None
    excluded_bologna_program_ids: list[int] = Field(default_factory=list)
    note: Text | None = None
    unresolved: bool = False
    evidence: DocumentEvidence | None = None

    @model_validator(mode="after")
    def consistent(self):
        allowed = self.allowed_bologna_program_ids
        excluded = self.excluded_bologna_program_ids
        if allowed == []:
            raise ValueError("Use null for no stated allowlist, not an empty list")
        for ids in (allowed or [], excluded):
            if len(ids) != len(set(ids)) or any(value < 1 for value in ids):
                raise ValueError("Program identifiers must be positive and distinct")
        if set(allowed or []) & set(excluded):
            raise ValueError("A program cannot be both allowed and excluded")
        if (allowed or excluded or self.note or self.unresolved) and self.evidence is None:
            raise ValueError("Restrictions need source evidence")
        if self.note and not allowed and not excluded and not self.unresolved:
            raise ValueError("An unmapped restriction note must be marked unresolved")
        return self


class DocumentGroup(DocumentModel):
    label: Text
    language: Text | None = None
    instructor: Text | None = None
    time_status: Literal["scheduled", "tba"]
    evidence: DocumentEvidence
    permissions: GroupPermissions = Field(default_factory=GroupPermissions)
    meetings: list[DocumentMeeting] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def schedule(self):
        if (self.time_status == "scheduled") != bool(self.meetings):
            raise ValueError("Scheduled groups need meetings; TBA groups must have none")
        seen = set()
        for meeting in self.meetings:
            key = (
                meeting.weekday,
                meeting.starts_at,
                meeting.ends_at,
                meeting.starts_on,
                meeting.ends_on,
            )
            if key in seen:
                raise ValueError("Duplicate meeting in group")
            seen.add(key)
        for index, left in enumerate(self.meetings):
            for right in self.meetings[index + 1 :]:
                dates_overlap = not (
                    left.ends_on
                    and right.starts_on
                    and left.ends_on < right.starts_on
                    or right.ends_on
                    and left.starts_on
                    and right.ends_on < left.starts_on
                )
                if (
                    dates_overlap
                    and left.weekday == right.weekday
                    and left.starts_at < right.ends_at
                    and right.starts_at < left.ends_at
                ):
                    raise ValueError("Meetings within a group overlap")
        return self


class DocumentCourse(DocumentModel):
    code: Text
    aliases: list[Text] = Field(default_factory=list, max_length=20)
    name: Text
    catalog_course_id: int | None = Field(
        default=None,
        ge=1,
        description="Optional owner-confirmed local ID; an extraction agent must not invent this",
    )
    groups: list[DocumentGroup] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def identifiers(self):
        codes = [normalize_course_code(code) for code in [self.code, *self.aliases]]
        if any(not code for code in codes) or len(codes) != len(set(codes)):
            raise ValueError("Course code and aliases must be nonempty and distinct")
        labels = [group.label.casefold() for group in self.groups]
        if len(labels) != len(set(labels)):
            raise ValueError("Duplicate group label within course")
        return self


class DocumentIssue(DocumentModel):
    severity: Literal["warning", "blocking"]
    message: Text
    evidence: DocumentEvidence


class OmittedEvent(DocumentModel):
    name: Text
    reason: Text
    evidence: DocumentEvidence


class ScheduleDocument(DocumentModel):
    schema_version: Literal["1.0"]
    source: DocumentSource
    term: DocumentTerm
    courses: list[DocumentCourse] = Field(max_length=2000)
    withdraw_all: bool = False
    issues: list[DocumentIssue] = Field(default_factory=list)
    ignored_events: list[OmittedEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct_courses(self):
        if not self.courses and not self.withdraw_all:
            raise ValueError("Empty schedule requires explicit withdraw_all=true")
        if self.courses and self.withdraw_all:
            raise ValueError("withdraw_all requires an empty course list")
        seen = set()
        for course in self.courses:
            codes = {normalize_course_code(code) for code in [course.code, *course.aliases]}
            if seen & codes:
                raise ValueError("A course code or alias occurs in more than one course")
            seen.update(codes)
        return self
