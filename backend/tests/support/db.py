"""Alembic helpers for tests: the template database and the migration tests (which step through
intermediate revisions, so they cannot start from the migrated template)."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from alembic import command
from alembic.config import Config

from apps.fitness.settings import FITNESS_DIR


def alembic_config(url: str) -> Config:
    """Alembic config of the fitness app pointed at `url` (for command.upgrade / downgrade)."""
    cfg = Config(str(FITNESS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(FITNESS_DIR / "alembic"))
    cfg.attributes["url"] = url
    return cfg


def migrate_template(path: Path) -> Path:
    """Migrate a new SQLite file at `path` to head and fold the WAL back into it, so copying the
    main file alone gives a complete database."""
    command.upgrade(alembic_config(f"sqlite:///{path}"), "head")  # env.py disposes its engine
    with closing(sqlite3.connect(path)) as con:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        assert not sidecar.exists() or sidecar.stat().st_size == 0, sidecar
    return path
