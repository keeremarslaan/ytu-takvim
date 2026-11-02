"""Owner-only local JSON validation and preview. No database mutations."""

import argparse
import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from ytu_scheduler.database import SessionLocal
from ytu_scheduler.schedule_document import ScheduleDocument
from ytu_scheduler.services.document_review import document_digest, review_document

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


def read_document(path: Path) -> ScheduleDocument:
    if path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ValueError("Schedule JSON exceeds 20 MiB")

    return parse_document(path.read_bytes())


def parse_document(content: bytes) -> ScheduleDocument:
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("Schedule JSON exceeds 20 MiB")

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    return ScheduleDocument.model_validate(
        json.loads(content.decode("utf-8"), object_pairs_hook=unique_keys)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate or review a standardized timetable JSON")
    parser.add_argument("action", choices=["schema", "validate", "review"])
    parser.add_argument("document", nargs="?", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--source-file", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    inputs = [p.resolve() for p in (args.document, args.baseline, args.source_file) if p]
    if args.output.resolve() in inputs:
        parser.error("Output must not overwrite an input document or source file")
    exit_code = 0
    try:
        if args.action == "schema":
            report = ScheduleDocument.model_json_schema()
        else:
            if args.document is None:
                parser.error("validate/review requires a document path")
            document = read_document(args.document)
            if args.action == "validate":
                report = {
                    "valid": True,
                    "document_sha256": document_digest(document),
                    "note": "Schema checks only; run review before owner approval.",
                }
            else:
                baseline = read_document(args.baseline) if args.baseline else None
                digest = None
                if args.source_file:
                    with args.source_file.open("rb") as source:
                        digest = hashlib.file_digest(source, "sha256").hexdigest()
                with SessionLocal() as session:
                    report = review_document(session, document, baseline, digest)
                exit_code = 0 if report["ready_for_owner_review"] else 1
    except ValidationError as exc:
        report = {
            "valid": False,
            "errors": exc.errors(include_url=False, include_context=False, include_input=False),
        }
        exit_code = 1
    except (OSError, ValueError) as exc:
        report = {"valid": False, "errors": [str(exc)]}
        exit_code = 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Report saved: {args.output}")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
