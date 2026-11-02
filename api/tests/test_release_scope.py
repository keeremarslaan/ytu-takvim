from fastapi.testclient import TestClient

from ytu_scheduler.main import create_app
from ytu_scheduler.services.program_sources import (
    PROGRAM_SOURCES,
    STUDENT_MANIFEST,
    STUDENT_PROGRAM_IDS,
)


def test_student_api_omits_all_maintenance_routes():
    app = create_app()
    paths = app.openapi()["paths"]
    assert "/owner/review" not in paths
    assert "/imports" not in paths
    assert "/catalog/sync" not in paths
    assert "post" not in paths["/terms"]
    assert "/schedules/offerings/{offering_id}/options" not in paths
    assert "post" not in paths["/catalog/courses/{course_id}/rules"]
    assert "post" in paths["/schedules/generate-from-offerings"]
    assert "get" in paths["/catalog/programs"]


def test_local_owner_rejects_network_and_foreign_origins():
    app = create_app(local_owner=True)
    client = TestClient(app, base_url="http://127.0.0.1:8001", client=("127.0.0.1", 1234))
    assert client.get("/owner/session").status_code == 200
    assert (
        client.get("/owner/session", headers={"Origin": "http://localhost:3001"}).status_code == 200
    )
    for headers in (
        {"Origin": "https://foreign.test"},
        {"Host": "foreign.test:8001"},
        {"Origin": "null"},
    ):
        assert client.get("/owner/session", headers=headers).status_code == 403
    remote = TestClient(app, base_url="http://127.0.0.1:8001", client=("192.168.1.4", 1234))
    assert remote.get("/owner/session").status_code == 403


def test_atlas_crosswalk_preserves_tracks_and_merges_quota_entries():
    assert len(STUDENT_PROGRAM_IDS) == 56
    codes = [c for p in STUDENT_MANIFEST["programs"] for c in p["atlas_program_codes"]]
    assert len(codes) == len(set(codes)) == 63
    assert STUDENT_PROGRAM_IDS <= {p.program_id for p in PROGRAM_SOURCES}
    assert STUDENT_MANIFEST["not_listed_bologna_ids"] == [206, 207, 208, 259, 511, 513, 516]
    assert {25, 9, 550, 557, 37, 222} <= STUDENT_PROGRAM_IDS


def test_student_catalog_filters_old_programs_and_rejects_stale_selections():
    import pytest
    from fastapi import HTTPException
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from ytu_scheduler.database import Base
    from ytu_scheduler.models import Program
    from ytu_scheduler.routers.catalog import list_programs
    from ytu_scheduler.services.eligibility import program_permissions

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for source in PROGRAM_SOURCES:
            session.add(
                Program(
                    name=source.name,
                    department=source.department,
                    bologna_program_id=source.program_id,
                    academic_unit_id=source.academic_unit_id,
                    source_url="test",
                )
            )
        session.commit()
        choices = list_programs(session)
        assert len(choices) == 56
        old = next(p for p in session.query(Program) if p.bologna_program_id == 511)
        assert old.id not in {p.id for p in choices}
        with pytest.raises(HTTPException) as exc:
            program_permissions(session, [old.id])
        assert exc.value.status_code == 422
    engine.dispose()
