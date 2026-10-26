"""Engine against a real (migrated) SQLite DB: program derivation, persistence, idempotency,
reconcile, decisions/triggers, and the plan/readiness/config API."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.auth import get_api_token
from app.api.routes import get_db
from app.db.models import (
    AdvisorTrigger,
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
from app.engine import config_service as cs
from app.engine import data as D
from app.engine import persist as P
from app.engine import service
from app.engine.program import (
    ExerciseUse,
    Program,
    ProgramExercise,
    SessionUses,
    derive_program,
    load_program,
    render_program,
)
from app.main import app
from app.settings import BACKEND_DIR, Settings
from app.stats import rebuild_stats

TODAY = date(2026, 10, 26)  # Monday


# --------------------------------------------------------------------------- program derivation


def uses(day: date, kind: str, *items: tuple[str, str, int]) -> SessionUses:
    return SessionUses(
        day, kind, tuple(ExerciseUse(e, v, n, i) for i, (e, v, n) in enumerate(items))
    )


def test_program_derivation_order_sets_and_share_threshold():
    bench, row, curl, fly = (
        ("bench_press", "Bench Press (Barbell)", 3),
        ("seated_row", "Seated Row (Cable)", 4),
        ("biceps_curl", "Biceps Curl (Dumbbell)", 3),
        ("chest_fly", "Chest Fly", 2),
    )
    sessions = [
        uses(date(2026, 10, 1), "upper", bench, row, curl),
        uses(date(2026, 10, 4), "upper", bench, row, curl, fly),
        uses(date(2026, 10, 8), "upper", row, bench, curl),  # order differs once
        uses(date(2026, 10, 11), "upper", bench, row),
        uses(date(2026, 10, 15), "upper", bench, row, curl),
        uses(date(2026, 10, 2), "lower", ("squat", "Squat (Barbell)", 4)),
        uses(
            date(2026, 10, 9),
            "lower",
            ("squat", "Squat (Barbell)", 3),
            ("leg_curl", "Lying Leg Curl (Machine)", 3),
        ),
    ]
    p = derive_program(sessions)
    upper = p.days["upper"]
    assert [e.variant for e in upper] == [
        "Bench Press (Barbell)",
        "Seated Row (Cable)",
        "Biceps Curl (Dumbbell)",
    ]
    assert upper[1].sets == 4 and upper[0].sets == 3  # median working sets
    assert "Chest Fly" not in [e.variant for e in upper]  # 1 of 5 sessions < 40 %
    assert [e.variant for e in p.days["lower"]] == ["Squat (Barbell)", "Lying Leg Curl (Machine)"]
    assert p.days["lower"][0].sets == 4  # median of 4 and 3 -> 3.5 -> 4
    assert p.meta["sessions"] == {"upper": 5, "lower": 2}


def test_program_yaml_roundtrip_and_overrides(tmp_path):
    prog = Program(
        {
            "upper": [
                ProgramExercise("bench_press", "Bench Press (Barbell)", 3),
                ProgramExercise("lateral_raise", "Lateral Raise (Cable)", 4),
            ],
            "lower": [ProgramExercise("squat", "Squat (Barbell)", 3)],
        },
        {"from": date(2026, 9, 1), "to": date(2026, 10, 23), "sessions": {"upper": 7, "lower": 3}},
    )
    path = tmp_path / "program.yaml"
    path.write_text(render_program(prog, gym="Gym 3"), encoding="utf-8")
    back = load_program(path)
    assert [(e.exercise, e.variant, e.sets) for e in back.days["upper"]] == [
        ("bench_press", "Bench Press (Barbell)", 3),
        ("lateral_raise", "Lateral Raise (Cable)", 4),
    ]
    # user edits: rep range, increment, disabled exercise
    path.write_text(
        path.read_text()
        .replace(
            "sets: 3}\n    - {exercise: lateral",
            "sets: 3, rep_range: [8, 15], increment_kg: 2.5}\n    - {exercise: lateral",
        )
        .replace("sets: 4}", "sets: 4, active: false}"),
        encoding="utf-8",
    )
    edited = load_program(path)
    assert (
        edited.days["upper"][0].rep_range == (8, 15) and edited.days["upper"][0].increment_kg == 2.5
    )
    assert [e.variant for e in edited.exercises("upper")] == [
        "Bench Press (Barbell)"
    ]  # inactive skipped


# --------------------------------------------------------------------------- fixtures


def utc(d: datetime) -> datetime:
    return d.replace(tzinfo=UTC)


@pytest.fixture
def world(db, tmp_path):
    """A small training history around TODAY: gym, variants, upper/lower sessions, runs, health."""
    cs.ensure_seeded(db, BACKEND_DIR / "config" / "engine.v3.yaml")
    gym = Gym(key="gym3", name="Gym 3")
    db.add(gym)
    ex_bench = Exercise(
        slug="bench_press",
        name="Bench Press",
        primary_muscle="chest",
        movement_pattern="horizontal_push",
        default_equipment_type="barbell",
    )
    ex_pull = Exercise(
        slug="lat_pulldown",
        name="Lat Pulldown",
        primary_muscle="lats",
        movement_pattern="vertical_pull",
        default_equipment_type="cable",
    )
    ex_squat = Exercise(
        slug="squat",
        name="Squat",
        primary_muscle="quads",
        movement_pattern="squat",
        default_equipment_type="barbell",
    )
    db.add_all([ex_bench, ex_pull, ex_squat])
    db.flush()
    bench = ExerciseVariant(
        exercise_id=ex_bench.id,
        key="b",
        name="Bench Press (Barbell)",
        equipment_type="barbell",
        load_convention="total",
    )
    pull = ExerciseVariant(
        exercise_id=ex_pull.id,
        gym_id=gym.id,
        key="p",
        name="Lat Pulldown (Cable) @ Gym 3",
        equipment_type="cable",
        load_convention="total",
        weight_ladder=[40, 45, 50, 55, 60],
    )
    squat = ExerciseVariant(
        exercise_id=ex_squat.id,
        key="s",
        name="Squat (Barbell)",
        equipment_type="barbell",
        load_convention="total",
    )
    db.add_all([bench, pull, squat])
    db.flush()

    def session(day: date, name: str, kind: str, blocks):
        start = datetime.combine(day, datetime.min.time()).replace(hour=18)
        s = TrainingSession(
            source=Source.APP,
            gym_id=gym.id,
            name=name,
            session_kind=kind,
            started_at_local=start,
            tz="Europe/Vienna",
            started_at_utc=utc(start - timedelta(hours=2)),
        )
        db.add(s)
        db.flush()
        for order, (variant, weight, reps) in enumerate(blocks):
            for i, r in enumerate(reps, start=1):
                db.add(
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
        db.flush()
        return s

    d = TODAY
    session(
        d - timedelta(days=11),
        "Upper",
        "upper",
        [(bench, 57.5, [12, 11, 10]), (pull, 45, [12, 12, 12])],
    )
    session(
        d - timedelta(days=7), "Upper", "upper", [(bench, 60, [10, 9, 9]), (pull, 50, [12, 12, 11])]
    )
    session(
        d - timedelta(days=4),
        "Upper",
        "upper",
        [(bench, 60, [11, 10, 9]), (pull, 50, [12, 12, 12])],
    )
    session(d - timedelta(days=6), "Lower", "lower", [(squat, 90, [10, 10, 9])])
    rebuild_stats(db, bodyweight_kg=82)

    for i in range(1, 15):
        db.add(
            DailyHealth(
                day=d - timedelta(days=i),
                source=Source.GARMIN,
                resting_hr=53,
                hrv_overnight_ms=70,
                sleep_s=8 * 3600,
                sleep_score=85,
            )
        )
    db.add(
        DailyHealth(
            day=d,
            source=Source.GARMIN,
            resting_hr=53,
            hrv_overnight_ms=66,
            sleep_s=8 * 3600,
            sleep_score=90,
        )
    )

    def run(day: date, km: int, hr: float, pace=320, ext=None):
        start = datetime.combine(day, datetime.min.time()).replace(hour=7)
        r = Run(
            source=Source.GARMIN,
            external_id=ext or f"g{day}",
            started_at_utc=utc(start - timedelta(hours=2)),
            started_at_local=start,
            tz="Europe/Vienna",
            activity_type="running",
            distance_m=km * 1000,
            duration_s=km * pace,
            avg_hr=hr,
        )
        db.add(r)
        db.flush()
        for i in range(km):
            db.add(
                Lap(
                    run_id=r.id,
                    lap_index=i + 1,
                    duration_s=pace,
                    distance_m=1000,
                    avg_hr=hr,
                    avg_pace_s_per_km=pace,
                )
            )
        return r

    for w in (1, 2, 3):
        run(d - timedelta(days=7 * w + 4), 7, 148)
        run(d - timedelta(days=7 * w + 1), 10, 147)
    db.commit()
    return {
        "gym": gym,
        "bench": bench,
        "pull": pull,
        "squat": squat,
        "session": session,
        "run": run,
    }


@pytest.fixture
def program(tmp_path) -> Program:
    return Program(
        {
            "upper": [
                ProgramExercise("bench_press", "Bench Press (Barbell)", 3),
                ProgramExercise("lat_pulldown", "Lat Pulldown (Cable)", 3),
            ],
            "lower": [ProgramExercise("squat", "Squat (Barbell)", 3)],
        }
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(program_path=tmp_path / "program.yaml", database_url="sqlite:///unused")


# --------------------------------------------------------------------------- data adapters


def test_variant_resolution_and_exposures(db, world):
    gym = world["gym"]
    assert D.resolve_variant(db, "Lat Pulldown (Cable)", gym).name == "Lat Pulldown (Cable) @ Gym 3"
    assert D.resolve_variant(db, "Bench Press (Barbell)", gym).gym_id is None
    assert D.resolve_variant(db, "Nope", gym) is None
    exps = D.load_exposures(db, world["bench"].id)
    assert [e.day for e in exps] == sorted(e.day for e in exps) and len(exps) == 3
    assert exps[-1].sets[0].weight == 60 and exps[-1].best_e1rm == pytest.approx(
        60 * (1 + 11 / 30), abs=0.01
    )
    assert D.load_exposures(db, world["bench"].id, before=TODAY - timedelta(days=7)) != exps


def test_weightless_sets_of_loaded_exercises_are_not_exposures(db, world):
    """A set ticked without a weight (machine not set up, nothing entered) is not history."""
    latest = db.scalars(select(TrainingSession).order_by(TrainingSession.started_at_local)).all()
    latest = latest[-1]
    before = D.load_exposures(db, world["bench"].id)
    db.add(
        WorkoutSet(
            session_id=latest.id, variant_id=world["bench"].id, source=Source.APP,
            exercise_order=9, set_index=1, set_type="working", weight_kg=None, reps=10,
        )
    )  # fmt: skip
    db.flush()
    after = D.load_exposures(db, world["bench"].id)
    assert [len(e.sets) for e in after] == [len(e.sets) for e in before]


def test_program_sessions_filtered_to_current_gym(db, world):
    sessions = D.sessions_for_program(db, TODAY - timedelta(weeks=8), TODAY, world["gym"])
    assert sorted(s.kind for s in sessions) == ["lower", "upper", "upper", "upper"]
    p = derive_program(sessions)
    assert [e.variant for e in p.days["upper"]] == ["Bench Press (Barbell)", "Lat Pulldown (Cable)"]


# --------------------------------------------------------------------------- run + persist


def test_run_engine_writes_plan_decisions_and_is_idempotent(db, world, program, settings):
    s1 = service.run_engine(db, today=TODAY, weeks=1, settings=settings, program=program)
    db.commit()
    r1 = s1.weeks[0].persisted
    assert r1.created == 7 and r1.updated == 0
    rows = list(db.scalars(select(PlannedSession).order_by(PlannedSession.day)))
    assert [r.kind for r in rows] == [
        "upper",
        "lower",
        "easy_run",
        "upper",
        "intervals",
        "rest",
        "long_run",
    ]
    assert all(r.status == "proposed" and r.config_version_id for r in rows)
    mon = rows[0]
    assert mon.prescription["session"]["exercises"][0]["sets"][0]["target_weight"] == 60
    assert mon.prescription["title"] == "Upper" and mon.gym_id == world["gym"].id
    n_dec = db.scalar(select(func.count()).select_from(DecisionLog))
    assert n_dec > 8
    ids = {r.id for r in rows}

    s2 = service.run_engine(db, today=TODAY, weeks=1, settings=settings, program=program)
    db.commit()
    r2 = s2.weeks[0].persisted
    assert (r2.created, r2.updated, r2.unchanged) == (0, 0, 7)
    assert r2.decisions == 0 and db.scalar(select(func.count()).select_from(DecisionLog)) == n_dec
    assert {r.id for r in db.scalars(select(PlannedSession))} == ids  # ids stable
    # readiness colour was written onto the day's health row
    assert db.scalar(select(DailyHealth.readiness_color).where(DailyHealth.day == TODAY)) == "green"
    # every decision references the config version
    assert (
        db.scalar(
            select(func.count())
            .select_from(DecisionLog)
            .where(DecisionLog.config_version_id.is_(None))
        )
        == 0
    )


def test_closed_sessions_are_never_overwritten_and_approved_ones_are_reproposed(
    db, world, program, settings
):
    service.run_engine(db, today=TODAY, weeks=1, settings=settings, program=program)
    db.commit()
    mon = db.scalars(select(PlannedSession).where(PlannedSession.day == TODAY)).one()
    thu = db.scalars(
        select(PlannedSession).where(PlannedSession.day == TODAY + timedelta(days=3))
    ).one()
    mon.status, thu.status = "done", "approved"
    mon.prescription = {**mon.prescription, "title": "MY EDIT"}
    db.commit()
    # new data changes Thursday's targets: a heavier bench session
    world["session"](
        TODAY - timedelta(days=1), "Upper", "upper", [(world["bench"], 62.5, [12, 12, 12])]
    )
    rebuild_stats(db, bodyweight_kg=82)
    db.commit()
    res = (
        service.run_engine(db, today=TODAY, weeks=1, settings=settings, program=program)
        .weeks[0]
        .persisted
    )
    db.commit()
    assert res.reproposed == 1 and res.created == 0  # the closed Monday is not even regenerated
    db.refresh(mon)
    db.refresh(thu)
    assert mon.prescription["title"] == "MY EDIT" and thu.status == "proposed"
    assert thu.prescription["session"]["exercises"][0]["sets"][0]["target_weight"] == 67.5


def test_yellow_readiness_is_written_into_todays_planned_session(db, world, program, settings):
    row = db.scalars(select(DailyHealth).where(DailyHealth.day == TODAY)).one()
    row.hrv_overnight_ms, row.resting_hr = 58, 60
    db.commit()
    service.run_engine(db, today=TODAY, weeks=1, settings=settings, program=program)
    db.commit()
    mon = db.scalars(select(PlannedSession).where(PlannedSession.day == TODAY)).one()
    assert len(mon.prescription["session"]["exercises"][0]["sets"]) == 2
    assert mon.prescription["readiness"]["color"] == "yellow" and mon.prescription["adjustments"]
    assert (
        db.scalar(select(DailyHealth.readiness_color).where(DailyHealth.day == TODAY)) == "yellow"
    )
    # the next morning's green plan replaces it (still 'proposed')
    row.hrv_overnight_ms, row.resting_hr = 66, 53
    db.commit()
    service.run_engine(db, today=TODAY, weeks=1, settings=settings, program=program)
    db.commit()
    db.refresh(mon)
    assert len(mon.prescription["session"]["exercises"][0]["sets"]) == 3


def test_stall_ladder_step_is_persisted_and_remembered(db, world, program, settings):
    squat = world["squat"]
    for ago in (40, 33, 26, 19, 12):  # weekly, flat, never the day after a 10 km run
        world["session"](TODAY - timedelta(days=ago), "Lower", "lower", [(squat, 90, [12, 12, 11])])
    rebuild_stats(db, bodyweight_kg=82)
    db.commit()
    service.run_engine(db, today=TODAY, weeks=1, settings=settings, program=program)
    db.commit()
    tue = db.scalars(
        select(PlannedSession).where(PlannedSession.day == TODAY + timedelta(days=1))
    ).one()
    ex = tue.prescription["session"]["exercises"][0]
    assert ex["sets"][0]["target_weight"] == 85.0  # -5 % of 90 on the barbell grid
    assert ex["progression_state"] == "rebuild" and ex["rule"] == "stall:reset_small"
    ladder = D.load_ladder(db, squat.id)
    assert [(e.day, e.step, e.load, e.from_load) for e in ladder] == [
        (TODAY + timedelta(days=1), 1, 85.0, 90.0)
    ]
    assert not db.scalars(select(AdvisorTrigger).where(AdvisorTrigger.type == "lift_stall")).all()
    res = service.run_engine(db, today=TODAY, weeks=1, settings=settings, program=program)
    db.commit()
    assert res.weeks[0].persisted.updated == 0  # the remembered step: same plan
    n = db.scalar(select(func.count()).select_from(DecisionLog))
    service.run_engine(db, today=TODAY, weeks=1, settings=settings, program=program)
    db.commit()
    assert db.scalar(select(func.count()).select_from(DecisionLog)) == n  # idempotent
    # trained at the new load: the reset is no longer pending, normal progression resumes
    world["session"](TODAY + timedelta(days=1), "Lower", "lower", [(squat, 85, [12, 12, 12])])
    rebuild_stats(db, bodyweight_kg=82)
    db.commit()
    nxt = TODAY + timedelta(days=7)
    plan, _cfg, _cv = service.plan_week(
        db, today=nxt, week_start=nxt, settings=settings, program=program
    )
    sq = next(d for d in plan.days if d.kind == "lower").prescription["exercises"][0]
    assert sq["rule"] != "stall:pending_reset" and sq["sets"][0]["target_weight"] >= 85


def _legacy_reset(db, variant, load: float, reps: list[int], reset_load: float) -> None:
    from app.engine.decisions import log_decision

    log_decision(
        db,
        kind="progression",
        rule="stall_reset",
        inputs={
            "last": {
                "date": (TODAY - timedelta(days=3)).isoformat(),
                "sets": [{"weight": load, "reps": r, "duration_s": None} for r in reps],
                "best_e1rm": None,
            }
        },
        output={
            "day": TODAY.isoformat(),
            "sets": [],
            "flags": ["stall", "identical_exposures"],
            "reset_day": TODAY.isoformat(),
            "reset_load": reset_load,
        },
        rationale="no e1RM improvement in the last 3 exposures (engine v1)",
        subject_type="variant",
        subject_id=variant.id,
    )


def test_legacy_minus_10_percent_resets_become_a_soft_deload_and_a_retest(
    db, world, program, settings
):
    _legacy_reset(db, world["bench"], 60, [11, 10, 9], 54.0)
    _legacy_reset(db, world["pull"], 50, [12, 12, 12], 45.0)
    _legacy_reset(db, world["squat"], 90, [10, 10, 9], 80.0)
    db.commit()
    cfg, _ = cs.load_active(db)
    (w,) = D.legacy_windows(db, cfg)
    assert (w.start, w.until, w.reason, w.deload) == (
        TODAY,
        TODAY + timedelta(days=6),
        "legacy_reset",
        True,
    )
    assert {k: (r.load, r.reps) for k, r in w.retest.items()} == {
        world["bench"].id: (60.0, (11, 10, 9)),
        world["pull"].id: (50.0, (12, 12, 12)),
        world["squat"].id: (90.0, (10, 10, 9)),
    }
    service.run_engine(db, today=TODAY, weeks=2, settings=settings, program=program)
    db.commit()
    rows = {r.day: r for r in db.scalars(select(PlannedSession))}
    mon = rows[TODAY].prescription["session"]
    bench = mon["exercises"][0]
    assert mon["deload"]["active"] and mon["deload"]["reason"] == "legacy_reset"
    # the old load, half the sets: no -10 %
    assert [(s["target_weight"], s["target_reps"]) for s in bench["sets"]] == [(60, 11), (60, 10)]
    assert bench["progression_state"] == "deload"
    squat = rows[TODAY + timedelta(days=1)].prescription["session"]["exercises"][0]
    assert squat["sets"][0]["target_weight"] == 90 and len(squat["sets"]) == 2
    nxt = rows[TODAY + timedelta(days=7)].prescription["session"]["exercises"][0]
    assert nxt["progression_state"] == "retest" and nxt["sets"][0]["target_weight"] == 60
    assert [s["target_reps"] for s in nxt["sets"]] == [12, 11, 10]
    trig = db.scalars(select(AdvisorTrigger).where(AdvisorTrigger.type == "multi_lift_stall")).all()
    assert len(trig) == 1 and len(trig[0].payload["lifts"]) == 3
    # the conversion is logged once and read back as one window, not twice
    assert len(D.load_windows(db, cfg, TODAY - timedelta(days=30))) == 1
    n = db.scalar(select(func.count()).select_from(DecisionLog))
    service.run_engine(db, today=TODAY, weeks=2, settings=settings, program=program)
    db.commit()
    assert db.scalar(select(func.count()).select_from(DecisionLog)) == n


def _stall_week(db, world):
    _legacy_reset(db, world["bench"], 60, [11, 10, 9], 54.0)
    _legacy_reset(db, world["pull"], 50, [12, 12, 12], 45.0)
    _legacy_reset(db, world["squat"], 90, [10, 10, 9], 80.0)
    db.commit()


def _cancel_rows(db):
    return db.scalars(select(DecisionLog).where(DecisionLog.kind == "deload_cancel")).all()


def test_cancelling_the_deload_gives_normal_sets_and_moves_the_retest_into_the_week(
    db, world, program, settings
):
    _stall_week(db, world)
    service.run_engine(db, today=TODAY, weeks=2, settings=settings, program=program)
    db.commit()
    soft = db.scalars(select(PlannedSession).where(PlannedSession.day == TODAY)).one()
    assert soft.prescription["session"]["deload"]["active"]
    res = service.cancel_deload(
        db, week_start=TODAY, reason="feeling fine", today=TODAY, settings=settings, program=program
    )
    db.commit()
    assert res.status == "cancelled" and res.replanned and len(res.retest) == 3
    (row,) = _cancel_rows(db)
    assert row.output["author"] == "user" and row.output["reason"] == "feeling fine"
    assert row.subject_id == TODAY.isoformat()
    rows = {r.day: r for r in db.scalars(select(PlannedSession))}
    mon = rows[TODAY].prescription["session"]
    bench = mon["exercises"][0]
    assert mon["deload"] is None and bench["progression_state"] == "retest"
    assert [(s["target_weight"], s["target_reps"]) for s in bench["sets"]] == [
        (60, 12),
        (60, 11),
        (60, 10),
    ]  # old load, normal 3 sets, beat 11/10/9
    assert bench["progression_note"] == "Retest at old load 60 kg: beat 11/10/9"
    squat = rows[TODAY + timedelta(days=1)].prescription["session"]["exercises"][0]
    assert squat["progression_state"] == "retest" and len(squat["sets"]) == 3
    # a later engine run keeps the week cancelled and writes nothing new
    n = db.scalar(select(func.count()).select_from(DecisionLog))
    service.run_engine(db, today=TODAY, weeks=2, settings=settings, program=program)
    db.commit()
    again = db.scalars(select(PlannedSession).where(PlannedSession.day == TODAY)).one()
    assert again.prescription["session"]["exercises"][0]["progression_state"] == "retest"
    assert again.prescription["session"]["deload"] is None
    assert db.scalar(select(func.count()).select_from(DecisionLog)) == n
    cfg, _ = cs.load_active(db)
    (w,) = D.load_windows(db, cfg, TODAY - timedelta(days=30))
    assert (w.deload, w.cancelled) == (False, True) and len(w.retest) == 3


def test_cancelling_twice_changes_nothing(db, world, program, settings):
    _stall_week(db, world)
    first = service.cancel_deload(
        db, week_start=TODAY, today=TODAY, settings=settings, program=program
    )
    db.commit()
    n = db.scalar(select(func.count()).select_from(DecisionLog))
    plans = {r.id: r.prescription for r in db.scalars(select(PlannedSession))}
    second = service.cancel_deload(
        db, week_start=TODAY, reason="again", today=TODAY, settings=settings, program=program
    )
    db.commit()
    assert first.status == "cancelled" and second.status == "already_cancelled"
    assert "already cancelled" in second.message and not second.as_dict()["changed"]
    assert db.scalar(select(func.count()).select_from(DecisionLog)) == n
    assert {r.id: r.prescription for r in db.scalars(select(PlannedSession))} == plans
    assert len(_cancel_rows(db)) == 1


def test_cancelling_a_week_without_a_deload_is_a_no_op(db, world, program, settings):
    res = service.cancel_deload(
        db, week_start=TODAY, today=TODAY, settings=settings, program=program
    )
    assert res.status == "no_deload" and "No lifting deload to cancel" in res.message
    assert not _cancel_rows(db)
    _stall_week(db, world)  # the deload is in the week of TODAY, not two weeks later
    later = service.cancel_deload(
        db, week_start=TODAY + timedelta(days=14), today=TODAY, settings=settings, program=program
    )
    assert later.status == "no_deload" and not _cancel_rows(db)


def test_deload_cancel_api(client, db, world, program, settings):
    _stall_week(db, world)
    service.run_engine(db, today=TODAY, weeks=1, settings=settings, program=program)
    db.commit()
    url = "/api/v1/engine/deload/cancel"
    assert TestClient(app).post(url, json={"week_start": "2026-10-26"}).status_code == 401
    r = client.post(url, json={"week_start": "2026-10-28", "reason": "fine"})  # any day of the week
    body = r.json()
    assert r.status_code == 200 and body["status"] == "cancelled" and body["changed"] is True
    assert body["week_start"] == "2026-10-26" and body["reason"] == "fine"
    assert len(body["retest"]) == 3 and body["replanned"] is True
    mon = client.get("/api/v1/plan/week", params={"start": "2026-10-26"}).json()["days"][0]
    assert mon["sessions"][0]["session"]["exercises"][0]["progression_state"] == "retest"
    again = client.post(url, json={"week_start": "2026-10-26"}).json()
    assert again["status"] == "already_cancelled" and again["changed"] is False
    none = client.post(url, json={"week_start": "2026-11-16"}).json()
    assert none["status"] == "no_deload" and "No lifting deload" in none["message"]
    assert client.post(url, json={}).status_code == 422


def test_deload_cancel_cli(db, world, program, settings, monkeypatch):
    from typer.testing import CliRunner

    from app import cli

    _stall_week(db, world)
    monkeypatch.setattr(service, "user_today", lambda settings=None: TODAY)
    monkeypatch.setattr(service, "get_program", lambda settings=None: program)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "session_scope", lambda: _Scope(db))
    out = CliRunner().invoke(
        cli.cli, ["engine", "deload", "cancel", "--week", "2026-10-26", "--reason", "fine"]
    )
    assert out.exit_code == 0, out.output
    assert "Deload of the week of 2026-10-26 cancelled" in out.output
    assert "retest at the old load" in out.output
    out = CliRunner().invoke(cli.cli, ["engine", "deload", "cancel", "--week", "2026-10-26"])
    assert out.exit_code == 0 and "already cancelled" in out.output


class _Scope:
    """`session_scope()` over the test session (commit on success)."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, *_):
        if exc_type is None:
            self.db.commit()
        return False


def test_a_trained_legacy_reset_is_left_alone(db, world, program, settings):
    _legacy_reset(db, world["bench"], 60, [11, 10, 9], 54.0)
    world["session"](TODAY, "Upper", "upper", [(world["bench"], 54, [12, 12, 12])])
    db.commit()
    cfg, _ = cs.load_active(db)
    assert D.legacy_windows(db, cfg) == []


def test_migration_0011_adds_the_volume_hold_keys(tmp_path):
    import json

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from app.engine.config import EngineConfig

    url = f"sqlite:///{tmp_path / 'm.db'}"
    acfg = Config(str(BACKEND_DIR / "alembic.ini"))
    acfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    acfg.attributes["url"] = url
    command.upgrade(acfg, "0010")
    content = {"version": "v6", "running": {"volume": {"cap_abs_km": 3.0}}}
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO config_versions (version, content, content_hash, is_active, "
                "created_at, author) VALUES ('v6', :c, 'h', 1, '2026-10-01 00:00:00', 'seed')"
            ),
            {"c": json.dumps(content)},
        )
    command.upgrade(acfg, "head")
    command.downgrade(acfg, "0010")
    command.upgrade(acfg, "head")  # a second run changes nothing: the keys exist
    with eng.connect() as c:
        rows = c.execute(
            text("SELECT version, is_active, author, content FROM config_versions ORDER BY id")
        ).all()
    eng.dispose()
    assert [(r[0], r[1], r[2]) for r in rows] == [("v6", 0, "seed"), ("v6.1", 1, "engine")]
    new = json.loads(rows[1][3]) if isinstance(rows[1][3], str) else rows[1][3]
    vol = EngineConfig.model_validate(new).running_cfg.volume
    assert vol.hold_last_week is True and vol.hold_limit_factor == 1.1
    assert vol.cap_abs_km == 3.0  # existing values stay


def test_migration_0010_adds_the_v2_keys_and_retires_v1_stall_triggers(tmp_path):
    import json

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from app.engine.config import EngineConfig

    url = f"sqlite:///{tmp_path / 'm.db'}"
    acfg = Config(str(BACKEND_DIR / "alembic.ini"))
    acfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    acfg.attributes["url"] = url
    command.upgrade(acfg, "0009")
    content = {
        "version": "v3.2",
        "strength": {"stall_exposures": 3, "deload": {"sets_pct": 40, "load_pct": 10}},
        "running": {"weekly_volume_floor_km": 18, "long_run": {"cap_km": 21, "step_km": 1.0}},
    }
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO config_versions (version, content, content_hash, is_active, "
                "created_at, author) VALUES ('v3.2', :c, 'h', 1, '2026-10-01 00:00:00', 'user')"
            ),
            {"c": json.dumps(content)},
        )
        for typ, key, payload in (
            ("lift_stall", "lift_stall:1:2026-10-26", {"variant": "x", "reset_load_kg": 54}),
            ("lift_stall", "lift_stall:2:2026-10-26", {"variant": "y", "ladder_step": 2}),
            ("rule_conflict", "rule_conflict:x", {"conflict": "x"}),
        ):
            c.execute(
                text(
                    "INSERT INTO advisor_triggers (created_at, type, status, dedupe_key, payload) "
                    "VALUES ('2026-10-25 00:00:00', :t, 'open', :k, :p)"
                ),
                {"t": typ, "k": key, "p": json.dumps(payload)},
            )
    command.upgrade(acfg, "0010")
    with eng.connect() as c:
        rows = c.execute(
            text("SELECT version, is_active, author, content FROM config_versions ORDER BY id")
        ).all()
        trig = dict(c.execute(text("SELECT dedupe_key, status FROM advisor_triggers")).all())
    eng.dispose()
    assert [(r[0], r[1], r[2]) for r in rows] == [("v3.2", 0, "user"), ("v3.1", 1, "engine")]
    new = json.loads(rows[1][3]) if isinstance(rows[1][3], str) else rows[1][3]
    cfg = EngineConfig.model_validate(new)
    assert cfg.strength.deload.sets_pct == 50 and cfg.strength.deload.load_pct == 0
    assert cfg.strength.deload.sets_rounding == "up"
    assert cfg.strength.stall_exposures == 3 and cfg.strength.stall_exposures_2x == 4
    assert cfg.running_cfg.long_run.cap_km == 20 and cfg.running_cfg.long_run.step_back_every == 4
    assert cfg.running_cfg.weekly_volume_floor_km == 18  # kept: now the target
    assert cfg.running_cfg.quality.phases[0].name == "threshold"
    assert cfg.schedule_cfg.run_makeup_max_shift_days == 1
    assert trig == {
        "lift_stall:1:2026-10-26": "dismissed",
        "lift_stall:2:2026-10-26": "open",
        "rule_conflict:x": "open",
    }


def test_reconcile_links_done_sessions_and_skips_missed_ones(db, world, program, settings):
    last_mon = TODAY - timedelta(days=7)
    service.run_engine(
        db, today=last_mon, weeks=1, settings=settings, program=program
    )  # plans last week
    db.commit()
    # fixture history: Upper Mon 10-19, Lower Tue 10-20, but no run on Wed 10-21
    counts = P.reconcile(db, TODAY)
    db.commit()
    rows = {r.day: r for r in db.scalars(select(PlannedSession))}
    assert rows[last_mon].status == "done"
    assert db.scalars(
        select(TrainingSession).where(TrainingSession.planned_session_id == rows[last_mon].id)
    ).all()
    assert rows[last_mon + timedelta(days=1)].status == "done"  # the Tuesday lower
    wed = rows[last_mon + timedelta(days=2)]
    assert wed.kind == "easy_run" and wed.status == "skipped"  # no run that day
    assert rows[last_mon + timedelta(days=5)].status == "done"  # rest day, past
    assert counts["skipped"] >= 1
    # a run that arrives late (sync / export) heals the skipped session and links the run
    r = world["run"](last_mon + timedelta(days=2), 6, 147, ext="late")
    db.commit()
    P.reconcile(db, TODAY)
    db.commit()
    assert db.get(PlannedSession, wed.id).status == "done"
    assert db.get(Run, r.id).planned_session_id == wed.id


def test_dry_run_plan_writes_nothing(db, world, program, settings):
    plan, _cfg, _cv = service.plan_week(
        db, today=TODAY, week_start=TODAY, settings=settings, program=program
    )
    db.rollback()
    assert len(plan.days) == 7
    assert db.scalar(select(func.count()).select_from(PlannedSession)) == 0
    assert db.scalar(select(func.count()).select_from(DecisionLog)) == 0


def test_evaluate_runs_and_readiness_service(db, world, settings):
    rows = service.evaluate_runs(db, today=TODAY, weeks=6, settings=settings)
    assert rows and all(r["type"] == "easy/long" and r["evaluation"]["passed"] for r in rows)
    r = service.readiness_for(db, TODAY, settings)
    assert r.color == "green" and r.known
    assert not service.readiness_for(db, TODAY + timedelta(days=5), settings).known


# --------------------------------------------------------------------------- API


@pytest.fixture
def client(db, world, program, monkeypatch):
    monkeypatch.setattr(service, "user_today", lambda settings=None: TODAY)
    monkeypatch.setattr(service, "get_program", lambda settings=None: program)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_api_token] = lambda: "tok"
    yield TestClient(app, headers={"Authorization": "Bearer tok"})
    app.dependency_overrides.clear()


def test_api_requires_the_bearer_token(client):
    bare = TestClient(app)
    assert bare.get("/api/v1/plan/today").status_code == 401
    assert client.get("/api/v1/plan/today").status_code == 200


def test_plan_today_and_week_preview_then_stored(client, db, program, settings):
    today = client.get("/api/v1/plan/today").json()
    assert today["date"] == "2026-10-26" and today["readiness"]["color"] == "green"
    rt = today["readiness_today"]  # compact view of the same score, for the Today screen
    assert (rt["level"], rt["headline"]) == ("green", "Ready")
    assert set(rt) == {"level", "headline", "metrics", "details"} and rt["details"]
    assert {m["key"] for m in rt["metrics"]} <= {"hrv", "rhr", "sleep"}
    assert all(m["status"] in {"ok", "warn", "bad"} for m in rt["metrics"])
    [s] = today["sessions"]
    assert s["status"] == "preview" and s["id"] is None and s["kind"] == "upper"
    assert s["session"]["exercises"][0]["sets"][0]["target_reps"] == 12  # last 11/10/9 + 1

    week = client.get(
        "/api/v1/plan/week", params={"start": "2026-10-28"}
    ).json()  # any day of the week
    assert week["start"] == "2026-10-26" and len(week["days"]) == 7
    assert [d["sessions"][0]["kind"] for d in week["days"]] == [
        "upper",
        "lower",
        "easy_run",
        "upper",
        "intervals",
        "rest",
        "long_run",
    ]

    service.run_engine(db, today=TODAY, weeks=1, settings=settings, program=program)
    db.commit()
    stored = client.get("/api/v1/plan/week", params={"start": "2026-10-26"}).json()
    first = stored["days"][0]["sessions"][0]
    assert first["status"] == "proposed" and first["id"] and first["slot_id"] == "mon_upper"
    iv = stored["days"][4]["sessions"][0]["session"]
    assert iv["type"] == "intervals" and iv["structure"]["work"]["hr"]["low"] == 166


def test_readiness_endpoint(client):
    r = client.get("/api/v1/readiness", params={"date": "2026-10-26"}).json()
    assert r["color"] == "green" and {s["name"] for s in r["signals"]} == {"hrv", "rhr", "sleep"}
    unknown = client.get("/api/v1/readiness", params={"date": "2026-12-01"}).json()
    assert unknown["known"] is False and unknown["reason"] == "no data"


def test_config_endpoint_and_rollback(client, db):
    cfg = client.get("/api/v1/config").json()
    assert (
        cfg["active"]["version"] == "v3"
        and cfg["active"]["content"]["running"]["heart_rate"]["lthr"] == 171
    )
    cs.create_version(db, {"strength.stall_exposures": 4}, reason="test", author="advisor")
    db.commit()
    cfg = client.get("/api/v1/config").json()
    assert cfg["active"]["version"] == "v3.1" and [h["version"] for h in cfg["history"]] == [
        "v3.1",
        "v3",
    ]
    assert (
        cfg["history"][0]["author"] == "advisor"
        and cfg["history"][0]["diff"]["strength.stall_exposures"]["new"] == 4
    )
    back = client.post("/api/v1/config/rollback/v3").json()
    assert back["active"]["version"] == "v3.2" and back["active"]["reason"] == "rollback to v3"
    assert (
        client.get("/api/v1/config").json()["active"]["content"]["strength"]["stall_exposures"] == 3
    )
    assert client.post("/api/v1/config/rollback/v42").status_code == 404
