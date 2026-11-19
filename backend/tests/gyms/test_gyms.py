"""Gym periods: resolution, per-gym variants, and clean re-imports after editing gyms.yaml."""

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from apps.fitness.db.models import (
    ExerciseSessionStats,
    ExerciseVariant,
    Gym,
    TrainingSession,
    WorkoutSet,
)
from apps.fitness.gyms.catalog import (
    GymConfigError,
    GymPeriods,
    GymSpec,
    clear_gyms_cache,
    load_gyms,
)
from apps.fitness.importers.strong.importer import ImportOptions, run_import, variant_uuid
from apps.fitness.importers.strong.report import weight_jumps
from apps.fitness.settings import FITNESS_DIR

HEADER = "Date,Workout Name,Duration,Exercise Name,Set Order,Weight,Reps,Distance,Seconds,RPE"
MAPPING = """\
strong_name,canonical_exercise,variant_name,equipment_type,load_convention,status,active,notes
Lat Pulldown (Cable),lat_pulldown,Lat Pulldown (Cable),cable,total,confirmed,true,
Squat (Barbell),squat,Squat (Barbell),barbell,total,confirmed,true,
Pull Up,pull_up,Pull Up,bodyweight,total,confirmed,true,
Shrug (Dumbbell),shrug,Shrug (Dumbbell),dumbbell,per_hand,confirmed,false,no longer done
Leg Curl (Machine),leg_curl,Leg Curl (Machine),machine_stack,total,confirmed,true,
"""
GYMS = """\
gyms:
  - {key: gym1, name: Gym 1}
  - {key: gym2, name: Gym 2, start: 2024-09-30}
  - {key: gym3, name: Gym 3, start: 2026-08-25}
"""


def _csv(rows: list[tuple[str, str, str, float, int]]) -> str:
    """(datetime, workout, exercise, weight, reps) -> Strong CSV, one set per row."""
    lines = [HEADER]
    for when, workout, ex, w, reps in rows:
        lines.append(f'{when},"{workout}",1h,"{ex}",1,{w},{reps},0,0.0,')
    return "\n".join(lines) + "\n"


ROWS = [
    ("2024-01-10 10:00:00", "Upper", "Lat Pulldown (Cable)", 50.0, 10),
    ("2024-01-10 10:00:00", "Upper", "Squat (Barbell)", 100.0, 5),
    ("2024-09-29 10:00:00", "Upper", "Lat Pulldown (Cable)", 60.0, 10),  # last day at Gym 1
    ("2024-09-29 10:00:00", "Upper", "Shrug (Dumbbell)", 40.0, 10),
    ("2024-09-30 10:00:00", "Upper", "Lat Pulldown (Cable)", 40.0, 10),  # first day at Gym 2
    ("2026-08-24 10:00:00", "Upper", "Lat Pulldown (Cable)", 45.0, 10),  # last day at Gym 2
    ("2026-08-25 10:00:00", "Upper", "Lat Pulldown (Cable)", 30.0, 10),  # first day at Gym 3
    ("2026-08-25 10:00:00", "Upper", "Leg Curl (Machine)", 45.0, 12),
]


@pytest.fixture
def env(tmp_path: Path):
    d = tmp_path / "imports"
    d.mkdir()
    (d / "strong.csv").write_text(_csv(ROWS), encoding="utf-8")
    mapping = tmp_path / "strong_mapping.csv"
    mapping.write_text(MAPPING, encoding="utf-8")
    gyms = tmp_path / "gyms.yaml"
    gyms.write_text(GYMS, encoding="utf-8")
    opts = ImportOptions(
        paths=[d],
        mapping_path=mapping,
        catalog_path=FITNESS_DIR / "data" / "catalog" / "exercises.yaml",
        engine_config_path=FITNESS_DIR / "config" / "engine.v2.yaml",
        gyms_path=gyms,
        apply=True,
    )
    return opts, gyms


def _variants(db) -> dict[str, ExerciseVariant]:
    return {
        v.name: v
        for v in db.scalars(select(ExerciseVariant).where(ExerciseVariant.deleted_at.is_(None)))
    }


def _count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def _gym_of_session(db, when: str) -> str:
    s = db.scalar(select(TrainingSession).where(TrainingSession.started_at_local >= when).limit(1))
    return db.get(Gym, s.gym_id).name


# --------------------------------------------------------------------------- GymPeriods


def test_period_boundaries_are_inclusive_start():
    p = load_gyms_from(GYMS)
    assert p.resolve(date(2020, 1, 1)).name == "Gym 1"
    assert p.resolve(date(2024, 9, 29)).name == "Gym 1"
    assert p.resolve(date(2024, 9, 30)).name == "Gym 2"
    assert p.resolve(date(2026, 8, 24)).name == "Gym 2"
    assert p.resolve(date(2026, 8, 25)).name == "Gym 3"
    assert p.resolve(date(2030, 1, 1)).name == "Gym 3"


def test_load_gyms_is_cached_until_the_file_changes(tmp_path):
    path = tmp_path / "gyms.yaml"
    path.write_text(GYMS, encoding="utf-8")
    first = load_gyms(path)
    assert load_gyms(path) is first  # parsed once
    path.write_text(GYMS + "  - {key: gym4, name: Gym 4, start: 2027-01-01}\n", encoding="utf-8")
    second = load_gyms(path)
    assert second is not first and second.latest.name == "Gym 4"
    clear_gyms_cache()
    assert load_gyms(path) is not second


def load_gyms_from(text: str) -> GymPeriods:
    import tempfile

    with tempfile.TemporaryDirectory() as t:
        path = Path(t) / "gyms.yaml"
        path.write_text(text, encoding="utf-8")
        return load_gyms(path)


def test_same_as_merges_by_key_or_name():
    by_key = load_gyms_from(GYMS.replace("start: 2026-08-25}", "start: 2026-08-25, same_as: gym1}"))
    by_name = load_gyms_from(
        GYMS.replace("start: 2026-08-25}", "start: 2026-08-25, same_as: Gym 1}")
    )
    for p in (by_key, by_name):
        assert p.resolve(date(2026, 9, 1)).key == "gym1"
        assert p.resolve(date(2025, 1, 1)).key == "gym2"
        assert [g.key for g in p.canonical_gyms] == ["gym1", "gym2"]
        assert p.period_gym(date(2026, 9, 1)).key == "gym3"  # the period itself is still listed


@pytest.mark.parametrize(
    "specs",
    [
        [],
        [GymSpec("a", "A"), GymSpec("a", "B", date(2024, 1, 1))],  # duplicate key
        [GymSpec("a", "A"), GymSpec("b", "A", date(2024, 1, 1))],  # duplicate name
        [GymSpec("a", "A"), GymSpec("b", "B")],  # second gym without start
        [
            GymSpec("a", "A"),
            GymSpec("b", "B", date(2025, 1, 1)),
            GymSpec("c", "C", date(2024, 1, 1)),
        ],
        [GymSpec("a", "A", same_as="zzz")],
        [GymSpec("a", "A", same_as="b"), GymSpec("b", "B", date(2024, 1, 1), same_as="a")],
    ],
)
def test_invalid_gym_configs(specs):
    with pytest.raises(GymConfigError):
        GymPeriods(specs)


# --------------------------------------------------------------------------- importer


def test_import_assigns_gym_variants_and_sessions(db, env):
    opts, _ = env
    res = run_import(db, opts)
    db.commit()
    assert res.error is None
    assert dict(res.sessions_by_gym) == {"Gym 1": 2, "Gym 2": 2, "Gym 3": 1}
    names = _variants(db)
    assert set(names) == {
        "Lat Pulldown (Cable) @ Gym 1",
        "Lat Pulldown (Cable) @ Gym 2",
        "Lat Pulldown (Cable) @ Gym 3",
        "Leg Curl (Machine) @ Gym 3",
        "Squat (Barbell)",  # barbell/dumbbell/bodyweight are generic
        "Shrug (Dumbbell)",
    }
    assert names["Squat (Barbell)"].gym_id is None
    assert names["Shrug (Dumbbell)"].gym_id is None and names["Shrug (Dumbbell)"].active is False
    assert names["Lat Pulldown (Cable) @ Gym 2"].active is True
    assert _gym_of_session(db, "2024-09-29") == "Gym 1"
    assert _gym_of_session(db, "2024-09-30") == "Gym 2"
    assert _gym_of_session(db, "2026-08-24") == "Gym 2"
    assert _gym_of_session(db, "2026-08-25") == "Gym 3"
    # ladders are per gym variant
    assert names["Lat Pulldown (Cable) @ Gym 1"].weight_ladder == [50.0, 60.0]
    assert names["Lat Pulldown (Cable) @ Gym 2"].weight_ladder == [40.0, 45.0]
    assert names["Lat Pulldown (Cable) @ Gym 3"].weight_ladder == [30.0]
    # the weight jump at the gym change is not reported as an anomaly any more
    assert weight_jumps(db) == []
    gym1_pulldown = names["Lat Pulldown (Cable) @ Gym 1"].id
    assert _count(db, ExerciseSessionStats, ExerciseSessionStats.variant_id == gym1_pulldown) == 2


def test_variant_keys_are_deterministic_across_databases(db, env, tmp_path):
    opts, _ = env
    run_import(db, opts)
    db.commit()
    v = _variants(db)["Lat Pulldown (Cable) @ Gym 2"]
    assert v.key == variant_uuid("Lat Pulldown (Cable)", "gym2")
    assert variant_uuid("Lat Pulldown (Cable)", "gym2") != variant_uuid(
        "Lat Pulldown (Cable)", "gym3"
    )
    assert variant_uuid("Squat (Barbell)", None) == variant_uuid("Squat (Barbell)", None)


def test_reimport_unchanged_gyms_is_idempotent(db, env):
    opts, _ = env
    run_import(db, opts)
    db.commit()
    again = run_import(db, opts)
    db.commit()
    assert again.sessions_unchanged == 5 and again.sets_unchanged == 8
    assert again.sets_created == again.sets_updated == again.sets_moved == 0
    assert again.variants_removed == 0
    assert _count(db, WorkoutSet) == 8 and _count(db, ExerciseVariant) == 6


def test_moving_a_boundary_moves_sets_without_duplicates(db, env):
    opts, gyms = env
    run_import(db, opts)
    db.commit()
    ids_before = {s.id for s in db.scalars(select(WorkoutSet))}
    # Gym 2 actually started a day earlier: the 2024-09-29 session moves from Gym 1 to Gym 2
    gyms.write_text(GYMS.replace("2024-09-30", "2024-09-29"), encoding="utf-8")
    res = run_import(db, opts)
    db.commit()
    assert res.sets_created == 0 and res.sets_moved == 1  # only the Gym-1 pulldown set moved
    assert {s.id for s in db.scalars(select(WorkoutSet))} == ids_before
    assert _count(db, WorkoutSet) == 8
    names = _variants(db)
    assert names["Lat Pulldown (Cable) @ Gym 1"].weight_ladder == [50.0]  # stale rung gone
    assert names["Lat Pulldown (Cable) @ Gym 2"].weight_ladder == [40.0, 45.0, 60.0]
    assert _gym_of_session(db, "2024-09-29") == "Gym 2"
    assert _count(db, ExerciseSessionStats) == 8  # stats fully rebuilt, no leftovers


def test_rename_keeps_variant_identity(db, env):
    opts, gyms = env
    run_import(db, opts)
    db.commit()
    old = _variants(db)["Lat Pulldown (Cable) @ Gym 2"]
    old_id, old_key = old.id, old.key
    gyms.write_text(GYMS.replace("name: Gym 2", "name: Iron Temple"), encoding="utf-8")
    res = run_import(db, opts)
    db.commit()
    assert res.sets_moved == 0 and res.variants_removed == 0 and res.sets_created == 0
    v = _variants(db)["Lat Pulldown (Cable) @ Iron Temple"]
    assert (v.id, v.key) == (old_id, old_key)
    assert db.get(Gym, v.gym_id).key == "gym2"
    assert "Lat Pulldown (Cable) @ Gym 2" not in _variants(db)


def test_same_as_merges_gym3_into_gym1_and_back(db, env):
    opts, gyms = env
    run_import(db, opts)
    db.commit()
    merged = GYMS.replace("start: 2026-08-25}", "start: 2026-08-25, same_as: gym1}")
    gyms.write_text(merged, encoding="utf-8")
    res = run_import(db, opts)
    db.commit()
    assert res.sets_created == 0 and _count(db, WorkoutSet) == 8
    names = _variants(db)
    assert "Lat Pulldown (Cable) @ Gym 3" not in names  # orphan removed ...
    orphan = db.scalar(
        select(ExerciseVariant).where(ExerciseVariant.name == "Lat Pulldown (Cable) @ Gym 3")
    )
    assert orphan is not None and orphan.deleted_at is not None  # ... as a tombstone, not deleted
    assert "Leg Curl (Machine) @ Gym 1" in names
    # the 2026-08-25 pulldown now shares Gym 1's variant and ladder
    assert names["Lat Pulldown (Cable) @ Gym 1"].weight_ladder == [30.0, 50.0, 60.0]
    assert _gym_of_session(db, "2026-08-25") == "Gym 1"
    gym3 = db.scalar(select(Gym).where(Gym.key == "gym3"))
    assert gym3.deleted_at is not None  # retired: nothing refers to it any more
    assert dict(res.sessions_by_gym) == {"Gym 1": 3, "Gym 2": 2}

    # un-merging recreates Gym 3 (same key => revived row) and moves the sets back
    gyms.write_text(GYMS, encoding="utf-8")
    run_import(db, opts)
    db.commit()
    names = _variants(db)
    assert names["Lat Pulldown (Cable) @ Gym 3"].weight_ladder == [30.0]
    assert names["Lat Pulldown (Cable) @ Gym 3"].id == orphan.id  # the tombstone was revived
    assert db.scalar(select(Gym).where(Gym.key == "gym3")).deleted_at is None
    assert _count(db, WorkoutSet) == 8


def test_legacy_single_gym_variants_are_replaced(db, env):
    """A DB imported before gym periods (one 'Main gym', unkeyed variants) migrates cleanly."""
    opts, _ = env
    legacy = ImportOptions(**{**opts.__dict__, "gyms_path": None})
    run_import(db, legacy)
    db.commit()
    for v in db.scalars(select(ExerciseVariant)):
        v.key = None  # as created by the first importer version
    db.commit()
    assert "Lat Pulldown (Cable)" in _variants(db)

    res = run_import(db, opts)
    db.commit()
    assert res.sets_created == 0 and _count(db, WorkoutSet) == 8
    names = _variants(db)
    assert "Lat Pulldown (Cable)" not in names and "Lat Pulldown (Cable) @ Gym 1" in names
    assert names["Squat (Barbell)"].key == variant_uuid("Squat (Barbell)", None)  # adopted
    assert db.scalar(select(Gym).where(Gym.name == "Main gym")).deleted_at is not None


# --------------------------------------------------------------------------- per-date overrides


OVERRIDE_GYMS = GYMS + "overrides:\n  - {date: 2026-08-26, gym: gym2, note: one visit}\n"


def test_override_pins_a_date_to_another_gym():
    p = load_gyms_from(OVERRIDE_GYMS)
    assert p.resolve(date(2026, 8, 25)).name == "Gym 3"
    assert p.resolve(date(2026, 8, 26)).name == "Gym 2"
    assert p.resolve(date(2026, 8, 27)).name == "Gym 3"
    # by name, and merged through same_as
    by_name = load_gyms_from(OVERRIDE_GYMS.replace("gym: gym2", "gym: Gym 2"))
    assert by_name.resolve(date(2026, 8, 26)).key == "gym2"
    merged = load_gyms_from(
        OVERRIDE_GYMS.replace("start: 2026-08-25}", "start: 2026-08-25, same_as: gym2}")
    )
    assert merged.resolve(date(2026, 8, 26)).key == "gym2"


@pytest.mark.parametrize(
    "extra",
    [
        "overrides:\n  - {date: 2026-08-26, gym: nope}\n",
        "overrides:\n  - {gym: gym2}\n",
        "overrides:\n  - {date: 2026-08-26, gym: gym2}\n  - {date: 2026-08-26, gym: gym1}\n",
    ],
)
def test_invalid_overrides(extra):
    with pytest.raises(GymConfigError):
        load_gyms_from(GYMS + extra)


def test_import_honours_override_and_is_idempotent(db, env):
    opts, gyms = env
    gyms.write_text(OVERRIDE_GYMS, encoding="utf-8")
    csv = opts.paths[0] / "strong.csv"
    extra = [
        ("2026-08-26 10:00:00", "Lower", "Lat Pulldown (Cable)", 47.0, 10),  # visit at Gym 2
        ("2026-08-27 10:00:00", "Lower", "Lat Pulldown (Cable)", 32.0, 10),
    ]
    csv.write_text(_csv(ROWS + extra), encoding="utf-8")
    run_import(db, opts)
    db.commit()
    assert _gym_of_session(db, "2026-08-26") == "Gym 2"
    assert _gym_of_session(db, "2026-08-27") == "Gym 3"
    names = _variants(db)
    assert names["Lat Pulldown (Cable) @ Gym 2"].weight_ladder == [40.0, 45.0, 47.0]
    assert names["Lat Pulldown (Cable) @ Gym 3"].weight_ladder == [30.0, 32.0]
    again = run_import(db, opts)
    db.commit()
    assert again.sets_created == again.sets_updated == again.sets_moved == 0
    # dropping the override moves the sets back (no duplicates)
    gyms.write_text(GYMS, encoding="utf-8")
    moved = run_import(db, opts)
    db.commit()
    assert moved.sets_moved == 1
    assert _gym_of_session(db, "2026-08-26") == "Gym 3"


def test_split_gym_with_new_machines_keeps_separate_variants(db, env):
    opts, gyms = env
    gyms.write_text(
        "gyms:\n"
        "  - {key: gym1_old, name: Gym 1 (old machines)}\n"
        "  - {key: gym1, name: Gym 1, start: 2024-06-01}\n"
        "  - {key: gym2, name: Gym 2, start: 2024-09-30}\n"
        "  - {key: gym3, name: Gym 3, start: 2026-08-25}\n",
        encoding="utf-8",
    )
    run_import(db, opts)
    db.commit()
    names = _variants(db)
    assert "Lat Pulldown (Cable) @ Gym 1 (old machines)" in names
    assert "Lat Pulldown (Cable) @ Gym 1" in names  # 2024-09-29 session, new machines
    assert names["Lat Pulldown (Cable) @ Gym 1 (old machines)"].weight_ladder == [50.0]
    assert names["Lat Pulldown (Cable) @ Gym 1"].weight_ladder == [60.0]
    assert _gym_of_session(db, "2024-01-10") == "Gym 1 (old machines)"
