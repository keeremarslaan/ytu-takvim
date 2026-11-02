import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ytu_scheduler.config import Settings, get_settings
from ytu_scheduler.database import Base, get_session
from ytu_scheduler.models import AcademicTerm, Course, Season
from ytu_scheduler.routers import catalog, imports, owner, schedules

EXAMPLES = Path(__file__).parents[2] / "docs/examples"
TOKEN = "owner-test-credential-" + "a" * 32


@pytest.fixture
def client(monkeypatch, tmp_path):
    settings = Settings(owner_token=TOKEN, draft_database=tmp_path / "drafts.db")
    monkeypatch.setattr("ytu_scheduler.auth.get_settings", lambda: settings)
    monkeypatch.setattr("ytu_scheduler.routers.owner.get_settings", lambda: settings)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                AcademicTerm(
                    code="2026-2027-fall",
                    academic_year="2026-2027",
                    season=Season.FALL,
                    timezone="Europe/Istanbul",
                ),
                Course(code="FIZ1111", normalized_code="FIZ1111", name="Fizik 1"),
            ]
        )
        session.commit()
        app = FastAPI()
        for router in (owner.router, catalog.router, imports.router, schedules.router):
            app.include_router(router)
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_settings] = lambda: settings
        with TestClient(app) as http:
            yield http, session, settings
    engine.dispose()


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/owner/session"),
        ("post", "/owner/review"),
        ("post", "/catalog/sync"),
        ("post", "/catalog/courses/1/rules"),
        ("post", "/terms"),
        ("get", "/imports"),
        ("get", "/imports/1"),
        ("get", "/imports/1/source"),
        ("post", "/imports"),
        ("post", "/imports/1/parse"),
        ("put", "/imports/1/review"),
        ("post", "/imports/1/approve"),
        ("post", "/schedules/offerings/1/options"),
    ],
)
def test_maintenance_requires_owner(client, method, path):
    http, _, _ = client
    assert getattr(http, method)(path).status_code == 401
    assert getattr(http, method)(path, headers={"Authorization": "Bearer wrong"}).status_code == 401


def files(document=None, source=None):
    return {
        "document": ("schedule.json", document or (EXAMPLES / "schedule-v1.json").read_bytes()),
        "source": ("source.txt", source or (EXAMPLES / "schedule-source.txt").read_bytes()),
    }


def test_read_only_review_preserves_data_and_evidence(client):
    http, session, _ = client
    before = {
        t.name: session.scalar(select(func.count()).select_from(t))
        for t in Base.metadata.sorted_tables
    }
    response = http.post(
        "/owner/review", files=files(), headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["report"]["ready_for_owner_review"]
    assert result["document"]["courses"][0]["groups"][0]["evidence"]
    assert result["report"]["summary"]["added"] == 1
    assert before == {
        t.name: session.scalar(select(func.count()).select_from(t))
        for t in Base.metadata.sorted_tables
    }
    assert not session.new and not session.dirty and not session.deleted


def test_bad_documents_and_hash_are_reported(client):
    http, _, settings = client
    headers = {"Authorization": f"Bearer {TOKEN}"}
    for invalid in (b'{"x": 1,"x": 2}', b"\xff", b"{", b"{}"):
        response = http.post("/owner/review", files=files(document=invalid), headers=headers)
        assert response.status_code == 422
    result = http.post("/owner/review", files=files(source=b"changed"), headers=headers).json()
    assert not result["report"]["ready_for_owner_review"]
    settings.max_upload_bytes = 10
    assert http.post("/owner/review", files=files(), headers=headers).status_code == 413


def test_replacement_diff_and_stale_parent(client):
    http, _, _ = client
    original = (EXAMPLES / "schedule-v1.json").read_bytes()
    changed = json.loads(original)
    changed["source"].update(revision="example-2", replaces_revision="wrong-parent")
    changed["courses"][0]["groups"][0]["meetings"][0]["room"] = "D-202"
    uploads = files(document=json.dumps(changed).encode())
    uploads["baseline"] = ("previous.json", original)
    result = http.post(
        "/owner/review", files=uploads, headers={"Authorization": f"Bearer {TOKEN}"}
    ).json()
    assert not result["report"]["ready_for_owner_review"]
    assert result["report"]["summary"]["changed"] == 1
    assert result["baseline"]["source"]["revision"] == "example-1"


def test_disabled_owner_and_public_student_access(client):
    http, _, settings = client
    assert (
        http.get("/owner/session", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    )
    settings.owner_token = None
    assert http.get("/owner/session").status_code == 503
    assert http.get("/terms").status_code == 200
    assert http.get("/catalog/programs").status_code == 200
    # Public student calls reach their input validation, rather than requiring owner access.
    assert http.get("/schedules/offerings").status_code == 422
    assert http.post("/schedules/generate-from-offerings", json={}).status_code == 422


def test_saved_draft_survives_reopen_and_keeps_original_source(client):
    http, session, _ = client
    headers = {"Authorization": f"Bearer {TOKEN}"}
    counts = {
        t.name: session.scalar(select(func.count()).select_from(t))
        for t in Base.metadata.sorted_tables
    }
    first = http.post("/owner/drafts", files=files(), headers=headers)
    assert first.status_code == 201
    draft_id = first.json()["draft_id"]
    again = http.post("/owner/drafts", files=files(), headers=headers)
    assert again.json()["draft_id"] == draft_id
    listing = http.get("/owner/drafts", headers=headers).json()
    assert len(listing["items"]) == 1
    assert listing["next_offset"] is None
    reopened = http.get(f"/owner/drafts/{draft_id}", headers=headers)
    assert reopened.status_code == 200
    assert reopened.json()["report"]["ready_for_owner_review"]
    source = http.get(f"/owner/drafts/{draft_id}/source", headers=headers)
    assert source.content == (EXAMPLES / "schedule-source.txt").read_bytes()
    assert source.headers["content-disposition"].startswith("attachment;")
    doc = http.get(f"/owner/drafts/{draft_id}/document", headers=headers).json()
    assert doc["courses"] == first.json()["document"]["courses"]
    assert counts == {
        t.name: session.scalar(select(func.count()).select_from(t))
        for t in Base.metadata.sorted_tables
    }
    # Reopen must reflect catalog changes, not the result from the upload day.
    course = session.scalar(select(Course))
    course.is_provisional = True
    session.commit()
    assert not http.get(f"/owner/drafts/{draft_id}", headers=headers).json()["report"][
        "ready_for_owner_review"
    ]


def test_saved_baseline_and_pagination(client):
    http, _, _ = client
    headers = {"Authorization": f"Bearer {TOKEN}"}
    first = http.post("/owner/drafts", files=files(), headers=headers).json()
    changed = json.loads((EXAMPLES / "schedule-v1.json").read_text())
    changed["source"].update(revision="example-2", replaces_revision="example-1")
    changed["courses"][0]["groups"][0]["meetings"][0]["room"] = "D-202"
    second = http.post(
        "/owner/drafts",
        files=files(document=json.dumps(changed).encode()),
        data={"baseline_id": first["draft_id"]},
        headers=headers,
    ).json()
    assert second["report"]["ready_for_owner_review"]
    assert second["report"]["summary"]["changed"] == 1
    assert second["baseline_id"] == first["draft_id"]
    assert second["baseline"]["source"]["revision"] == "example-1"
    assert first["document"]["courses"][0]["groups"][0]["meetings"][0]["room"] == "D-101"
    page = http.get("/owner/drafts?limit=1", headers=headers).json()
    assert page["items"][0]["id"] == second["draft_id"]
    assert page["next_offset"] == 1
    page2 = http.get("/owner/drafts?limit=1&offset=1", headers=headers).json()
    assert page2["items"][0]["id"] == first["draft_id"]
    assert page2["next_offset"] is None


def test_invalid_drafts_and_integrity_failure(client):
    import sqlite3

    http, _, settings = client
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert (
        http.post("/owner/drafts", files=files(document=b"{}"), headers=headers).status_code == 422
    )
    assert http.get("/owner/drafts", headers=headers).json()["items"] == []
    assert (
        http.post(
            "/owner/drafts", files=files(), data={"baseline_id": "missing"}, headers=headers
        ).status_code
        == 404
    )
    result = http.post("/owner/drafts", files=files(source=b"wrong file"), headers=headers).json()
    assert not result["report"]["ready_for_owner_review"]
    draft_id = result["draft_id"]
    with sqlite3.connect(settings.draft_database) as db:
        db.execute("UPDATE drafts SET source = ? WHERE id = ?", (b"tampered", draft_id))
    for suffix in ("", "/source", "/document"):
        assert http.get(f"/owner/drafts/{draft_id}{suffix}", headers=headers).status_code == 409
    assert http.get("/owner/drafts/unknown", headers=headers).status_code == 404
