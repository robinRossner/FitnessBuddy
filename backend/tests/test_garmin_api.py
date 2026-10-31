"""/api/v1/runs, /api/v1/runs/{id}, /api/v1/health/daily (bearer protected) + scheduler wiring."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from apps.fitness.api.routes import get_db
from apps.fitness.db.models import DailyHealth, Lap, Run
from apps.fitness.db.types import Source
from apps.fitness.garmin_sync import run_id
from apps.fitness.jobs.scheduler import build_scheduler
from apps.fitness.settings import Settings
from core.auth import get_api_token
from server import app

TOKEN = "test-token"


@pytest.fixture
def client(db):
    def add_run(ext, local, typ, dist=5000.0, laps=0):
        run = Run(
            id=run_id(ext),
            external_id=ext,
            source=Source.GARMIN,
            started_at_utc=datetime.fromisoformat(local).replace(tzinfo=UTC),
            started_at_local=datetime.fromisoformat(local),
            tz="Europe/Vienna",
            activity_type=typ,
            title=f"run {ext}",
            duration_s=1500.0,
            distance_m=dist,
            avg_pace_s_per_km=300.0,
            training_effect_aerobic=3.1,
        )
        db.add(run)
        db.flush()
        for i in range(1, laps + 1):
            db.add(
                Lap(
                    run_id=run.id,
                    lap_index=i,
                    duration_s=300.0,
                    distance_m=1000.0,
                    avg_hr=150.0,
                    intensity="active",
                )
            )
        return run

    add_run("1", "2026-10-01T07:30:00", "running", laps=3)
    add_run("2", "2026-10-05T07:30:00", "trail_running")
    add_run("3", "2026-10-06T18:00:00", "strength_training", dist=0.0)
    gone = add_run("4", "2026-10-07T07:30:00", "running")
    gone.deleted_at = datetime.now(UTC)
    for d, rhr in ((date(2026, 10, 1), 52), (date(2026, 10, 2), 54), (date(2026, 10, 3), 53)):
        db.add(DailyHealth(day=d, source=Source.GARMIN, resting_hr=rhr, sleep_s=28000, steps=9000))
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_api_token] = lambda: TOKEN
    yield TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})
    app.dependency_overrides.clear()


def test_endpoints_require_the_bearer_token(client):
    anon = TestClient(app)
    for path in ("/api/v1/runs", "/api/v1/runs/x", "/api/v1/health/daily"):
        assert anon.get(path).status_code == 401
        assert anon.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_runs_default_to_running_types_without_deleted(client):
    rows = client.get("/api/v1/runs").json()
    assert [r["external_id"] for r in rows] == ["2", "1"]  # newest first, no strength, no deleted
    assert rows[0]["activity_type"] == "trail_running" and rows[1]["training_effect_aerobic"] == 3.1


def test_runs_filters(client):
    ids = lambda r: [x["external_id"] for x in r.json()]  # noqa: E731
    assert ids(client.get("/api/v1/runs?include_other=true")) == ["3", "2", "1"]
    assert ids(client.get("/api/v1/runs?activity_type=strength_training")) == ["3"]
    assert ids(client.get("/api/v1/runs?from=2026-10-02&to=2026-10-05")) == ["2"]
    assert ids(client.get("/api/v1/runs?to=2026-10-01")) == ["1"]  # `to` is inclusive of that day
    assert ids(client.get("/api/v1/runs?limit=1")) == ["2"]


def test_run_detail_includes_ordered_laps(client):
    body = client.get(f"/api/v1/runs/{run_id('1')}").json()
    assert [lap["lap_index"] for lap in body["laps"]] == [1, 2, 3]
    assert body["laps"][0]["intensity"] == "active" and body["title"] == "run 1"
    assert client.get("/api/v1/runs/missing").status_code == 404
    assert client.get(f"/api/v1/runs/{run_id('4')}").status_code == 404  # tombstoned


def test_daily_health_range(client):
    rows = client.get("/api/v1/health/daily?from=2026-10-02&to=2026-10-03").json()
    assert [(r["day"], r["resting_hr"]) for r in rows] == [("2026-10-02", 54), ("2026-10-03", 53)]
    assert len(client.get("/api/v1/health/daily").json()) == 3
    assert rows[0]["training_readiness"] is None


def test_scheduler_jobs_and_default_off():
    s = Settings(
        _env_file=None, sync_nightly_time="03:30", sync_day_times="07:30,11:30,15:30,19:30"
    )
    assert s.scheduler_enabled is False
    sched = build_scheduler(s)
    sched.start(paused=True)
    try:
        jobs = {j.id: j for j in sched.get_jobs()}
        assert set(jobs) == {
            "garmin_nightly",
            "garmin_day_0730",
            "garmin_day_1130",
            "garmin_day_1530",
            "garmin_day_1930",
            "garmin_workouts_1945",
            "garmin_workouts_0500",
            "advisor_weekly",
            "advisor_triggers",
            "advisor_outcomes",
        }
        night = jobs["garmin_nightly"].trigger
        assert str(night.timezone) == "Europe/Vienna"
        fields = {f.name: str(f) for f in night.fields}
        assert (fields["hour"], fields["minute"]) == ("3", "30")
        assert len(jobs["garmin_nightly"].args[0]) > len(jobs["garmin_day_0730"].args[0])
    finally:
        sched.shutdown(wait=False)


def test_lifespan_does_not_start_scheduler_by_default(monkeypatch):
    started = []
    monkeypatch.setattr("apps.fitness.jobs.scheduler.build_scheduler", lambda s: started.append(s))
    with TestClient(app):
        pass
    assert started == []


def test_lifespan_starts_and_stops_scheduler_when_enabled(monkeypatch):
    events = []

    class FakeScheduler:
        def start(self):
            events.append("start")

        def get_jobs(self):
            return [1, 2]

        def shutdown(self, wait=False):
            events.append("stop")

    monkeypatch.setattr(
        "server.get_settings", lambda: Settings(_env_file=None, scheduler_enabled=True)
    )
    monkeypatch.setattr("apps.fitness.jobs.scheduler.build_scheduler", lambda s: FakeScheduler())
    with TestClient(app):
        assert events == ["start"]
    assert events == ["start", "stop"]


def test_engine_hook_is_a_harmless_placeholder():
    from apps.fitness.garmin_sync import SyncReport
    from apps.fitness.jobs.garmin_jobs import on_new_data

    assert on_new_data(SyncReport()) is None
