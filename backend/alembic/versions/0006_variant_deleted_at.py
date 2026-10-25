"""exercise_variants.deleted_at (soft delete of orphan variants)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-25 10:00:00.000000

Plain ADD COLUMN (no batch recreate), so the 0005 sync triggers on exercise_variants survive.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.types

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "exercise_variants", sa.Column("deleted_at", app.db.types.UTCDateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("exercise_variants", "deleted_at")  # native DROP COLUMN: triggers survive
