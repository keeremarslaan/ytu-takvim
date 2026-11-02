"""Local reviews and immutable drafts; no student timetable publication."""

import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ytu_scheduler.auth import require_owner
from ytu_scheduler.config import get_settings
from ytu_scheduler.database import get_session
from ytu_scheduler.schedule_document_cli import parse_document
from ytu_scheduler.services.document_review import review_document
from ytu_scheduler.services.drafts import DraftStore

router = APIRouter(prefix="/owner", tags=["owner"], dependencies=[Depends(require_owner)])


@router.get("/session")
def check_access(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": True, "publication_available": False}


async def read_upload(upload: UploadFile) -> bytes:
    content = await upload.read(get_settings().max_upload_bytes + 1)
    if len(content) > get_settings().max_upload_bytes:
        raise HTTPException(413, "Each file must be at most 20 MiB")
    if not content:
        raise HTTPException(422, "Uploaded file is empty")
    return content


@router.post("/review")
async def preview(
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    document: Annotated[UploadFile, File()],
    source: Annotated[UploadFile, File()],
    baseline: Annotated[UploadFile | None, File()] = None,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        parsed = parse_document(await read_upload(document))
        previous = parse_document(await read_upload(baseline)) if baseline else None
        digest = hashlib.sha256(await read_upload(source)).hexdigest()
    except ValidationError as exc:
        raise HTTPException(
            422, exc.errors(include_url=False, include_context=False, include_input=False)
        ) from exc
    except (ValueError, RecursionError) as exc:
        raise HTTPException(422, "Invalid schedule JSON: " + str(exc)) from exc
    return {
        "report": review_document(session, parsed, previous, digest),
        "document": parsed.model_dump(mode="json"),
        "baseline": previous.model_dump(mode="json") if previous else None,
    }


def draft_store() -> DraftStore:
    return DraftStore(get_settings().draft_database)


def saved_draft(store: DraftStore, draft_id: str) -> dict:
    try:
        return store.get(draft_id)
    except KeyError as exc:
        raise HTTPException(404, "Draft not found") from exc
    except ValueError as exc:
        raise HTTPException(409, "Saved draft integrity check failed") from exc


def draft_review(session: Session, store: DraftStore, draft: dict) -> dict:
    previous = saved_draft(store, draft["baseline_id"]) if draft["baseline_id"] else None
    baseline = previous["document"] if previous else None
    return {
        "draft_id": draft["id"],
        "created_at": draft["created_at"],
        "baseline_id": draft["baseline_id"],
        "report": review_document(session, draft["document"], baseline, draft["source_sha256"]),
        "document": draft["document"].model_dump(mode="json"),
        "baseline": baseline.model_dump(mode="json") if baseline else None,
    }


@router.post("/drafts", status_code=201)
async def save_draft(
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    document: Annotated[UploadFile, File()],
    source: Annotated[UploadFile, File()],
    baseline_id: Annotated[str | None, Form()] = None,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    store = draft_store()
    if baseline_id:
        saved_draft(store, baseline_id)
    try:
        parsed = parse_document(await read_upload(document))
        content = await read_upload(source)
    except ValidationError as exc:
        raise HTTPException(
            422, exc.errors(include_url=False, include_context=False, include_input=False)
        ) from exc
    except (ValueError, RecursionError) as exc:
        raise HTTPException(422, "Invalid schedule JSON: " + str(exc)) from exc
    draft_id = store.save(parsed, content, baseline_id or None)
    return draft_review(session, store, saved_draft(store, draft_id))


@router.get("/drafts")
def list_drafts(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return draft_store().list(limit, offset)


@router.get("/drafts/{draft_id}")
def reopen_draft(
    draft_id: str,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    store = draft_store()
    return draft_review(session, store, saved_draft(store, draft_id))


@router.get("/drafts/{draft_id}/source")
def download_source(draft_id: str) -> Response:
    draft = saved_draft(draft_store(), draft_id)
    # Attachment only: uploaded content must never execute on the local owner's origin.
    from urllib.parse import quote

    filename = draft["document"].source.filename.replace("\\", "/").rsplit("/", 1)[-1]
    filename = "".join(c for c in filename if c.isprintable())[:200] or "source"
    return Response(
        draft["source"],
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/drafts/{draft_id}/document")
def download_document(draft_id: str) -> Response:
    draft = saved_draft(draft_store(), draft_id)
    return Response(
        draft["document"].model_dump_json(indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="schedule.json"',
            "Cache-Control": "no-store",
        },
    )
