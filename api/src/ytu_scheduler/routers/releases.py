"""Local-only export review, confirmation and immutable artifact downloads."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ytu_scheduler.auth import require_owner
from ytu_scheduler.config import get_settings
from ytu_scheduler.database import get_session
from ytu_scheduler.services.releases import (
    ExportCommit,
    ExportSelection,
    ReleaseStore,
    build_export,
    canonical,
    digest,
)

router = APIRouter(prefix="/owner/exports", tags=["owner"], dependencies=[Depends(require_owner)])


def release_store() -> ReleaseStore:
    return ReleaseStore(get_settings().draft_database)


def prepare(session: Session, selection: ExportSelection) -> dict:
    try:
        return build_export(session, release_store(), selection)
    except KeyError as exc:
        raise HTTPException(404, "Selected draft or export not found") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(409, "Stored artifact failed validation: " + str(exc)) from exc


@router.get("")
def list_exports(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return release_store().list_exports(limit, offset)


@router.post("/preview")
def preview(
    selection: ExportSelection,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    bundle = prepare(session, selection)
    return {"digest": digest(bundle), "report": bundle["report"]}


@router.post("", status_code=201)
def export(
    selection: ExportCommit, response: Response, session: Annotated[Session, Depends(get_session)]
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    bundle = prepare(session, selection)
    if not bundle["report"]["ready_to_export"]:
        raise HTTPException(409, bundle["report"])
    if digest(bundle) != selection.expected_digest:
        raise HTTPException(409, "Data or selection changed since preview; review again")
    try:
        export_id = release_store().save_export(selection, bundle)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": export_id, "report": bundle["report"]}


@router.get("/{export_id}/{artifact}")
def download(export_id: str, artifact: Literal["student-data", "owner-report"]) -> Response:
    try:
        saved = release_store().get_export(export_id)
    except KeyError as exc:
        raise HTTPException(404, "Export not found") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(409, "Saved export integrity check failed") from exc
    value = saved["package" if artifact == "student-data" else "report"]
    return Response(
        canonical({"export_id": export_id, **value}),
        media_type="application/json",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{artifact}-{export_id[:12]}.json"',
        },
    )
