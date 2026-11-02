import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ytu_scheduler.database import Base, get_session
from ytu_scheduler.main import create_app
from ytu_scheduler.models import AcademicTerm, Season


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(AcademicTerm(code="synthetic", academic_year="2026-2027", season=Season.FALL))
        session.commit()
    app = create_app(local_owner=True)

    def sessions():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    yield TestClient(app, base_url="http://127.0.0.1:8001", client=("127.0.0.1", 1234))
    engine.dispose()


def edit(**changes):
    return {
        "starts_on": "2026-09-21",
        "ends_on": "2027-01-10",
        "expected_starts_on": None,
        "expected_ends_on": None,
        "owner_confirmed": True,
        **changes,
    }


def test_save_readback_and_stale_tab_cannot_overwrite(client):
    assert client.get("/owner/terms").json()[0]["starts_on"] is None
    result = client.post("/owner/terms/synthetic/dates", json=edit())
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert client.get("/owner/terms").json()[0]["ends_on"] == "2027-01-10"
    assert (
        client.post("/owner/terms/synthetic/dates", json=edit(ends_on="2027-02-01")).status_code
        == 409
    )
    assert client.get("/owner/terms").json()[0]["ends_on"] == "2027-01-10"
    assert (
        client.post(
            "/owner/terms/synthetic/dates",
            json=edit(
                expected_starts_on="2026-09-21", expected_ends_on="2027-01-10", ends_on="2027-01-11"
            ),
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"ends_on": "2026-09-20"},
        {"starts_on": "2026-02-30"},
        {"ends_on": "2028-01-01"},
        {"starts_on": None},
        {"owner_confirmed": False},
        {"timezone": "UTC"},
    ],
)
def test_invalid_changes_leave_dates_unset(client, changes):
    assert client.post("/owner/terms/synthetic/dates", json=edit(**changes)).status_code == 422
    assert client.get("/owner/terms").json()[0]["starts_on"] is None


def test_missing_term_and_owner_boundary(client):
    assert client.post("/owner/terms/missing/dates", json=edit()).status_code == 404
    assert (
        client.post(
            "/owner/terms/synthetic/dates", json=edit(), headers={"Origin": "https://foreign.test"}
        ).status_code
        == 403
    )
    assert "/owner/terms" not in create_app().openapi()["paths"]
    assert "/owner/terms/{code}/dates" not in create_app().openapi()["paths"]


def test_single_day_range_supported(client):
    response = client.post("/owner/terms/synthetic/dates", json=edit(ends_on="2026-09-21"))
    assert response.status_code == 200


def new_term(**changes):
    return {"academic_year": "2027-2028", "season": "fall", "owner_confirmed": True, **changes}


@pytest.mark.parametrize("season", ["fall", "spring", "summer"])
def test_create_term_then_edit_dates_without_changing_existing_term(client, season):
    response = client.post("/owner/terms", json=new_term(season=season))
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    code = f"2027-2028-{season}"
    assert response.json() == {
        "code": code, "starts_on": None, "ends_on": None, "timezone": "Europe/Istanbul"
    }
    assert client.post(
        f"/owner/terms/{code}/dates",
        json=edit(starts_on="2027-09-20", ends_on="2028-01-07"),
    ).status_code == 200
    terms = {t["code"]: t for t in client.get("/owner/terms").json()}
    assert terms[code]["ends_on"] == "2028-01-07"
    assert terms["synthetic"]["starts_on"] is None
    # Duplicate requests cannot replace dates or create another record.
    assert client.post("/owner/terms", json=new_term(season=season)).status_code == 409
    assert client.get("/owner/terms").json() == list(terms.values())


@pytest.mark.parametrize("changes", [
    {"academic_year": "2027-2029"}, {"academic_year": "2027"},
    {"academic_year": "0000-0001"}, {"academic_year": "2027-2028\n"},
    {"season": "winter"}, {"owner_confirmed": False},
    {"code": "custom"}, {"timezone": "UTC"}, {"starts_on": "2027-09-20"},
])
def test_invalid_term_creation_does_not_write(client, changes):
    assert client.post("/owner/terms", json=new_term(**changes)).status_code == 422
    assert len(client.get("/owner/terms").json()) == 1


def test_create_term_requires_confirmation_and_local_origin(client):
    payload = new_term()
    del payload["owner_confirmed"]
    assert client.post("/owner/terms", json=payload).status_code == 422
    assert client.post(
        "/owner/terms", json=new_term(), headers={"Origin": "https://foreign.test"}
    ).status_code == 403
    assert client.post(
        "/owner/terms", json=new_term(), headers={"Host": "foreign.test"}
    ).status_code == 403
    assert len(client.get("/owner/terms").json()) == 1
    assert "/owner/terms" not in create_app().openapi()["paths"]
