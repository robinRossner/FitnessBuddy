"""Migration 0017: `withings` is a valid source and the sync triggers survive the table rebuild."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command

from tests.support.db import alembic_config

TRIGGERS = {"trg_body_measurements_seq_ins", "trg_body_measurements_seq_upd"}
INSERT = (
    "INSERT INTO body_measurements (id, measured_at, day, weight_kg, source, created_at, "
    "updated_at) VALUES (?, '2026-11-02 05:30:00', '2026-11-02', 80.0, ?, "
    "'2026-11-02 05:30:00', '2026-11-02 05:30:00')"
)


def triggers(con: sqlite3.Connection) -> set[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='body_measurements'"
    )
    return {r[0] for r in rows}


def test_head_has_the_triggers_and_accepts_withings(template_db: Path, tmp_path: Path):
    path = tmp_path / "m.db"
    path.write_bytes(template_db.read_bytes())
    with closing(sqlite3.connect(path)) as con:
        assert triggers(con) == TRIGGERS
        con.execute(INSERT, ("a", "withings"))
        con.execute(INSERT, ("b", "manual"))
        seqs = dict(con.execute("SELECT id, server_seq FROM body_measurements"))
        assert seqs["a"] is not None and seqs["b"] is not None and seqs["a"] != seqs["b"]
        before = seqs["a"]
        con.execute("UPDATE body_measurements SET weight_kg = 81 WHERE id = 'a'")
        (after,) = con.execute("SELECT server_seq FROM body_measurements WHERE id='a'").fetchone()
        assert after > before  # the update trigger still bumps the sequence
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(INSERT, ("c", "fitbit"))


def test_upgrade_from_0016_keeps_rows_and_recreates_triggers_then_downgrades(tmp_path: Path):
    path = tmp_path / "up.db"
    cfg = alembic_config(f"sqlite:///{path}")
    command.upgrade(cfg, "0016")
    with closing(sqlite3.connect(path)) as con:
        con.execute(INSERT, ("old", "garmin"))
        con.commit()
        (seq,) = con.execute("SELECT server_seq FROM body_measurements").fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(INSERT, ("w", "withings"))

    command.upgrade(cfg, "0017")
    with closing(sqlite3.connect(path)) as con:
        assert triggers(con) == TRIGGERS
        assert con.execute(
            "SELECT server_seq FROM body_measurements WHERE id='old'"
        ).fetchone() == (seq,)
        con.execute(INSERT, ("w", "withings"))
        con.commit()
        (wseq,) = con.execute("SELECT server_seq FROM body_measurements WHERE id='w'").fetchone()
        assert wseq is not None and wseq > seq
        indexes = {r[1] for r in con.execute("PRAGMA index_list(body_measurements)")}
        assert "ix_body_measurements_server_seq" in indexes

    command.downgrade(cfg, "0016")
    with closing(sqlite3.connect(path)) as con:
        assert triggers(con) == TRIGGERS
        assert dict(con.execute("SELECT id, source FROM body_measurements")) == {
            "old": "garmin",
            "w": "manual",
        }
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(INSERT, ("x", "withings"))
