from __future__ import annotations

import hashlib
import re
import ssl
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import truststore
from bs4 import BeautifulSoup, Tag
from sqlalchemy import select
from sqlalchemy.orm import Session

from ytu_scheduler.config import Settings
from ytu_scheduler.models import (
    Course,
    CurriculumSnapshot,
    ElectivePool,
    PoolCourse,
    Program,
    ProgramRequirement,
    RequirementKind,
    Season,
)
from ytu_scheduler.schemas import SyncProgramResult
from ytu_scheduler.services.program_sources import (
    PROGRAM_SOURCES,
    ProgramSource,
    parse_program_directory,
)


@dataclass
class ParsedCourse:
    source_id: int
    code: str
    name: str
    prerequisite_text: str | None
    lecture_hours: int | None
    practice_hours: int | None
    laboratory_hours: int | None
    local_credits: float | None
    ects: float | None


@dataclass
class ParsedRequirement:
    kind: RequirementKind
    year: int
    season: Season
    semester_number: int
    source_order: int
    course_source_id: int | None = None
    pool_source_id: str | None = None
    slot_code: str | None = None
    slot_name: str | None = None
    prerequisite_text: str | None = None
    local_credits: float | None = None
    ects: float | None = None


@dataclass
class ParsedPool:
    source_id: str
    name: str
    course_source_ids: list[int] = field(default_factory=list)


@dataclass
class ParsedCurriculum:
    courses: dict[int, ParsedCourse] = field(default_factory=dict)
    requirements: list[ParsedRequirement] = field(default_factory=list)
    pools: dict[str, ParsedPool] = field(default_factory=dict)


def normalize_course_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", code.upper())


def course_name_key(name: str) -> str:
    value = "".join(
        c for c in unicodedata.normalize("NFKD", name.casefold()) if not unicodedata.combining(c)
    ).replace("ı", "i")
    words = re.findall(r"\w+", value)
    return " ".join({"ii": "2", "iii": "3", "i": "1"}.get(word, word) for word in words)


def _number(value: str, integer: bool = False) -> float | int | None:
    cleaned = value.strip().replace(",", ".")
    if not cleaned:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return int(number) if integer else number


def _course_id(anchor: Tag) -> int | None:
    href = anchor.get("href", "")
    values = parse_qs(urlparse(href).query).get("id")
    if not values or not values[0].isdigit():
        return None
    return int(values[0])


def _semester(heading: str) -> tuple[int, Season, int] | None:
    year_match = re.search(r"([1-4])\s*\.\s*Yıl", heading, re.IGNORECASE)
    if not year_match:
        return None
    year = int(year_match.group(1))
    if "Güz" in heading:
        season = Season.FALL
    elif "Bahar" in heading:
        season = Season.SPRING
    else:
        return None
    semester_number = (year - 1) * 2 + (1 if season == Season.FALL else 2)
    return year, season, semester_number


def _pool_id_from_heading(heading: Tag) -> str | None:
    for class_name in heading.get("class", []):
        if class_name.startswith("elective_course_section-"):
            return class_name.removeprefix("elective_course_section-")
    return None


def _pool_id_from_cell(cell: Tag) -> str | None:
    classes = cell.get("class", [])
    return classes[0].strip() if classes else None


def _parse_course(cells: list[Tag], anchor: Tag, source_id: int) -> ParsedCourse:
    return ParsedCourse(
        source_id=source_id,
        code=anchor.get_text(" ", strip=True),
        name=cells[2].get_text(" ", strip=True),
        prerequisite_text=cells[1].get_text(" ", strip=True) or None,
        lecture_hours=_number(cells[3].get_text(" ", strip=True), integer=True),
        practice_hours=_number(cells[4].get_text(" ", strip=True), integer=True),
        laboratory_hours=_number(cells[5].get_text(" ", strip=True), integer=True),
        local_credits=_number(cells[6].get_text(" ", strip=True)),
        ects=_number(cells[7].get_text(" ", strip=True)),
    )


def parse_curriculum(html: str) -> ParsedCurriculum:
    soup = BeautifulSoup(html, "lxml")
    curriculum = soup.select_one("#curriculum")
    if curriculum is None:
        raise ValueError("Bologna page does not contain a #curriculum section")

    result = ParsedCurriculum()
    current_semester: tuple[int, Season, int] | None = None
    current_pool: ParsedPool | None = None
    requirement_order = 0

    for row in curriculum.select("tr"):
        if "table_semester_heading" in row.get("class", []):
            heading = row.find("th")
            if not isinstance(heading, Tag):
                continue
            title = heading.get_text(" ", strip=True)
            pool_id = _pool_id_from_heading(heading)
            if pool_id:
                current_semester = None
                current_pool = result.pools.setdefault(pool_id, ParsedPool(pool_id, title))
            else:
                current_pool = None
                current_semester = _semester(title)
                if current_semester is None:
                    raise ValueError(f"Unrecognized curriculum heading: {title}")
            continue

        cells = row.find_all("td", recursive=False)
        if cells and not any(cell.get_text(" ", strip=True) for cell in cells):
            continue
        if len(cells) < 8:
            if cells and cells[0].find("a"):
                raise ValueError("Course row has fewer than eight columns")
            continue
        anchor = cells[0].find("a")
        if not isinstance(anchor, Tag):
            continue

        source_id = _course_id(anchor)
        if current_pool is not None:
            if source_id is None:
                raise ValueError("Elective pool contains an invalid course link")
            course = _parse_course(cells, anchor, source_id)
            result.courses[source_id] = course
            if source_id not in current_pool.course_source_ids:
                current_pool.course_source_ids.append(source_id)
            continue

        if current_semester is None:
            raise ValueError("Course row has no semester or elective pool heading")

        year, season, semester_number = current_semester
        requirement_order += 1
        if "elective" in anchor.get("class", []) or (
            source_id is None and "secmeli" in course_name_key(cells[2].get_text(" ", strip=True))
        ):
            result.requirements.append(
                ParsedRequirement(
                    kind=RequirementKind.ELECTIVE_SLOT,
                    year=year,
                    season=season,
                    semester_number=semester_number,
                    source_order=requirement_order,
                    pool_source_id=_pool_id_from_cell(cells[0]),
                    slot_code=anchor.get_text(" ", strip=True),
                    slot_name=cells[2].get_text(" ", strip=True),
                    prerequisite_text=cells[1].get_text(" ", strip=True) or None,
                    local_credits=_number(cells[6].get_text(" ", strip=True)),
                    ects=_number(cells[7].get_text(" ", strip=True)),
                )
            )
            continue

        if source_id is None:
            raise ValueError("Compulsory course has an invalid source identifier")
        course = _parse_course(cells, anchor, source_id)
        result.courses[source_id] = course
        result.requirements.append(
            ParsedRequirement(
                kind=RequirementKind.COMPULSORY,
                year=year,
                season=season,
                semester_number=semester_number,
                source_order=requirement_order,
                course_source_id=source_id,
                prerequisite_text=course.prerequisite_text,
                local_credits=course.local_credits,
                ects=course.ects,
            )
        )

    return result


def validate_curriculum(parsed: ParsedCurriculum) -> None:
    semesters = {item.semester_number for item in parsed.requirements}
    if semesters != set(range(1, 9)):
        raise ValueError(f"Expected eight undergraduate semesters; found {sorted(semesters)}")
    if not parsed.courses:
        raise ValueError("Curriculum contains no courses")
    for course in parsed.courses.values():
        if not course.code or not course.name:
            raise ValueError("Curriculum contains an unnamed course")


def curriculum_issues(parsed: ParsedCurriculum) -> list[str]:
    issues = []
    for item in parsed.requirements:
        if item.kind == RequirementKind.ELECTIVE_SLOT:
            pool = parsed.pools.get(item.pool_source_id or "")
            if pool is None or not pool.course_source_ids:
                issues.append(f"Elective slot {item.slot_code} has a missing or empty pool")
    return issues


class BolognaService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def source_url(self, source: ProgramSource) -> str:
        return (
            f"{self.settings.bologna_base_url}/index.php?r=program/view"
            f"&id={source.program_id}&aid={source.academic_unit_id}"
        )

    def register_programs(self, session: Session) -> None:
        for source in PROGRAM_SOURCES:
            program = session.scalar(
                select(Program).where(Program.bologna_program_id == source.program_id)
            )
            if program is None:
                program = Program(bologna_program_id=source.program_id)
                session.add(program)
            program.name = source.name
            program.department = source.department
            program.academic_unit_id = source.academic_unit_id
            program.source_url = self.source_url(source)
        session.commit()

    def sync_all(self, session: Session) -> list[SyncProgramResult]:
        results: list[SyncProgramResult] = []
        headers = {"User-Agent": "YTU-Scheduler/0.3 (curriculum catalog)"}
        tls_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with httpx.Client(
            headers=headers,
            follow_redirects=True,
            timeout=45,
            verify=tls_context,
        ) as client:
            directory = client.get(f"{self.settings.bologna_base_url}/index.php?r=program/bachelor")
            directory.raise_for_status()
            discovered = parse_program_directory(directory.text)
            if set(discovered) != set(PROGRAM_SOURCES):
                raise ValueError(
                    "Official program directory changed. Review and refresh the source manifest "
                    "before syncing."
                )
            self.register_programs(session)
            for index, source in enumerate(PROGRAM_SOURCES):
                if index:
                    time.sleep(self.settings.bologna_request_delay_seconds)
                try:
                    response = client.get(self.source_url(source))
                    response.raise_for_status()
                    results.append(self.sync_html(session, source, response.text))
                except (httpx.HTTPError, ValueError) as exc:
                    session.rollback()
                    results.append(SyncProgramResult(program=source.name, error=str(exc)))
        return results

    def sync_html(self, session: Session, source: ProgramSource, html: str) -> SyncProgramResult:
        parsed = parse_curriculum(html)
        validate_curriculum(parsed)
        source_url = self.source_url(source)
        program = session.scalar(
            select(Program).where(Program.bologna_program_id == source.program_id)
        )
        if program is None:
            program = Program(
                name=source.name,
                department=source.department,
                bologna_program_id=source.program_id,
                academic_unit_id=source.academic_unit_id,
                source_url=source_url,
            )
            session.add(program)
            session.flush()

        program.name = source.name
        program.department = source.department
        program.academic_unit_id = source.academic_unit_id
        program.source_url = source_url
        source_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
        existing = session.scalar(
            select(CurriculumSnapshot).where(
                CurriculumSnapshot.program_id == program.id,
                CurriculumSnapshot.source_hash == source_hash,
            )
        )
        if existing is not None:
            existing.fetched_at = datetime.now(UTC)
        snapshot = existing or CurriculumSnapshot(
            program_id=program.id,
            source_hash=source_hash,
            fetched_at=datetime.now(UTC),
        )
        session.add(snapshot)
        session.flush()

        courses: dict[int, Course] = {}
        for source_id, parsed_course in parsed.courses.items():
            course = session.scalar(select(Course).where(Course.bologna_course_id == source_id))
            if course is None:
                candidates = list(
                    session.scalars(
                        select(Course).where(
                            Course.normalized_code == normalize_course_code(parsed_course.code)
                        )
                    )
                )
                key = course_name_key(parsed_course.name)
                matches = [c for c in candidates if course_name_key(c.name) == key]
                if len(matches) > 1:
                    raise ValueError(f"Duplicate existing course identity: {parsed_course.code}")
                course = matches[0] if matches else None
            if course is None:
                course = Course(
                    bologna_course_id=source_id,
                    code=parsed_course.code,
                    normalized_code=normalize_course_code(parsed_course.code),
                    name=parsed_course.name,
                )
                session.add(course)
            if course.bologna_course_id is None:
                course.bologna_course_id = source_id
            course.is_provisional = False
            course.code = parsed_course.code
            course.normalized_code = normalize_course_code(parsed_course.code)
            course.name = parsed_course.name
            course.prerequisite_text = parsed_course.prerequisite_text
            course.lecture_hours = parsed_course.lecture_hours
            course.practice_hours = parsed_course.practice_hours
            course.laboratory_hours = parsed_course.laboratory_hours
            course.local_credits = parsed_course.local_credits
            course.ects = parsed_course.ects
            course.source_url = (
                f"{self.settings.bologna_base_url}/index.php?r=course/view&id={source_id}"
                f"&aid={source.academic_unit_id}&pid={source.program_id}"
            )
            session.flush()
            courses[source_id] = course

        if existing is not None:
            session.commit()
            return self._result(source, existing.id, parsed, unchanged=True)

        pools: dict[str, ElectivePool] = {}
        for source_pool_id, parsed_pool in parsed.pools.items():
            pool = ElectivePool(
                snapshot_id=snapshot.id,
                source_pool_id=source_pool_id,
                name=parsed_pool.name,
            )
            session.add(pool)
            session.flush()
            pools[source_pool_id] = pool
            for course_id in {courses[sid].id for sid in parsed_pool.course_source_ids}:
                session.add(PoolCourse(pool_id=pool.id, course_id=course_id))

        for requirement in parsed.requirements:
            pool = pools.get(requirement.pool_source_id or "")
            session.add(
                ProgramRequirement(
                    snapshot_id=snapshot.id,
                    course_id=(
                        courses[requirement.course_source_id].id
                        if requirement.course_source_id is not None
                        else None
                    ),
                    elective_pool_id=pool.id if pool else None,
                    kind=requirement.kind,
                    year=requirement.year,
                    season=requirement.season,
                    semester_number=requirement.semester_number,
                    source_order=requirement.source_order,
                    slot_code=requirement.slot_code,
                    slot_name=requirement.slot_name,
                    prerequisite_text=requirement.prerequisite_text,
                    local_credits=requirement.local_credits,
                    ects=requirement.ects,
                )
            )

        session.commit()
        return self._result(source, snapshot.id, parsed, unchanged=False)

    @staticmethod
    def _result(
        source: ProgramSource,
        snapshot_id: int,
        parsed: ParsedCurriculum,
        unchanged: bool,
    ) -> SyncProgramResult:
        compulsory = sum(
            requirement.kind == RequirementKind.COMPULSORY for requirement in parsed.requirements
        )
        elective_slots = len(parsed.requirements) - compulsory
        return SyncProgramResult(
            warnings=curriculum_issues(parsed),
            program=source.name,
            snapshot_id=snapshot_id,
            unchanged=unchanged,
            courses=len(parsed.courses),
            compulsory_requirements=compulsory,
            elective_slots=elective_slots,
            elective_pools=len(parsed.pools),
        )
