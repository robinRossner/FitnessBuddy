"""Planned sessions per week: lifting and running counts for the stats dashboards.

A week whose `planned_sessions` cover every template day (the planner stores all seven, rest days
included; soft-deleted rows still mark their day, but are not counted) uses the stored sessions.
Any other week, e.g. one with only a carried-over Sunday long run, or none, falls back to the
count in the engine's weekly template (Mon..Sun, `weekly_template`).

A missed (`skipped`) session that was made up (a live session of the same kind has `moved_from` =
its day) counts once: the make-up stands for it, the skipped original is not counted.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.analytics import common
from apps.fitness.db.models import PlannedSession
from apps.fitness.engine.store import config_service as cs
from apps.fitness.settings import get_settings

LIFTING_KINDS = frozenset({"upper", "lower"})
RUN_KINDS = frozenset({"easy_run", "long_run", "intervals"})
DEFAULT_TEMPLATE_LIFTING = 3
DEFAULT_TEMPLATE_RUNS = 3


def _template(db: Session) -> tuple[int, int, int]:
    """(lifting sessions, runs, days) of the weekly template; the defaults if it is empty."""
    cs.ensure_seeded(db, get_settings().engine_config_path)
    cfg, _ = cs.load_active(db)
    kinds = list(cfg.weekly_template.values())
    if not kinds:
        return DEFAULT_TEMPLATE_LIFTING, DEFAULT_TEMPLATE_RUNS, 7
    return (
        sum(k in LIFTING_KINDS for k in kinds),
        sum(k in RUN_KINDS for k in kinds),
        len(kinds),
    )


def planned_counts(db: Session, weeks: list[date]) -> dict[date, tuple[int, int]]:
    """Monday -> (planned lifting sessions, planned runs) for each of the given Mondays."""
    if not weeks:
        return {}
    lo, hi = min(weeks), max(weeks) + timedelta(days=7)
    lifting: Counter[date] = Counter()
    runs: Counter[date] = Counter()
    days: dict[date, set[date]] = {}
    rows = db.execute(
        select(
            PlannedSession.day,
            PlannedSession.kind,
            PlannedSession.deleted_at,
            PlannedSession.status,
        ).where(PlannedSession.day >= lo, PlannedSession.day < hi)
    )
    # (original day, kind) of sessions that a live make-up session took over (it may sit in a
    # later week than the original, so look two weeks past the range)
    made_up = {
        (moved_from, getattr(kind, "value", kind))
        for moved_from, kind in db.execute(
            select(PlannedSession.moved_from, PlannedSession.kind).where(
                PlannedSession.moved_from >= lo,
                PlannedSession.moved_from < hi,
                PlannedSession.day >= lo,
                PlannedSession.day < hi + timedelta(days=14),
                PlannedSession.deleted_at.is_(None),
            )
        )
    }
    for day, kind, deleted_at, status in rows:
        w = common.week_start(day)
        days.setdefault(w, set()).add(day)
        k = getattr(kind, "value", kind)
        if deleted_at is not None:
            continue
        if k in LIFTING_KINDS:
            if getattr(status, "value", status) == "skipped" and (day, k) in made_up:
                continue
            lifting[w] += 1
        elif k in RUN_KINDS:
            runs[w] += 1
    t_lifting, t_runs, t_days = _template(db)
    return {
        w: (lifting[w], runs[w]) if len(days.get(w, ())) >= t_days else (t_lifting, t_runs)
        for w in weeks
    }
