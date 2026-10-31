"""Database side of schedule changes: load the layout around a week, and write an `Edit` to a
planned session (day / kind / distance cap / skip) together with the lock the planner keeps on
every re-run (`planned_sessions.schedule_lock`, see `scheduling.SlotLock`).

Used by the missed-session policy (`makeup.py`) and by approved coach schedule changes
(`apps/fitness/advisor/service.py`). The rules themselves are pure and live in `schedule_rules.py`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.db.models import PlannedSession
from apps.fitness.engine import data as D
from apps.fitness.engine import running as R
from apps.fitness.engine.config import EngineConfig
from apps.fitness.engine.schedule_rules import Edit, Layout, Sess, apply_edits

WINDOW_BEFORE, WINDOW_AFTER = 3, 8  # days around a week that count for hard-day neighbours


def prescribed_km(p: PlannedSession) -> float | None:
    sess = (p.prescription or {}).get("session") or {}
    km = sess.get("distance_km", sess.get("total_km"))
    return float(km) if isinstance(km, int | float) else None


def sess_from_row(p: PlannedSession) -> Sess:
    lock = p.schedule_lock or {}
    return Sess(
        id=p.id,
        day=p.day,
        kind=p.kind,
        status=p.status,
        km=prescribed_km(p),
        locked=bool(lock),
        makeup_for=lock.get("makeup_for"),
    )


def load_rows(db: Session, week_start: date) -> list[PlannedSession]:
    return list(
        db.scalars(
            select(PlannedSession)
            .where(
                PlannedSession.deleted_at.is_(None),
                PlannedSession.day >= week_start - timedelta(days=WINDOW_BEFORE),
                PlannedSession.day <= week_start + timedelta(days=WINDOW_AFTER),
            )
            .order_by(PlannedSession.day, PlannedSession.kind)
        )
    )


def load_layout(db: Session, week_start: date, today: date) -> Layout:
    rows = load_rows(db, week_start)
    lower = D.lower_days(db, week_start - timedelta(days=WINDOW_BEFORE), today)
    return Layout([sess_from_row(r) for r in rows], lower)


def volume_room(db: Session, cfg: EngineConfig, today: date, week_start: date) -> float | None:
    """Weekly run cap minus the km already run this week (None = no cap yet)."""
    rc = cfg.running_cfg
    runs = D.load_runs(db, today - timedelta(weeks=8), today)
    weekly = R.weekly_km(runs)
    cap = R.volume_cap_km(
        weekly,
        week_start,
        rc.weekly_volume_cap_pct,
        weeks_avg=rc.volume.mean_weeks,
        abs_km=rc.volume.cap_abs_km,
    )
    if cap is None:
        return None
    done = sum(r.km for r in runs if week_start <= r.day <= today)
    return round(max(cap - done, 0.0), 2)


def write_edit(
    row: PlannedSession,
    e: Edit,
    *,
    source: str,
    action: str,
    consultation_id: int | None = None,
) -> None:
    """Apply one edit to the row. A skip closes the session; anything else writes the lock the
    planner honours (the next engine run keeps the move and only recomputes the content)."""
    if e.skip:
        row.status = "skipped"
        row.change_reason = e.reason or row.change_reason
        row.changed_by = source
        return
    old_day = row.day
    lock: dict[str, Any] = dict(row.schedule_lock or {})
    if e.day is not None:
        row.day = e.day
        if e.moved_from is None and e.day != old_day and row.moved_from is None:
            row.moved_from = old_day
    if e.moved_from is not None:
        row.moved_from = e.moved_from
    if e.kind is not None and e.kind != row.kind:
        row.kind = e.kind
        row.gym_id = row.gym_id if e.kind in ("upper", "lower") else None
        if row.status == "approved":
            row.status, row.approved_at = "proposed", None
    lock.update(day=row.day.isoformat(), source=source, action=action)
    if e.kind is not None:  # only a deliberate kind change is kept; readiness conversions are not
        lock["kind"] = e.kind
    if e.distance_km is not None:
        lock["distance_km"] = e.distance_km
    if e.makeup_for:
        lock["makeup_for"] = e.makeup_for
    if consultation_id is not None:
        lock["consultation_id"] = consultation_id
    row.schedule_lock = lock
    row.change_reason = e.reason or row.change_reason
    row.changed_by = source


def clear_lock(row: PlannedSession) -> None:
    """Back to the template: the next engine run puts the session where the template says."""
    row.schedule_lock = None
    row.moved_from = None
    row.change_reason = None
    row.changed_by = None


__all__ = [
    "Edit",
    "apply_edits",
    "clear_lock",
    "load_layout",
    "load_rows",
    "sess_from_row",
    "volume_room",
    "write_edit",
]
