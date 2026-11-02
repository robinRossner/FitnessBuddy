"""Indexes on foreign keys that are filtered or joined on

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-02 12:00:00.000000

`sessions.gym_id`, `sessions.planned_session_id` and the two FKs of `exercise_name_mappings`
(SQLite does not index foreign keys by itself). Names follow the models' naming convention
(`ix_<table>_<column>`), so `alembic check` sees no difference.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEXES = [
    ("sessions", "gym_id"),
    ("sessions", "planned_session_id"),
    ("exercise_name_mappings", "exercise_id"),
    ("exercise_name_mappings", "variant_id"),
]


def upgrade() -> None:
    for table, column in INDEXES:
        op.create_index(f"ix_{table}_{column}", table, [column], unique=False)


def downgrade() -> None:
    for table, column in reversed(INDEXES):
        op.drop_index(f"ix_{table}_{column}", table_name=table)
