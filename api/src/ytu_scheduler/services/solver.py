from __future__ import annotations

from collections import defaultdict
from datetime import date, time

from ortools.sat.python import cp_model

from ytu_scheduler.models import DeliveryMode
from ytu_scheduler.schemas import (
    CourseSelectionInput,
    GenerateScheduleRequest,
    GenerateScheduleResponse,
    MeetingInput,
    ScheduleCandidateOut,
    ScheduleConflictOut,
    ScheduleMetrics,
    SelectedOptionOut,
)


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _date_ranges_overlap(
    left_start: date | None,
    left_end: date | None,
    right_start: date | None,
    right_end: date | None,
) -> bool:
    if left_end and right_start and left_end < right_start:
        return False
    if right_end and left_start and right_end < left_start:
        return False
    return True


def meetings_overlap(left: MeetingInput, right: MeetingInput) -> bool:
    if left.weekday != right.weekday:
        return False
    if not _date_ranges_overlap(left.starts_on, left.ends_on, right.starts_on, right.ends_on):
        return False
    return left.starts_at < right.ends_at and right.starts_at < left.ends_at


def _options_conflict(left: list[MeetingInput], right: list[MeetingInput]) -> bool:
    return any(meetings_overlap(a, b) for a in left for b in right)


def _option_has_internal_conflict(meetings: list[MeetingInput]) -> bool:
    return any(
        meetings_overlap(meetings[left], meetings[right])
        for left in range(len(meetings))
        for right in range(left + 1, len(meetings))
    )


def _metrics(
    selections: list[SelectedOptionOut], request: GenerateScheduleRequest
) -> tuple[ScheduleMetrics, float]:
    meetings = [meeting for selection in selections for meeting in selection.meetings]
    by_day: dict[int, list[MeetingInput]] = defaultdict(list)
    campus_days: set[int] = set()
    early = 0
    late = 0
    for meeting in meetings:
        by_day[meeting.weekday].append(meeting)
        if meeting.delivery_mode != DeliveryMode.ONLINE:
            campus_days.add(meeting.weekday)
        early += meeting.starts_at < request.preferences.early_before
        late += meeting.ends_at > request.preferences.late_after

    gap_minutes = 0
    for day_meetings in by_day.values():
        ordered = sorted(day_meetings, key=lambda meeting: meeting.starts_at)
        gap_minutes += sum(
            max(0, _minutes(current.starts_at) - _minutes(previous.ends_at))
            for previous, current in zip(ordered, ordered[1:], strict=False)
        )

    metrics = ScheduleMetrics(
        days_on_campus=len(campus_days),
        total_gap_minutes=gap_minutes,
        early_meetings=early,
        late_meetings=late,
    )
    score = (
        metrics.days_on_campus * request.preferences.free_day_weight
        + (metrics.total_gap_minutes / 60) * request.preferences.gap_hour_weight
        + metrics.early_meetings * request.preferences.early_meeting_weight
        + metrics.late_meetings * request.preferences.late_meeting_weight
    )
    return metrics, round(score, 3)


class _SolutionCollector(cp_model.CpSolverSolutionCallback):
    def __init__(self, variables: dict[tuple[int, int], cp_model.IntVar], limit: int):
        super().__init__()
        self.variables = variables
        self.limit = limit
        self.assignments: list[set[tuple[int, int]]] = []
        self.truncated = False

    def on_solution_callback(self) -> None:
        self.assignments.append(
            {key for key, variable in self.variables.items() if self.value(variable)}
        )
        if len(self.assignments) >= self.limit:
            self.truncated = True
            self.stop_search()


def generate_schedules(request: GenerateScheduleRequest) -> GenerateScheduleResponse:
    model = cp_model.CpModel()
    variables: dict[tuple[int, int], cp_model.IntVar] = {}
    valid_options: dict[tuple[int, int], object] = {}
    conflicts: list[ScheduleConflictOut] = []

    for course_index, course in enumerate(request.courses):
        course_variables = []
        for option_index, option in enumerate(course.options):
            if _option_has_internal_conflict(option.meetings):
                continue
            key = (course_index, option_index)
            variable = model.new_bool_var(f"course_{course.course_id}_option_{option.id}")
            variables[key] = variable
            valid_options[key] = option
            course_variables.append(variable)
        if not course_variables:
            return GenerateScheduleResponse(
                schedules=[],
                explored_solutions=0,
                truncated=False,
                message=f"{course.code} has no internally valid enrollment option.",
            )
        model.add_exactly_one(course_variables)

    keys = list(variables)
    for left_index, left_key in enumerate(keys):
        for right_key in keys[left_index + 1 :]:
            if left_key[0] == right_key[0]:
                continue
            left_option = valid_options[left_key]
            right_option = valid_options[right_key]
            overlapping_meetings = [
                (left_meeting, right_meeting)
                for left_meeting in left_option.meetings
                for right_meeting in right_option.meetings
                if meetings_overlap(left_meeting, right_meeting)
            ]
            if overlapping_meetings:
                model.add(variables[left_key] + variables[right_key] <= 1)
                left_course = request.courses[left_key[0]]
                right_course = request.courses[right_key[0]]
                for left_meeting, right_meeting in overlapping_meetings:
                    conflicts.append(
                        ScheduleConflictOut(
                            left_course_id=left_course.course_id,
                            left_course_code=left_course.code,
                            left_course_name=left_course.name,
                            left_option_label=left_option.label,
                            right_course_id=right_course.course_id,
                            right_course_code=right_course.code,
                            right_course_name=right_course.name,
                            right_option_label=right_option.label,
                            weekday=left_meeting.weekday,
                            starts_at=max(left_meeting.starts_at, right_meeting.starts_at),
                            ends_at=min(left_meeting.ends_at, right_meeting.ends_at),
                        )
                    )

    solver = cp_model.CpSolver()
    solver.parameters.enumerate_all_solutions = True
    collector = _SolutionCollector(variables, request.enumeration_limit)
    solver.solve(model, collector)

    candidates: list[ScheduleCandidateOut] = []
    for assignment in collector.assignments:
        selections: list[SelectedOptionOut] = []
        warnings: list[str] = []
        for course_index, option_index in sorted(assignment):
            course: CourseSelectionInput = request.courses[course_index]
            option = course.options[option_index]
            selections.append(
                SelectedOptionOut(
                    course_id=course.course_id,
                    course_code=course.code,
                    course_name=course.name,
                    option_id=option.id,
                    option_label=option.label,
                    meetings=option.meetings,
                )
            )
            if option.untimed or not option.meetings:
                warnings.append(
                    f"{course.code} {option.label} has no confirmed meeting time."
                )
        metrics, score = _metrics(selections, request)
        candidates.append(
            ScheduleCandidateOut(
                rank=0,
                score=score,
                selections=selections,
                metrics=metrics,
                warnings=warnings,
            )
        )

    candidates.sort(
        key=lambda candidate: (
            candidate.score,
            candidate.metrics.days_on_campus,
            candidate.metrics.total_gap_minutes,
            [selection.option_id for selection in candidate.selections],
        )
    )
    selected = candidates[: request.max_results]
    for rank, candidate in enumerate(selected, start=1):
        candidate.rank = rank

    message = None if selected else "No conflict-free combination exists for these courses."
    return GenerateScheduleResponse(
        schedules=selected,
        explored_solutions=len(collector.assignments),
        truncated=collector.truncated,
        message=message,
        conflicts=[] if selected else conflicts,
    )
