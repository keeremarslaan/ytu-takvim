"""Reviewed bachelor directory; refreshing the manifest is an explicit owner action."""

import json
from dataclasses import dataclass
from importlib.resources import files
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class ProgramSource:
    name: str
    department: str
    program_id: int
    academic_unit_id: int


def parse_program_directory(html: str) -> list[ProgramSource]:
    soup = BeautifulSoup(html, "lxml")
    content = soup.select_one("#content")
    if content is None or "Lisans Programları" not in content.get_text(" ", strip=True):
        raise ValueError("Expected the official bachelor program directory")
    sources: dict[int, ProgramSource] = {}
    for anchor in content.select("a[href]"):
        query = parse_qs(urlparse(anchor["href"]).query)
        if query.get("r") != ["program/view"]:
            continue
        name = anchor.get_text(" ", strip=True)
        if "Lisans Programı" not in name:
            continue
        try:
            source = ProgramSource(
                name, name.split(" Lisans Programı")[0], int(query["id"][0]), int(query["aid"][0])
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Invalid bachelor program link: {name}") from exc
        if source.program_id in sources and sources[source.program_id] != source:
            raise ValueError(f"Conflicting program identifier: {source.program_id}")
        sources[source.program_id] = source
    if not sources:
        raise ValueError("No bachelor programs found; refusing an empty catalog")
    return list(sources.values())


MANIFEST = json.loads(
    files("ytu_scheduler").joinpath("data/undergraduate-programs.json").read_text(encoding="utf-8")
)
PROGRAM_SOURCES = tuple(ProgramSource(**source) for source in MANIFEST["programs"])


STUDENT_MANIFEST = json.loads(
    files("ytu_scheduler").joinpath("data/student-programs.json").read_text(encoding="utf-8")
)
STUDENT_PROGRAM_IDS = frozenset(p["bologna_program_id"] for p in STUDENT_MANIFEST["programs"])
