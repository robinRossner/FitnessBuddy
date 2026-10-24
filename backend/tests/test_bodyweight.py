import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.bodyweight import get_api_token
from app.api.routes import get_db
from app.bodyweight import BodyweightTimeline, bodyweight_at, manual_id, upsert_measurement
from app.db.models import BodyMeasurement
from app.db.types import MeasurementSource
from app.main import app

TZ = "Europe/Vienna"


def _add(db, day: date, kg: float, hour: int = 8, **kw) -> BodyMeasurement:
    row, _ = upsert_measurement(
        db,
        id=str(uuid.uuid4()),
        measured_at=datetime(day.year, day.month, day.day, hour, 0),
        weight_kg=kg,
        tz=TZ,
        **kw,
    )
    return row


def test_bodyweight_at_latest_before_else_earliest_after_else_fallback(db):
    assert bodyweight_at(db, date(2024, 1, 1)) is None
    assert bodyweight_at(db, date(2024, 1, 1), fallback_kg=82) == 82
    _add(db, date(2024, 3, 1), 80.0)
    _add(db, date(2024, 6, 1), 78.0)
    _add(db, date(2024, 6, 1), 77.5, hour=18)  # same day: the later weigh-in wins
    db.commit()
    assert bodyweight_at(db, date(2024, 1, 1), fallback_kg=82) == 80.0  # earliest after
    assert bodyweight_at(db, date(2024, 3, 1), fallback_kg=82) == 80.0  # on the day
    assert bodyweight_at(db, date(2024, 5, 31), fallback_kg=82) == 80.0  # latest before
    assert bodyweight_at(db, date(2024, 6, 1), fallback_kg=82) == 77.5
    assert bodyweight_at(db, date(2030, 1, 1), fallback_kg=82) == 77.5


def test_deleted_measurements_are_ignored(db):
    row = _add(db, date(2024, 3, 1), 80.0)
    _add(db, date(2024, 1, 1), 70.0)
    row.deleted_at = datetime.now(UTC)
    db.commit()
    assert bodyweight_at(db, date(2024, 4, 1)) == 70.0


def test_timeline_matches_db_lookup():
    t = BodyweightTimeline([(date(2024, 3, 1), 80.0), (date(2024, 6, 1), 78.0)], fallback_kg=82)
    assert [t.at(date(2024, 1, 1)), t.at(date(2024, 3, 1)), t.at(date(2024, 5, 1))] == [
        80.0,
        80.0,
        80.0,
    ]
    assert t.at(date(2025, 1, 1)) == 78.0
    assert BodyweightTimeline([], fallback_kg=82).at(date(2024, 1, 1)) == 82
    assert BodyweightTimeline([]).at(date(2024, 1, 1)) is None


def test_measured_at_timezone_decides_the_day(db):
    # 23:30 UTC on Dec 31 is already Jan 1 in Vienna
    row, _ = upsert_measurement(
        db,
        id=str(uuid.uuid4()),
        measured_at=datetime(2023, 12, 31, 23, 30, tzinfo=UTC),
        weight_kg=82,
        tz=TZ,
    )
    assert row.day == date(2024, 1, 1)


def test_manual_id_is_deterministic_per_day_and_upsert_is_idempotent(db):
    d = date(2026, 10, 24)
    assert manual_id(d) == manual_id(d) != manual_id(date(2026, 10, 25))
    for kg in (82.0, 82.0, 81.5):
        upsert_measurement(
            db, id=manual_id(d), measured_at=datetime(2026, 10, 24, 12), weight_kg=kg, tz=TZ
        )
    db.commit()
    rows = db.scalars(select(BodyMeasurement)).all()
    assert len(rows) == 1 and rows[0].weight_kg == 81.5 and rows[0].source == "manual"


# --------------------------------------------------------------------------- API

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_api_token] = lambda: TOKEN
    yield TestClient(app)
    app.dependency_overrides.clear()


def _count(db):
    return db.scalar(select(func.count()).select_from(BodyMeasurement))


def test_create_twice_with_same_uuid_gives_one_row(client, db):
    body = {"id": str(uuid.uuid4()), "measured_at": "2026-10-24T07:30:00", "weight_kg": 82.2}
    first = client.post("/api/v1/bodyweight", json=body, headers=AUTH)
    assert first.status_code == 201
    second = client.post("/api/v1/bodyweight", json={**body, "weight_kg": 82.4}, headers=AUTH)
    assert second.status_code == 200 and second.json()["weight_kg"] == 82.4
    assert _count(db) == 1
    assert first.json()["id"] == body["id"] and first.json()["day"] == "2026-10-24"
    assert first.json()["source"] == "manual"


def test_date_only_and_source(client, db):
    body = {
        "id": str(uuid.uuid4()),
        "measured_at": "2026-10-20",
        "weight_kg": 76,
        "source": MeasurementSource.GARMIN.value,
    }
    r = client.post("/api/v1/bodyweight", json=body, headers=AUTH)
    assert (
        r.status_code == 201 and r.json()["day"] == "2026-10-20" and r.json()["source"] == "garmin"
    )


def test_delete_is_a_tombstone(client, db):
    mid = str(uuid.uuid4())
    client.post(
        "/api/v1/bodyweight",
        json={"id": mid, "measured_at": "2026-10-24T07:30:00", "weight_kg": 82},
        headers=AUTH,
    )
    assert client.delete(f"/api/v1/bodyweight/{mid}", headers=AUTH).status_code == 204
    assert client.delete(f"/api/v1/bodyweight/{mid}", headers=AUTH).status_code == 204  # idempotent
    assert client.get("/api/v1/bodyweight", headers=AUTH).json() == []
    assert _count(db) == 1  # row kept ...
    assert db.get(BodyMeasurement, mid).deleted_at is not None  # ... as a tombstone
    assert bodyweight_at(db, date(2026, 10, 24)) is None
    # re-posting a deleted id does not resurrect it
    again = client.post(
        "/api/v1/bodyweight",
        json={"id": mid, "measured_at": "2026-10-24T07:30:00", "weight_kg": 82},
        headers=AUTH,
    )
    assert again.status_code == 409
    assert client.delete(f"/api/v1/bodyweight/{uuid.uuid4()}", headers=AUTH).status_code == 404


def test_list_filters_by_date(client):
    for day, kg in (("2026-01-05", 81), ("2026-02-05", 82), ("2026-03-05", 83)):
        client.post(
            "/api/v1/bodyweight",
            json={"id": str(uuid.uuid4()), "measured_at": day, "weight_kg": kg},
            headers=AUTH,
        )
    everything = client.get("/api/v1/bodyweight", headers=AUTH).json()
    assert [m["weight_kg"] for m in everything] == [81, 82, 83]
    window = client.get("/api/v1/bodyweight?from=2026-02-01&to=2026-02-28", headers=AUTH).json()
    assert [m["weight_kg"] for m in window] == [82]


def test_validation(client):
    ok = {"measured_at": "2026-10-24", "weight_kg": 82}
    assert (
        client.post("/api/v1/bodyweight", json={**ok, "id": "nope"}, headers=AUTH).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/bodyweight",
            json={**ok, "id": str(uuid.uuid4()), "weight_kg": 7.5},
            headers=AUTH,
        ).status_code
        == 422
    )


def test_auth(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        c = TestClient(app)
        # no token configured on the server: refuse everything
        assert c.get("/api/v1/bodyweight", headers=AUTH).status_code == 503
        app.dependency_overrides[get_api_token] = lambda: TOKEN
        assert c.get("/api/v1/bodyweight").status_code == 401
        assert (
            c.get("/api/v1/bodyweight", headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        assert (
            c.get("/api/v1/bodyweight", headers={"Authorization": f"Basic {TOKEN}"}).status_code
            == 401
        )
        assert c.get("/api/v1/bodyweight", headers=AUTH).status_code == 200
        assert c.get("/health").status_code == 200  # the rest of the skeleton stays open
    finally:
        app.dependency_overrides.clear()
