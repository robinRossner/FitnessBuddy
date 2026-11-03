"""body_measurements.source accepts 'withings'

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-03 14:00:00.000000

`measurement_source` is a VARCHAR + CHECK constraint, and SQLite cannot alter a CHECK in place:
the batch alter recreates `body_measurements`. That drops its server_seq triggers (see 0005's
CAUTION and `models.ServerSeq`), so they are re-created here exactly as 0005 made them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = ("manual", "garmin", "healthkit")
_NEW = (*_OLD, "withings")

_STAMP = """
  INSERT INTO sync_seq (seq) VALUES (NULL);
  UPDATE {t} SET server_seq = last_insert_rowid() WHERE rowid = NEW.rowid;
"""


def _enum(values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(
        *values, name="measurement_source", native_enum=False, create_constraint=True, length=24
    )


def _create_triggers() -> None:
    """The two body_measurements triggers of 0005 `_create_triggers` (watched = any column)."""
    table = "body_measurements"
    op.execute(
        f"CREATE TRIGGER trg_{table}_seq_ins AFTER INSERT ON {table}\n"
        f"BEGIN{_STAMP.format(t=table)}END"
    )
    op.execute(
        f"CREATE TRIGGER trg_{table}_seq_upd AFTER UPDATE ON {table}\n"
        f"WHEN NEW.server_seq IS OLD.server_seq AND (1)\n"
        f"BEGIN{_STAMP.format(t=table)}END"
    )


def _fk_pragma(on: bool) -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f"PRAGMA foreign_keys={'ON' if on else 'OFF'}")


def _retype(old: tuple[str, ...], new: tuple[str, ...]) -> None:
    _fk_pragma(False)
    op.execute("DROP TRIGGER IF EXISTS trg_body_measurements_seq_ins")
    op.execute("DROP TRIGGER IF EXISTS trg_body_measurements_seq_upd")
    with op.batch_alter_table("body_measurements", schema=None, recreate="always") as batch_op:
        batch_op.alter_column(
            "source", existing_type=_enum(old), type_=_enum(new), existing_nullable=False
        )
    _create_triggers()
    _fk_pragma(True)


def upgrade() -> None:
    _retype(_OLD, _NEW)


def downgrade() -> None:
    # Rows from Withings cannot satisfy the old CHECK; they are manual-looking weigh-ins at worst.
    op.execute("UPDATE body_measurements SET source = 'manual' WHERE source = 'withings'")
    _retype(_NEW, _OLD)
