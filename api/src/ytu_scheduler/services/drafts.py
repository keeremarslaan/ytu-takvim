"""Immutable local drafts in a separate transactional store; no student-data writes."""

import hashlib
import json
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from ytu_scheduler.schedule_document import ScheduleDocument
from ytu_scheduler.schedule_document_cli import parse_document
from ytu_scheduler.services.document_review import document_digest


class DraftStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("""CREATE TABLE IF NOT EXISTS drafts (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                document TEXT NOT NULL, document_sha256 TEXT NOT NULL,
                source BLOB NOT NULL, source_sha256 TEXT NOT NULL,
                baseline_id TEXT REFERENCES drafts(id),
                publisher TEXT NOT NULL, source_key TEXT NOT NULL,
                revision TEXT NOT NULL, term_code TEXT NOT NULL
            )""")
            yield db

    @staticmethod
    def identity(document_hash: str, source_hash: str, baseline_id: str | None) -> str:
        return hashlib.sha256(
            json.dumps([document_hash, source_hash, baseline_id], separators=(",", ":")).encode()
        ).hexdigest()

    def save(self, document: ScheduleDocument, source: bytes, baseline_id: str | None) -> str:
        if baseline_id is not None:
            self.get(baseline_id)  # Verify referenced draft integrity before persisting.
        document_hash = document_digest(document)
        source_hash = hashlib.sha256(source).hexdigest()
        draft_id = self.identity(document_hash, source_hash, baseline_id)
        with self.connection() as db:
            db.execute(
                """INSERT OR IGNORE INTO drafts
                (id, created_at, document, document_sha256, source, source_sha256,
                 baseline_id, publisher, source_key, revision, term_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    draft_id,
                    datetime.now(UTC).isoformat(),
                    document.model_dump_json(),
                    document_hash,
                    source,
                    source_hash,
                    baseline_id,
                    document.source.publisher,
                    document.source.key,
                    document.source.revision,
                    document.term.code,
                ),
            )
        self.get(draft_id)
        return draft_id

    def get(self, draft_id: str) -> dict:
        if not re.fullmatch(r"[a-f0-9]{64}", draft_id):
            raise KeyError("Draft not found")
        with self.connection() as db:
            row = db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            raise KeyError("Draft not found")
        result = dict(row)
        document = parse_document(result["document"].encode())
        if (
            document_digest(document) != result["document_sha256"]
            or hashlib.sha256(result["source"]).hexdigest() != result["source_sha256"]
            or self.identity(
                result["document_sha256"], result["source_sha256"], result["baseline_id"]
            )
            != draft_id
        ):
            raise ValueError("Saved draft integrity check failed; restore a verified backup")
        result["document"] = document
        return result

    def list(self, limit: int = 50, offset: int = 0) -> dict:
        with self.connection() as db:
            rows = db.execute(
                """SELECT id, created_at, publisher, source_key, revision,
                term_code, baseline_id FROM drafts ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?""",
                (limit + 1, offset),
            ).fetchall()
        return {
            "items": [dict(row) for row in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
        }
