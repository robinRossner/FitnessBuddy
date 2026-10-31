"""progression engine v2: config keys (data only) + retire the v1 stall-reset triggers

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-25 23:30:00.000000

No schema change. An existing database keeps its active config version's content and gains the
v2 progression keys (docs/research/lifting-progression.md, docs/research/running-progression.md,
docs/spec/decisions.md "Progression engine v2") as a derived version: keys it already has keep
their values, except the deload form (sets halved, loads kept) and the long-run cap (max 20 km),
whose meaning changed. A fresh database has none yet and gets them from the engine defaults /
the v6 seed.

The v1 engine's planned -10 % stall resets are NOT carried out: the v2 engine converts every
untrained one into a retest of the old load (with a soft deload week first when >= 3 lifts share
it, `app.engine.data.legacy_windows`). Their open `lift_stall` advisor triggers (payload
`reset_load_kg`) are dismissed here so the coach is not consulted about resets that will not
happen; the engine raises one `multi_lift_stall` trigger instead.
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
revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# frozen copy of the keys config/engine.v6.yaml adds over v5 (app.engine.config defaults at the
# time of this migration); only missing keys are added
NEW = {'strength': {'stall_exposures_2x': 4,
              'stall_min_days': 12,
              'e1rm_progress_pct': 1.0,
              'stall_reset_small_pct': 5,
              'multi_stall_lifts': 3,
              'multi_stall_days': 14,
              'noise_reps': 2,
              'big_miss_reps': 3,
              'rung_min_reps': 8,
              'rep_ceiling_cap': 15,
              'jump_abort_reps': 5,
              'deload': {'sets_rounding': 'up',
                         'days': 7,
                         'min_gap_days': 28,
                         'fatigue_index_pct': 40,
                         'fatigue_weeks': 2,
                         'fatigue_min_exposures': 3,
                         'align_running_step_back': True},
              'censor': {'readiness': ['yellow', 'red'],
                         'hard_run_hours': 24,
                         'hard_run_min_km': 10,
                         'calibration_sessions': 2,
                         'break_days': 10},
              'break_tiers': [{'min_days': 11, 'sets_minus': 1, 'days': 1},
                              {'min_days': 22, 'load_pct': 5, 'sets_minus': 1, 'days': 7},
                              {'min_days': 43, 'load_pct': 10, 'sets_factor': 0.5, 'days': 7}]},
 'running': {'weekly_volume_floor_km': 18,
             'interval_pace_tolerance_pct': 3,
             'interval_fast_flag_pct': 5,
             'volume': {'mean_weeks': 4,
                        'cap_abs_km': 2.0,
                        'single_run_factor': 1.1,
                        'single_run_abs_km': 0.0,
                        'single_run_days': 30,
                        'min_run_km': 3.0},
             'vdot': {'max_step': 0.5,
                      'max_per_4w': 1.0,
                      'green_sessions': 2,
                      'green_window_weeks': 3,
                      'ef_trend_weeks': 4,
                      'ef_trend_min_pct': 1.0,
                      'fails_to_lower': 3,
                      'fail_drop': 0.5,
                      'layoff': [{'min_days': 15, 'pct': 2},
                                 {'min_days': 29, 'pct': 5},
                                 {'min_days': 57, 'pct': 8}]},
             'easy': {'strides': {'reps': 6, 'duration_s': 20, 'recovery_s': 60},
                      'exempt_after_lower': True},
             'intervals': {'rep_ladder_m': [800, 1000, 1200],
                           'max_work_min': 20,
                           'warmup_min_km': 1.0,
                           'cooldown_min_km': 0.5,
                           'pass_rep_share': 0.8,
                           'fade_max_pct': 3,
                           'hr_ceiling_pct_lthr': 1.08,
                           'green_hr_pct_lthr': 1.02,
                           'fails_to_step_back': 2},
             'threshold': {'reps_start': 3,
                           'reps_min': 3,
                           'reps_max': 5,
                           'rep_ladder_m': [1000, 1200, 1600, 2000],
                           'recovery_ladder_s': [90, 75, 60],
                           'max_work_min': 30,
                           'hr_ceiling_pct_lthr': 0.99,
                           'green_hr_pct_lthr': 0.95},
             'race_5k': {'reps_start': 6,
                         'reps_min': 4,
                         'reps_max': 6,
                         'rep_ladder_m': [800, 1000, 1200],
                         'recovery_s': 120,
                         'max_work_min': 20,
                         'hr_ceiling_pct_lthr': 1.08,
                         'green_hr_pct_lthr': 1.02},
             'time_trial': {'distance_m': 5000, 'warmup_km': 2.0, 'cooldown_km': 1.5},
             'quality': {'cycle_start': None,
                         'phases': [{'name': 'threshold',
                                     'weeks': 12,
                                     'sessions': ['threshold']},
                                    {'name': 'threshold_vo2max',
                                     'weeks': 8,
                                     'sessions': ['threshold', 'vo2max']},
                                    {'name': '5k_specific',
                                     'weeks': 6,
                                     'sessions': ['race_5k', 'threshold']},
                                    {'name': 'time_trial',
                                     'weeks': 1,
                                     'sessions': ['time_trial']}],
                         'phase_weeks': {},
                         'no_quality_after_break_days': 15},
             'fourth_run': {'consistent_weeks': 8,
                            'min_runs_per_week': 3,
                            'min_weekly_km': 28,
                            'min_long_run_km': 14},
             'long_run': {'step_back_every': 4,
                          'step_back_pct': 25,
                          'drift_hold_pct': 5,
                          'drift_cut_pct': 8,
                          'drift_cut_by_pct': 10,
                          'drift_min_minutes': 60,
                          'recent_days': 21}},
 'schedule': {'run_makeup_max_shift_days': 1}}

# keys whose meaning changed: set even when present
FORCED = {
    ("strength", "deload", "sets_pct"): 50,
    ("strength", "deload", "load_pct"): 0,
}
LONG_RUN_CAP_MAX = 20


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
    for path, value in FORCED.items():
        node = content
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = value
    lr = content.setdefault("running", {}).setdefault("long_run", {})
    if float(lr.get("cap_km", 21)) > LONG_RUN_CAP_MAX:
        lr["cap_km"] = LONG_RUN_CAP_MAX
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
                "Progression engine v2 (research 2026-10-25): reps-at-load signal, two-strike "
                "gate, rung rule, censored misses, stall ladder, soft deloads; quality cycle, "
                "pace-based intervals, VDOT hysteresis, 4-week-mean volume cap, long-run "
                "step-back + drift gate"
            ),
            diff=diff,
            meta={},
        )
    )


def _retire_v1_stall_triggers(bind) -> None:
    t = sa.table(
        "advisor_triggers",
        sa.column("id", sa.Integer),
        sa.column("type", sa.String),
        sa.column("status", sa.String),
        sa.column("payload", sa.JSON),
    )
    rows = bind.execute(
        sa.select(t.c.id, t.c.payload).where(t.c.type == "lift_stall", t.c.status == "open")
    ).all()
    for rid, payload in rows:
        p = payload if isinstance(payload, dict) else json.loads(payload or "{}")
        if "reset_load_kg" in p:
            bind.execute(t.update().where(t.c.id == rid).values(status="dismissed"))


def upgrade() -> None:
    """Upgrade (data only)."""
    bind = op.get_bind()
    _add_config(bind)
    _retire_v1_stall_triggers(bind)


def downgrade() -> None:
    """Nothing to undo: the derived config version stays (versions are immutable history)."""
