"""schedule changes: planned_sessions move markers + lock, coach schedule changes, schedule config

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-25 18:10:00.000000

Adds `moved_from`, `change_reason`, `changed_by` and `schedule_lock` to planned_sessions (the
engine's missed-session policy and approved coach schedule changes keep their placement there),
the `advisor_schedule_changes` table, and a derived engine config version that carries the new
`schedule` section (an existing database keeps its active version's content and gains the
section; a fresh database has none yet and gets the section from the engine defaults / v4 seed).
"""
import copy
import hashlib
import json
from datetime import UTC, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import apps.fitness.db.types
from apps import fitness as app  # package `app` moved to `apps.fitness`


# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# frozen copy of the defaults (app.engine.config.ScheduleConfig) at the time of this migration
SCHEDULE_DEFAULTS = {
    "makeup_priority": ["lower", "intervals", "upper", "long", "easy"],
    "makeup_never": ["easy"],
    "makeup_replaceable": ["easy"],
    "makeup_use_rest_day": True,
    "long_run_makeup_day": "sat",
    "hard_kinds": ["lower", "intervals"],
    "max_hard_per_day": 1,
    "no_consecutive_hard_days": True,
    "max_sessions_per_day": 2,
    "shorten_max_pct": 50,
    "horizon_weeks": 2,
    "kind_load": {"rest": 0, "easy": 1, "upper": 2, "long": 3, "lower": 4, "intervals": 4},
}


def _flatten(d, prefix=""):
    if isinstance(d, dict) and d:
        out = {}
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: d}


def _add_schedule_config(bind) -> None:
    cv = sa.table(
        "config_versions",
        sa.column("id", sa.Integer),
        sa.column("version", sa.String),
        sa.column("content", sa.JSON),
        sa.column("content_hash", sa.String),
        sa.column("source_path", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", app.db.types.UTCDateTime()),
        sa.column("parent_id", sa.Integer),
        sa.column("author", sa.String),
        sa.column("reason", sa.Text),
        sa.column("diff", sa.JSON),
        sa.column("meta", sa.JSON),
    )
    active = bind.execute(sa.select(cv).where(cv.c.is_active.is_(True)).limit(1)).first()
    if active is None:
        return  # fresh database: the seed (and the engine defaults) provide the section
    content = copy.deepcopy(active.content)
    if "schedule" in content:
        return
    content["schedule"] = copy.deepcopy(SCHEDULE_DEFAULTS)
    family = active.version.split(".")[0]
    taken = {r[0] for r in bind.execute(sa.select(cv.c.version).where(cv.c.version.like(f"{family}.%")))}
    n = 1
    while f"{family}.{n}" in taken:
        n += 1
    label = f"{family}.{n}"
    content["version"] = label
    old, new = _flatten(active.content), _flatten(content)
    diff = {
        k: {"old": old.get(k), "new": new.get(k)}
        for k in sorted(set(old) | set(new))
        if old.get(k) != new.get(k) and k != "version"
    }
    bind.execute(cv.update().values(is_active=False))
    bind.execute(
        cv.insert().values(
            version=label,
            content=content,
            content_hash=hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest(),
            source_path=None,
            is_active=True,
            created_at=datetime.now(UTC),
            parent_id=active.id,
            author="engine",
            reason="Missed-session policy and scheduling rules added (schedule section, defaults)",
            diff=diff,
            meta={},
        )
    )


def upgrade() -> None:
    """Upgrade schema."""
    # native ADD COLUMN (no batch copy): the sync-sequence triggers of planned_sessions survive
    op.add_column('planned_sessions', sa.Column('moved_from', sa.Date(), nullable=True))
    op.add_column('planned_sessions', sa.Column('change_reason', sa.Text(), nullable=True))
    op.add_column('planned_sessions', sa.Column('changed_by', sa.String(length=16), nullable=True))
    op.add_column('planned_sessions', sa.Column('schedule_lock', sa.JSON(), nullable=True))

    op.create_table('advisor_schedule_changes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('consultation_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('day', sa.Date(), nullable=True),
    sa.Column('action', sa.String(length=12), nullable=False),
    sa.Column('target', sa.String(length=24), nullable=False),
    sa.Column('to_day', sa.Date(), nullable=True),
    sa.Column('new_kind', sa.String(length=16), nullable=True),
    sa.Column('distance_km', sa.Float(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('risk', sa.String(length=8), nullable=False),
    sa.Column('valid', sa.Boolean(), nullable=False),
    sa.Column('validation_error', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('planned_session_id', sa.String(length=36), nullable=True),
    sa.Column('applied_at', app.db.types.UTCDateTime(), nullable=True),
    sa.ForeignKeyConstraint(['consultation_id'], ['advisor_consultations.id'], name=op.f('fk_advisor_schedule_changes_consultation_id_advisor_consultations')),
    sa.ForeignKeyConstraint(['planned_session_id'], ['planned_sessions.id'], name=op.f('fk_advisor_schedule_changes_planned_session_id_planned_sessions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_advisor_schedule_changes'))
    )
    with op.batch_alter_table('advisor_schedule_changes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_advisor_schedule_changes_consultation_id'), ['consultation_id'], unique=False)

    _add_schedule_config(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (the derived config version stays: versions are immutable history)."""
    with op.batch_alter_table('advisor_schedule_changes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_advisor_schedule_changes_consultation_id'))

    op.drop_table('advisor_schedule_changes')
    op.drop_column('planned_sessions', 'schedule_lock')
    op.drop_column('planned_sessions', 'changed_by')
    op.drop_column('planned_sessions', 'change_reason')
    op.drop_column('planned_sessions', 'moved_from')
