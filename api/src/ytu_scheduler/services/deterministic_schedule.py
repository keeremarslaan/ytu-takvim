from __future__ import annotations

import re
from datetime import time
from typing import Any

import pymupdf

from ytu_scheduler.extraction_schemas import (
    ExtractedCourse,
    ExtractedEligibilityExclusion,
    ExtractedGroup,
    ExtractedMeeting,
    ExtractedSchedule,
    IgnoredEvent,
    SourceEvidence,
)
from ytu_scheduler.models import DeliveryMode, Season, TimeStatus
from ytu_scheduler.services.bologna import normalize_course_code
from ytu_scheduler.services.extraction import extract_document

COURSE_CODE = re.compile(r"\*{0,2}\b([A-ZÇĞİÖŞÜ]{2,4}\s*\d{4})\b")
TIME_RANGE = re.compile(
    r"(\d{1,2})[.:](\d{2})(?::\d{2})?\s*[-–]\s*"
    r"(\d{1,2})[.:](\d{2})(?::\d{2})?"
)
GROUP = re.compile(
    r"\bGr\.?\s*([0-9]+(?:\s*,\s*(?:(?:Gr\.?)\s*)?[0-9]+)*)",
    re.IGNORECASE,
)
ROOM = re.compile(r"\b(?:BZ|B1|B2|EZ|B)-\s*[A-Z]?\d{2,4}\b", re.IGNORECASE)
DAY_NAMES = {
    "PAZARTESİ": 1,
    "PAZARTESI": 1,
    "SALI": 2,
    "ÇARŞAMBA": 3,
    "CARSAMBA": 3,
    "PERŞEMBE": 4,
    "PERSEMBE": 4,
    "CUMA": 5,
    "CUMARTESİ": 6,
    "CUMARTESI": 6,
    "PAZAR": 7,
}
ACADEMIC_TITLE = re.compile(
    r"\b(?:Prof\.?\s*Dr\.?|Doç\.?\s*Dr\.?|Dr\.?\s*Öğr\.?\s*Üyesi|"
    r"Öğr\.?\s*Gör\.?|Arş\.?\s*Gör\.?)\b",
    re.IGNORECASE,
)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _letters(value: str) -> str:
    return re.sub(r"[^A-ZÇĞİÖŞÜ]", "", value.upper())


def _time_range(match: re.Match[str]) -> tuple[time, time]:
    return (
        time(int(match.group(1)), int(match.group(2))),
        time(int(match.group(3)), int(match.group(4))),
    )


def _bbox_key(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return tuple(round(value, 2) for value in bbox)


def _intersects_vertically(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
    tolerance: float = 1.5,
) -> bool:
    return left[1] < right[3] + tolerance and right[1] < left[3] + tolerance


def _span_records(page: pymupdf.Page) -> list[dict[str, Any]]:
    records = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text_value = span.get("text", "").strip()
                if text_value:
                    records.append(
                        {
                            "text": text_value,
                            "bbox": tuple(span["bbox"]),
                            "color": span.get("color", 0),
                        }
                    )
    return records


def _table_cells(table: Any) -> list[tuple[float, float, float, float]]:
    cells: dict[tuple[float, float, float, float], tuple[float, float, float, float]] = {}
    for row in table.rows:
        for cell in row.cells:
            if cell:
                cells[_bbox_key(cell)] = tuple(cell)
    return list(cells.values())


def _semester_ranges(
    table: Any, page: pymupdf.Page, season: Season
) -> list[tuple[float, float, int, int]]:
    ranges = []
    for cell in _table_cells(table):
        text_value = _clean_text(page.get_textbox(cell))
        match = re.search(r"([1-8])\s*\.\s*YARIYIL", text_value, re.IGNORECASE)
        if match:
            semester = int(match.group(1))
            ranges.append((cell[0], cell[2], (semester + 1) // 2, semester))
            continue
        match = re.search(r"([1-4])\s*\.\s*S[İI]NIF", text_value, re.IGNORECASE)
        if match:
            year = int(match.group(1))
            semester = (year - 1) * 2 + (1 if season == Season.FALL else 2)
            ranges.append((cell[0], cell[2], year, semester))
    return sorted(ranges)


def _day_and_time_rows(
    table: Any,
    page: pymupdf.Page,
    course_x: float,
    current_day: int | None,
    previous_start: time | None,
) -> tuple[list[dict[str, Any]], int | None, time | None]:
    slots = []
    for row in table.rows:
        row_cells = [tuple(cell) for cell in row.cells if cell]
        explicit_day = None
        for cell in row_cells:
            if cell[0] >= course_x:
                continue
            text_value = page.get_textbox(cell)
            day = DAY_NAMES.get(_letters(text_value))
            if day:
                current_day = day
                explicit_day = day
                break
        time_candidates: list[tuple[tuple[float, float, float, float], re.Match[str]]] = []
        for cell in row_cells:
            if cell[0] >= course_x:
                continue
            matches = list(TIME_RANGE.finditer(page.get_textbox(cell)))
            if matches:
                time_candidates.append((cell, matches[-1]))
        if not time_candidates:
            continue
        cell, match = max(time_candidates, key=lambda item: item[0][0])
        starts_at, ends_at = _time_range(match)
        if (
            explicit_day is None
            and current_day is not None
            and previous_start is not None
            and starts_at < previous_start
        ):
            current_day = min(current_day + 1, 7)
        if current_day is None:
            continue
        previous_start = starts_at
        slots.append(
            {
                "day": current_day,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "y0": cell[1],
                "y1": cell[3],
            }
        )
    return slots, current_day, previous_start


def _best_cell(
    page: pymupdf.Page,
    span_bbox: tuple[float, float, float, float],
    cells: list[tuple[float, float, float, float]],
    course_x: float,
    primary_code: str,
) -> tuple[float, float, float, float] | None:
    center_x = (span_bbox[0] + span_bbox[2]) / 2
    candidates = [
        cell
        for cell in cells
        if cell[0] >= course_x - 1
        and cell[0] - 1 <= center_x <= cell[2] + 1
        and _intersects_vertically(cell, span_bbox)
    ]
    if not candidates:
        return None

    def score(cell: tuple[float, float, float, float]) -> tuple[Any, ...]:
        text_value = _clean_text(page.get_textbox(cell))
        trimmed = _trim_course_text(text_value, primary_code)
        codes = [
            normalize_course_code(match.group(1))
            for match in COURSE_CODE.finditer(text_value)
        ]
        has_name = _course_name(trimmed, primary_code) != primary_code
        return (
            has_name,
            bool(GROUP.search(trimmed)),
            -sum(code != primary_code for code in codes),
            len(trimmed),
            cell[3] - cell[1],
            -(cell[2] - cell[0]),
        )

    return max(candidates, key=score)


def _different_code(text_value: str, primary_code: str) -> bool:
    codes = [normalize_course_code(match.group(1)) for match in COURSE_CODE.finditer(text_value)]
    return any(code != primary_code for code in codes)


def _extend_fragmented_cell(
    page: pymupdf.Page,
    cell: tuple[float, float, float, float],
    cells: list[tuple[float, float, float, float]],
    primary_code: str,
    median_slot_height: float,
) -> tuple[float, float, float, float]:
    text_value = _clean_text(page.get_textbox(cell))
    if GROUP.search(text_value) or cell[3] - cell[1] >= median_slot_height * 0.8:
        return cell

    same_column = sorted(
        [
            candidate
            for candidate in cells
            if abs(candidate[0] - cell[0]) < 1 and abs(candidate[2] - cell[2]) < 1
        ],
        key=lambda candidate: candidate[1],
    )
    current = cell
    combined = text_value
    for candidate in reversed(same_column):
        if candidate[3] < current[1] - 1.5 or candidate[3] > current[1] + 1:
            continue
        candidate_text = _clean_text(page.get_textbox(candidate))
        candidate_codes = {
            normalize_course_code(match.group(1))
            for match in COURSE_CODE.finditer(candidate_text)
        }
        if primary_code not in candidate_codes:
            continue
        current = (current[0], candidate[1], current[2], current[3])
        combined = f"{candidate_text} {combined}".strip()
    for candidate in same_column:
        if candidate[1] < current[3] - 1 or candidate[1] > current[3] + 1.5:
            continue
        candidate_text = _clean_text(page.get_textbox(candidate))
        if _different_code(candidate_text, primary_code):
            break
        current = (current[0], current[1], current[2], candidate[3])
        combined = f"{combined} {candidate_text}".strip()
        has_location = bool(ROOM.search(combined) or re.search(r"online|hibrit", combined, re.I))
        if GROUP.search(combined) and has_location:
            break
        if current[3] - current[1] > median_slot_height * 5:
            break
    return current


def _trim_course_text(text_value: str, primary_code: str) -> str:
    cleaned = _clean_text(text_value.replace("**", ""))
    matches = list(COURSE_CODE.finditer(cleaned))
    primary_match = next(
        (
            match
            for match in matches
            if normalize_course_code(match.group(1)) == primary_code
        ),
        None,
    )
    if primary_match is None:
        return cleaned
    cleaned = cleaned[primary_match.start() :]
    matches = list(COURSE_CODE.finditer(cleaned))
    for match in matches[1:]:
        prefix = cleaned[max(0, match.start() - 2) : match.start()]
        if prefix.endswith("("):
            continue
        if normalize_course_code(match.group(1)) != primary_code:
            cleaned = cleaned[: match.start()]
            break
    return cleaned.strip(" |")


def _course_name(text_value: str, primary_code: str) -> str:
    without_code = re.sub(
        rf"^\s*{re.escape(primary_code)}\s*",
        "",
        text_value,
        count=1,
        flags=re.IGNORECASE,
    )
    without_code = re.sub(r"^\s*\([A-ZÇĞİÖŞÜ]{2,4}\s*\d{4}\)\s*", "", without_code)
    group = GROUP.search(without_code)
    if group:
        without_code = without_code[: group.start()]
    stop_positions = [
        match.start()
        for pattern in (ACADEMIC_TITLE, ROOM, re.compile(r"\b(?:Online|Hibrit)\b", re.I))
        if (match := pattern.search(without_code))
    ]
    if stop_positions:
        without_code = without_code[: min(stop_positions)]
    return _clean_text(without_code).strip(" -|") or primary_code


def _delivery_mode(text_value: str) -> DeliveryMode:
    if re.search(r"hibrit", text_value, re.IGNORECASE):
        return DeliveryMode.HYBRID
    if re.search(r"online", text_value, re.IGNORECASE):
        return DeliveryMode.ONLINE
    return DeliveryMode.IN_PERSON


def _rooms(text_value: str) -> list[str]:
    return [re.sub(r"\s+", "", room.upper()) for room in ROOM.findall(text_value)]


def _language(segment: str) -> str | None:
    match = re.search(r"\((Tür|İng|EN)\)", segment, re.IGNORECASE)
    return match.group(1) if match else None


def _instructor(segment: str) -> str | None:
    value = GROUP.sub("", segment)
    value = ROOM.sub("", value)
    value = re.sub(r"\((?:Tür|İng|EN)\)", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:online|hibrit)\b", "", value, flags=re.IGNORECASE)
    value = _clean_text(value).strip(" ,-|()")
    return value or None


def _instructors(segment: str) -> list[str]:
    matches = list(ACADEMIC_TITLE.finditer(segment))
    instructors = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(segment)
        value = _clean_text(segment[match.start() : end]).strip(" ,-|()")
        value = ROOM.sub("", value)
        value = re.sub(r"\b(?:online|hibrit)\b", "", value, flags=re.IGNORECASE)
        value = _clean_text(value).strip(" ,-|()")
        if value:
            instructors.append(value)
    return instructors


def _groups(
    text_value: str,
    meeting: ExtractedMeeting,
    evidence: SourceEvidence,
) -> list[ExtractedGroup]:
    matches = list(GROUP.finditer(text_value))
    if not matches:
        instructors = _instructors(text_value)
        return [
            ExtractedGroup(
                label="Unspecified",
                language=_language(text_value),
                instructor=", ".join(instructors) or None,
                time_status=TimeStatus.SCHEDULED,
                confidence=0.72,
                evidence=evidence,
                meetings=[meeting],
            )
        ]

    groups = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text_value)
        segment = text_value[match.start() : end]
        group_numbers = re.findall(r"\d+", match.group(1))
        rooms = _rooms(segment)
        instructors = _instructors(segment)
        if len(matches) == 1 and len(group_numbers) > 1:
            rooms = _rooms(text_value)
            instructors = _instructors(text_value)
        for group_index, number in enumerate(group_numbers):
            room = rooms[group_index] if group_index < len(rooms) else (rooms[0] if rooms else None)
            instructor = (
                instructors[group_index]
                if len(instructors) == len(group_numbers)
                else _instructor(segment)
            )
            group_meeting = meeting.model_copy(update={"room": room or meeting.room})
            groups.append(
                ExtractedGroup(
                    label=f"Gr.{number}",
                    language=_language(text_value),
                    instructor=instructor,
                    time_status=TimeStatus.SCHEDULED,
                    confidence=0.84,
                    evidence=evidence,
                    meetings=[group_meeting],
                )
            )
    return groups


def _meeting_for_cell(
    bbox: tuple[float, float, float, float],
    slots: list[dict[str, Any]],
    text_value: str,
    page_number: int,
) -> ExtractedMeeting | None:
    matching = [
        slot
        for slot in slots
        if slot["y0"] < bbox[3] - 0.5 and slot["y1"] > bbox[1] + 0.5
    ]
    if not matching:
        return None
    day = matching[0]["day"]
    matching = [slot for slot in matching if slot["day"] == day]
    evidence = SourceEvidence(
        page=page_number,
        raw_text=text_value,
        bbox=[round(value, 2) for value in bbox],
    )
    rooms = _rooms(text_value)
    return ExtractedMeeting(
        weekday=day,
        starts_at=matching[0]["starts_at"],
        ends_at=matching[-1]["ends_at"],
        room=rooms[0] if len(rooms) == 1 else None,
        delivery_mode=_delivery_mode(text_value),
        confidence=0.86,
        evidence=evidence,
    )


def _meeting_signature(group: ExtractedGroup) -> tuple[Any, ...]:
    return tuple(
        sorted(
            (
                meeting.weekday,
                meeting.starts_at,
                meeting.ends_at,
                meeting.room,
            )
            for meeting in group.meetings
        )
    )


def _merge_occurrence(
    records: dict[str, ExtractedCourse],
    occurrence: ExtractedCourse,
) -> None:
    existing = records.get(occurrence.code)
    if existing is None:
        records[occurrence.code] = occurrence
        return
    existing.aliases = list(dict.fromkeys([*existing.aliases, *occurrence.aliases]))
    if len(occurrence.name) > len(existing.name):
        existing.name = occurrence.name
    for group in occurrence.groups:
        current = next((item for item in existing.groups if item.label == group.label), None)
        if current is None:
            existing.groups.append(group)
            continue
        signatures = {_meeting_signature(current)}
        for meeting in group.meetings:
            candidate = current.model_copy(update={"meetings": [meeting]})
            if _meeting_signature(candidate) not in signatures:
                current.meetings.append(meeting)
                signatures.add(_meeting_signature(candidate))


def _merge_equivalent_codes(records: dict[str, ExtractedCourse]) -> None:
    keys = list(records)
    for index, left_key in enumerate(keys):
        if left_key not in records:
            continue
        left = records[left_key]
        normalized_name = re.sub(r"\W", "", left.name.casefold())
        if not normalized_name:
            continue
        left_signature = sorted((group.label, _meeting_signature(group)) for group in left.groups)
        for right_key in keys[index + 1 :]:
            right = records.get(right_key)
            if right is None:
                continue
            right_name = re.sub(r"\W", "", right.name.casefold())
            right_signature = sorted(
                (group.label, _meeting_signature(group)) for group in right.groups
            )
            if normalized_name == right_name and left_signature == right_signature:
                left.aliases = list(dict.fromkeys([*left.aliases, right.code, *right.aliases]))
                del records[right_key]


def _parse_fiz9000(page: pymupdf.Page, tables: list[Any]) -> ExtractedCourse | None:
    for table in tables:
        data = table.extract()
        if not any("FIZ9000" in str(cell) for row in data for cell in row):
            continue
        groups = []
        for row in data[2:]:
            values = [str(value).strip() if value is not None else "" for value in row]
            if not values or not values[0].isdigit() or "FIZ9000" not in values:
                continue
            groups.append(
                ExtractedGroup(
                    label=f"Gr.{values[0]}",
                    language=values[7] or None,
                    instructor=values[8] or None,
                    time_status=TimeStatus.TBA,
                    confidence=0.99,
                    evidence=SourceEvidence(
                        page=page.number + 1,
                        raw_text=" | ".join(value for value in values if value),
                    ),
                )
            )
        if groups:
            return ExtractedCourse(
                code="FIZ9000",
                name="Bitirme Çalışması",
                curriculum_year=4,
                curriculum_semester=7,
                groups=groups,
            )
    return None


def parse_yildiz_pdf(filename: str, content: bytes) -> ExtractedSchedule:
    document = pymupdf.open(stream=content, filetype="pdf")
    full_text = "\n".join(page.get_text("text") for page in document)
    if "FİZİK BÖLÜMÜ" in full_text:
        department = "Physics"
    elif "İSTATİSTİK BÖLÜMÜ" in full_text:
        department = "Statistics"
    else:
        raise ValueError(
            "No deterministic profile matches this PDF. Use reviewed JSON for this layout."
        )

    year_match = re.search(r"(20\d{2}-20\d{2})", full_text)
    season = Season.FALL if "GÜZ" in full_text.upper() else Season.SPRING
    records: dict[str, ExtractedCourse] = {}
    semester_ranges: list[tuple[float, float, int, int]] = []
    current_day: int | None = None
    previous_start: time | None = None

    for page in document:
        found = page.find_tables()
        tables = found.tables
        if department == "Physics" and page.number > 0:
            project = _parse_fiz9000(page, tables)
            if project:
                _merge_occurrence(records, project)
            continue

        schedule_tables = [
            table
            for table in tables
            if len(table.rows) >= 3
            and any(TIME_RANGE.search(str(cell) or "") for row in table.extract() for cell in row)
            and any(COURSE_CODE.search(str(cell) or "") for row in table.extract() for cell in row)
        ]
        for table in schedule_tables:
            page_ranges = _semester_ranges(table, page, season)
            if page_ranges:
                semester_ranges = page_ranges
            if not semester_ranges:
                continue
            course_x = min(item[0] for item in semester_ranges)
            slots, current_day, previous_start = _day_and_time_rows(
                table, page, course_x, current_day, previous_start
            )
            if not slots:
                continue
            slot_heights = sorted(slot["y1"] - slot["y0"] for slot in slots)
            median_slot_height = slot_heights[len(slot_heights) // 2]
            cells = _table_cells(table)
            table_bbox = tuple(table.bbox)

            for span in _span_records(page):
                span_bbox = span["bbox"]
                if (
                    span_bbox[0] < course_x
                    or not _intersects_vertically(span_bbox, table_bbox)
                    or not (match := COURSE_CODE.search(span["text"]))
                ):
                    continue
                primary_code = normalize_course_code(match.group(1))
                cell = _best_cell(page, span_bbox, cells, course_x, primary_code)
                if cell is None:
                    continue
                cell = _extend_fragmented_cell(
                    page, cell, cells, primary_code, median_slot_height
                )
                cell_text = _trim_course_text(page.get_textbox(cell), primary_code)
                meeting = _meeting_for_cell(cell, slots, cell_text, page.number + 1)
                if meeting is None:
                    continue
                aliases = [
                    normalize_course_code(alias.group(1))
                    for alias in re.finditer(
                        r"\(([A-ZÇĞİÖŞÜ]{2,4}\s*\d{4})\)", cell_text
                    )
                ]
                semester_match = next(
                    (
                        item
                        for item in semester_ranges
                        if item[0] <= (cell[0] + cell[2]) / 2 <= item[1]
                    ),
                    None,
                )
                evidence = meeting.evidence
                occurrence = ExtractedCourse(
                    code=primary_code,
                    aliases=aliases,
                    name=_course_name(cell_text, primary_code),
                    curriculum_year=semester_match[2] if semester_match else None,
                    curriculum_semester=semester_match[3] if semester_match else None,
                    # Curriculum classification is sourced from Bologna, never the timetable.
                    source_classification="unknown",
                    groups=_groups(cell_text, meeting, evidence),
                )
                _merge_occurrence(records, occurrence)

    _merge_equivalent_codes(records)
    exclusions = []
    if department == "Statistics" and "IST3557" in full_text and re.search(
        r"alamaz", full_text, re.IGNORECASE
    ):
        note_page = next(
            (
                page
                for page in document
                if "IST3557" in page.get_text("text")
                and re.search(r"alamaz", page.get_text("text"), re.IGNORECASE)
            ),
            None,
        )
        note = (
            _clean_text(note_page.get_text("text").split("**:", maxsplit=1)[-1])
            if note_page
            else "İstatistik Bölümü öğrencileri IST3557 dersini alamazlar."
        )
        exclusions.append(
            ExtractedEligibilityExclusion(
                course_code="IST3557",
                reason=note,
                applies_globally_to_profile=True,
                evidence=SourceEvidence(
                    page=note_page.number + 1 if note_page else None,
                    raw_text=note,
                ),
            )
        )
    ignored_events = []
    if department == "Statistics" and "Seminer" in full_text:
        ignored_events.append(
            IgnoredEvent(
                name="Seminer",
                reason="Department-wide uncoded event configured to be ignored.",
                evidence=SourceEvidence(page=1, raw_text="Seminer"),
            )
        )
    warnings = [
        "Deterministic extraction must be reviewed before approval.",
    ]
    if not records:
        warnings.append("No course blocks were recognized.")
    return ExtractedSchedule(
        department=department,
        academic_year=year_match.group(1) if year_match else None,
        season=season,
        courses=sorted(records.values(), key=lambda course: course.code),
        eligibility_exclusions=exclusions,
        ignored_events=ignored_events,
        warnings=warnings,
    )


def _header_key(value: Any) -> str:
    normalized = str(value).casefold().translate(
        str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u"})
    )
    return re.sub(r"[^a-z0-9]", "", normalized)


def _row_value(row: list[Any], columns: dict[str, int | None], key: str) -> str:
    index = columns[key]
    return str(row[index]).strip() if index is not None and index < len(row) else ""


def parse_tabular_schedule(filename: str, content: bytes, department: str) -> ExtractedSchedule:
    extracted = extract_document(filename, content)
    aliases = {
        "code": {"derskodu", "coursecode", "kod", "code"},
        "name": {"dersadi", "coursename", "course", "name"},
        "group": {"grup", "group", "sube", "section"},
        "day": {"gun", "day", "weekday"},
        "start": {"baslangic", "start", "starttime"},
        "end": {"bitis", "end", "endtime"},
        "room": {"derslik", "room", "location"},
        "instructor": {"ogretimelemani", "instructor", "lecturer"},
    }
    records: dict[str, ExtractedCourse] = {}
    warnings = []
    for sheet in extracted.get("sheets", []):
        rows = sheet.get("rows", [])
        header_index = next(
            (
                index
                for index, row in enumerate(rows)
                if any(_header_key(cell) in aliases["code"] for cell in row)
            ),
            None,
        )
        if header_index is None:
            warnings.append(f"Sheet {sheet['name']} has no recognized row-based header.")
            continue
        headers = [_header_key(value) for value in rows[header_index]]
        columns = {
            key: next(
                (index for index, header in enumerate(headers) if header in values),
                None,
            )
            for key, values in aliases.items()
        }
        if any(columns[key] is None for key in ("code", "name", "day", "start", "end")):
            warnings.append(f"Sheet {sheet['name']} is missing required schedule columns.")
            continue
        for row in rows[header_index + 1 :]:
            code = normalize_course_code(_row_value(row, columns, "code"))
            day = DAY_NAMES.get(_letters(_row_value(row, columns, "day")))
            time_match = TIME_RANGE.search(
                f"{_row_value(row, columns, 'start')}-{_row_value(row, columns, 'end')}"
            )
            if not code or day is None or time_match is None:
                continue
            starts_at, ends_at = _time_range(time_match)
            evidence = SourceEvidence(
                sheet=sheet["name"],
                raw_text=" | ".join(str(cell) for cell in row),
            )
            meeting = ExtractedMeeting(
                weekday=day,
                starts_at=starts_at,
                ends_at=ends_at,
                room=_row_value(row, columns, "room") or None,
                delivery_mode=DeliveryMode.IN_PERSON,
                confidence=0.96,
                evidence=evidence,
            )
            occurrence = ExtractedCourse(
                code=code,
                name=_row_value(row, columns, "name") or code,
                groups=[
                    ExtractedGroup(
                        label=_row_value(row, columns, "group") or "Unspecified",
                        instructor=_row_value(row, columns, "instructor") or None,
                        time_status=TimeStatus.SCHEDULED,
                        confidence=0.96,
                        evidence=evidence,
                        meetings=[meeting],
                    )
                ],
            )
            _merge_occurrence(records, occurrence)
    warnings.append("Deterministic extraction must be reviewed before approval.")
    return ExtractedSchedule(
        department=department,
        courses=sorted(records.values(), key=lambda course: course.code),
        warnings=warnings,
    )


def parse_schedule(filename: str, content: bytes, department: str) -> ExtractedSchedule:
    if filename.lower().endswith(".pdf"):
        return parse_yildiz_pdf(filename, content)
    return parse_tabular_schedule(filename, content, department)
