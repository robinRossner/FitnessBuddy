"""Shared test fixtures for the advisor (step 6): a synthetic world in a migrated SQLite, a fake
Claude client and settings helpers. Nothing here ever calls the real API."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.fitness.advisor import claude
from apps.fitness.advisor.schema import AdvisorOutput, ProposedChange, SessionOverride
from apps.fitness.analytics import cache
from apps.fitness.api.routes import get_db
from apps.fitness.db import session as dbsession
from apps.fitness.db.models import (
    DailyHealth,
    Exercise,
    ExerciseVariant,
    Gym,
    Lap,
    Run,
    TrainingSession,
    WorkoutSet,
)
from apps.fitness.db.types import Source
from apps.fitness.engine import config_service as cs
from apps.fitness.settings import FITNESS_DIR, Settings, get_settings
from apps.fitness.stats import rebuild_stats
from core.auth import get_api_token
from core.settings import get_core_settings
from server import app

TODAY = date(2026, 10, 25)  # a Sunday; the week starts Monday 2026-10-19
TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CFG_PATH = FITNESS_DIR / "config" / "engine.v3.yaml"
KEY = "sk-ant-test-0000000000000000"

PROGRAM_YAML = """\
version: 1
days:
  upper:
    - {exercise: bench_press, variant: Bench Press (Barbell), sets: 3}
    - {exercise: incline_bench_press, variant: Incline Bench Press (Smith Machine), sets: 3}
    - {exercise: chest_fly, variant: Chest Fly (Machine), sets: 3}
    - {exercise: lateral_raise, variant: Lateral Raise (Cable), sets: 3}
  lower:
    - {exercise: squat, variant: Squat (Smith Machine), sets: 3}
    - {exercise: leg_extension, variant: Leg Extension (Machine), sets: 2}
"""


def utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC)


def days_ago(n: int) -> date:
    return TODAY - timedelta(days=n)


# --------------------------------------------------------------------------- fake Claude


def make_output(**over: Any) -> AdvisorOutput:
    data: dict[str, Any] = {
        "diagnosis": "Bench press has been flat for four weeks while recovery looks fine.",
        "proposed_changes": [
            ProposedChange(
                param="exercise_overrides.bench_press.rep_range",
                **{"from": [7, 12]},
                to=[8, 12],
                reason="Flat e1RM; a slightly heavier range may break the plateau.",
                risk="low",
            ),
            ProposedChange(
                param="running.weekly_volume_cap_pct",
                **{"from": 10},
                to=12,
                reason="Weekly km are well below the cap.",
                risk="medium",
            ),
            ProposedChange(
                param="running.vdot.seed_race.time_s",
                **{"from": 3150},
                to=2800,
                reason="Pace looks conservative.",
                risk="low",
            ),
        ],
        "session_overrides": [
            SessionOverride(
                date="2026-10-26",
                session="Upper",
                change="Keep the first bench set easy.",
                reason="Shoulder warm-up.",
            )
        ],
        "confidence": "medium",
        "notes_for_user": "Watch bench e1RM over the next three weeks.",
    }
    data.update(over)
    return AdvisorOutput(**data)


class FakeClient:
    """Stands in for anthropic.Anthropic: records the request, returns / raises what it is told."""

    def __init__(
        self,
        output: AdvisorOutput | None = None,
        *,
        stop_reason: str = "end_turn",
        usage: dict[str, int] | None = None,
        exc: Exception | None = None,
        stop_details: Any = None,
    ):
        self.output = output if output is not None else make_output()
        self.stop_reason = stop_reason
        self.usage = usage or {
            "input_tokens": 6000,
            "output_tokens": 2500,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 2200,
        }
        self.exc = exc
        self.stop_details = stop_details
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        refused = self.stop_reason in ("refusal", "max_tokens")
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            stop_details=self.stop_details,
            parsed_output=None if refused else self.output,
            usage=SimpleNamespace(iterations=None, **self.usage),
            content=[],
            model="claude-opus-5-5",
            _request_id="req_test_1",
        )


@pytest.fixture
def fake(monkeypatch) -> FakeClient:
    """A FakeClient wired in as `claude.make_client` (replace `fake.output` etc. in the test)."""
    client = FakeClient()
    monkeypatch.setattr(claude, "make_client", lambda settings: client)
    return client


# --------------------------------------------------------------------------- settings


def configure(monkeypatch, tmp_path, *, key: str | None = KEY, **env: str) -> Settings:
    """Environment for one test: program file, API key (or none), extras. Returns Settings."""
    program = tmp_path / "program.yaml"
    program.write_text(PROGRAM_YAML, encoding="utf-8")
    monkeypatch.setenv("PROGRAM_PATH", str(program))
    monkeypatch.setenv("ANTHROPIC_API_KEY", key or "")
    monkeypatch.setenv("ADVISOR_DRY_RUN", "false")
    monkeypatch.setenv("ENGINE_AUTO_RUN", "false")
    monkeypatch.setenv("API_TOKEN", TOKEN)
    for k, v in env.items():
        monkeypatch.setenv(k.upper(), v)
    get_settings.cache_clear()
    get_core_settings.cache_clear()
    return get_settings()


@pytest.fixture(autouse=True)
def _clean_settings():
    get_settings.cache_clear()
    get_core_settings.cache_clear()
    cache.invalidate()
    yield
    get_settings.cache_clear()
    get_core_settings.cache_clear()
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- the world


class World:
    def __init__(self, db):
        self.db = db
        self.gym = Gym(key="g1", name="Gym One")
        self.other_gym = Gym(key="g2", name="Gym Two")
        db.add_all([self.gym, self.other_gym])
        catalog = {  # slug -> (name, muscle, movement_pattern)
            "bench_press": ("Bench Press", "chest", "horizontal_push"),
            "incline_bench_press": ("Incline Bench Press", "chest", "horizontal_push"),
            "chest_fly": ("Chest Fly", "chest", "horizontal_adduction"),
            "chest_press": ("Chest Press", "chest", "horizontal_push"),
            "lateral_raise": ("Lateral Raise", "shoulders", "shoulder_abduction"),
            "squat": ("Squat", "quads", "squat"),
            "leg_extension": ("Leg Extension", "quads", "knee_extension"),
            "leg_curl": ("Leg Curl", "hamstrings", "knee_flexion"),
            "calf_raise": ("Calf Raise", "calves", "calf_raise"),
        }
        self.ex = {
            slug: Exercise(
                slug=slug,
                name=name,
                primary_muscle=muscle,
                movement_pattern=pattern,
                default_equipment_type="barbell",
            )
            for slug, (name, muscle, pattern) in catalog.items()
        }
        db.add_all(self.ex.values())
        db.flush()

        def variant(slug, name, *, gym=None, eq="barbell", **kw):
            v = ExerciseVariant(
                exercise_id=self.ex[slug].id,
                gym_id=gym.id if gym else None,
                key=name,
                name=name,
                equipment_type=eq,
                load_convention="total",
                **kw,
            )
            db.add(v)
            return v

        self.bench = variant("bench_press", "Bench Press (Barbell)")
        self.incline = variant(
            "incline_bench_press", "Incline Bench Press (Smith Machine)", eq="smith"
        )
        self.incline_db = variant(
            "incline_bench_press", "Incline Bench Press (Dumbbell)", eq="dumbbell"
        )
        self.fly = variant("chest_fly", "Chest Fly (Machine)", eq="machine_stack")
        self.chest_press = variant("chest_press", "Chest Press (Machine)", eq="machine_stack")
        self.lateral = variant("lateral_raise", "Lateral Raise (Cable)", eq="cable")
        self.squat = variant("squat", "Squat (Smith Machine)", eq="smith")
        self.leg_ext = variant("leg_extension", "Leg Extension (Machine)", eq="machine_stack")
        self.leg_curl = variant("leg_curl", "Leg Curl (Machine)", eq="machine_stack")
        self.retired = variant("chest_press", "Chest Press (Retired)", active=False)
        self.foreign = variant(
            "chest_press", "Chest Press (Plate) @ Gym Two", gym=self.other_gym, eq="plate_loaded"
        )
        self.local = variant("chest_press", "Chest Press (Plate)", gym=self.gym, eq="plate_loaded")
        db.flush()

    def lift_session(
        self, day: date, kind: str, blocks: list[tuple[ExerciseVariant, float, list[int]]]
    ):
        start = datetime.combine(day, datetime.min.time()).replace(hour=18)
        s = TrainingSession(
            source=Source.APP,
            gym_id=self.gym.id,
            name=kind.title(),
            session_kind=kind,
            started_at_local=start,
            tz="Europe/Vienna",
            started_at_utc=utc(start - timedelta(hours=2)),
        )
        self.db.add(s)
        self.db.flush()
        for order, (variant, weight, reps) in enumerate(blocks):
            for i, r in enumerate(reps, start=1):
                self.db.add(
                    WorkoutSet(
                        session_id=s.id,
                        variant_id=variant.id,
                        source=Source.APP,
                        exercise_order=order,
                        set_index=i,
                        set_type="working",
                        weight_kg=weight,
                        reps=r,
                    )
                )
        self.db.flush()
        return s

    def run(self, day: date, laps: list[tuple], title: str = "Running") -> Run:
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


def build_world(db, monkeypatch) -> World:
    monkeypatch.setattr("apps.fitness.analytics.common.today", lambda: TODAY)
    monkeypatch.setattr("apps.fitness.engine.service.user_today", lambda settings=None: TODAY)
    cs.ensure_seeded(db, CFG_PATH)
    w = World(db)
    # ten weeks of Upper / Lower; bench flat for the last four weeks (stalled)
    for wk in range(10, 0, -1):
        up, lo = days_ago(wk * 7 - 1), days_ago(wk * 7 - 2)
        bench_kg = 60 + min(10 - wk, 5) * 2.5
        w.lift_session(
            up,
            "upper",
            [
                (w.bench, bench_kg, [8, 7, 6]),
                (w.incline, 40 + (10 - wk), [9, 8, 8]),
                (w.fly, 30, [12, 11, 10]),
                (w.lateral, 10, [12, 12, 10]),
            ],
        )
        w.lift_session(
            lo, "lower", [(w.squat, 80 + (10 - wk) * 2, [8, 8, 7]), (w.leg_ext, 50, [12, 11])]
        )
    rebuild_stats(db, bodyweight_kg=82)
    for i in range(45):
        d = days_ago(i)
        hrv, rhr = 68 + (i % 5), 53
        if i < 3:
            hrv, rhr = 54, 61  # three low-HRV days up to today
        db.add(
            DailyHealth(
                day=d,
                source=Source.GARMIN,
                resting_hr=rhr,
                hrv_overnight_ms=hrv,
                sleep_s=(7 + i % 2) * 3600,
                sleep_score=75,
                body_battery_max=90,
                body_battery_min=20,
                stress_avg=30,
                steps=9000,
            )
        )
    for d in (3, 10, 17, 24, 31, 38):
        w.run(days_ago(d), easy_laps(6, 142 + d % 3), "Easy run")
    w.run(days_ago(5), interval_laps(), "Intervals 4x1000")
    w.run(days_ago(12), interval_laps(work_hr=182), "Intervals again")
    w.run(days_ago(26), interval_laps(), "Intervals 4x1000")
    db.commit()
    return w


@pytest.fixture
def logs(db, caplog):
    """caplog that works after alembic's fileConfig disabled the app loggers."""
    import logging

    for name, lg in logging.root.manager.loggerDict.items():
        if name.startswith("apps.") and isinstance(lg, logging.Logger):
            lg.disabled = False
    caplog.set_level(logging.INFO, logger="apps")
    return caplog


@pytest.fixture
def env(monkeypatch, tmp_path) -> Settings:
    return configure(monkeypatch, tmp_path)


@pytest.fixture
def world(db, env, monkeypatch) -> World:
    return build_world(db, monkeypatch)


@pytest.fixture
def pin_session_factory(session_factory, monkeypatch):
    """Make `get_session_factory()` (background tasks, jobs) use the test database."""
    monkeypatch.setattr(dbsession, "_factory", session_factory)
    return session_factory


@pytest.fixture
def no_rerun(monkeypatch) -> list[str]:
    """Replace the engine re-run after an applied change by a recorder."""
    calls: list[str] = []
    monkeypatch.setattr(
        "apps.fitness.advisor.service.rerun_engine_after_change", lambda: calls.append("rerun")
    )
    return calls


@pytest.fixture
def client(world, session_factory, pin_session_factory, no_rerun, fake) -> TestClient:
    def _db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_api_token] = lambda: TOKEN
    return TestClient(app, headers=AUTH)
