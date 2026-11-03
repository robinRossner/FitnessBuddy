"""Per-exercise progression mode: load | reps | time

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-03 12:00:00.000000

`exercises.progression` (NOT NULL, default 'load'). Backfill: timed exercises ('is_timed') are
'time'; the captain's chair knee raise (catalog slug `knee_raise`) is 'reps' (bodyweight, never a
weight step). Every other row keeps 'load', the behaviour before this column existed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "exercises",
        sa.Column("progression", sa.String(length=8), nullable=False, server_default="load"),
    )
    op.execute("UPDATE exercises SET progression = 'time' WHERE is_timed = 1")
    op.execute("UPDATE exercises SET progression = 'reps' WHERE slug = 'knee_raise' AND is_timed = 0")


def downgrade() -> None:
    op.drop_column("exercises", "progression")  # native DROP COLUMN: triggers survive
