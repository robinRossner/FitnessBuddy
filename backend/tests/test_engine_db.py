"""Engine against a real (migrated) SQLite DB: program derivation, persistence, idempotency,
reconcile, decisions/triggers, and the plan/readiness/config API."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.bodyweight import get_api_token
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


def test_stall_persists_trigger_and_reset_is_remembered(db, world, program, settings):
    squat = world["squat"]
    for i in range(4):  # better squat days long ago, then 3 weaker/equal ones -> no improvement
        world["session"](
            TODAY - timedelta(days=40 - 3 * i), "Lower", "lower", [(squat, 90, [12, 12, 11])]
        )
    rebuild_stats(db, bodyweight_kg=82)
    db.commit()
    service.run_engine(db, today=TODAY, weeks=1, settings=settings, program=program)
    db.commit()
    trig = db.scalars(select(AdvisorTrigger).where(AdvisorTrigger.type == "lift_stall")).all()
    assert (
        len(trig) == 1
        and trig[0].status == "open"
        and trig[0].payload["variant"] == "Squat (Barbell)"
    )
    tue = db.scalars(
        select(PlannedSession).where(PlannedSession.day == TODAY + timedelta(days=1))
    ).one()
    assert tue.prescription["session"]["exercises"][0]["sets"][0]["target_weight"] == 80.0
    reset = D.last_stall_reset(db, squat.id)
    assert reset is not None and reset.load == 80.0 and reset.day == TODAY + timedelta(days=1)
    service.run_engine(db, today=TODAY, weeks=1, settings=settings, program=program)
    db.commit()
    assert (
        db.scalar(
            select(func.count())
            .select_from(AdvisorTrigger)
            .where(AdvisorTrigger.type == "lift_stall")
        )
        == 1
    )


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
