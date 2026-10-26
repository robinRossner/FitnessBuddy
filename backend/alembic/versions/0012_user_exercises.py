"""user-managed exercises: exercise_variants.increment_kg, exercises.edited_by_user

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-26 12:00:00.000000

`increment_kg`: the user's load step for one variant (null = the equipment default / ladder).
`edited_by_user`: the user changed the exercise's muscle in the app, so the catalog sync keeps it.
Plain ADD COLUMN (no batch recreate), so the 0005 sync triggers survive.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("exercise_variants", sa.Column("increment_kg", sa.Float(), nullable=True))
    op.add_column(
        "exercises",
        sa.Column("edited_by_user", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("exercises", "edited_by_user")  # native DROP COLUMN: triggers survive
    op.drop_column("exercise_variants", "increment_kg")
