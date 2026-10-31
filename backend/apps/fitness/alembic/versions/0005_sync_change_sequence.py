"""sync change sequence (server_seq + triggers), sets.completed_at

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-25 09:30:00.000000

`GET /api/v1/sync/pull` pages through changes by a monotonic server-side sequence. Every row of
the synced tables carries `server_seq`, stamped by SQLite triggers from the global `sync_seq`
counter on INSERT and on every UPDATE that touches a sync-visible column, so the cursor is right
whichever code path wrote the row (importer, engine, Garmin, API). The client-supplied
`updated_at` is kept for last-write-wins and is unrelated to the cursor.

CAUTION: SQLite batch migrations recreate a table and thereby drop its triggers. Any later
migration that batch-alters one of these tables must re-create them (see `_create_triggers`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import apps.fitness.db.types
from apps import fitness as app  # package `app` moved to `apps.fitness`

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# table -> columns whose change must bump server_seq (None = any column).
# For sets, e1rm_kg / source / fidelity are derived or internal and excluded: a stats rebuild
# must not make every set look changed.
SYNCED: dict[str, list[str] | None] = {
    "gyms": None,
    "exercises": None,
    "exercise_variants": None,
    "sessions": None,
    "body_measurements": None,
    "planned_sessions": None,
    "sets": [
        "session_id",
        "variant_id",
        "exercise_order",
        "block_occurrence",
        "set_index",
        "set_type",
        "weight_kg",
        "reps",
        "duration_s",
        "completed_at",
        "notes",
        "deleted_at",
        "updated_at",
    ],
}

_STAMP = """
  INSERT INTO sync_seq (seq) VALUES (NULL);
  UPDATE {t} SET server_seq = last_insert_rowid() WHERE rowid = NEW.rowid;
"""


def _create_triggers(table: str, watched: list[str] | None) -> None:
    changed = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in watched) if watched else "1"
    op.execute(
        f"CREATE TRIGGER trg_{table}_seq_ins AFTER INSERT ON {table}\n"
        f"BEGIN{_STAMP.format(t=table)}END"
    )
    op.execute(
        f"CREATE TRIGGER trg_{table}_seq_upd AFTER UPDATE ON {table}\n"
        f"WHEN NEW.server_seq IS OLD.server_seq AND ({changed})\n"
        f"BEGIN{_STAMP.format(t=table)}END"
    )


def upgrade() -> None:
    op.create_table(
        "sync_seq",
        sa.Column("seq", sa.Integer(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_sync_seq")),
        sqlite_autoincrement=True,
    )
    op.add_column("sets", sa.Column("completed_at", app.db.types.UTCDateTime(), nullable=True))
    for table in SYNCED:
        op.add_column(table, sa.Column("server_seq", sa.Integer(), nullable=True))
        op.create_index(op.f(f"ix_{table}_server_seq"), table, ["server_seq"], unique=False)

    # Backfill existing rows, oldest change first, then arm the triggers.
    bind = op.get_bind()
    for table in SYNCED:
        ids = [
            r[0] for r in bind.execute(sa.text(f"SELECT id FROM {table} ORDER BY updated_at, id"))
        ]
        for row_id in ids:
            seq = bind.execute(sa.text("INSERT INTO sync_seq DEFAULT VALUES")).lastrowid
            bind.execute(
                sa.text(f"UPDATE {table} SET server_seq = :s WHERE id = :i"),
                {"s": seq, "i": row_id},
            )
    for table, watched in SYNCED.items():
        _create_triggers(table, watched)


def downgrade() -> None:
    for table in SYNCED:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_seq_ins")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_seq_upd")
    for table in SYNCED:
        op.drop_index(op.f(f"ix_{table}_server_seq"), table_name=table)
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("server_seq")
    with op.batch_alter_table("sets", schema=None) as batch_op:
        batch_op.drop_column("completed_at")
    op.drop_table("sync_seq")
