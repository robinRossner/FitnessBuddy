"""Migration 0016 (exercises.progression): timed exercises become 'time', the knee raise 'reps',
everything else keeps 'load'; the downgrade drops the column."""

import sqlite3
from contextlib import closing

from alembic import command

from tests.support.db import alembic_config

INSERT = (
    "INSERT INTO exercises (slug, name, primary_muscle, secondary_muscles, movement_pattern,"
    " default_equipment_type, e1rm_meaningful, is_timed, bodyweight_loaded, created_at,"
    " updated_at) VALUES (?, ?, 'abs', '[]', 'x', 'bodyweight', 0, ?, 1,"
    " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
)


def test_progression_backfill_and_downgrade(tmp_path):
    path = tmp_path / "m.db"
    cfg = alembic_config(f"sqlite:///{path}")
    command.upgrade(cfg, "0015")
    with closing(sqlite3.connect(path)) as con:
        for slug, timed in (("squat", 0), ("plank", 1), ("knee_raise", 0), ("user_hold", 1)):
            con.execute(INSERT, (slug, slug, timed))
        con.commit()
    command.upgrade(cfg, "0016")
    with closing(sqlite3.connect(path)) as con:
        got = dict(con.execute("SELECT slug, progression FROM exercises"))
        assert got == {
            "squat": "load", "plank": "time", "knee_raise": "reps", "user_hold": "time",
        }  # fmt: skip
        # a row inserted without the column (older code) gets the default
        con.execute(INSERT, ("new", "new", 0))
        assert con.execute("SELECT progression FROM exercises WHERE slug='new'").fetchone() == (
            "load",
        )
    command.downgrade(cfg, "0015")
    with closing(sqlite3.connect(path)) as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(exercises)")]
        assert "progression" not in cols and "is_timed" in cols
