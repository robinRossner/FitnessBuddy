"""Session-kind inference, duration cap and inactive variants during import/report."""

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import Exercise, ExerciseVariant, TrainingSession
from app.db.types import SessionKind
from app.importers.strong.importer import ImportOptions, run_import, session_uuid
from app.importers.strong.mapping import read_mapping, write_mapping
from app.importers.strong.report import build_report
from app.settings import BACKEND_DIR

HEADER = "Date,Workout Name,Duration,Exercise Name,Set Order,Weight,Reps,Distance,Seconds,RPE"
MAPPING = """\
strong_name,canonical_exercise,variant_name,equipment_type,load_convention,status,active,notes
Bench Press (Barbell),bench_press,Bench Press (Barbell),barbell,total,confirmed,true,
Bent Over Row (Barbell),bent_over_row,Bent Over Row (Barbell),barbell,total,confirmed,true,
Squat (Barbell),squat,Squat (Barbell),barbell,total,confirmed,true,
Lying Leg Curl (Machine),leg_curl,Lying Leg Curl (Machine),machine_stack,total,confirmed,true,
Pull Up,pull_up,Pull Up,bodyweight,total,confirmed,true,
Chest Dip,dip,Chest Dip,bodyweight,total,confirmed,true,
Shrug (Dumbbell),shrug,Shrug (Dumbbell),dumbbell,per_hand,auto,false,old
Mystery,,,,,unmapped,true,
"""


def _workout(when: str, name: str, duration: str, sets: list[tuple[str, float, int]]) -> list[str]:
    return [
        f'{when},"{name}",{duration},"{ex}",{i},{w},{reps},0,0.0,'
        for i, (ex, w, reps) in enumerate(sets, 1)
    ]


UPPER = [("Bench Press (Barbell)", 60.0, 8), ("Bent Over Row (Barbell)", 50.0, 8)]
LOWER = [("Squat (Barbell)", 80.0, 8), ("Lying Leg Curl (Machine)", 40.0, 10)]
ROWS = [
    *_workout("2025-01-01 08:00:00", "Morning Workout", "1h", [*LOWER, *LOWER]),
    *_workout("2025-01-02 13:00:00", "Afternoon Workout", "1h", [*UPPER, *UPPER]),
    *_workout("2025-01-03 18:00:00", "Evening Workout", "1h", [*UPPER, *LOWER]),
    *_workout(
        "2025-01-04 12:00:00",
        "Midday Workout",
        "50m",
        [("Pull Up", 0.0, 8), ("Pull Up", 0.0, 7), ("Chest Dip", 0.0, 10), ("Chest Dip", 0.0, 9)],
    ),
    *_workout("2025-01-05 09:00:00", "Upper", "1h", LOWER),  # a real name wins over the exercises
    *_workout("2025-01-06 09:00:00", "Morning Workout", "1h", [("Mystery", 1.0, 1)]),
    *_workout("2025-01-07 09:00:00", "Morning Workout", "19h 22m", UPPER),  # timer left running
    *_workout("2025-01-08 09:00:00", "Upper", "3h 59m", UPPER),  # long but plausible
    *_workout("2025-01-09 09:00:00", "Upper", "1h", [("Shrug (Dumbbell)", 0.0, 10), *UPPER]),
]


@pytest.fixture
def env(tmp_path: Path):
    d = tmp_path / "imports"
    d.mkdir()
    (d / "x.csv").write_text("\n".join([HEADER, *ROWS]) + "\n", encoding="utf-8")
    mapping = tmp_path / "m.csv"
    mapping.write_text(MAPPING, encoding="utf-8")
    return ImportOptions(
        paths=[d],
        mapping_path=mapping,
        catalog_path=BACKEND_DIR / "data" / "catalog" / "exercises.yaml",
        engine_config_path=BACKEND_DIR / "config" / "engine.v2.yaml",
        apply=True,
    )


def _session(db, day: str) -> TrainingSession:
    y, m, d = map(int, day.split("-"))
    s = db.scalar(
        select(TrainingSession).where(
            TrainingSession.started_at_local >= datetime(y, m, d),
            TrainingSession.started_at_local < datetime(y, m, d + 1),
        )
    )
    assert s is not None
    return s


def test_generic_workout_names_get_their_kind_from_the_exercises(db, env):
    res = run_import(db, env)
    db.commit()
    kinds = {d: _session(db, d).session_kind for d in (f"2025-01-0{i}" for i in range(1, 7))}
    assert kinds == {
        "2025-01-01": SessionKind.LOWER,
        "2025-01-02": SessionKind.UPPER,
        "2025-01-03": SessionKind.FULL,
        "2025-01-04": SessionKind.CALISTHENICS,
        "2025-01-05": SessionKind.UPPER,  # named "Upper": the name wins
        "2025-01-06": SessionKind.OTHER,  # nothing importable to go on
    }
    assert res.kinds_inferred["lower"] == 1 and res.kinds_inferred["full"] == 1


def test_implausible_duration_is_nulled_and_reported(db, env):
    res = run_import(db, env)
    db.commit()
    assert _session(db, "2025-01-07").duration_s is None
    assert _session(db, "2025-01-08").duration_s == 3 * 3600 + 59 * 60
    assert _session(db, "2025-01-01").duration_s == 3600
    assert [(d, sec) for d, _, sec in res.duration_corrected] == [
        (datetime(2025, 1, 7, 9), 19 * 3600 + 22 * 60)
    ]
    report = build_report(db, res, datetime(2026, 1, 1))
    assert "Durations corrected (> 4h 0m, stored as NULL): 1" in report
    assert "2025-01-07 09:00:00 Morning Workout: was 19h 22m" in report
    # only the 3h59m session is a (soft) anomaly; the corrected one is not counted twice
    assert res.anomaly_counts["odd durations"] == 1


def test_duration_cap_is_configurable_and_reimport_restores(db, env):
    run_import(db, env)
    db.commit()
    env.max_duration_s = 24 * 3600
    res = run_import(db, env)
    db.commit()
    assert res.duration_corrected == []
    assert _session(db, "2025-01-07").duration_s == 19 * 3600 + 22 * 60
    assert db.get(TrainingSession, session_uuid(datetime(2025, 1, 7, 9))).duration_s is not None


def test_reimport_keeps_a_name_the_user_gave_in_the_app(db, env):
    run_import(db, env)
    db.commit()
    bench = db.scalar(select(Exercise).where(Exercise.slug == "bench_press"))
    v = db.scalar(select(ExerciseVariant).where(ExerciseVariant.exercise_id == bench.id))
    row = db.scalar(select(ExerciseVariant).where(ExerciseVariant.name == "Squat (Barbell)"))
    key, squat = v.key, row.exercise
    v.name = "Flat Press (Barbell)"  # renamed in the app (PATCH /exercises/{id})
    bench.edited_by_user = True
    db.commit()
    run_import(db, env)
    db.commit()
    db.refresh(v)
    assert v.key == key and v.name == "Flat Press (Barbell)"
    # a variant of an exercise the user did not edit still follows the mapping
    row.name = "Squat (renamed by hand)"
    db.commit()
    run_import(db, env)
    db.commit()
    db.refresh(row)
    assert row.name == "Squat (Barbell)" and not squat.edited_by_user
    # an edited exercise whose variant still has the mapping name (muscle edit) resets as usual
    squat.edited_by_user = True
    row.name = "Squat (Barbell) @ stale"
    db.commit()
    run_import(db, env)
    db.commit()
    db.refresh(row)
    assert row.name == "Squat (Barbell)"


def test_inactive_variants_keep_data_but_leave_review_and_anomalies(db, env):
    res = run_import(db, env)
    db.commit()
    shrug = db.scalar(select(ExerciseVariant).where(ExerciseVariant.name == "Shrug (Dumbbell)"))
    assert shrug.active is False
    assert _count_sets(db, shrug.id) == 1  # data kept
    report = build_report(db, res, datetime(2026, 1, 1))
    review = report.split("## Anomalies")[0]
    anomalies = report.split("## Anomalies")[1].split("## Inferred weight ladders")[0]
    assert "| Shrug (Dumbbell)" not in review  # status=auto but inactive: not "to review"
    assert "Shrug" not in anomalies  # its 0 kg set is not a zero-weight anomaly
    assert "Shrug (Dumbbell)" in report  # still in the per-variant summary


def _count_sets(db, variant_id: int) -> int:
    from sqlalchemy import func

    from app.db.models import WorkoutSet

    return db.scalar(
        select(func.count()).select_from(WorkoutSet).where(WorkoutSet.variant_id == variant_id)
    )


def test_mapping_active_column_roundtrip(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text(MAPPING, encoding="utf-8")
    rows = read_mapping(p)
    assert rows["Shrug (Dumbbell)"].active is False and rows["Squat (Barbell)"].active is True
    write_mapping(p, rows)
    assert ",auto,false," in p.read_text(encoding="utf-8")
    assert read_mapping(p)["Shrug (Dumbbell)"].active is False
    # a file from before the column existed: everything is active
    old = tmp_path / "old.csv"
    old.write_text(
        "strong_name,canonical_exercise,variant_name,equipment_type,load_convention,status,notes\n"
        "Plank,plank,Plank,bodyweight,total,auto,\n",
        encoding="utf-8",
    )
    assert read_mapping(old)["Plank"].active is True
