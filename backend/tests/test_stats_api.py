"""Stats API (docs/api/stats.md): shapes, empty data, auth, gap filling, zone collapse, Pearson,
PR listing, run-analysis verdicts and bodyweight change, on synthetic data in a migrated SQLite."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.analytics import cache, common
from app.analytics.health import bodyweight_change_pct
from app.analytics.zones import collapse, time_in_zones
from app.api.auth import get_api_token
from app.api.routes import get_db
from app.db.models import (
    BodyMeasurement,
    DailyHealth,
    Exercise,
    ExerciseVariant,
    Gym,
    Lap,
    PlannedSession,
    Run,
    TrainingSession,
    WorkoutSet,
)
from app.db.types import Source
from app.engine import running as R
from app.engine.config import load_engine_config
from app.engine.hr import zones_from_config
from app.main import app
from app.settings import BACKEND_DIR
from app.stats import rebuild_stats

TODAY = date(2026, 10, 25)  # a Sunday: the current week starts Monday 2026-10-19
TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CFG_PATH = BACKEND_DIR / "config" / "engine.v3.yaml"

ALL_PATHS = [
    "/api/v1/stats/lifts",
    "/api/v1/stats/lifts/1",
    "/api/v1/stats/volume/weekly",
    "/api/v1/stats/running",
    "/api/v1/runs/x/analysis",
    "/api/v1/stats/health",
    "/api/v1/stats/correlations",
]


def utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC)


def days_ago(n: int) -> date:
    return TODAY - timedelta(days=n)


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.invalidate()
    yield
    cache.invalidate()


def _client(db, monkeypatch) -> TestClient:
    monkeypatch.setattr("app.analytics.common.today", lambda: TODAY)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_api_token] = lambda: TOKEN
    return TestClient(app, headers=AUTH)


@pytest.fixture
def empty(db, monkeypatch):
    yield _client(db, monkeypatch)
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- synthetic world


class World:
    def __init__(self, db):
        self.db = db
        gym = Gym(key="g1", name="Gym One")
        db.add(gym)
        ex = {
            slug: Exercise(
                slug=slug,
                name=name,
                primary_muscle=muscle,
                movement_pattern="x",
                default_equipment_type="barbell",
            )
            for slug, name, muscle in (
                ("bench_press", "Bench Press", "chest"),
                ("squat", "Squat", "quads"),
                ("row", "Row", "back"),
            )
        }
        db.add_all(ex.values())
        db.flush()
        self.gym = gym

        def variant(slug, name, gym_id=None, **kw):
            v = ExerciseVariant(
                exercise_id=ex[slug].id,
                gym_id=gym_id,
                key=name,
                name=name,
                equipment_type="barbell",
                load_convention="total",
                **kw,
            )
            db.add(v)
            return v

        self.bench = variant("bench_press", "Bench Press (Barbell)")
        self.squat = variant("squat", "Squat (Barbell) @ Gym One", gym.id)
        self.row = variant("row", "Row (Barbell)")
        self.unused = variant("row", "Row (Dumbbell)")  # no sessions -> not listed
        db.flush()

    def lift_session(self, day: date, variant, weight: float, reps: list[int], hour: int = 18):
        start = datetime.combine(day, datetime.min.time()).replace(hour=hour)
        s = TrainingSession(
            source=Source.APP,
            gym_id=self.gym.id,
            name="Upper",
            session_kind="upper",
            started_at_local=start,
            tz="Europe/Vienna",
            started_at_utc=utc(start - timedelta(hours=2)),
        )
        self.db.add(s)
        self.db.flush()
        for i, r in enumerate(reps, start=1):
            self.db.add(
                WorkoutSet(
                    session_id=s.id,
                    variant_id=variant.id,
                    source=Source.APP,
                    exercise_order=0,
                    set_index=i,
                    set_type="working",
                    weight_kg=weight,
                    reps=r,
                )
            )
        self.db.flush()
        return s

    def run(self, day: date, laps: list[tuple], title: str = "Running", **kw) -> Run:
        """laps: (distance_m, duration_s, avg_hr, intensity)."""
        start = datetime.combine(day, datetime.min.time()).replace(hour=7)
        dist = sum(lp[0] for lp in laps)
        dur = sum(lp[1] for lp in laps)
        hr = sum(lp[2] * lp[1] for lp in laps) / dur
        r = Run(
            source=Source.GARMIN,
            external_id=f"{title}-{day}",
            started_at_utc=utc(start - timedelta(hours=2)),
            started_at_local=start,
            tz="Europe/Vienna",
            activity_type="running",
            title=title,
            distance_m=dist,
            duration_s=dur,
            avg_pace_s_per_km=dur / dist * 1000,
            avg_hr=hr,
            max_hr=hr + 8,
            training_effect_aerobic=3.2,
            **kw,
        )
        self.db.add(r)
        self.db.flush()
        for i, (d, t, h, inten) in enumerate(laps, start=1):
            self.db.add(
                Lap(
                    run_id=r.id,
                    lap_index=i,
                    distance_m=d,
                    duration_s=t,
                    avg_hr=h,
                    avg_pace_s_per_km=t / d * 1000,
                    intensity=inten,
                )
            )
        self.db.flush()
        return r


def easy_laps(n: int = 6, hr: float = 140, pace: float = 330):
    return [(1000, pace, hr, None)] * n


def interval_laps(work_hr: float = 168, pace: float = 250, reps: int = 4):
    laps = [(1000, 330, 140, "warmup")]
    for _ in range(reps):
        laps += [(1000, pace, work_hr, "active"), (400, 140, work_hr - 18, "recovery")]
    return [*laps, (1000, 330, 148, "cooldown")]


@pytest.fixture
def world(db, monkeypatch):
    from app.engine import config_service as cs

    cs.ensure_seeded(db, CFG_PATH)
    w = World(db)
    # bench: PRs at -42 and -35, then four flat sessions -> stalled
    for d, kg in ((49, 60), (42, 62.5), (35, 65), (28, 65), (21, 65), (14, 65), (7, 65)):
        w.lift_session(days_ago(d), w.bench, kg, [5, 5, 5])
    # squat: 6 reps at the same weight on the second session -> a rep PR (and an e1RM PR)
    w.lift_session(days_ago(40), w.squat, 100, [5, 5])
    w.lift_session(days_ago(33), w.squat, 100, [6, 5])
    # row: +10 % best e1RM in the last 4 weeks vs the 4 weeks before
    w.lift_session(days_ago(45), w.row, 50, [5, 5])
    w.lift_session(days_ago(7), w.row, 55, [5, 5])
    rebuild_stats(db, bodyweight_kg=82)

    for i in range(60):  # 60 days of Garmin health up to today, day -5 missing
        day = days_ago(i)
        if i == 5:
            continue
        hrv, rhr = 68 + (i % 5), 53
        if i == 3:
            hrv, rhr = 52, 62  # HRV -25 % and RHR +9: red
        db.add(
            DailyHealth(
                day=day,
                source=Source.GARMIN,
                resting_hr=rhr,
                hrv_overnight_ms=hrv,
                sleep_s=(7 + i % 3) * 3600,
                sleep_score=70 + (i % 4) * 7,
                body_battery_max=95,
                body_battery_min=20,
                stress_avg=30,
                steps=9000,
            )
        )
    db.add_all(
        [
            BodyMeasurement(
                measured_at=utc(datetime.combine(days_ago(30), datetime.min.time())),
                day=days_ago(30),
                weight_kg=82.0,
                source="manual",
            ),
            BodyMeasurement(
                measured_at=utc(datetime.combine(days_ago(0), datetime.min.time())),
                day=days_ago(0),
                weight_kg=76.5,
                source="garmin",
            ),
        ]
    )
    # runs: interval session (-5), failing interval (-12), long (-10), five easy runs
    plan = PlannedSession(
        day=days_ago(5),
        kind="intervals",
        status="done",
        prescription={
            "title": "Intervals 4x1000 m",
            "session": {
                "type": "intervals",
                "structure": {"work": {"reps": 4, "pace_s_per_km": 250, "distance_m": 1000}},
            },
        },
    )
    db.add(plan)
    db.flush()
    w.planned = plan
    w.interval = w.run(days_ago(5), interval_laps(), "Intervals 4x1000", planned_session_id=plan.id)
    w.bad_interval = w.run(days_ago(12), interval_laps(work_hr=182), "Intervals again")
    w.long = w.run(days_ago(10), easy_laps(10, 146), "Long run")
    w.easy = w.run(days_ago(3), easy_laps(6, 142), "Easy run")
    w.hot = w.run(days_ago(17), easy_laps(6, 165), "Hot easy run")
    for d in (24, 31, 38):
        w.run(days_ago(d), easy_laps(5, 143 + d % 4), "Easy run")
    db.commit()
    yield w
    app.dependency_overrides.clear()


@pytest.fixture
def client(db, world, monkeypatch):
    return _client(db, monkeypatch)


def strict_json(resp) -> dict:
    """Parse the body refusing NaN / Infinity."""

    def boom(name):
        raise AssertionError(f"non-finite number {name} in response")

    return json.loads(resp.text, parse_constant=boom)


# --------------------------------------------------------------------------- auth + empty data


def test_every_endpoint_requires_the_bearer_token(empty):
    anon = TestClient(app)
    for path in ALL_PATHS:
        assert anon.get(path).status_code == 401, path
        assert anon.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401, path


def test_empty_database_gives_empty_but_well_formed_responses(empty):
    lifts = empty.get("/api/v1/stats/lifts").json()
    assert lifts["lifts"] == [] and lifts["generated_at"].endswith("Z")

    weeks = empty.get("/api/v1/stats/volume/weekly?weeks=3").json()["weeks"]
    assert [w["week_start"] for w in weeks] == ["2026-10-05", "2026-10-12", "2026-10-19"]
    assert all(w["lifting_sets"] == 0 and w["running_km"] == 0 for w in weeks)
    assert all(w["sets_by_muscle_group"] == {} for w in weeks)

    running = empty.get("/api/v1/stats/running?weeks=2").json()
    assert len(running["weekly"]) == 2 and running["efficiency"] == []
    assert running["vdot"] == [] and running["intervals"] == []

    health = empty.get("/api/v1/stats/health?days=7").json()
    assert len(health["days"]) == 7
    assert all(d["readiness"]["level"] == "unknown" for d in health["days"])
    assert all(d["hrv_ms"] is None for d in health["days"])
    assert health["bodyweight"] == []
    assert set(health["summary"].values()) == {None}

    items = empty.get("/api/v1/stats/correlations").json()["items"]
    assert [i["id"] for i in items] == [
        "sleep_vs_lifting",
        "hrv_vs_easy_efficiency",
        "sleep_score_vs_readiness",
    ]
    assert all(i["points"] == [] and i["r"] is None and i["n"] == 0 for i in items)

    assert empty.get("/api/v1/stats/lifts/1").status_code == 404
    assert empty.get("/api/v1/runs/missing/analysis").status_code == 404


# --------------------------------------------------------------------------- lifts


def test_lifts_summary_shape_filtering_sort_and_stall(client, world):
    body = strict_json(client.get("/api/v1/stats/lifts"))
    assert body["generated_at"].endswith("Z")
    lifts = body["lifts"]
    assert {x["name"] for x in lifts} == {
        "Bench Press (Barbell)",
        "Squat (Barbell)",
        "Row (Barbell)",
    }
    assert [x["last_date"] for x in lifts] == sorted((x["last_date"] for x in lifts), reverse=True)
    keys = {
        "variant_id", "name", "exercise_id", "muscle_group", "gym_id", "gym_name", "active",
        "sessions", "first_date", "last_date", "best_e1rm", "latest_e1rm", "trend_4w_pct",
        "last_pr_date", "stalled",
    }  # fmt: skip
    assert all(set(x) == keys for x in lifts)

    by = {x["name"]: x for x in lifts}
    bench, squat, row = by["Bench Press (Barbell)"], by["Squat (Barbell)"], by["Row (Barbell)"]
    assert bench["sessions"] == 7 and bench["first_date"] == days_ago(49).isoformat()
    assert bench["best_e1rm"] == pytest.approx(65 * (1 + 5 / 30), abs=0.01)
    assert bench["last_pr_date"] == days_ago(35).isoformat()
    assert bench["stalled"] is True and squat["stalled"] is False
    assert (
        bench["muscle_group"] == "chest" and bench["gym_id"] is None and bench["gym_name"] is None
    )
    assert squat["gym_name"] == "Gym One" and squat["gym_id"] == world.gym.id
    assert bench["trend_4w_pct"] == 0.0
    assert row["trend_4w_pct"] == pytest.approx(10.0, abs=0.01)
    assert squat["trend_4w_pct"] is None  # nothing in the last 4 weeks


def test_open_lift_stall_trigger_marks_stalled(client, db, world):
    from app.db.models import AdvisorTrigger

    assert (
        next(
            x
            for x in client.get("/api/v1/stats/lifts").json()["lifts"]
            if x["name"].startswith("Row")
        )["stalled"]
        is False
    )
    db.add(
        AdvisorTrigger(
            type="lift_stall",
            status="open",
            dedupe_key="t1",
            subject_type="variant",
            subject_id=str(world.row.id),
        )
    )
    db.commit()  # the cache notices the changed data by itself
    row = next(
        x for x in client.get("/api/v1/stats/lifts").json()["lifts"] if x["name"].startswith("Row")
    )
    assert row["stalled"] is True


def test_lift_detail_points_prs_and_range(client, world):
    body = strict_json(client.get(f"/api/v1/stats/lifts/{world.bench.id}"))
    assert body["lift"]["variant_id"] == world.bench.id
    pts = body["points"]
    assert pts[0]["date"] == days_ago(49).isoformat() and len(pts) == 7
    assert set(pts[0]) == {"date", "session_id", "best_e1rm", "top_set", "volume_kg", "sets"}
    assert pts[1]["top_set"] == {"weight_kg": 62.5, "reps": 5}
    assert pts[1]["volume_kg"] == 62.5 * 15 and pts[1]["sets"] == 3
    prs = body["prs"]
    assert [(p["date"], p["type"]) for p in prs] == [
        (days_ago(42).isoformat(), "e1rm"),
        (days_ago(42).isoformat(), "weight"),
        (days_ago(35).isoformat(), "e1rm"),
        (days_ago(35).isoformat(), "weight"),
    ]
    assert prs[0]["value"] == pytest.approx(62.5 * (1 + 5 / 30), abs=0.01)
    assert (prs[2]["weight_kg"], prs[2]["reps"]) == (65.0, 5)

    ranged = client.get(
        f"/api/v1/stats/lifts/{world.bench.id}",
        params={"from": days_ago(30).isoformat(), "to": days_ago(14).isoformat()},
    ).json()
    assert [p["date"] for p in ranged["points"]] == [days_ago(d).isoformat() for d in (28, 21, 14)]
    assert ranged["prs"] == [] and ranged["lift"]["sessions"] == 7  # summary stays all-history


def test_lift_detail_lists_rep_prs(client, world):
    prs = client.get(f"/api/v1/stats/lifts/{world.squat.id}").json()["prs"]
    kinds = {p["type"]: p for p in prs}
    assert set(kinds) == {"e1rm", "reps"}
    assert kinds["reps"]["value"] == 6 and kinds["reps"]["weight_kg"] == 100.0
    # the e1RM PR names the set that produced it (6 x 100), not just the heaviest
    assert (kinds["e1rm"]["weight_kg"], kinds["e1rm"]["reps"]) == (100.0, 6)
    assert all(p["date"] == days_ago(33).isoformat() for p in prs)
    assert client.get(f"/api/v1/stats/lifts/{world.unused.id}").status_code == 404


# --------------------------------------------------------------------------- volume + running


def test_weekly_volume_fills_gaps_and_sums(client, world):
    weeks = strict_json(client.get("/api/v1/stats/volume/weekly?weeks=8"))["weeks"]
    assert len(weeks) == 8
    starts = [w["week_start"] for w in weeks]
    assert starts == sorted(starts) and starts[-1] == "2026-10-19"
    assert all(date.fromisoformat(s).weekday() == 0 for s in starts)
    by = {w["week_start"]: w for w in weeks}
    # -7 (Sun 18 Oct, week of 12 Oct): bench 3 sets + row 2 sets; none in the week of 19 Oct
    wk = by["2026-10-12"]
    assert wk["lifting_sets"] == 5 and wk["lifting_sessions"] == 2
    assert wk["sets_by_muscle_group"] == {"back": 2, "chest": 3}
    assert wk["lifting_volume_kg"] == 65 * 15 + 55 * 10
    assert wk["runs"] == 2 and wk["running_km"] == pytest.approx(10.0 + 7.6, abs=0.01)
    empty_week = by["2026-10-19"]
    assert empty_week["lifting_sets"] == 0 and empty_week["lifting_volume_kg"] == 0
    # a week with no activity at all is present with zeros
    long = client.get("/api/v1/stats/volume/weekly?weeks=12").json()["weeks"]
    assert len(long) == 12 and long[0]["week_start"] == "2026-08-03"
    assert long[0]["lifting_sessions"] == 0 and long[0]["runs"] == 0 and long[0]["running_km"] == 0


def test_running_weekly_gap_filling_and_totals(client, world):
    body = strict_json(client.get("/api/v1/stats/running?weeks=12"))
    weekly = body["weekly"]
    assert len(weekly) == 12 and weekly[-1]["week_start"] == "2026-10-19"
    assert all(set(w["time_in_zones_s"]) == {"z1", "z2", "z3", "z4", "z5"} for w in weekly)
    this_week = weekly[-1]  # easy run 3 days ago (Thu 22 Oct) + interval run 5 days ago (Tue 20)
    assert this_week["runs"] == 2
    assert this_week["km"] == pytest.approx(6.0 + (1 + 4 * 1.4 + 1), abs=0.01)
    assert this_week["time_s"] == (6 * 330 + (330 + 4 * 390 + 330))
    assert sum(this_week["time_in_zones_s"].values()) == pytest.approx(this_week["time_s"], abs=5)
    assert any(w["runs"] == 0 and sum(w["time_in_zones_s"].values()) == 0 for w in weekly)


def test_running_efficiency_vdot_and_intervals(client, world):
    body = client.get("/api/v1/stats/running?weeks=12").json()
    eff = {e["run_id"]: e for e in body["efficiency"]}
    assert world.interval.id not in eff and world.easy.id in eff
    assert eff[world.long.id]["type"] == "long" and eff[world.easy.id]["type"] == "easy"
    e = eff[world.easy.id]
    assert e["efficiency"] == pytest.approx((6000 / (1980 / 60)) / 142, abs=1e-3)
    assert e["distance_m"] == 6000 and e["avg_hr"] == 142 and e["avg_pace_s_per_km"] == 330
    assert e["decoupling_pct"] == 0.0
    assert [x["date"] for x in body["efficiency"]] == sorted(x["date"] for x in body["efficiency"])

    assert body["vdot"] and set(body["vdot"][0]) == {"date", "value"}
    ivs = {i["run_id"]: i for i in body["intervals"]}
    assert set(ivs) == {world.interval.id, world.bad_interval.id}
    ok, bad = ivs[world.interval.id], ivs[world.bad_interval.id]
    assert set(ok) == {
        "date", "run_id", "reps", "target_pace_s_per_km", "avg_work_pace_s_per_km",
        "avg_work_hr", "target_hr_low", "target_hr_high", "hr_in_range", "recovery_ok", "passed",
    }  # fmt: skip
    assert ok["reps"] == 4 and ok["target_pace_s_per_km"] == 250  # from the linked plan
    assert ok["avg_work_pace_s_per_km"] == 250 and ok["avg_work_hr"] == 168
    assert (ok["target_hr_low"], ok["target_hr_high"]) == (166, 179)
    assert ok["hr_in_range"] is True and ok["recovery_ok"] is True and ok["passed"] is True
    assert bad["hr_in_range"] is False and bad["passed"] is False


# --------------------------------------------------------------------------- zones


def test_zone_collapse_maps_friel_seven_to_five():
    assert [collapse(z) for z in ("z1", "z2", "z3", "z4", "z5a", "z5b", "z5c")] == [
        "z1", "z2", "z3", "z4", "z5", "z5", "z5",
    ]  # fmt: skip


def test_time_in_zones_from_laps_uses_engine_zones_and_collapses_z5():
    zones = zones_from_config(load_engine_config(CFG_PATH))  # LTHR 171: z1<=149 ... z5a>=174
    hrs = [140, 152, 160, 170, 176, 183, 190]  # one lap each: z1 z2 z3 z4 z5a z5b z5c
    laps = tuple(R.LapData(60, 200, hr) for hr in hrs)
    run = R.RunData("r", TODAY, 1400, 420, 160, laps=laps)
    secs, source = time_in_zones(run, zones)
    assert source == "laps"
    assert secs == {"z1": 60, "z2": 60, "z3": 60, "z4": 60, "z5": 180}


def test_time_in_zones_rebins_garmin_zone_seconds_onto_engine_zones():
    zones = zones_from_config(load_engine_config(CFG_PATH))
    floors = (98.0, 117.0, 137.0, 156.0, 176.0)
    # all of Garmin z4 (156-175): spread evenly over 20 bpm -> 1 bpm in z2, 9 z3, 8 z4, 2 z5
    run = R.RunData(
        "r", TODAY, 5000, 2000, 160,
        time_in_zones_s={"z1": 0, "z2": 0, "z3": 0, "z4": 2000, "z5": 0},
        zone_floors=floors,
    )  # fmt: skip
    secs, source = time_in_zones(run, zones)
    assert source == "garmin_zones"
    assert secs == {"z1": 0, "z2": 100, "z3": 900, "z4": 800, "z5": 200}
    assert sum(secs.values()) == 2000
    none, src = time_in_zones(R.RunData("r", TODAY, 5000, 2000), zones)
    assert src == "none" and set(none.values()) == {0}


# --------------------------------------------------------------------------- run analysis


def test_interval_run_analysis_laps_targets_and_verdict(client, world):
    body = strict_json(client.get(f"/api/v1/runs/{world.interval.id}/analysis"))
    assert set(body) == {"generated_at", "run", "planned", "laps", "verdict"}
    run = body["run"]
    assert run["type"] == "intervals" and run["name"] == "Intervals 4x1000"
    assert run["date"] == days_ago(5).isoformat() and run["training_effect_aerobic"] == 3.2
    assert set(run) == {
        "id", "date", "type", "name", "distance_m", "duration_s", "avg_hr", "max_hr",
        "avg_pace_s_per_km", "training_effect_aerobic", "training_effect_anaerobic",
    }  # fmt: skip
    assert body["planned"]["id"] == world.planned.id and body["planned"]["kind"] == "intervals"

    kinds = [lp["kind"] for lp in body["laps"]]
    assert kinds == ["warmup", *["work", "recovery"] * 4, "cooldown"]
    work = [lp for lp in body["laps"] if lp["kind"] == "work"]
    assert all(lp["target_pace_s_per_km"] == 250 and lp["pace_ok"] is True for lp in work)
    assert all((lp["target_hr_low"], lp["target_hr_high"]) == (166, 179) for lp in work)
    assert all(lp["hr_ok"] is True for lp in work)
    warm = body["laps"][0]
    assert warm["index"] == 1 and warm["target_pace_s_per_km"] is None and warm["pace_ok"] is None
    assert set(warm) == {
        "index", "kind", "distance_m", "duration_s", "avg_pace_s_per_km", "avg_hr", "max_hr",
        "target_pace_s_per_km", "target_hr_low", "target_hr_high", "pace_ok", "hr_ok",
    }  # fmt: skip
    v = body["verdict"]
    assert v["passed"] is True and "4 reps" in v["summary"] and v["reasons"]


def _planned_interval_run(db, world, ago: int, pace: float, target: float = 250):
    plan = PlannedSession(
        day=days_ago(ago),
        kind="intervals",
        status="done",
        prescription={
            "session": {"structure": {"work": {"reps": 4, "pace_s_per_km": target}}},
        },
    )
    db.add(plan)
    db.flush()
    run = world.run(
        days_ago(ago), interval_laps(pace=pace), f"Intervals {ago}", planned_session_id=plan.id
    )
    db.commit()
    return run


def test_pace_ok_is_true_unless_slower_than_the_tolerance(client, db, world):
    def work_laps(run):
        laps = client.get(f"/api/v1/runs/{run.id}/analysis").json()["laps"]
        return [lp for lp in laps if lp["kind"] == "work"]

    # target 250 s/km, tolerance 3 % -> up to 257.5 s/km is fine; faster is always fine
    for ago, pace, ok in ((20, 255, True), (21, 262, False), (22, 240, True), (23, 200, True)):
        run = _planned_interval_run(db, world, ago, pace)
        laps = work_laps(run)
        assert {lp["pace_ok"] for lp in laps} == {ok}, (pace, laps[0])
        assert all(lp["target_pace_s_per_km"] == 250 for lp in laps)


def test_pace_tolerance_follows_the_config_version(client, db, world):
    from app.engine import config_service as cs

    run = _planned_interval_run(db, world, 20, 262)  # 4.8 % slower than the 250 target
    assert client.get(f"/api/v1/runs/{run.id}/analysis").json()["laps"][1]["pace_ok"] is False
    new = cs.create_version(
        db,
        {"running.interval_pace_tolerance_pct": 6, "running.interval_fast_flag_pct": 8},
        reason="test",
        author="user",
    )
    db.commit()
    assert new.content["running"]["interval_pace_tolerance_pct"] == 6
    assert client.get(f"/api/v1/runs/{run.id}/analysis").json()["laps"][1]["pace_ok"] is True


def test_much_faster_reps_stay_ok_but_the_verdict_says_so(client, db, world):
    fast = _planned_interval_run(db, world, 20, 235)  # 6 % faster than target 250 (flag at 5 %)
    body = client.get(f"/api/v1/runs/{fast.id}/analysis").json()
    assert all(lp["pace_ok"] is True for lp in body["laps"] if lp["kind"] == "work")
    note = "Reps ran ~6% faster than target 4:10/km; the target will catch up via VDOT"
    assert note in body["verdict"]["reasons"]
    assert body["verdict"]["passed"] is True  # pace alone never fails the engine's verdict

    near = _planned_interval_run(db, world, 21, 243)  # 2.8 % faster: no note
    reasons = client.get(f"/api/v1/runs/{near.id}/analysis").json()["verdict"]["reasons"]
    assert not any("faster than target" in r for r in reasons)


def test_failing_interval_verdict_explains_why(client, world):
    v = client.get(f"/api/v1/runs/{world.bad_interval.id}/analysis").json()["verdict"]
    assert v["passed"] is False
    assert any("above the target zone" in r for r in v["reasons"])


def test_easy_run_analysis_and_verdicts(client, world):
    body = client.get(f"/api/v1/runs/{world.easy.id}/analysis").json()
    assert body["run"]["type"] == "easy" and body["planned"] is None
    assert body["verdict"]["passed"] is True and body["verdict"]["reasons"]
    assert all(lp["kind"] == "other" for lp in body["laps"])
    assert all((lp["target_hr_low"], lp["target_hr_high"]) == (127, 156) for lp in body["laps"])
    assert all(lp["hr_ok"] is True and lp["pace_ok"] is None for lp in body["laps"])

    hot = client.get(f"/api/v1/runs/{world.hot.id}/analysis").json()
    assert hot["verdict"]["passed"] is False
    assert any("easy ceiling" in r for r in hot["verdict"]["reasons"])
    assert all(lp["hr_ok"] is False for lp in hot["laps"])
    assert client.get(f"/api/v1/runs/{world.long.id}/analysis").json()["run"]["type"] == "long"


def test_non_running_activity_has_no_verdict(client, db, world):
    hike = world.run(days_ago(8), [(5000, 3000, 110, None)], "Hike")
    hike.activity_type = "hiking"
    db.commit()
    body = client.get(f"/api/v1/runs/{hike.id}/analysis").json()
    assert body["verdict"]["passed"] is None and body["run"]["type"] == "other"


# --------------------------------------------------------------------------- health


def test_health_days_readiness_and_summary(client, world):
    body = strict_json(client.get("/api/v1/stats/health?days=30"))
    days = body["days"]
    assert len(days) == 30 and days[-1]["date"] == TODAY.isoformat()
    assert [d["date"] for d in days] == sorted(d["date"] for d in days)
    by = {d["date"]: d for d in days}
    keys = {
        "date", "hrv_ms", "hrv_baseline_ms", "rhr", "rhr_baseline", "sleep_s", "sleep_score",
        "body_battery_high", "body_battery_low", "stress_avg", "steps", "readiness",
    }  # fmt: skip
    assert all(set(d) == keys for d in days)
    red = by[days_ago(3).isoformat()]
    assert red["readiness"]["level"] == "red" and red["hrv_ms"] == 52 and red["rhr"] == 62
    assert red["hrv_baseline_ms"] is not None and red["rhr_baseline"] == 53
    assert by[days_ago(1).isoformat()]["readiness"]["level"] in {"green", "yellow"}
    missing = by[days_ago(5).isoformat()]  # no Garmin row that day
    assert missing["readiness"] == {"level": "unknown", "reason": "no data"}
    assert missing["hrv_ms"] is None and missing["sleep_s"] is None
    today = by[TODAY.isoformat()]
    assert today["body_battery_high"] == 95 and today["body_battery_low"] == 20
    assert today["steps"] == 9000 and today["stress_avg"] == 30

    s = body["summary"]
    assert set(s) == {
        "hrv_7d", "hrv_28d", "rhr_7d", "rhr_28d", "sleep_7d_s", "sleep_28d_s",
        "bodyweight_latest", "bodyweight_change_4w_pct",
    }  # fmt: skip
    hrv7 = [52 if i == 3 else 68 + i % 5 for i in range(7) if i != 5]
    assert s["hrv_7d"] == pytest.approx(sum(hrv7) / len(hrv7), abs=0.1)
    assert 53 < s["rhr_28d"] < 54 and s["rhr_7d"] > s["rhr_28d"]
    assert s["sleep_7d_s"] > 7 * 3600


def test_bodyweight_series_and_four_week_change(client, world):
    body = client.get("/api/v1/stats/health?days=90").json()
    assert body["bodyweight"] == [
        {"date": days_ago(30).isoformat(), "weight_kg": 82.0, "source": "manual"},
        {"date": TODAY.isoformat(), "weight_kg": 76.5, "source": "garmin"},
    ]
    assert body["summary"]["bodyweight_latest"] == 76.5
    assert body["summary"]["bodyweight_change_4w_pct"] == 2.0  # vs the weigh-in 30 days ago
    short = client.get("/api/v1/stats/health?days=10").json()
    assert [b["date"] for b in short["bodyweight"]] == [TODAY.isoformat()]
    assert short["summary"]["bodyweight_latest"] == 76.5  # summary ignores the window


def test_bodyweight_change_pct_rules():
    d = date(2026, 10, 25)
    assert bodyweight_change_pct([]) is None
    assert bodyweight_change_pct([(d, 82.0)]) is None
    assert bodyweight_change_pct([(d - timedelta(days=28), 80.0), (d, 76.0)]) == pytest.approx(-5.0)
    # closest to "4 weeks before the latest" wins
    pts = [(d - timedelta(days=40), 70.0), (d - timedelta(days=27), 75.0), (d, 78.0)]
    assert bodyweight_change_pct(pts) == pytest.approx(4.0)
    # the only older weigh-in is too far from the 4-week mark -> unknown rather than misleading
    assert bodyweight_change_pct([(d - timedelta(days=90), 70.0), (d, 75.0)]) is None
    assert (
        bodyweight_change_pct([(d - timedelta(days=60), 70.0), (d - timedelta(days=5), 75.0)])
        is None
    )


# --------------------------------------------------------------------------- correlations


def test_pearson_needs_five_points_and_variance():
    assert common.pearson([1, 2, 3, 4], [2, 4, 6, 8]) is None  # n < 5
    assert common.pearson([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) == pytest.approx(1.0)
    assert common.pearson([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]) == pytest.approx(-1.0)
    assert common.pearson([1, 1, 1, 1, 1], [1, 2, 3, 4, 5]) is None  # constant series
    assert common.pearson([1, 2, 3, 4, 5], [3, 1, 4, 1, 5]) == pytest.approx(0.2, abs=0.35)


def test_correlation_items_shape_r_and_notes(client, world):
    body = strict_json(client.get("/api/v1/stats/correlations?days=120"))
    items = {i["id"]: i for i in body["items"]}
    assert set(items) == {"sleep_vs_lifting", "hrv_vs_easy_efficiency", "sleep_score_vs_readiness"}
    for item in items.values():
        assert set(item) == {"id", "title", "x_label", "y_label", "points", "r", "n", "note"}
        assert item["n"] == len(item["points"])
        assert all(set(p) == {"date", "x", "y"} for p in item["points"])
        assert item["note"].startswith(f"n={item['n']}")
        assert item["r"] is None or -1 <= item["r"] <= 1

    lifting = items[
        "sleep_vs_lifting"
    ]  # sessions after the first of each lift, 8-week rolling best
    assert lifting["n"] >= 5 and lifting["r"] is not None
    by_day = {p["date"]: p for p in lifting["points"]}
    p = by_day[
        days_ago(42).isoformat()
    ]  # bench 62.5 vs the earlier 60: ~104.2 % of the 8-week best
    assert p["y"] == pytest.approx(62.5 / 60 * 100, abs=0.1)
    assert p["x"] == (7 + 42 % 3)

    hrv = items[
        "hrv_vs_easy_efficiency"
    ]  # easy runs only (the 5 easy + hot ones, not long / intervals)
    assert hrv["n"] == 5 and hrv["r"] is not None
    ready = items["sleep_score_vs_readiness"]
    assert {p["y"] for p in ready["points"]} <= {0, 1, 2} and 0 in {p["y"] for p in ready["points"]}


def test_correlation_with_fewer_than_five_points_has_no_r(client, world):
    item = {i["id"]: i for i in client.get("/api/v1/stats/correlations?days=20").json()["items"]}[
        "hrv_vs_easy_efficiency"
    ]
    assert item["n"] < 5 and item["r"] is None
    assert item["note"].startswith(f"n={item['n']}, too few points")


# --------------------------------------------------------------------------- cache


def test_cache_serves_repeats_and_follows_new_data(client, db, world):
    first = client.get("/api/v1/stats/lifts").json()
    assert client.get("/api/v1/stats/lifts").json()["generated_at"] == first["generated_at"]
    world.lift_session(days_ago(1), world.bench, 70, [5, 5, 5])
    rebuild_stats(db, bodyweight_kg=82)
    db.commit()
    second = client.get("/api/v1/stats/lifts").json()
    bench = next(x for x in second["lifts"] if x["name"].startswith("Bench"))
    assert bench["sessions"] == 8 and bench["last_pr_date"] == days_ago(1).isoformat()
    cache.invalidate()
    assert client.get("/api/v1/stats/lifts").json()["generated_at"] >= second["generated_at"]
