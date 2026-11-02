from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ytu_scheduler.auth import require_owner
from ytu_scheduler.config import get_settings
from ytu_scheduler.database import get_session
from ytu_scheduler.extraction_schemas import ExtractedSchedule
from ytu_scheduler.models import (
    AcademicTerm,
    ImportStatus,
    OfferingStatus,
    ScheduleImport,
)
from ytu_scheduler.schemas import ApprovalResult, ImportDetail, ImportOut, TermCreate, TermOut
from ytu_scheduler.services.approval import approve_extraction, find_course
from ytu_scheduler.services.deterministic_schedule import parse_schedule
from ytu_scheduler.services.extraction import SUPPORTED_EXTENSIONS, extract_document

router = APIRouter(tags=["imports"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("/terms", response_model=list[TermOut])
def list_terms(session: SessionDependency) -> list[AcademicTerm]:
    return list(session.scalars(select(AcademicTerm).order_by(AcademicTerm.code.desc())))


@router.post(
    "/terms",
    response_model=TermOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_owner)],
)
def create_term(payload: TermCreate, session: SessionDependency) -> AcademicTerm:
    term = AcademicTerm(**payload.model_dump())
    session.add(term)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail="Academic term code already exists") from error
    session.refresh(term)
    return term


@router.get("/imports", response_model=list[ImportOut], dependencies=[Depends(require_owner)])
def list_imports(session: SessionDependency) -> list[ScheduleImport]:
    return list(session.scalars(select(ScheduleImport).order_by(ScheduleImport.created_at.desc())))


@router.get(
    "/imports/{import_id}", response_model=ImportDetail, dependencies=[Depends(require_owner)]
)
def get_import(import_id: int, session: SessionDependency) -> ScheduleImport:
    schedule_import = session.get(ScheduleImport, import_id)
    if schedule_import is None:
        raise HTTPException(status_code=404, detail="Schedule import not found")
    return schedule_import


@router.get(
    "/imports/{import_id}/source",
    response_class=FileResponse,
    dependencies=[Depends(require_owner)],
)
def get_import_source(import_id: int, session: SessionDependency) -> FileResponse:
    schedule_import = session.get(ScheduleImport, import_id)
    if schedule_import is None:
        raise HTTPException(status_code=404, detail="Schedule import not found")
    return FileResponse(
        _source_path(schedule_import),
        filename=schedule_import.original_filename,
        content_disposition_type="inline",
    )


@router.post(
    "/imports",
    response_model=ImportDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_owner)],
)
async def upload_schedule(
    term_id: Annotated[int, Form()],
    department: Annotated[str, Form(min_length=1, max_length=200)],
    file: Annotated[UploadFile, File()],
    session: SessionDependency,
) -> ScheduleImport:
    settings = get_settings()
    term = session.get(AcademicTerm, term_id)
    if term is None:
        raise HTTPException(status_code=404, detail="Academic term not found")

    filename = Path(file.filename or "schedule").name
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type. Allowed: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Schedule file is too large")

    file_hash = hashlib.sha256(content).hexdigest()
    existing = session.scalar(
        select(ScheduleImport).where(
            ScheduleImport.term_id == term_id,
            ScheduleImport.file_hash == file_hash,
        )
    )
    if existing is not None:
        return existing

    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    object_key = f"{term.code}/{file_hash[:12]}-{filename}"
    destination = settings.upload_dir / object_key
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)

    schedule_import = ScheduleImport(
        term_id=term_id,
        department=department.strip(),
        original_filename=filename,
        object_key=object_key,
        file_hash=file_hash,
        status=ImportStatus.EXTRACTING,
    )
    session.add(schedule_import)
    session.flush()
    try:
        schedule_import.extraction_payload = {
            "deterministic": extract_document(filename, content),
            "structured": None,
        }
        schedule_import.status = ImportStatus.NEEDS_REVIEW
    except Exception as error:
        schedule_import.status = ImportStatus.FAILED
        schedule_import.error_message = str(error)
    session.commit()
    session.refresh(schedule_import)
    return schedule_import


def _source_path(schedule_import: ScheduleImport) -> Path:
    settings = get_settings()
    upload_root = settings.upload_dir.resolve()
    source_path = (settings.upload_dir / schedule_import.object_key).resolve()
    if not source_path.is_relative_to(upload_root) or not source_path.is_file():
        raise HTTPException(status_code=404, detail="Uploaded source file is unavailable")
    return source_path


@router.post(
    "/imports/{import_id}/parse", response_model=ImportDetail, dependencies=[Depends(require_owner)]
)
def parse_import(import_id: int, session: SessionDependency) -> ScheduleImport:
    schedule_import = session.get(ScheduleImport, import_id)
    if schedule_import is None:
        raise HTTPException(status_code=404, detail="Schedule import not found")

    source_path = _source_path(schedule_import)
    payload = schedule_import.extraction_payload or {}
    deterministic = payload.get("deterministic", payload)
    try:
        extraction = parse_schedule(
            schedule_import.original_filename,
            source_path.read_bytes(),
            schedule_import.department,
        )
        for extracted_course in extraction.courses:
            catalog_course = find_course(
                session,
                [extracted_course.code, *extracted_course.aliases],
            )
            if catalog_course is not None:
                extracted_course.name = catalog_course.name
    except Exception as error:
        schedule_import.error_message = str(error)
        session.commit()
        raise HTTPException(
            status_code=422,
            detail=f"Deterministic parsing needs reviewed JSON: {error}",
        ) from error

    schedule_import.extraction_payload = {
        "deterministic": deterministic,
        "structured": extraction.model_dump(mode="json"),
        "structured_source": "deterministic",
    }
    schedule_import.error_message = None
    schedule_import.status = ImportStatus.NEEDS_REVIEW
    session.commit()
    session.refresh(schedule_import)
    return schedule_import


@router.put(
    "/imports/{import_id}/review",
    response_model=ImportDetail,
    dependencies=[Depends(require_owner)],
)
def save_reviewed_json(
    import_id: int,
    reviewed: ExtractedSchedule,
    session: SessionDependency,
) -> ScheduleImport:
    schedule_import = session.get(ScheduleImport, import_id)
    if schedule_import is None:
        raise HTTPException(status_code=404, detail="Schedule import not found")
    payload = schedule_import.extraction_payload or {}
    deterministic = payload.get("deterministic", payload)
    schedule_import.extraction_payload = {
        "deterministic": deterministic,
        "structured": reviewed.model_dump(mode="json"),
        "structured_source": "reviewed_json",
    }
    schedule_import.error_message = None
    schedule_import.status = ImportStatus.NEEDS_REVIEW
    session.commit()
    session.refresh(schedule_import)
    return schedule_import


@router.post(
    "/imports/{import_id}/approve",
    response_model=ApprovalResult,
    dependencies=[Depends(require_owner)],
)
def approve_schedule(
    import_id: int,
    session: SessionDependency,
    reviewed: Annotated[ExtractedSchedule | None, Body()] = None,
) -> ApprovalResult:
    schedule_import = session.get(ScheduleImport, import_id)
    if schedule_import is None:
        raise HTTPException(status_code=404, detail="Schedule import not found")
    if reviewed is None:
        payload = schedule_import.extraction_payload or {}
        structured_payload = payload.get("structured") or payload.get("ai")
        if structured_payload is None:
            raise HTTPException(status_code=409, detail="No reviewed extraction is available")
        reviewed = ExtractedSchedule.model_validate(structured_payload)

    try:
        offerings = approve_extraction(session, schedule_import, reviewed)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApprovalResult(
        import_id=import_id,
        offerings_created=len(offerings),
        eligible=sum(offering.status == OfferingStatus.ELIGIBLE for offering in offerings),
        excluded=sum(offering.status == OfferingStatus.EXCLUDED for offering in offerings),
        ineligible=sum(offering.status == OfferingStatus.INELIGIBLE for offering in offerings),
    )
