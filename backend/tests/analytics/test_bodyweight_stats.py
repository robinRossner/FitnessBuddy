"""e1RM of bodyweight / assistance variants follows the bodyweight log by session date."""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

from apps.fitness.bodyweight.service import upsert_measurement
from apps.fitness.db.models import ExerciseSessionStats, ExerciseVariant
from apps.fitness.importers.strong.importer import ImportOptions, run_import
from apps.fitness.settings import FITNESS_DIR
from apps.fitness.stats import rebuild_stats

HEADER = "Date,Workout Name,Duration,Exercise Name,Set Order,Weight,Reps,Distance,Seconds,RPE"
MAPPING = """\
strong_name,canonical_exercise,variant_name,equipment_type,load_convention,status,active,notes
Pull Up,pull_up,Pull Up,bodyweight,total,confirmed,true,
Pull Up (Assisted),pull_up,Pull Up (Assisted),machine_stack,assistance,confirmed,true,
Pull Up (Band),pull_up,Pull Up (Band),bodyweight,assistance,confirmed,true,
Crunch,crunch,Crunch,bodyweight,total,confirmed,true,
"""
SETS = [
    ("2024-01-10 10:00:00", "Pull Up", 10.0, 5),
    ("2024-01-10 10:00:00", "Pull Up (Assisted)", 20.0, 8),
    ("2024-01-10 10:00:00", "Pull Up (Band)", 0.0, 10),
    ("2024-01-10 10:00:00", "Crunch", 10.0, 10),
    ("2024-01-10 10:00:00", "Crunch", 0.0, 10),
    ("2025-01-10 10:00:00", "Pull Up", 10.0, 5),
    ("2025-01-10 10:00:00", "Pull Up (Band)", 5.0, 10),
]


@pytest.fixture
def imported(db, tmp_path):
    d = tmp_path / "imports"
    d.mkdir()
    lines = [HEADER]
    lines += [f'{w},"Calisthenics","1h","{ex}",1,{kg},{reps},0,0.0,' for w, ex, kg, reps in SETS]
    (d / "x.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    mapping = tmp_path / "m.csv"
    mapping.write_text(MAPPING, encoding="utf-8")
    opts = ImportOptions(
        paths=[d],
        mapping_path=mapping,
        catalog_path=FITNESS_DIR / "data" / "catalog" / "exercises.yaml",
        engine_config_path=FITNESS_DIR / "config" / "engine.v2.yaml",  # fallback 82 kg
        apply=True,
    )
    run_import(db, opts)
    db.commit()
    return db


def _best(db, variant: str) -> dict[str, float | None]:
    v = db.scalar(select(ExerciseVariant).where(ExerciseVariant.name == variant))
    rows = db.scalars(
        select(ExerciseSessionStats)
        .where(ExerciseSessionStats.variant_id == v.id)
        .order_by(ExerciseSessionStats.session_date)
    )
    return {str(r.session_date): r.best_e1rm_kg for r in rows}


def test_config_fallback_when_no_measurements(imported):
    assert _best(imported, "Pull Up") == {
        "2024-01-10": pytest.approx(85 * (1 + 5 / 30), abs=0.01),
        "2025-01-10": pytest.approx(85 * (1 + 5 / 30), abs=0.01),
    }


def test_e1rm_follows_the_bodyweight_log_by_date(imported):
    db = imported
    for day, kg in ((datetime(2024, 6, 1, 8), 80.0), (datetime(2025, 1, 1, 8), 82.0)):
        upsert_measurement(
            db, id=str(uuid.uuid4()), measured_at=day, weight_kg=kg, tz="Europe/Vienna"
        )
    rebuild_stats(db, bodyweight_kg=82)
    db.commit()
    # first session predates every weigh-in: the earliest one (80) is used; the second uses 82
    pull = _best(db, "Pull Up")
    assert pull["2024-01-10"] == pytest.approx(90 * (1 + 5 / 30), abs=0.01)
    assert pull["2025-01-10"] == pytest.approx(92 * (1 + 5 / 30), abs=0.01)
    assert _best(db, "Pull Up (Assisted)") == {"2024-01-10": pytest.approx(60 * (1 + 8 / 30))}
    band = _best(db, "Pull Up (Band)")
    # band values are band levels (0-5), not kilograms: never an e1RM, whatever the level
    assert band == {"2024-01-10": None, "2025-01-10": None}
    stats = db.scalars(
        select(ExerciseSessionStats)
        .join(ExerciseVariant)
        .where(ExerciseVariant.name == "Pull Up (Band)")
    ).all()
    assert stats and not any(x.is_e1rm_pr or x.is_weight_pr or x.best_e1rm_kg for x in stats)


def test_non_bodyweight_loaded_exercise_uses_added_weight_only(imported):
    assert _best(imported, "Crunch")["2024-01-10"] == pytest.approx(10 * (1 + 10 / 30), abs=0.01)
