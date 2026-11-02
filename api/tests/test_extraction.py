from io import BytesIO

import pymupdf
from openpyxl import Workbook

from ytu_scheduler.services.deterministic_schedule import parse_schedule
from ytu_scheduler.services.extraction import extract_document


def test_extracts_pdf_text_with_coordinates() -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "FIZ1111 Fizik 1")

    result = extract_document("schedule.pdf", document.tobytes())

    assert result["kind"] == "pdf"
    assert result["page_count"] == 1
    assert "FIZ1111" in result["pages"][0]["text"]
    assert any(span["text"] == "FIZ1111 Fizik 1" for span in result["pages"][0]["spans"])


def test_extracts_workbook_values() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Fall"
    sheet.append(["Course", "Group", "Day"])
    sheet.append(["IST1141", 1, "Salı"])
    output = BytesIO()
    workbook.save(output)

    result = extract_document("schedule.xlsx", output.getvalue())

    assert result == {
        "kind": "spreadsheet",
        "sheets": [
            {
                "name": "Fall",
                "rows": [["Course", "Group", "Day"], ["IST1141", 1, "Salı"]],
                "merged_ranges": [],
            }
        ],
    }


def test_parses_repeated_rows_from_a_tabular_workbook() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Fall"
    sheet.append(
        [
            "Ders Kodu",
            "Ders Adı",
            "Grup",
            "Gün",
            "Başlangıç",
            "Bitiş",
            "Derslik",
            "Öğretim Elemanı",
        ]
    )
    sheet.append(
        ["FIZ1111", "Fizik 1", "Gr.1", "Salı", "09:00", "10:50", "B1-D04", "Öğretim Üyesi"]
    )
    sheet.append(
        ["FIZ1111", "Fizik 1", "Gr.1", "Cuma", "09:00", "10:50", "B1-D04", "Öğretim Üyesi"]
    )
    output = BytesIO()
    workbook.save(output)

    result = parse_schedule("schedule.xlsx", output.getvalue(), "Physics")

    assert len(result.courses) == 1
    assert result.courses[0].code == "FIZ1111"
    assert result.courses[0].groups[0].label == "Gr.1"
    assert [meeting.weekday for meeting in result.courses[0].groups[0].meetings] == [2, 5]


def test_parses_invented_pdf_grid_and_preserves_multiple_meetings() -> None:
    """Draw a fictional grid at runtime; no university document is distributed."""
    document = pymupdf.open()
    page = document.new_page(width=640, height=420)
    font = pymupdf.Font("cjk")
    page.insert_font(fontname="fixture", fontbuffer=font.buffer)
    page.insert_text(
        (35, 35), "İSTATİSTİK BÖLÜMÜ — SYNTHETIC 2026-2027 GÜZ", fontname="fixture", fontsize=10
    )
    xs, ys = [35, 125, 235, 600], [80, 115, 165, 215, 265]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    rows = [
        ["DAY", "TIME", "1. YARIYIL"],
        ["SALI", "08:00-09:00", "QAA1001 Invented Course Gr.1 B1-D99"],
        ["CUMA", "13:00-14:00", "QAA1001 Invented Course Gr.1 B1-D99"],
        ["CUMA", "15:00-16:00", "QAA2001 Other Example Gr.2 Online"],
    ]
    for row, cells in enumerate(rows):
        for col, text in enumerate(cells):
            page.insert_text((xs[col] + 5, ys[row] + 20), text, fontname="fixture", fontsize=9)
    result = parse_schedule("invented.pdf", document.tobytes(), "Statistics")
    courses = {course.code: course for course in result.courses}
    assert set(courses) == {"QAA1001", "QAA2001"}
    assert result.academic_year == "2026-2027"
    assert result.season == "fall"
    group = courses["QAA1001"].groups[0]
    assert group.label == "Gr.1"
    assert [meeting.weekday for meeting in group.meetings] == [2, 5]
    assert all(meeting.room == "B1-D99" for meeting in group.meetings)
    assert courses["QAA2001"].groups[0].meetings[0].delivery_mode == "online"
    assert all(course.source_classification == "unknown" for course in courses.values())
