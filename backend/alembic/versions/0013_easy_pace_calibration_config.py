"""easy pace calibration from the athlete's own easy runs (config keys, data only)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-28 12:00:00.000000

No schema change. The active config version gains `running.easy_calibration.*` as a derived
version (docs/spec/decisions.md "Easy pace calibration (2026-10-28)"); keys that already exist keep
their values. A fresh database gets them from the engine defaults.
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
revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW = {
    "running": {
        "easy_calibration": {
            "enabled": True,
            "window_days": 28,
            "min_runs": 3,
            "min_minutes": 25,
            "hr_margin_bpm": 2,
            "max_vdot_gain": 4.0,
            "max_elevation_m_per_km": 25,
        }
    }
}


def _flatten(d, prefix=""):
    if isinstance(d, dict) and d:
        out = {}
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: d}


def _merge_missing(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if k not in dst:
            dst[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and isinstance(dst[k], dict):
            _merge_missing(dst[k], v)


def _add_config(bind) -> None:
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
        return  # fresh database: the seed (and the engine defaults) provide the keys
    content = copy.deepcopy(active.content)
    _merge_missing(content, NEW)
    old, new = _flatten(active.content), _flatten(content)
    diff = {
        k: {"old": old.get(k), "new": new.get(k)}
        for k in sorted(set(old) | set(new))
        if old.get(k) != new.get(k) and k != "version"
    }
    if not diff:
        return
    family = active.version.split(".")[0]
    taken = {
        r[0] for r in bind.execute(sa.select(cv.c.version).where(cv.c.version.like(f"{family}.%")))
    }
    n = 1
    while f"{family}.{n}" in taken:
        n += 1
    label = f"{family}.{n}"
    content["version"] = label
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
            reason=(
                "Easy and long run pace range calibrated from the recent easy runs at HR <= the "
                "easy ceiling (running.easy_calibration: median pace = fast end of the easy "
                "range, never slower than Daniels, at most +max_vdot_gain VDOT; quality paces "
                "unchanged)"
            ),
            diff=diff,
            meta={},
        )
    )


def upgrade() -> None:
    """Upgrade (data only)."""
    _add_config(op.get_bind())


def downgrade() -> None:
    """Nothing to undo: the derived config version stays (versions are immutable history)."""
