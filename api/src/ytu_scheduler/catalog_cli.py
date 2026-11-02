"""Owner catalog tools: python -m ytu_scheduler.catalog_cli --help."""

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from ytu_scheduler.config import get_settings
from ytu_scheduler.database import SessionLocal
from ytu_scheduler.schemas import SyncProgramResult
from ytu_scheduler.services.bologna import BolognaService
from ytu_scheduler.services.coverage import catalog_coverage
from ytu_scheduler.services.program_sources import PROGRAM_SOURCES, parse_program_directory


def main() -> None:
    parser = argparse.ArgumentParser(description="Review, sync and report undergraduate curricula")
    parser.add_argument("action", choices=["discover", "sync", "coverage"])
    parser.add_argument(
        "--directory-html",
        type=Path,
        help="Downloaded official bachelor directory, required for discover",
    )
    parser.add_argument(
        "--cache-dir", type=Path, help="Reviewed HTML files named <program_id>.html"
    )
    parser.add_argument("--output", type=Path, required=True, help="JSON report output")
    parser.add_argument("--term-id", type=int)
    args = parser.parse_args()
    if args.action == "discover":
        if not args.directory_html:
            parser.error("discover requires --directory-html")
        report = {
            "programs": [
                asdict(source)
                for source in parse_program_directory(
                    args.directory_html.read_text(encoding="utf-8")
                )
            ]
        }
    else:
        with SessionLocal() as session:
            results = []
            if args.action == "sync":
                service = BolognaService(get_settings())
                if args.cache_dir:
                    service.register_programs(session)
                    for source in PROGRAM_SOURCES:
                        try:
                            html = (args.cache_dir / f"{source.program_id}.html").read_text(
                                encoding="utf-8"
                            )
                            result = service.sync_html(session, source, html)
                        except (OSError, ValueError) as exc:
                            session.rollback()
                            result = SyncProgramResult(program=source.name, error=str(exc))
                        results.append(result)
                        status = "FAILED: " + result.error if result.error else "OK"
                        print(f"{source.program_id}: {status}", flush=True)
                else:
                    results = service.sync_all(session)
            report = catalog_coverage(session, args.term_id)
            report["generated_at"] = datetime.now(UTC).isoformat()
            if results:
                report["sync_results"] = [r.model_dump() for r in results]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Report saved: {args.output}")
    if any(r.get("error") for r in report.get("sync_results", [])):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
