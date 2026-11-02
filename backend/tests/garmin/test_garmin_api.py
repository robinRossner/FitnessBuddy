"""/api/v1/runs, /api/v1/runs/{id}, /api/v1/health/daily (bearer protected) + scheduler wiring."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from apps.fitness.api.routes import get_db
from apps.fitness.db.models import DailyHealth, Lap, Run
from apps.fitness.db.types import Source
from apps.fitness.garmin.errors import GarminError, RateLimitedError, SourceUnavailable
from apps.fitness.garmin.sync import run_id
from apps.fitness.jobs.scheduler import build_scheduler
from apps.fitness.settings import Settings
from core.auth import get_api_token
from server import app
from tests.support.garmin_fakes import FakeSource, activity_raw, hrv_raw, sleep_raw

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


def test_engine_hook_is_a_harmless_placeholder(pin_session_factory, monkeypatch):
    from apps.fitness.garmin.sync import SyncReport
    from apps.fitness.jobs.garmin_jobs import on_new_data
    from apps.fitness.settings import get_settings

    # The hook opens get_session_factory() and may run the engine + a Garmin push: keep it on
    # the test database and off the engine run (it used to open the real var/fitness.db).
    monkeypatch.setenv("ENGINE_AUTO_RUN", "false")
    get_settings.cache_clear()
    assert on_new_data(SyncReport()) is None


# ---- POST /garmin/sync (manual sync) ------------------------------------------------------------


class Env:
    """Wires the endpoint to a FakeSource and the temp database; records engine hook calls."""

    def __init__(self, settings, source):
        self.settings, self.source, self.hooks, self.build_kwargs = settings, source, [], []
        self.error: Exception | None = None

    @property
    def today(self) -> str:
        from apps.fitness.garmin import workouts as garmin_workouts

        return garmin_workouts.user_today(self.settings).isoformat()


@pytest.fixture
def manual(client, session_factory, monkeypatch, tmp_path):
    from apps.fitness.garmin.manual import manual_sync_guard
    from apps.fitness.jobs import garmin_jobs

    s = Settings(
        _env_file=None,
        garmin_fit_dir=tmp_path / "fit",
        garmin_manual_sync_cooldown_s=0,
        garmin_manual_sync_daily_max=3,
    )
    env = Env(s, FakeSource())

    def build_source(settings, **kwargs):
        env.build_kwargs.append(kwargs)
        if env.error is not None:
            raise env.error
        return env.source

    monkeypatch.setattr("apps.fitness.api.garmin.get_settings", lambda: s)
    monkeypatch.setattr(garmin_jobs, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(garmin_jobs, "build_source", build_source)
    monkeypatch.setattr(garmin_jobs, "on_new_data", env.hooks.append)
    manual_sync_guard.reset()
    yield env
    manual_sync_guard.reset()


def post_sync(client):
    return client.post("/api/v1/garmin/sync")


def test_manual_sync_requires_the_bearer_token(manual):
    assert TestClient(app).post("/api/v1/garmin/sync").status_code == 401


def test_manual_sync_pulls_light_kinds_and_reports_what_is_new(client, manual):
    day = manual.today
    manual.source.activity_items = [activity_raw(900, day, activityName="Morning Run")]
    manual.source.daily_items["hrv"] = [hrv_raw(day, 75)]
    manual.source.daily_items["sleep"] = [sleep_raw(day)]
    res = post_sync(client)
    assert res.status_code == 200
    assert res.json() == {
        "status": "synced",
        "retry_after_s": None,
        "new_activities": 1,
        "latest_activity": {
            "title": "Morning Run",
            "activity_type": "running",
            "started_at_local": f"{day}T07:30:00",
        },
        "hrv_today": True,
        "manual_syncs_today": 1,
        "manual_syncs_limit": 3,
    }
    assert manual.build_kwargs == [{"cached_tokens_only": True}]  # never a fresh SSO login
    assert {c[0] for c in manual.source.calls} >= {"activities", "daily_summary", "sleep", "hrv"}
    assert len(manual.hooks) == 1  # engine ran for the new data
    again = post_sync(client).json()  # overlap re-read of the same data
    assert again["status"] == "synced" and again["new_activities"] == 0
    assert again["manual_syncs_today"] == 2
    assert len(manual.hooks) == 1  # nothing changed: no engine run


def test_manual_sync_hrv_today_false_until_it_exists(client, manual):
    assert post_sync(client).json()["hrv_today"] is False
    manual.source.daily_items["hrv"] = [hrv_raw(manual.today)]
    body = post_sync(client).json()
    assert body["hrv_today"] is True and len(manual.hooks) == 2  # late HRV re-ran the engine


def test_manual_sync_cooldown_makes_no_garmin_calls(client, manual):
    manual.settings.garmin_manual_sync_cooldown_s = 30
    assert post_sync(client).json()["status"] == "synced"
    calls = len(manual.source.calls)
    body = post_sync(client).json()
    assert body["status"] == "cooldown" and 1 <= body["retry_after_s"] <= 30
    assert body["new_activities"] == 0 and body["manual_syncs_today"] == 1
    assert body["latest_activity"] is not None and "hrv_today" in body
    assert len(manual.source.calls) == calls and len(manual.build_kwargs) == 1


def test_manual_sync_daily_limit(client, manual):
    manual.settings.garmin_manual_sync_daily_max = 2
    assert [post_sync(client).json()["status"] for _ in range(2)] == ["synced", "synced"]
    calls = len(manual.source.calls)
    body = post_sync(client).json()
    assert body["status"] == "limit_reached" and 1 <= body["retry_after_s"] <= 86400
    assert body["manual_syncs_today"] == 2 and body["manual_syncs_limit"] == 2
    assert len(manual.source.calls) == calls


def test_manual_sync_busy_does_not_use_up_quota(client, manual):
    from apps.fitness.jobs import garmin_jobs

    manual.settings.garmin_manual_sync_cooldown_s = 30
    assert garmin_jobs._lock.acquire(blocking=False)
    try:
        body = post_sync(client).json()
    finally:
        garmin_jobs._lock.release()
    assert body["status"] == "busy" and body["retry_after_s"] == 5
    assert body["new_activities"] == 0 and body["manual_syncs_today"] == 0
    assert manual.source.calls == []
    assert post_sync(client).json()["status"] == "synced"  # no cooldown left behind either


def test_garmin_429_pauses_manual_syncs_until_retry_after(client, manual):
    class Limited(FakeSource):
        def activities(self, start, end):
            self.calls.append(("activities",))
            raise RateLimitedError("slow down", retry_after=120)

    manual.source = Limited()
    res = post_sync(client)
    assert res.status_code == 429 and res.headers["Retry-After"] == "120"
    calls = len(manual.source.calls)
    again = post_sync(client)
    assert again.status_code == 429 and 1 <= int(again.headers["Retry-After"]) <= 120
    assert len(manual.source.calls) == calls and len(manual.build_kwargs) == 1


def test_garmin_429_without_retry_after_blocks_for_15_minutes(client, manual):
    class Limited(FakeSource):
        def activities(self, start, end):
            raise RateLimitedError("slow down")

    manual.source = Limited()
    res = post_sync(client)
    assert res.status_code == 429 and "Retry-After" not in res.headers
    again = post_sync(client)
    assert again.status_code == 429 and 890 <= int(again.headers["Retry-After"]) <= 900


@pytest.mark.parametrize(
    ("error", "code"),
    [(SourceUnavailable("no key"), 503), (GarminError("boom"), 502)],
)
def test_manual_sync_garmin_errors(client, manual, error, code):
    manual.error = error
    res = post_sync(client)
    assert res.status_code == code and str(error) in res.json()["detail"]
    from apps.fitness.garmin.manual import manual_sync_guard

    today = date.fromisoformat(manual.today)
    assert manual_sync_guard.count(today) == 1  # a failed attempt still counts


def test_guard_rolls_over_at_local_midnight_and_refunds():
    from apps.fitness.garmin.manual import ManualSyncGuard, seconds_to_next_midnight

    clock = [1000.0]
    guard = ManualSyncGuard(lambda: clock[0])
    d1, d2 = date(2026, 11, 1), date(2026, 11, 2)
    args = ("Europe/Vienna", 30, 2)
    v = guard.reserve(d1, *args)
    assert v.status == "go" and v.count == 1
    assert guard.reserve(d1, *args).status == "cooldown"
    guard.refund(v)
    assert guard.reserve(d1, *args).status == "go"
    clock[0] += 31
    assert guard.reserve(d1, *args).status == "go"
    clock[0] += 31
    assert guard.reserve(d1, *args).status == "limit_reached"
    assert guard.reserve(d2, *args).status == "go" and guard.count(d1) == 0  # new day, new quota
    noon = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)  # 13:00 in Vienna
    assert seconds_to_next_midnight("Europe/Vienna", noon) == 11 * 3600
