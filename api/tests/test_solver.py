from datetime import time

from ytu_scheduler.models import DeliveryMode
from ytu_scheduler.schemas import (
    CourseSelectionInput,
    GenerateScheduleRequest,
    MeetingInput,
    OptionInput,
)
from ytu_scheduler.services.solver import generate_schedules, meetings_overlap


def meeting(day: int, start: tuple[int, int], end: tuple[int, int]) -> MeetingInput:
    return MeetingInput(
        weekday=day,
        starts_at=time(*start),
        ends_at=time(*end),
        delivery_mode=DeliveryMode.IN_PERSON,
    )


def test_back_to_back_meetings_do_not_overlap() -> None:
    assert not meetings_overlap(meeting(1, (9, 0), (9, 50)), meeting(1, (9, 50), (10, 40)))


def test_date_and_time_intersection_is_detected() -> None:
    assert meetings_overlap(meeting(2, (9, 0), (10, 50)), meeting(2, (10, 0), (11, 50)))
    assert not meetings_overlap(meeting(2, (9, 0), (10, 50)), meeting(3, (9, 0), (10, 50)))


def test_generates_only_conflict_free_options_across_any_curriculum_year() -> None:
    request = GenerateScheduleRequest(
        max_results=10,
        courses=[
            CourseSelectionInput(
                course_id=1,
                code="FIZ1111",
                name="Fizik 1",
                curriculum_year=1,
                options=[
                    OptionInput(
                        id=11,
                        label="Gr.1",
                        meetings=[meeting(2, (9, 0), (10, 50)), meeting(5, (9, 0), (10, 50))],
                    ),
                    OptionInput(
                        id=12,
                        label="Gr.2",
                        meetings=[meeting(2, (13, 0), (14, 50))],
                    ),
                ],
            ),
            CourseSelectionInput(
                course_id=2,
                code="IST4412",
                name="Veri Madenciliğine Giriş",
                curriculum_year=4,
                options=[
                    OptionInput(
                        id=21,
                        label="Gr.1",
                        meetings=[meeting(2, (10, 0), (11, 50))],
                    )
                ],
            ),
        ],
    )

    result = generate_schedules(request)

    assert result.explored_solutions == 1
    assert [selection.option_id for selection in result.schedules[0].selections] == [12, 21]


def test_untimed_option_is_allowed_with_warning() -> None:
    request = GenerateScheduleRequest(
        courses=[
            CourseSelectionInput(
                course_id=9000,
                code="FIZ9000",
                name="Bitirme Çalışması",
                curriculum_year=4,
                options=[OptionInput(id=1, label="Gr.1", untimed=True)],
            )
        ]
    )

    result = generate_schedules(request)

    assert len(result.schedules) == 1
    assert result.schedules[0].warnings == ["FIZ9000 Gr.1 has no confirmed meeting time."]


def test_reports_the_courses_and_groups_that_make_a_schedule_infeasible() -> None:
    result = generate_schedules(
        GenerateScheduleRequest(
            courses=[
                CourseSelectionInput(
                    course_id=1,
                    code="FIZ1111",
                    name="Fizik 1",
                    options=[
                        OptionInput(
                            id=11,
                            label="Gr.1",
                            meetings=[meeting(2, (9, 0), (10, 50))],
                        )
                    ],
                ),
                CourseSelectionInput(
                    course_id=2,
                    code="IST1141",
                    name="Olasılık",
                    options=[
                        OptionInput(
                            id=21,
                            label="Gr.2",
                            meetings=[meeting(2, (10, 0), (11, 50))],
                        )
                    ],
                ),
            ]
        )
    )

    assert result.schedules == []
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert (conflict.left_course_code, conflict.left_option_label) == ("FIZ1111", "Gr.1")
    assert (conflict.right_course_code, conflict.right_option_label) == ("IST1141", "Gr.2")
    assert conflict.weekday == 2
    assert conflict.starts_at == time(10)
    assert conflict.ends_at == time(10, 50)
