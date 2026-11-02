from __future__ import annotations

import csv
import io
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pymupdf
from python_calamine import CalamineWorkbook

SUPPORTED_EXTENSIONS = {".pdf", ".xls", ".xlsx", ".xlsm", ".csv"}


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def extract_document(filename: str, content: bytes) -> dict[str, Any]:
    extension = Path(filename).suffix.lower()
    if extension == ".pdf":
        return _extract_pdf(content)
    if extension in {".xls", ".xlsx", ".xlsm"}:
        return _extract_workbook(content)
    if extension == ".csv":
        return _extract_csv(content)
    raise ValueError(f"Unsupported schedule file type: {extension or 'unknown'}")


def _extract_pdf(content: bytes) -> dict[str, Any]:
    document = pymupdf.open(stream=content, filetype="pdf")
    pages: list[dict[str, Any]] = []
    for page_number, page in enumerate(document, start=1):
        text_dict = page.get_text("dict")
        spans: list[dict[str, Any]] = []
        for block in text_dict.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        spans.append(
                            {
                                "text": text,
                                "bbox": [round(value, 2) for value in span["bbox"]],
                                "color": span.get("color"),
                                "size": round(span.get("size", 0), 2),
                            }
                        )
        pages.append(
            {
                "page": page_number,
                "width": round(page.rect.width, 2),
                "height": round(page.rect.height, 2),
                "text": page.get_text("text"),
                "spans": spans,
            }
        )
    return {"kind": "pdf", "page_count": len(pages), "pages": pages}


def _extract_workbook(content: bytes) -> dict[str, Any]:
    workbook = CalamineWorkbook.from_filelike(io.BytesIO(content))
    sheets = []
    for sheet_name in workbook.sheet_names:
        sheet = workbook.get_sheet_by_name(sheet_name)
        rows = [[_json_value(value) for value in row] for row in sheet.to_python()]
        sheets.append(
            {
                "name": sheet.name,
                "rows": rows,
                "merged_ranges": sheet.merged_cell_ranges or [],
            }
        )
    return {"kind": "spreadsheet", "sheets": sheets}


def _extract_csv(content: bytes) -> dict[str, Any]:
    text = content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    return {"kind": "spreadsheet", "sheets": [{"name": "CSV", "rows": rows}]}
