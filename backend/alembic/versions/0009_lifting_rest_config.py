"""lifting config: the default rest timer (data only)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-25 21:00:00.000000

No schema change. An existing database keeps its active config version's content and gains the
`lifting` section (`rest_default_s: 120`) as a derived version; a fresh database has none yet and
gets the section from the engine defaults / the v5 seed. Per-exercise rest times are
`exercise_overrides.<slug>.rest_s`, written later by the user or the advisor.
"""
import copy
import hashlib
import json
from datetime import UTC, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.db.types


# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# frozen copy of the defaults (app.engine.config.LiftingConfig) at the time of this migration
LIFTING_DEFAULTS = {"rest_default_s": 120}


def _flatten(d, prefix=""):
    if isinstance(d, dict) and d:
        out = {}
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: d}


def _add_lifting_config(bind) -> None:
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
    if "lifting" in content:
        return
    content["lifting"] = copy.deepcopy(LIFTING_DEFAULTS)
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
            reason="Default rest timer added (lifting section, 2:00 for every exercise)",
            diff=diff,
            meta={},
        )
    )


def upgrade() -> None:
    """Upgrade (data only)."""
    _add_lifting_config(op.get_bind())


def downgrade() -> None:
    """Nothing to undo: the derived config version stays (versions are immutable history)."""
