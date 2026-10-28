"""Stats API (docs/api/stats.md): shapes, empty data, auth, gap filling, zone collapse, Pearson,
PR listing, run-analysis verdicts and bodyweight change, on synthetic data in a migrated SQLite."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.analytics import cache, common, correlations
from app.analytics.health import bodyweight_change_pct, readiness_view
from app.analytics.zones import collapse, time_in_zones
from app.api.auth import get_api_token
from app.api.routes import get_db
from app.db.catalog import BODY_REGIONS, load_catalog, region_of_muscle
from app.db.models import (
    BodyMeasurement,
    DailyHealth,
    DecisionLog,
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
from app.engine import service
from app.engine.config import load_engine_config
from app.engine.hr import zones_from_config
from app.engine.program import Program, ProgramExercise
from app.engine.readiness import Readiness, Signal
from app.main import app
from app.settings import BACKEND_DIR
from app.stats import rebuild_stats

TODAY = date(2026, 10, 25)  # a Sunday: the current week starts Monday 2026-10-19
TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CFG_PATH = BACKEND_DIR / "config" / "engine.v3.yaml"

NULL_KPIS = (
    "vdot_current", "vdot_4w_ago", "vdot_change", "vdot_source", "vdot_fit_current",
    "efficiency_current", "efficiency_4w_ago", "efficiency_change_pct",
)  # fmt: skip
CORRELATION_IDS = [
    "sleep_vs_lifting",
    "hrv_vs_lift_performance",
    "hrv_vs_easy_efficiency",
    "rhr_vs_easy_efficiency",
    "run_load_vs_lift_performance",
]

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
    assert all(w["prs"] == 0 and w["pr_lifts"] == 0 for w in weeks)
    assert all(w["planned_lifting_sessions"] == 3 for w in weeks)  # template count

    running = empty.get("/api/v1/stats/running?weeks=2").json()
    assert len(running["weekly"]) == 2 and running["efficiency"] == []
    assert running["vdot"] == [] and running["intervals"] == []
    assert all(
        w["runs_detail"] == [] and sum(w["runs_by_type"].values()) == 0 for w in running["weekly"]
    )
    assert running["kpis"] == {
        **dict.fromkeys(NULL_KPIS),
        "easy_hr_drift_flags_4w": 0,
        "runs_this_week": 0,
        "runs_planned_this_week": 3,  # template
        "km_this_week": 0.0,
    }

    health = empty.get("/api/v1/stats/health?days=7").json()
    assert len(health["days"]) == 7
    assert all(d["readiness"]["level"] == "unknown" for d in health["days"])
    assert all(d["hrv_ms"] is None for d in health["days"])
    assert health["bodyweight"] == []
    assert set(health["summary"].values()) == {None}
    assert all(d["steps_7d_avg"] is None for d in health["days"])
    assert health["readiness_today"] == {
        "level": "unknown",
        "headline": "No data",
        "metrics": [],
        "details": ["No data"],
    }

    items = empty.get("/api/v1/stats/correlations").json()["items"]
    assert [i["id"] for i in items] == CORRELATION_IDS
    assert all(i["points"] == [] and i["r"] is None and i["n"] == 0 for i in items)
    assert all(i["insight"] == "Not enough data yet (n=0)" for i in items)

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
        "last_pr_date", "stalled", "body_region", "in_program",
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

    assert body["vdot"] == []  # the engine has not logged a VDOT yet (see the vdot tests)
    assert body["vdot_fit"] and set(body["vdot_fit"][0]) == {"date", "value"}
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
    # HR above the zone no longer fails a session: pace decides (HR is a safety ceiling)
    assert bad["hr_in_range"] is False and bad["passed"] is True


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
    # HR: the green line and the safety ceiling of a VO2max session (102 / 108 % of LTHR 171)
    assert all((lp["target_hr_low"], lp["target_hr_high"]) == (179, 189) for lp in work)
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


def test_failing_interval_verdict_explains_why(client, db, world):
    slow = _planned_interval_run(db, world, 20, 262)  # every rep 4.8 % slower than 250
    v = client.get(f"/api/v1/runs/{slow.id}/analysis").json()["verdict"]
    assert v["passed"] is False and "pace not held" in v["summary"]
    assert any("0 of 4 reps on pace" in r for r in v["reasons"])


def test_high_hr_alone_does_not_fail_an_interval_session(client, world):
    v = client.get(f"/api/v1/runs/{world.bad_interval.id}/analysis").json()["verdict"]
    assert v["passed"] is True and "passed on pace" in v["summary"]
    assert not any("above the target zone" in r for r in v["reasons"])


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
        "body_battery_high", "body_battery_low", "stress_avg", "steps", "steps_7d_avg", "readiness",
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
        "bodyweight_latest", "bodyweight_change_4w_pct", "steps_7d_avg", "days_10k_last_7",
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
    assert [i["id"] for i in body["items"]] == CORRELATION_IDS  # no sleep-score-vs-readiness
    items = {i["id"]: i for i in body["items"]}
    for item in items.values():
        assert set(item) == {
            "id", "category", "title", "x_label", "y_label", "points", "r", "n", "note", "insight",
        }  # fmt: skip
        assert item["n"] == len(item["points"])
        assert all(set(p) == {"date", "x", "y"} for p in item["points"])
        assert item["note"].startswith(f"n={item['n']}")
        assert item["r"] is None or -1 <= item["r"] <= 1
        assert item["insight"].endswith(")") and f"n={item['n']}" in item["insight"]
    assert {i["id"]: i["category"] for i in items.values()} == {
        "sleep_vs_lifting": "recovery",
        "hrv_vs_lift_performance": "recovery",
        "hrv_vs_easy_efficiency": "recovery",
        "rhr_vs_easy_efficiency": "recovery",
        "run_load_vs_lift_performance": "hybrid",
    }

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

    # same performance metric, x = that morning's overnight HRV
    hrv_lift = {p["date"]: p for p in items["hrv_vs_lift_performance"]["points"]}
    assert set(hrv_lift) == set(by_day)
    assert {d: p["y"] for d, p in hrv_lift.items()} == {d: p["y"] for d, p in by_day.items()}
    assert hrv_lift[days_ago(42).isoformat()]["x"] == 68 + 42 % 5

    hrv = items[
        "hrv_vs_easy_efficiency"
    ]  # easy runs only (the 5 easy + hot ones, not long / intervals)
    assert hrv["n"] == 5 and hrv["r"] is not None
    rhr = items["rhr_vs_easy_efficiency"]
    assert rhr["n"] == 5 and {p["x"] for p in rhr["points"]} == {53, 62}  # 62 on the red day
    assert rhr["r"] is not None and rhr["insight"].endswith("n=5, early read)")


def test_run_load_vs_lift_performance_counts_km_in_the_48h_before_the_session(client, db, world):
    # bench PR-day sessions are at 18:00. Runs are at 07:00: a run 1 day before (35 h) counts, a
    # run 3 days before (and one on the same morning, 11 h before) per the 48 h window.
    day = days_ago(28)  # bench session, flat 65 kg
    world.run(day - timedelta(days=1), easy_laps(5), "Day before")  # 35 h before: 5 km
    world.run(day, easy_laps(3), "Same morning")  # 11 h before: 3 km
    world.run(day - timedelta(days=2), easy_laps(4), "Too early")  # 59 h before: not counted
    world.run(day, easy_laps(2), "Deleted", deleted_at=utc(datetime(2026, 10, 1)))  # ignored
    db.commit()
    cache.invalidate()
    item = {i["id"]: i for i in client.get("/api/v1/stats/correlations?days=120").json()["items"]}[
        "run_load_vs_lift_performance"
    ]
    by_day = {p["date"]: p for p in item["points"]}
    assert by_day[day.isoformat()]["x"] == pytest.approx(8.0)
    assert by_day[days_ago(21).isoformat()]["x"] == 0.0  # no running before this session
    assert item["x_label"].startswith("Running") and item["category"] == "hybrid"
    assert item["n"] >= 5 and item["r"] is not None


def test_insight_wording_from_r_and_n():
    w = correlations.Wording(
        "sleep and lifting", "more sleep", "better lifting days", "weaker days"
    )
    assert correlations.insight(w, None, 4) == "Not enough data yet (n=4)"
    assert correlations.insight(w, 0.9, 3) == "Not enough data yet (n=3)"  # n below the minimum
    assert (
        correlations.insight(w, 0.42, 31)
        == "More sleep tends to go with better lifting days (r=0.42, n=31)"
    )
    assert (
        correlations.insight(w, -0.5, 31)
        == "More sleep tends to go with weaker days (r=-0.50, n=31)"
    )
    assert (
        correlations.insight(w, 0.25, 31)
        == "Weak hint that more sleep goes with better lifting days (r=0.25, n=31)"
    )
    assert (
        correlations.insight(w, -0.05, 31)
        == "No clear link between sleep and lifting (r=-0.05, n=31)"
    )
    assert correlations.insight(w, 0.6, 9).endswith("(r=0.60, n=9, early read)")


def test_correlation_with_fewer_than_five_points_has_no_r(client, world):
    item = {i["id"]: i for i in client.get("/api/v1/stats/correlations?days=20").json()["items"]}[
        "hrv_vs_easy_efficiency"
    ]
    assert item["n"] < 5 and item["r"] is None
    assert item["note"].startswith(f"n={item['n']}, too few points")
    assert item["insight"] == f"Not enough data yet (n={item['n']})"


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


# --------------------------------------------------------------------------- dashboard rework

PROGRAM = Program(
    days={
        "upper": [
            ProgramExercise("bench_press", "Bench Press (Barbell)"),
            ProgramExercise("row", "Row (Barbell)", active=False),  # inactive -> not in the program
            ProgramExercise("nothing", "No Such Variant"),
        ],
        "lower": [ProgramExercise("squat", "Squat (Barbell)")],  # resolves to the "@ Gym One" one
    }
)


def test_lifts_body_region_and_in_program(client, world, monkeypatch):
    monkeypatch.setattr(service, "get_program", lambda settings=None: PROGRAM)
    lifts = {x["name"]: x for x in client.get("/api/v1/stats/lifts").json()["lifts"]}
    assert lifts["Bench Press (Barbell)"]["body_region"] == "Chest"
    assert lifts["Squat (Barbell)"]["body_region"] == "Legs"
    assert lifts["Row (Barbell)"]["body_region"] == "Back"  # not in the catalog: from the muscle
    assert {n: x["in_program"] for n, x in lifts.items()} == {
        "Bench Press (Barbell)": True,
        "Squat (Barbell)": True,
        "Row (Barbell)": False,
    }
    detail = client.get(f"/api/v1/stats/lifts/{world.bench.id}").json()["lift"]
    assert detail["body_region"] == "Chest" and detail["in_program"] is True


def test_in_program_is_false_without_a_program_file(client, world, monkeypatch):
    def missing(settings=None):
        raise FileNotFoundError("no program")

    monkeypatch.setattr(service, "get_program", missing)
    lifts = client.get("/api/v1/stats/lifts").json()["lifts"]
    assert lifts and all(x["in_program"] is False for x in lifts)


def test_in_program_needs_the_variant_of_the_current_gym(client, db, world, monkeypatch):
    """A gym-bound variant of another gym is not 'in the program' for the current gym."""
    other = Gym(key="g2", name="Gym Two")
    db.add(other)
    db.flush()
    other_squat = ExerciseVariant(
        exercise_id=world.squat.exercise_id,
        gym_id=other.id,
        key="sq2",
        name="Squat (Barbell) @ Gym Two",
        equipment_type="barbell",
        load_convention="total",
    )
    db.add(other_squat)
    db.flush()
    world.lift_session(days_ago(2), other_squat, 100, [5, 5])  # in the (current) Gym One
    rebuild_stats(db, bodyweight_kg=82)
    db.commit()
    monkeypatch.setattr(service, "get_program", lambda settings=None: PROGRAM)
    lifts = {x["variant_id"]: x for x in client.get("/api/v1/stats/lifts").json()["lifts"]}
    assert lifts[world.squat.id]["in_program"] is True
    assert lifts[other_squat.id]["gym_name"] == "Gym Two"
    assert lifts[other_squat.id]["in_program"] is False


def test_catalog_has_a_valid_coarse_body_region_for_every_exercise():
    catalog = load_catalog(BACKEND_DIR / "data" / "catalog" / "exercises.yaml")
    assert catalog and all(c.body_region in BODY_REGIONS for c in catalog.values())
    region = {slug: c.body_region for slug, c in catalog.items()}
    assert region["bench_press"] == "Chest" and region["reverse_fly"] == "Shoulders"  # rear delts
    assert region["shrug"] == "Back" and region["pull_up"] == "Back"  # traps, lats
    assert region["biceps_curl"] == region["triceps_extension"] == region["wrist_curl"] == "Arms"
    assert {region[s] for s in ("squat", "leg_curl", "hip_adduction", "calf_raise")} == {"Legs"}
    assert region["plank"] == "Core" and region["cardio"] == "Other"
    for c in catalog.values():  # the explicit value agrees with the muscle mapping
        assert c.body_region == region_of_muscle(c.primary_muscle), c.slug
    raw = yaml.safe_load((BACKEND_DIR / "data" / "catalog" / "exercises.yaml").read_text())
    assert all("body_region" in e for e in raw["exercises"])


def test_unknown_body_region_in_the_catalog_is_rejected(tmp_path):
    p = tmp_path / "ex.yaml"
    p.write_text(
        "exercises:\n  - {slug: a, name: A, primary_muscle: chest, body_region: Torso,"
        " movement_pattern: x, default_equipment: barbell}\n"
    )
    with pytest.raises(ValueError, match="body_region"):
        load_catalog(p)


def test_weekly_prs_pr_lifts_and_planned_sessions(client, db, world):
    # week of 19 Oct: two sessions with PRs on bench (same lift twice) and one on the row
    world.lift_session(days_ago(3), world.bench, 70, [5, 5, 5])  # Thu
    world.lift_session(days_ago(2), world.bench, 72.5, [5, 5, 5])  # Fri
    world.lift_session(days_ago(1), world.row, 60, [5, 5])  # Sat
    world.lift_session(days_ago(1), world.squat, 100, [5, 5])  # no PR: same as -40 / -33
    # a PR on a retired lift does not count
    retired = ExerciseVariant(
        exercise_id=world.row.exercise_id,
        key="old",
        name="Row (Old)",
        equipment_type="barbell",
        load_convention="total",
        active=False,
    )
    db.add(retired)
    db.flush()
    world.lift_session(days_ago(1), retired, 40, [5, 5])
    # planned lifting: a complete stored week (12 Oct) has 2 upper/lower sessions (+1 soft-deleted)
    gone = utc(datetime(2026, 10, 1))
    full_week = ("upper", "lower", "easy_run", "upper", "intervals", "rest", "long_run")
    for i, kind in enumerate(full_week):  # Mon 12 Oct .. Sun 18 Oct, the 2nd upper soft-deleted
        db.add(
            PlannedSession(
                day=date(2026, 10, 12) + timedelta(days=i),
                kind=kind,
                status="proposed",
                deleted_at=gone if i == 3 else None,
            )
        )
    rebuild_stats(db, bodyweight_kg=82)
    db.commit()
    cache.invalidate()

    weeks = strict_json(client.get("/api/v1/stats/volume/weekly?weeks=8"))["weeks"]
    assert all(
        {"prs", "pr_lifts", "planned_lifting_sessions"} <= set(w) for w in weeks
    )  # additive: the old fields stay
    by = {w["week_start"]: w for w in weeks}
    cur = by["2026-10-19"]
    assert cur["lifting_sessions"] == 5
    assert cur["prs"] == 3  # 2 bench sessions + the row session
    assert cur["pr_lifts"] == 2  # bench, row
    # a partial plan (only the intervals run is stored) is not trusted: the template count
    assert cur["planned_lifting_sessions"] == 3
    assert (
        by["2026-10-12"]["planned_lifting_sessions"] == 2
    )  # complete week: stored, deleted ignored
    # the older bench PRs (-42, -35 and the squat's -33) land in their own weeks
    assert by["2026-09-14"]["prs"] >= 1
    assert by["2026-10-05"]["planned_lifting_sessions"] == 3  # no plans: the template (3)
    assert by["2026-10-05"]["prs"] == 0 and by["2026-10-05"]["pr_lifts"] == 0


def test_weekly_runs_detail_and_runs_by_type(client, world):
    weekly = strict_json(client.get("/api/v1/stats/running?weeks=4"))["weekly"]
    cur = weekly[-1]
    assert cur["runs_detail"] == [
        {
            "run_id": world.interval.id,
            "date": days_ago(5).isoformat(),
            "type": "intervals",
            "km": 7.6,
        },
        {"run_id": world.easy.id, "date": days_ago(3).isoformat(), "type": "easy", "km": 6.0},
    ]
    assert cur["runs_by_type"] == {"easy": 1, "intervals": 1, "long": 0, "other": 0}
    wk = {w["week_start"]: w for w in weekly}["2026-10-12"]  # long run -10, bad intervals -12
    assert wk["runs_by_type"] == {"easy": 0, "intervals": 1, "long": 1, "other": 0}
    assert {d["type"] for d in wk["runs_detail"]} == {"intervals", "long"}
    for w in weekly:
        assert sum(w["runs_by_type"].values()) == w["runs"] == len(w["runs_detail"])
        assert set(w["runs_by_type"]) == {"easy", "intervals", "long", "other"}


def _eff(km: float, pace: float, hr: float) -> float:
    return (km * 1000 / (km * pace / 60)) / hr


def test_running_kpis_this_week_and_efficiency(client, db, world):
    # the whole current week planned (Mon 19 .. Sun 25): the world's own intervals plan sits on
    # Tue 20, add 3 lifting, 2 more runs and a rest day
    others = {19: "upper", 21: "easy_run", 22: "upper", 23: "lower", 24: "rest", 25: "long_run"}
    for d, kind in others.items():
        db.add(PlannedSession(day=date(2026, 10, d), kind=kind, status="proposed"))
    db.commit()
    cache.invalidate()
    k = strict_json(client.get("/api/v1/stats/running?weeks=4"))["kpis"]
    assert set(k) == {
        *NULL_KPIS, "easy_hr_drift_flags_4w", "runs_this_week", "runs_planned_this_week",
        "km_this_week",
    }  # fmt: skip
    assert k["runs_this_week"] == 2 and k["km_this_week"] == pytest.approx(13.6)
    assert (
        k["runs_planned_this_week"] == 3
    )  # intervals (world) + easy + long; the upper is not a run
    # last 3 easy / long runs: easy -3 (142), long -10 (146), hot easy -17 (165)
    cur = (_eff(6, 330, 142) + _eff(10, 330, 146) + _eff(6, 330, 165)) / 3
    # 3 closest to 28 days ago (not the same runs): -31, -24, -38 (HR 143 + d % 4)
    ago = (_eff(5, 330, 146) + _eff(5, 330, 143) + _eff(5, 330, 145)) / 3
    assert k["efficiency_current"] == pytest.approx(cur, abs=1e-4)
    assert k["efficiency_4w_ago"] == pytest.approx(ago, abs=1e-4)
    assert k["efficiency_change_pct"] == pytest.approx((cur - ago) / ago * 100, abs=0.01)
    assert k["easy_hr_drift_flags_4w"] == 0
    # no VDOT record in the decision log yet: the headline VDOT is unknown (the per-run fit is
    # a separate, secondary number)
    assert k["vdot_current"] is None and k["vdot_4w_ago"] is None and k["vdot_change"] is None
    assert k["vdot_source"] is None and k["vdot_fit_current"] is not None


def test_running_kpis_do_not_depend_on_the_chart_window(client, world):
    short = client.get("/api/v1/stats/running?weeks=1").json()["kpis"]
    long = client.get("/api/v1/stats/running?weeks=26").json()["kpis"]
    assert short == long and short["efficiency_4w_ago"] is not None


def test_vdot_change_and_hr_drift_flags(client, db, world):
    # hard 5 km efforts (HR above 0.88 x LTHR): 35 days ago slower, 2 days ago faster
    world.run(days_ago(35), [(1000, 240, 168, None)] * 5, "Hard 5k old")
    world.run(days_ago(2), [(1000, 225, 168, None)] * 5, "Hard 5k new")
    drift = [(1000, 330, 140, None)] * 3 + [(1000, 330, 156, None)] * 3  # ~10 % decoupling
    world.run(days_ago(1), drift, "Drifting easy run")
    world.run(days_ago(40), drift, "Old drift, outside 4 weeks")
    world.run(
        days_ago(4),
        [(1000, 330, 140, None)] * 4 + [(1000, 330, 156, None)] * 4 + [(1000, 330, 156, None)] * 2,
        "Drifting long run",
    )  # 'long' is not an easy run: not flagged
    db.commit()
    cache.invalidate()
    k = client.get("/api/v1/stats/running?weeks=4").json()["kpis"]
    assert k["easy_hr_drift_flags_4w"] == 1
    body = client.get("/api/v1/stats/running?weeks=4").json()
    # the per-run fit rose after the faster 5 km effort, but it is only the secondary series
    fit = {p["date"]: p["value"] for p in body["vdot_fit"]}
    assert k["vdot_fit_current"] == fit[days_ago(2).isoformat()]
    assert k["vdot_current"] is None  # no engine VDOT record


def _vdot_record(db, day: date, vdot: float, source: str, delta: float, reason: str) -> None:
    """A `running:vdot` decision as the planner writes it (engine/planner.py)."""
    rec = {"day": day.isoformat(), "vdot": vdot, "source": source, "delta": delta}
    db.add(
        DecisionLog(
            kind="running",
            subject_type="vdot",
            subject_id=day.isoformat(),
            output={"vdot": vdot, "vdot_record": rec},
            rationale=reason,
        )
    )


def test_vdot_series_is_the_engine_state_from_the_decision_log(client, db, world):
    _vdot_record(db, days_ago(60), 43.44, "initial", 0.0, "initial VDOT 41.0 from the fit")
    _vdot_record(
        db,
        days_ago(10),
        44.51,
        "interval_reps",
        3.0,
        "VDOT 41.0 -> 45.1 measured from interval reps",
    )
    _vdot_record(db, days_ago(3), 45.9, "green_sessions", 0.5, "VDOT 45.4 -> 45.9: 2 passed")
    # junk rows are ignored: no record, other subject
    db.add(DecisionLog(kind="running", subject_type="vdot", output={"vdot": 1.0}))
    db.add(
        DecisionLog(
            kind="running", subject_type="week", output={"vdot_record": {"day": "2026-10-01"}}
        )
    )
    db.commit()
    cache.invalidate()
    body = strict_json(client.get("/api/v1/stats/running?weeks=2"))
    assert [(p["date"], p["value"], p["source"], p["delta"]) for p in body["vdot"]] == [
        (days_ago(60).isoformat(), 43.44, "initial", 0.0),
        (days_ago(10).isoformat(), 44.51, "interval_reps", 3.0),
        (days_ago(3).isoformat(), 45.9, "green_sessions", 0.5),
    ]  # the whole history, not cut to the 2-week chart window
    assert body["vdot"][1]["reason"].startswith("VDOT 41.0 -> 45.1 measured from interval reps")
    k = body["kpis"]
    assert k["vdot_current"] == 45.9 and k["vdot_source"] == "green_sessions"
    assert k["vdot_4w_ago"] == 43.44 and k["vdot_change"] == pytest.approx(3.5)
    assert body["vdot_fit"] and k["vdot_fit_current"] is not None  # secondary, may differ


def test_vdot_current_equals_the_engine_state(client, db, world):
    from app.engine import data as D

    _vdot_record(db, days_ago(20), 43.44, "initial", 0.0, "initial")
    _vdot_record(db, days_ago(6), 44.51, "interval_reps", 3.0, "measured")
    _vdot_record(
        db, days_ago(6), 44.51, "interval_reps", 3.0, "measured again"
    )  # idempotent re-run
    db.commit()
    cache.invalidate()
    body = client.get("/api/v1/stats/running").json()
    log = D.load_vdot_log(db)  # what the planner takes as its VDOT state
    assert len(body["vdot"]) == len(log) == 2
    assert body["kpis"]["vdot_current"] == body["vdot"][-1]["value"] == log[-1].vdot == 44.51
    assert body["vdot"][-1]["reason"] == "measured again"  # the latest decision of the day wins


def test_vdot_record_before_the_4w_cutoff_only(client, db, world):
    _vdot_record(db, days_ago(5), 44.0, "initial", 0.0, "initial")
    db.commit()
    cache.invalidate()
    k = client.get("/api/v1/stats/running").json()["kpis"]
    assert k["vdot_current"] == 44.0 and k["vdot_4w_ago"] is None and k["vdot_change"] is None


def test_running_kpis_are_null_without_enough_runs(db, monkeypatch):
    from app.engine import config_service as cs

    cs.ensure_seeded(db, CFG_PATH)
    w = World(db)
    for d in (2, 5, 8):  # only three easy runs: no "4 weeks ago" set to compare with
        w.run(days_ago(d), easy_laps(5), "Easy run")
    db.commit()
    app.dependency_overrides.clear()
    client = _client(db, monkeypatch)
    try:
        k = client.get("/api/v1/stats/running").json()["kpis"]
    finally:
        app.dependency_overrides.clear()
    assert k["efficiency_current"] == pytest.approx(_eff(5, 330, 140), abs=1e-4)
    assert k["efficiency_4w_ago"] is None and k["efficiency_change_pct"] is None
    assert k["vdot_current"] is None  # no hard effort: only the seed race
    assert k["runs_this_week"] == 2 and k["km_this_week"] == 10.0


def _set_steps(db, values: dict[int, int | None]):
    for i, v in values.items():
        row = db.scalar(select(DailyHealth).where(DailyHealth.day == days_ago(i)))
        if row is not None:  # day -5 has no row
            row.steps = v
    db.commit()
    cache.invalidate()


def test_steps_7d_avg_and_days_over_10k(client, db, world):
    # today 12000, -1 10000 (exactly the goal), -2 9999, -3 3000, -4 15000, -5 no row, -6 11000
    _set_steps(db, {0: 12000, 1: 10000, 2: 9999, 3: 3000, 4: 15000, 6: 11000, 7: None})
    body = strict_json(client.get("/api/v1/stats/health?days=30"))
    s = body["summary"]
    assert s["steps_7d_avg"] == pytest.approx(
        (12000 + 10000 + 9999 + 3000 + 15000 + 11000) / 6, abs=1
    )
    assert s["days_10k_last_7"] == 4
    by = {d["date"]: d for d in body["days"]}
    assert by[TODAY.isoformat()]["steps_7d_avg"] == s["steps_7d_avg"]
    assert by[days_ago(15).isoformat()]["steps_7d_avg"] == 9000  # untouched days: constant 9000
    # a day whose whole 7-day window has no steps is null; the window never looks past the day
    _set_steps(db, {i: None for i in range(20, 40)})
    by = {d["date"]: d for d in client.get("/api/v1/stats/health?days=30").json()["days"]}
    assert by[days_ago(25).isoformat()]["steps_7d_avg"] is None
    assert by[days_ago(19).isoformat()]["steps_7d_avg"] == 9000  # windows 19..13 have data
    assert by[days_ago(14).isoformat()]["steps_7d_avg"] == 9000


def test_steps_summary_is_null_without_step_data(client, db, world):
    _set_steps(db, {i: None for i in range(0, 8)})
    s = client.get("/api/v1/stats/health?days=14").json()["summary"]
    assert s["steps_7d_avg"] is None and s["days_10k_last_7"] is None


def test_readiness_today_metrics_baselines_and_details(client, world):
    ready = strict_json(client.get("/api/v1/stats/health?days=30"))["readiness_today"]
    assert set(ready) == {"level", "headline", "metrics", "details"}
    assert ready["level"] in {"green", "yellow"} and ready["headline"] in {
        "Ready",
        "Take it easier",
    }
    m = {x["key"]: x for x in ready["metrics"]}
    assert [x["key"] for x in ready["metrics"]] == ["hrv", "rhr", "sleep"]
    assert all(
        set(x) == {"key", "label", "value", "unit", "baseline", "delta_pct", "status"}
        for x in ready["metrics"]
    )
    assert (m["hrv"]["label"], m["hrv"]["unit"], m["hrv"]["value"]) == ("HRV", "ms", 68)
    hrv_base = (
        sum(68 + i % 5 if i != 3 else 52 for i in (1, 2, 3, 4, 6, 7)) / 6
    )  # day -5 is missing
    assert m["hrv"]["baseline"] == pytest.approx(hrv_base, abs=0.1)
    assert m["hrv"]["delta_pct"] == pytest.approx((68 - hrv_base) / hrv_base * 100, abs=0.1)
    assert (m["rhr"]["unit"], m["rhr"]["value"]) == ("bpm", 53)
    assert m["rhr"]["baseline"] == pytest.approx((53 * 5 + 62) / 6, abs=0.1)
    sleep_base = sum(7 + i % 3 for i in (1, 2, 3, 4, 6, 7)) / 6
    assert (m["sleep"]["unit"], m["sleep"]["value"]) == ("h", 7.0)
    assert m["sleep"]["baseline"] == pytest.approx(sleep_base, abs=0.1)
    assert m["sleep"]["delta_pct"] == pytest.approx((7 - sleep_base) / sleep_base * 100, abs=0.1)
    assert all(x["status"] in {"ok", "warn", "bad"} for x in ready["metrics"])
    assert m["hrv"]["status"] == "ok" and m["rhr"]["status"] == "ok"
    assert ready["details"] and all(isinstance(x, str) and x[0].isupper() for x in ready["details"])
    # the day entry keeps the old long reason
    today = client.get("/api/v1/stats/health?days=3").json()["days"][-1]["readiness"]
    assert set(today) == {"level", "reason"}


def test_readiness_today_red_day(client, db, world):
    row = db.scalar(select(DailyHealth).where(DailyHealth.day == TODAY))
    row.hrv_overnight_ms, row.resting_hr = 48, 63  # HRV ~-30 %, RHR +9
    db.commit()
    cache.invalidate()
    ready = client.get("/api/v1/stats/health?days=7").json()["readiness_today"]
    assert (ready["level"], ready["headline"]) == ("red", "Recover")
    m = {x["key"]: x for x in ready["metrics"]}
    assert m["hrv"]["status"] == "bad" and m["rhr"]["status"] == "bad"
    assert m["hrv"]["delta_pct"] < -20 and m["rhr"]["delta_pct"] > 0
    assert any(x.startswith("HRV 48 ms") for x in ready["details"])
    assert any(x.startswith("Resting HR 63") for x in ready["details"])
    assert len(ready["headline"]) <= 15


def test_readiness_view_headlines_status_and_details():
    d = TODAY
    hrv = Signal("hrv", "yellow", "HRV 61 ms (-12% vs 70 baseline)", 61, 70)
    rhr = Signal("rhr", "yellow", "resting HR 50 (+5 vs 45)", 50, 45)
    sleep = Signal("sleep", "green", "sleep 8.0 h, score 85", 8.0)
    ok = Readiness(d, "green", True, "all signals normal: ...", [sleep])
    assert readiness_view(ok, None)["details"][0] == "All signals normal"
    assert readiness_view(ok, None)["metrics"][0]["baseline"] is None  # no sleep baseline yet
    assert readiness_view(ok, None)["metrics"][0]["delta_pct"] is None

    yellow = readiness_view(Readiness(d, "yellow", True, "x", [hrv, rhr, sleep]), 8.0)
    assert (yellow["level"], yellow["headline"]) == ("yellow", "Take it easier")
    assert [m["status"] for m in yellow["metrics"]] == ["warn", "warn", "ok"]
    assert yellow["details"] == [
        "HRV 61 ms (-12% vs 70 baseline) - pulls it down a bit",
        "Resting HR 50 (+5 vs 45) - pulls it down a bit",
        "Sleep 8.0 h, score 85 (usual 8.0 h) - neutral",
    ]
    assert yellow["metrics"][2]["delta_pct"] == 0.0

    minor = readiness_view(Readiness(d, "green", True, "x", [hrv, sleep]), None)
    assert minor["headline"] == "Ready" and minor["details"][-1].startswith("Only a minor flag")
    assert minor["details"][-2] == "Resting HR: no data"  # missing factors are listed too

    short_night = Signal("sleep", "red", "sleep 4.0 h", 4.0)
    sn = readiness_view(Readiness(d, "yellow", True, "x", [short_night]), 7.0)
    assert sn["metrics"][0]["status"] == "bad" and sn["details"][-1] == "Short night alone: yellow"

    red = readiness_view(
        Readiness(d, "red", True, "x", [Signal("hrv", "red", "HRV low", 44, 70)]), None
    )
    assert (red["headline"], red["metrics"][0]["status"]) == ("Recover", "bad")
    none = readiness_view(Readiness(d, "green", False, "no data"), None)
    assert none == {
        "level": "unknown",
        "headline": "No data",
        "metrics": [],
        "details": ["No data"],
    }
