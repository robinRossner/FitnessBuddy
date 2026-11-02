"""Planned runs -> Garmin workouts (fitness/garmin/workouts.py) against a fake python-garminconnect
client: workout JSON per run type, names, idempotency, replace on change, cleanup of past / done /
removed workouts, foreign workouts untouched, scheduler wiring, endpoint auth. No network."""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.fitness.api.deps import get_db, get_settings_dep
from apps.fitness.db.models import PlannedSession, Run
from apps.fitness.db.types import Source
from apps.fitness.garmin import workouts as gw
from apps.fitness.garmin.errors import SourceUnavailable
from apps.fitness.garmin.python_garminconnect import GarminConnectLogin
from apps.fitness.jobs.scheduler import build_scheduler
from apps.fitness.settings import Settings
from core.auth import get_api_token
from server import app

TODAY = date(2026, 10, 28)  # a Wednesday
TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
HR_EASY = {"zone": "Z1-Z2", "low": 127, "high": 156, "basis": "lthr"}
PACE_EASY = {"min_s_per_km": 324, "max_s_per_km": 388, "label": "5:24-6:28"}


def easy(km: float = 5.0, strides: bool = True, typ: str = "easy_run") -> dict[str, Any]:
    s: dict[str, Any] = {"type": typ, "distance_km": km, "hr": HR_EASY, "pace": PACE_EASY}
    if strides:
        s["structure"] = {"strides": {"reps": 6, "duration_s": 20.0, "recovery_s": 60.0}}
    return s


def intervals(reps: int = 3, rep_m: float = 1000.0, stype: str = "threshold") -> dict[str, Any]:
    return {
        "type": "intervals",
        "structure": {
            "session_type": stype,
            "warmup": {"distance_km": 1.0, "hr": HR_EASY},
            "work": {
                "reps": reps,
                "distance_m": rep_m,
                "duration_s": 294,
                "pace_s_per_km": 294,
                "pace_label": "4:54",
                "pace_range_s_per_km": [290.0, 298.0],
                "hr": {"zone": "Z4", "low": 166, "high": 173},
            },
            "recovery": None
            if stype == "time_trial"
            else {"distance_m": 240, "duration_s": 90.0, "type": "jog"},
            "cooldown": {"distance_km": 0.7},
        },
        "total_km": 5.2,
    }


def settings(**kw: Any) -> Settings:
    base = {
        "garmin_email": "me@example.test",
        "garmin_password": "pw-not-real",
        "garmin_push_days": 2,
        "garmin_request_interval_s": 0.0,
    }
    return Settings(_env_file=None, **{**base, **kw})


class FakeGarmin:
    """In-memory Garmin workout library + calendar (the python-garminconnect methods we use)."""

    def __init__(self) -> None:
        self.ids = itertools.count(1000)
        self.workouts: dict[int, dict[str, Any]] = {}
        self.schedules: dict[int, tuple[int, str]] = {}  # schedule id -> (workout id, date)
        self.calls: list[tuple] = []
        self.list_without_description = False

    def add_foreign(self, name: str, description: str | None = None) -> int:
        wid = next(self.ids)
        self.workouts[wid] = {"workoutId": wid, "workoutName": name, "description": description}
        return wid

    def get_workouts(self, start: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        self.calls.append(("get_workouts", start, limit))
        items = list(self.workouts.values())[start : start + limit]
        if self.list_without_description:
            return [{k: v for k, v in w.items() if k != "description"} for w in items]
        return [dict(w) for w in items]

    def get_workout_by_id(self, wid: int) -> dict[str, Any]:
        self.calls.append(("get_workout_by_id", wid))
        return dict(self.workouts[wid])

    def upload_running_workout(self, workout: Any) -> dict[str, Any]:
        payload = workout.to_dict()
        wid = next(self.ids)
        self.workouts[wid] = {"workoutId": wid, **payload}
        self.calls.append(("upload", payload["workoutName"]))
        return {"workoutId": wid, "workoutName": payload["workoutName"]}

    def schedule_workout(self, wid: int, day: str) -> dict[str, Any]:
        sid = next(self.ids)
        self.schedules[sid] = (wid, day)
        self.calls.append(("schedule", wid, day))
        return {"workoutScheduleId": sid}

    def get_scheduled_workouts_by_period(self, start: str, end: str) -> list[dict[str, Any]]:
        self.calls.append(("schedules", start, end))
        return [
            {"workoutScheduleId": sid, "calendarDate": d, "workout": {"workoutId": wid}}
            for sid, (wid, d) in self.schedules.items()
            if start <= d <= end
        ]

    def unschedule_workout(self, sid: int) -> None:
        self.calls.append(("unschedule", sid))
        self.schedules.pop(sid)

    def delete_workout(self, wid: int) -> None:
        self.calls.append(("delete", wid))
        self.workouts.pop(wid)
        for sid in [s for s, (w, _) in self.schedules.items() if w == wid]:
            self.schedules.pop(sid)

    # helpers for assertions
    def ours(self) -> dict[str, tuple[int, str]]:
        return {
            w["workoutName"]: (wid, w.get("description") or "")
            for wid, w in self.workouts.items()
            if "[FitnessBuddy ps=" in (w.get("description") or "")
        }

    def writes(self) -> list[tuple]:
        return [c for c in self.calls if c[0] in ("upload", "schedule", "unschedule", "delete")]


@pytest.fixture
def fake(monkeypatch):
    f = FakeGarmin()
    monkeypatch.setattr(gw, "client_provider", lambda s: f)
    monkeypatch.setattr(gw, "_last_fingerprint", None)
    return f


def add_planned(db, pid: str, day: date, kind: str, session: dict, status: str = "proposed"):
    db.add(
        PlannedSession(
            id=pid,
            day=day,
            kind=kind,
            status=status,
            prescription={"title": kind, "session": session},
        )
    )
    db.commit()


def add_run(db, day: date) -> None:
    local = datetime.combine(day, datetime.min.time()) + timedelta(hours=7)
    db.add(
        Run(
            source=Source.GARMIN,
            external_id=f"x{day}",
            started_at_utc=local.replace(tzinfo=UTC),
            started_at_local=local,
            activity_type="running",
            distance_m=5000.0,
        )
    )
    db.commit()


P1 = "11111111-1111-5111-8111-111111111111"
P2 = "22222222-2222-5222-8222-222222222222"
P3 = "33333333-3333-5333-8333-333333333333"
P4 = "44444444-4444-5444-8444-444444444444"


def push(db, **kw):
    return gw.push_workouts(
        db, settings(), today=kw.pop("today", TODAY), sleep=lambda s: None, **kw
    )


# ---------------------------------------------------------------------------- workout JSON


def steps_of(spec) -> list[dict]:
    return spec.payload()["workoutSegments"][0]["workoutSteps"]


def test_easy_run_json_distance_step_with_hr_range_and_strides():
    run = gw.PlannedRun(P1, date(2026, 10, 29), "easy_run", "Easy run 5 km", easy(5.0))
    spec = gw.build_workout(run)
    p = spec.payload()
    assert p["sportType"]["sportTypeKey"] == "running"
    main, strides = steps_of(spec)
    assert main["endCondition"]["conditionTypeKey"] == "distance"
    assert main["endConditionValue"] == 5000.0
    assert main["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert (main["targetValueOne"], main["targetValueTwo"]) == (127.0, 156.0)
    assert strides["type"] == "RepeatGroupDTO" and strides["numberOfIterations"] == 6
    work, rec = strides["workoutSteps"]
    assert work["endConditionValue"] == 20.0 and rec["stepType"]["stepTypeKey"] == "recovery"
    assert p["estimatedDurationInSecs"] == 5 * 370 + 6 * 80
    assert (
        "[FitnessBuddy ps=" + P1 + " d=2026-10-29 h=" + spec.content_hash + "]"
        in (p["description"])
    )


def test_long_run_without_strides_is_one_step():
    run = gw.PlannedRun(P1, date(2026, 11, 1), "long_run", None, easy(11.0, False, "long_run"))
    spec = gw.build_workout(run)
    [main] = steps_of(spec)
    assert main["endConditionValue"] == 11000.0
    assert spec.name == "Sun 01.11 Long 11 km"


def test_intervals_json_warmup_repeats_recovery_cooldown_with_pace_and_hr():
    run = gw.PlannedRun(P2, date(2026, 10, 30), "intervals", None, intervals())
    spec = gw.build_workout(run)
    wu, rep, cd = steps_of(spec)
    assert wu["stepType"]["stepTypeKey"] == "warmup" and wu["endConditionValue"] == 1000.0
    assert wu["targetValueTwo"] == 156.0
    assert rep["numberOfIterations"] == 3 and rep["skipLastRestStep"] is True
    work, recovery = rep["workoutSteps"]
    assert work["endConditionValue"] == 1000.0
    assert work["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    # m/s, slower pace = lower limit: 298 s/km .. 290 s/km
    assert work["targetValueOne"] == pytest.approx(1000 / 298, abs=1e-3)
    assert work["targetValueTwo"] == pytest.approx(1000 / 290, abs=1e-3)
    assert work["secondaryTargetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert work["secondaryTargetValueTwo"] == 173.0
    assert recovery["endCondition"]["conditionTypeKey"] == "time"
    assert recovery["endConditionValue"] == 90.0
    assert cd["stepType"]["stepTypeKey"] == "cooldown" and cd["endConditionValue"] == 700.0
    orders = [wu["stepOrder"], rep["stepOrder"], work["stepOrder"], recovery["stepOrder"]]
    assert orders == [1, 2, 3, 4] and cd["stepOrder"] == 5
    assert spec.name == "Fri 30.10 Thr 3x1k"


def test_time_trial_has_no_repeat_group():
    run = gw.PlannedRun(P2, date(2026, 10, 30), "intervals", None, intervals(1, 3000, "time_trial"))
    spec = gw.build_workout(run)
    _wu, work, _cd = steps_of(spec)
    assert work["stepType"]["stepTypeKey"] == "interval" and work["endConditionValue"] == 3000.0
    assert spec.name == "Fri 30.10 TT 3 km"


@pytest.mark.parametrize(
    ("session", "kind", "name"),
    [
        (easy(4.0), "easy_run", "Thu 29.10 Easy 4 km"),
        (intervals(5, 800, "vo2max"), "intervals", "Thu 29.10 VO2 5x800m"),
        (intervals(4, 1000, "race_5k"), "intervals", "Thu 29.10 5K 4x1k"),
        (intervals(6, 400, "unknown"), "intervals", "Thu 29.10 Int 6x400m"),
    ],
)
def test_names_are_short_dated_and_ascii(session, kind, name):
    spec = gw.build_workout(gw.PlannedRun(P1, date(2026, 10, 29), kind, None, session))
    assert spec.name == name
    assert len(spec.name) <= gw.NAME_MAX and spec.name.isascii()


def test_hash_changes_with_content_and_date():
    a = gw.build_workout(gw.PlannedRun(P1, date(2026, 10, 29), "easy_run", None, easy(5.0)))
    b = gw.build_workout(gw.PlannedRun(P1, date(2026, 10, 29), "easy_run", None, easy(5.0)))
    c = gw.build_workout(gw.PlannedRun(P1, date(2026, 10, 29), "easy_run", None, easy(4.0)))
    d = gw.build_workout(gw.PlannedRun(P1, date(2026, 10, 30), "easy_run", None, easy(5.0)))
    assert a.content_hash == b.content_hash
    assert len({a.content_hash, c.content_hash, d.content_hash}) == 3


# ---------------------------------------------------------------------------- reconcile


def test_uploads_and_schedules_window_then_second_run_is_unchanged(db, fake):
    add_planned(db, P1, TODAY, "easy_run", easy(5.0))
    add_planned(db, P2, TODAY + timedelta(days=2), "intervals", intervals())
    add_planned(db, P3, TODAY + timedelta(days=4), "long_run", easy(10.0, False, "long_run"))
    rep = push(db)
    assert rep.counts["uploaded"] == 2 and rep.counts["failed"] == 0
    assert set(fake.ours()) == {"Wed 28.10 Easy 5 km", "Fri 30.10 Thr 3x1k"}
    assert sorted(d for _, d in fake.schedules.values()) == ["2026-10-28", "2026-10-30"]
    fake.calls.clear()
    again = push(db)
    assert again.counts["unchanged"] == 2 and again.counts["uploaded"] == 0
    assert fake.writes() == []  # idempotent: no Garmin writes
    out = again.as_dict()
    assert out["window"] == {"from": "2026-10-28", "to": "2026-10-30"}
    assert [d["date"] for d in out["days"]] == ["2026-10-28", "2026-10-30"]


def test_changed_prescription_replaces_the_workout(db, fake):
    add_planned(db, P1, TODAY + timedelta(days=1), "intervals", intervals())
    push(db)
    [(old_id, _)] = fake.ours().values()
    row = db.get(PlannedSession, P1)  # readiness turned the intervals into an easy run
    row.kind, row.prescription = "easy_run", {"session": easy(4.0)}
    db.commit()
    rep = push(db)
    assert rep.counts["updated"] == 1
    assert list(fake.ours()) == ["Thu 29.10 Easy 4 km"]
    assert old_id not in fake.workouts
    assert [d for _, d in fake.schedules.values()] == ["2026-10-29"]
    # upload first, then delete the old one (never an empty day on failure)
    kinds = [c[0] for c in fake.writes()]
    assert kinds.index("upload") < kinds.index("delete")


def test_moved_session_is_rescheduled_under_a_new_name(db, fake):
    add_planned(db, P1, TODAY + timedelta(days=1), "easy_run", easy(5.0))
    push(db)
    row = db.get(PlannedSession, P1)
    row.day = TODAY + timedelta(days=2)
    db.commit()
    rep = push(db)
    assert rep.counts["updated"] == 1
    assert list(fake.ours()) == ["Fri 30.10 Easy 5 km"]
    assert [d for _, d in fake.schedules.values()] == ["2026-10-30"]


def test_cleanup_past_done_and_removed_but_never_foreign(db, fake):
    add_planned(db, P1, TODAY, "easy_run", easy(5.0))
    add_planned(db, P2, TODAY + timedelta(days=1), "intervals", intervals())
    add_planned(db, P3, TODAY + timedelta(days=2), "easy_run", easy(3.0))
    add_planned(db, P4, TODAY - timedelta(days=1), "easy_run", easy(6.0))
    # pushed yesterday (window 27..29, plus Friday on request)
    push(db, today=TODAY - timedelta(days=1), extra_day=TODAY + timedelta(days=2))
    assert len(fake.ours()) == 4
    foreign = fake.add_foreign("Thu 29.10 Easy 5 km", "my own workout")  # looks like ours
    foreign2 = fake.add_foreign("Tempo 10k")
    fake.schedules[999] = (foreign, "2026-10-29")
    add_run(db, TODAY)  # ran today already
    db.get(PlannedSession, P3).deleted_at = datetime.now(UTC)  # coach removed it
    db.commit()
    rep = push(db)
    deleted = {(i.date, i.reason) for i in rep.items if i.action == "deleted"}
    assert deleted == {
        ("2026-10-27", "past"),
        ("2026-10-28", "run done or session no longer planned"),
        ("2026-10-30", "run done or session no longer planned"),
    }
    assert list(fake.ours()) == ["Thu 29.10 Thr 3x1k"]
    assert foreign in fake.workouts and foreign2 in fake.workouts
    assert fake.schedules[999] == (foreign, "2026-10-29")
    unscheduled = [c for c in fake.calls if c[0] == "unschedule"]
    assert len(unscheduled) == 3  # schedule entries are removed explicitly before the delete


def test_closed_session_is_removed(db, fake):
    add_planned(db, P1, TODAY + timedelta(days=1), "easy_run", easy(5.0))
    push(db)
    db.get(PlannedSession, P1).status = "skipped"
    db.commit()
    assert push(db).counts["deleted"] == 1 and fake.ours() == {}


def test_marker_checked_in_full_workout_when_list_has_no_description(db, fake):
    add_planned(db, P1, TODAY + timedelta(days=1), "easy_run", easy(5.0))
    push(db)
    foreign = fake.add_foreign("Thu 29.10 Easy 5 km", "not ours")
    fake.list_without_description = True
    fake.calls.clear()
    rep = push(db)
    assert rep.counts["unchanged"] == 1 and rep.counts["deleted"] == 0
    assert foreign in fake.workouts
    assert ("get_workout_by_id", foreign) in fake.calls


def test_duplicate_of_ours_is_deleted(db, fake):
    add_planned(db, P1, TODAY + timedelta(days=1), "easy_run", easy(5.0))
    push(db)
    [(wid, desc)] = fake.ours().values()
    dup = next(fake.ids)
    fake.workouts[dup] = {
        "workoutId": dup,
        "workoutName": "Thu 29.10 Easy 5 km",
        "description": desc,
    }
    rep = push(db)
    assert rep.counts == {**rep.counts, "unchanged": 1}
    assert len(fake.ours()) == 1 and (wid in fake.workouts) != (dup in fake.workouts)


def test_unscheduled_workout_of_ours_is_rescheduled(db, fake):
    add_planned(db, P1, TODAY + timedelta(days=1), "easy_run", easy(5.0))
    push(db)
    fake.schedules.clear()  # user removed it from the calendar
    rep = push(db)
    assert rep.counts["scheduled"] == 1
    assert [d for _, d in fake.schedules.values()] == ["2026-10-29"]


def test_extra_day_is_pushed_and_kept_up_to_date(db, fake):
    add_planned(db, P3, TODAY + timedelta(days=5), "long_run", easy(10.0, False, "long_run"))
    assert push(db).counts["uploaded"] == 0
    assert push(db, extra_day=TODAY + timedelta(days=5)).counts["uploaded"] == 1
    assert push(db).counts["unchanged"] == 1  # outside the window but still open: kept


def test_dry_run_never_contacts_garmin(db, monkeypatch):
    def boom(s):
        raise AssertionError("dry run must not log in")

    monkeypatch.setattr(gw, "client_provider", boom)
    add_planned(db, P1, TODAY + timedelta(days=1), "easy_run", easy(5.0))
    rep = push(db, dry_run=True)
    assert rep.dry_run and not rep.remote_checked
    assert [w.name for w in rep.workouts] == ["Thu 29.10 Easy 5 km"]


def test_failed_upload_is_reported_and_old_workout_kept(db, fake):
    add_planned(db, P1, TODAY + timedelta(days=1), "easy_run", easy(5.0))
    push(db)
    db.get(PlannedSession, P1).prescription = {"session": easy(4.0)}
    db.commit()

    def broken(workout):
        raise type("GarminConnectConnectionError", (Exception,), {})("HTTP 500")

    fake.upload_running_workout = broken
    rep = push(db)
    assert rep.counts["failed"] == 1
    assert list(fake.ours()) == ["Thu 29.10 Easy 5 km"]  # nothing deleted


# ---------------------------------------------------------------------------- hooks and login


def test_push_if_changed_skips_garmin_when_nothing_changed(db, fake, session_factory, monkeypatch):
    monkeypatch.setattr(gw, "user_today", lambda s: TODAY)
    add_planned(db, P1, TODAY + timedelta(days=1), "easy_run", easy(5.0))
    assert gw.push_if_changed("test", settings(), session_factory) is not None
    fake.calls.clear()
    assert gw.push_if_changed("test", settings(), session_factory) is None
    assert fake.calls == []
    db.get(PlannedSession, P1).prescription = {"session": easy(4.0)}
    db.commit()
    rep = gw.push_if_changed("test", settings(), session_factory)
    assert rep is not None and rep.counts["updated"] == 1


def test_push_soon_only_in_the_server_process(monkeypatch):
    started = []
    monkeypatch.setattr(gw.threading, "Thread", lambda **kw: started.append(kw) or _NoThread())
    monkeypatch.setattr(gw, "get_settings", lambda: settings(scheduler_enabled=False))
    gw.push_soon("x")
    assert started == []
    monkeypatch.setattr(gw, "get_settings", lambda: settings(scheduler_enabled=True))
    gw.push_soon("x")
    assert len(started) == 1
    monkeypatch.setattr(
        gw, "get_settings", lambda: settings(scheduler_enabled=True, garmin_push_workouts=False)
    )
    gw.push_soon("x")
    assert len(started) == 1


class _NoThread:
    def start(self) -> None:
        pass


def test_failed_login_is_not_retried_until_the_cooldown(tmp_path):
    attempts = []
    now = [0.0]

    class Lib:
        def __init__(self, email, pw):
            pass

        def login(self, tokenstore):
            attempts.append(tokenstore)
            raise type("GarminConnectTooManyRequestsError", (Exception,), {})("429")

    login = GarminConnectLogin(
        "me@example.test",
        "pw-not-real",
        tmp_path,
        client_factory=Lib,
        retry_after_s=3600,
        clock=lambda: now[0],
    )
    for _ in range(3):
        with pytest.raises(SourceUnavailable):
            login.client()
    assert len(attempts) == 1
    now[0] = 3601
    with pytest.raises(SourceUnavailable):
        login.client()
    assert len(attempts) == 2


def test_job_never_raises(monkeypatch):
    monkeypatch.setattr(gw, "get_settings", lambda: settings())

    def boom(*a, **k):
        raise SourceUnavailable("python-garminconnect login failed (X)")

    monkeypatch.setattr(gw, "push_workouts", boom)
    monkeypatch.setattr("apps.fitness.db.session.get_session_factory", lambda: None)
    gw.push_job()  # logged, not raised


# ---------------------------------------------------------------------------- scheduler + API


def test_scheduler_has_evening_and_morning_push():
    sched = build_scheduler(Settings(_env_file=None))
    jobs = {j.id: j for j in sched.get_jobs()}
    for jid, hour, minute in (
        ("garmin_workouts_1945", "19", "45"),
        ("garmin_workouts_0500", "5", "0"),
    ):
        fields = {f.name: str(f) for f in jobs[jid].trigger.fields}
        assert (fields["hour"], fields["minute"]) == (hour, minute)
        assert jobs[jid].func is gw.push_job
    off = build_scheduler(Settings(_env_file=None, garmin_push_workouts=False))
    assert not [j for j in off.get_jobs() if j.id.startswith("garmin_workouts")]


@pytest.fixture
def client(db, fake, monkeypatch):
    monkeypatch.setattr(gw, "user_today", lambda s: TODAY)
    app.dependency_overrides[get_settings_dep] = lambda: settings()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_api_token] = lambda: TOKEN
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_endpoint_requires_the_token(client):
    assert client.post("/api/v1/garmin/workouts/push").status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert client.post("/api/v1/garmin/workouts/push", headers=bad).status_code == 401


def test_endpoint_pushes_and_summarises(client, db, fake):
    add_planned(db, P1, TODAY + timedelta(days=1), "easy_run", easy(5.0))
    r = client.post("/api/v1/garmin/workouts/push", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"]["uploaded"] == 1 and body["dry_run"] is False
    [day] = body["days"]
    assert day["date"] == "2026-10-29"
    assert day["items"][0]["name"] == "Thu 29.10 Easy 5 km"
    r = client.post("/api/v1/garmin/workouts/push", headers=AUTH, json={"dry_run": True})
    assert r.json()["remote_checked"] is False


def test_endpoint_date_and_errors(client, monkeypatch):
    far = (TODAY + timedelta(days=60)).isoformat()
    r = client.post("/api/v1/garmin/workouts/push", headers=AUTH, json={"date": far})
    assert r.status_code == 422

    def no_login(s):
        raise SourceUnavailable("python-garminconnect login failed (X); not retrying for now")

    monkeypatch.setattr(gw, "client_provider", no_login)
    r = client.post("/api/v1/garmin/workouts/push", headers=AUTH)
    assert r.status_code == 503 and "login failed" in r.json()["detail"]
    app.dependency_overrides[get_settings_dep] = lambda: settings(garmin_push_workouts=False)
    assert client.post("/api/v1/garmin/workouts/push", headers=AUTH).status_code == 503
