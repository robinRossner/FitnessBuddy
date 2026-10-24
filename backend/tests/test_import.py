import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import (
    ConfigVersion,
    ExerciseNameMapping,
    ExerciseSessionStats,
    ExerciseVariant,
    Gym,
    ImportRun,
    TrainingSession,
    WorkoutSet,
)
from app.db.types import Fidelity, SessionKind, SetType
from app.importers.strong.importer import run_import, session_uuid, set_uuid
from app.importers.strong.report import build_report

FIX = Path(__file__).parent / "fixtures"


def _count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def _live_sets(db):
    return _count(db, WorkoutSet, WorkoutSet.deleted_at.is_(None))


def test_import_basic_counts(db, options):
    res = run_import(db, options)
    db.commit()
    assert res.error is None
    assert res.rows_read == 19
    assert res.unmapped_names == {"Mystery Exercise": 1}
    assert res.skip_reasons == {"exercise unmapped": 1}
    assert res.rows_imported == 18
    assert res.sessions_total == res.sessions_created == 4
    assert _count(db, TrainingSession) == 4
    assert _live_sets(db) == 18
    assert {d.isoformat(): f for d, f in res.dst_flags} == {
        "2024-03-31T02:30:00": "nonexistent",
        "2024-10-27T02:30:00": "ambiguous",
    }
    assert _count(db, Gym) == 1 and db.scalar(select(Gym.name)) == "Main gym"
    assert db.scalar(select(ConfigVersion.version)) == "v2"
    assert _count(db, ExerciseNameMapping) == 9
    assert _count(db, ImportRun) == 1


def test_session_and_set_fields(db, options):
    run_import(db, options)
    db.commit()
    s = db.get(TrainingSession, session_uuid(datetime(2024, 10, 27, 2, 30)))
    assert s.session_kind is SessionKind.LOWER and s.duration_s == 2700
    assert s.started_at_utc == datetime(2024, 10, 27, 0, 30, tzinfo=UTC)
    assert s.tz == "Europe/Vienna" and s.started_at_local == datetime(2024, 10, 27, 2, 30)
    squat = db.scalars(
        select(WorkoutSet).where(WorkoutSet.session_id == s.id).order_by(WorkoutSet.set_index)
    ).all()
    types = [w.set_type for w in squat if w.source_exercise_name == "Squat (Barbell)"]
    assert types == [SetType.WARMUP, SetType.WORKING, SetType.WORKING, SetType.DROP]
    evening = db.get(TrainingSession, session_uuid(datetime(2024, 11, 2, 18)))
    assert evening.fidelity is Fidelity.LOW  # contains a cardio row
    plank = db.get(WorkoutSet, set_uuid(datetime(2024, 3, 31, 2, 30), "Plank", 1, 1))
    assert plank.reps is None and plank.duration_s == 60.0 and plank.e1rm_kg is None


def test_duplicate_blocks_get_distinct_ids(db, options):
    run_import(db, options)
    db.commit()
    sid = session_uuid(datetime(2024, 3, 31, 2, 30))
    pulls = db.scalars(
        select(WorkoutSet).where(
            WorkoutSet.session_id == sid, WorkoutSet.source_exercise_name == "Pull Up"
        )
    ).all()
    assert len(pulls) == 4
    assert sorted((p.block_occurrence, p.set_index) for p in pulls) == [
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 2),
    ]


def test_reimport_is_idempotent(db, options):
    first = run_import(db, options)
    db.commit()
    second = run_import(db, options)
    db.commit()
    assert second.sessions_created == 0 and second.sets_created == 0
    assert second.sessions_unchanged == first.sessions_created
    assert second.sets_unchanged == first.sets_created
    assert second.sets_updated == 0 and second.sets_tombstoned == 0
    assert _count(db, TrainingSession) == 4
    assert _count(db, WorkoutSet) == 18
    assert _count(db, ExerciseVariant) == 7  # only variants with sets (created on first use)
    assert _count(db, ImportRun) == 2


def test_dry_run_rollback_writes_nothing(db, options):
    options.apply = False
    run_import(db, options)
    db.rollback()
    assert _count(db, TrainingSession) == 0
    assert _count(db, WorkoutSet) == 0


def test_overlapping_exports_newest_wins(db, options, imports_dir):
    # an older export that also has an extra workout; a newer one with a corrected set
    # and one set removed from the Lower workout
    base = (FIX / "strong_basic.csv").read_text(encoding="utf-8").splitlines()
    old = [*base[:1], "2024-01-05 10:00:00,Upper,1h,Squat (Barbell),1,70.0,5.0,0,0.0,"]
    old += [line for line in base[1:] if not line.startswith("2024-11-02")]
    (imports_dir / "export_old.csv").write_text("\n".join(old) + "\n", encoding="utf-8")
    newer = [
        line.replace('"Lat Pulldown (Cable)",2,55.0', '"Lat Pulldown (Cable)",2,57.5')
        for line in base
    ]
    newer = [*newer, "2024-12-01 10:00:00,Upper,1h,Squat (Barbell),1,90.0,5.0,0,0.0,"]
    newer = [line for line in newer if '"Squat (Barbell)",D,' not in line]
    (imports_dir / "export_a.csv").unlink()
    (imports_dir / "export_new.csv").write_text("\n".join(newer) + "\n", encoding="utf-8")
    os.utime(imports_dir / "export_old.csv", (1, 1))

    res = run_import(db, options)
    db.commit()
    assert res.sessions_total == 6  # 4 shared + 1 only-old + 1 only-new
    assert res.overlapping_workouts == 2  # the two pre-November workouts are in both files
    assert res.overlapping_differing == 1  # Lower differs
    assert res.skip_reasons["superseded by newer export"] == 8 + 6
    lower = session_uuid(datetime(2024, 10, 27, 2, 30))
    lat = db.get(WorkoutSet, set_uuid(datetime(2024, 10, 27, 2, 30), "Lat Pulldown (Cable)", 1, 2))
    assert lat.weight_kg == 57.5
    assert _count(db, WorkoutSet, WorkoutSet.session_id == lower) == 5


def test_reimport_replaces_session_sets_from_newer_export(db, options, imports_dir):
    run_import(db, options)
    db.commit()
    base = (FIX / "strong_basic.csv").read_text(encoding="utf-8").splitlines()
    newer = [line for line in base if '"Squat (Barbell)",D,' not in line]
    newer = [*newer, "2025-01-01 10:00:00,Upper,1h,Squat (Barbell),1,90.0,5.0,0,0.0,"]
    path = imports_dir / "export_b.csv"
    path.write_text("\n".join(newer) + "\n", encoding="utf-8")
    shutil.copystat(imports_dir / "export_a.csv", path)

    res = run_import(db, options)
    db.commit()
    assert res.sets_tombstoned == 1  # the drop set no longer in the newest export
    assert res.sessions_created == 1 and res.sessions_updated == 1
    drop = db.get(WorkoutSet, set_uuid(datetime(2024, 10, 27, 2, 30), "Squat (Barbell)", 1, 4))
    assert drop.deleted_at is not None


def test_stats_and_ladder(db, options):
    run_import(db, options)
    db.commit()
    squat = db.scalar(select(ExerciseVariant).where(ExerciseVariant.name == "Squat (Barbell)"))
    stats = db.scalars(
        select(ExerciseSessionStats)
        .where(ExerciseSessionStats.variant_id == squat.id)
        .order_by(ExerciseSessionStats.session_date)
    ).all()
    assert [round(s.best_e1rm_kg, 2) for s in stats] == [93.33, 99.17]
    assert stats[0].working_set_count == 3 and stats[0].set_count == 4
    assert stats[0].total_volume_kg == 80 * 5 * 2 + 60 * 8  # warm-up excluded, drop included
    assert stats[1].is_e1rm_pr and stats[1].is_weight_pr and not stats[0].is_e1rm_pr
    lat = db.scalar(select(ExerciseVariant).where(ExerciseVariant.name == "Lat Pulldown (Cable)"))
    assert lat.weight_ladder == [50.0, 55.0, 60.0] and lat.ladder_inferred
    assert lat.gym_id is not None and squat.gym_id is None  # cable is gym-specific
    pull = db.scalar(select(ExerciseVariant).where(ExerciseVariant.name == "Pull Up"))
    pull_stats = db.scalar(
        select(ExerciseSessionStats).where(ExerciseSessionStats.variant_id == pull.id)
    )
    # engine config v2 falls back to 82 kg bodyweight: (82 + 10 added) * (1 + 5/30)
    assert pull_stats.e1rm_basis == "total"
    assert pull_stats.best_e1rm_kg == round(85 * (1 + 5 / 30), 2)


def test_user_edited_ladder_is_kept(db, options):
    run_import(db, options)
    db.commit()
    lat = db.scalar(select(ExerciseVariant).where(ExerciseVariant.name == "Lat Pulldown (Cable)"))
    lat.weight_ladder, lat.ladder_inferred = [50.0, 52.5, 55.0], False
    db.commit()
    run_import(db, options)
    db.commit()
    db.refresh(lat)
    assert lat.weight_ladder == [50.0, 52.5, 55.0]


def test_mapping_errors_abort(db, options, mapping_path):
    mapping_path.write_text(
        mapping_path.read_text().replace("pull_up,Pull Up", "pulll_up,Pull Up"), encoding="utf-8"
    )
    res = run_import(db, options)
    assert res.error and any("pulll_up" in e for e in res.mapping_errors)
    db.rollback()
    assert _count(db, TrainingSession) == 0


def test_report_renders(db, options):
    res = run_import(db, options)
    text = build_report(db, res, datetime(2024, 12, 1, 12, 0))
    for needle in (
        "## Summary",
        "Mystery Exercise",
        "Dumbbell weights",
        "nonexistent",
        "Days with more than one workout: 1",
        "Timed holds",
        "Squat (Barbell)",
    ):
        assert needle in text
