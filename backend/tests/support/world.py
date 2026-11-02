"""The shared synthetic world: ten weeks of Upper / Lower lifting, runs and daily health in a
migrated SQLite, with "today" pinned to TODAY. Used by engine, advisor, api and gyms tests through
the `world` fixture (tests/conftest.py); `build_world` is for tests that need a second world."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

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
from apps.fitness.engine.store import config_service as cs
from apps.fitness.settings import FITNESS_DIR, Settings, get_settings
from apps.fitness.stats import rebuild_stats
from core.settings import get_core_settings
from tests.support.fakes_anthropic import KEY

TODAY = date(2026, 10, 25)  # a Sunday; the week starts Monday 2026-10-19
TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
# The world's database is seeded like a fresh install: from Settings.engine_config_path's default
# (engine.v3.yaml; later keys come from migrations / defaults). Deliberately NOT the newest file
# (the `engine_config` fixture): world tests assert v3-seeded values (advisor limits, validator
# bounds, makeup rules, rest times), 15 of them fail on v6.
WORLD_CONFIG_PATH = FITNESS_DIR / "config" / "engine.v3.yaml"

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
    monkeypatch.setattr("apps.fitness.engine.store.service.user_today", lambda settings=None: TODAY)
    cs.ensure_seeded(db, WORLD_CONFIG_PATH)
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
