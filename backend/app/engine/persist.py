"""Writing a `WeekPlan` to the database (planned_sessions, decisions_log, advisor_triggers) and
reconciling earlier plans with what actually happened.

Regeneration is idempotent: a planned session has a stable id per (week, template slot), so
re-running updates the same row. Rows the user (or reconcile) already closed (done / skipped /
modified) or deleted are never touched. A session the user had `approved` that changes content is
sent back to `proposed` (approval_mode is manual) with a note.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DailyHealth, PlannedSession, Run, TrainingSession
from app.db.types import iso_z, utcnow
from app.engine.config import EngineConfig
from app.engine.data import planned_id
from app.engine.decisions import add_trigger, log_decision
from app.engine.planner import DayPlan, WeekPlan
from app.engine.rest import annotate_session
from app.sources.garmin.mapping import RUN_TYPES

LIFT_MATCH = {
    "upper": {"upper", "full", "push", "pull", "calisthenics"},
    "lower": {"lower", "legs", "full"},
}


def stored_prescription(day: DayPlan) -> dict[str, Any]:
    return json.loads(
        json.dumps(
            {
                "title": day.title,
                "slot_id": day.slot_id,
                "template_kind": day.template_kind,
                "readiness": day.readiness,
                "adjustments": day.adjustments,
                "notes": day.notes,
                "session": day.prescription,
            },
            default=str,
        )
    )


@dataclass
class PersistResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_closed: int = 0
    reproposed: int = 0
    decisions: int = 0
    triggers: int = 0
    ids: dict[str, str] = field(default_factory=dict)  # slot -> planned id


def persist_week(
    db: Session,
    plan: WeekPlan,
    *,
    config_version_id: int | None,
    gym_id: int | None,
    only_days: set[date] | None = None,
) -> PersistResult:
    """Write the plan. `only_days` limits the sessions written to those dates (a targeted re-run
    after a schedule change); decisions and triggers are always written."""
    res = PersistResult()
    for d in plan.days:
        if only_days is not None and d.day not in only_days:
            continue
        pid = planned_id(plan.week_start, d.slot_id)
        res.ids[d.slot_id] = pid
        row = db.get(PlannedSession, pid)
        presc = stored_prescription(d)
        if row is None:
            db.add(
                PlannedSession(
                    id=pid,
                    day=d.day,
                    kind=d.kind,
                    status="proposed",
                    gym_id=gym_id if d.kind in ("upper", "lower") else None,
                    config_version_id=config_version_id,
                    prescription=presc,
                    moved_from=d.moved_from,
                    change_reason=d.change_reason,
                    changed_by=d.changed_by,
                )
            )
            res.created += 1
            continue
        if row.deleted_at is not None or row.status in ("done", "skipped", "modified"):
            res.skipped_closed += 1
            continue
        marks = (d.moved_from, d.change_reason, d.changed_by)
        changed = (
            row.day != d.day
            or row.kind != d.kind
            or row.prescription != presc
            or (row.moved_from, row.change_reason, row.changed_by) != marks
        )
        if not changed:
            res.unchanged += 1
            continue
        row.day, row.kind, row.prescription = d.day, d.kind, presc
        row.moved_from, row.change_reason, row.changed_by = marks
        row.config_version_id = config_version_id
        row.gym_id = gym_id if d.kind in ("upper", "lower") else None
        if row.status == "approved":
            row.status = "proposed"
            row.approved_at = None
            row.notes = f"re-proposed {utcnow().date()}: plan changed after approval"
            res.reproposed += 1
        res.updated += 1
    db.flush()

    for rec in plan.decisions:
        pid = res.ids.get(rec.slot_id) if rec.slot_id else None
        row = log_decision(
            db,
            kind=rec.kind,
            rule=rec.rule,
            inputs=rec.inputs,
            output=rec.output,
            rationale=rec.rationale,
            subject_type=rec.subject_type,
            subject_id=rec.subject_id,
            config_version_id=config_version_id,
            planned_session_id=pid,
        )
        res.decisions += row is not None
    for t in plan.triggers:
        row = add_trigger(
            db,
            type=t.type,
            dedupe_key=t.dedupe_key,
            payload=t.payload,
            subject_type=t.subject_type,
            subject_id=t.subject_id,
            day=t.day,
        )
        res.triggers += row is not None
    write_readiness_colors(db, plan.readiness_history)
    return res


def write_readiness_colors(db: Session, colors: dict[date, str]) -> None:
    """Keep daily_health.readiness_color (engine output) in sync."""
    if not colors:
        return
    rows = db.scalars(
        select(DailyHealth).where(DailyHealth.source == "garmin", DailyHealth.day.in_(list(colors)))
    )
    for r in rows:
        if r.readiness_color != colors[r.day]:
            r.readiness_color = colors[r.day]


def reconcile(db: Session, today: date) -> dict[str, int]:
    return reconcile_detail(db, today)[0]


def reconcile_detail(db: Session, today: date) -> tuple[dict[str, int], list[PlannedSession]]:
    """Close earlier proposals: done (matching activity found, linked) or skipped (day passed).

    Lifting: a session of a matching kind on that date; runs: a running activity on that date;
    rest days are simply done once past. Today's unmatched sessions stay open.

    Returns the counts and the sessions that were marked skipped just now (the missed ones the
    make-up policy, `makeup.py`, has to decide about). A session that was skipped earlier and now
    matches an activity (late Strong export) is healed, and a make-up made for it is cancelled.
    """
    counts = {"done": 0, "skipped": 0}
    newly_skipped: list[PlannedSession] = []
    healed: list[str] = []
    rows = db.scalars(
        select(PlannedSession).where(
            PlannedSession.deleted_at.is_(None),
            PlannedSession.status.in_(["proposed", "approved", "skipped"]),
            PlannedSession.day <= today,
            # Strong exports arrive late: re-check what was skipped recently ("self-healing")
            PlannedSession.day >= today - timedelta(days=14),
        )
    )
    for p in rows:
        start = datetime.combine(p.day, datetime.min.time())
        end = start + timedelta(days=1)
        kind = p.kind
        was_skipped = p.status == "skipped"
        if kind in LIFT_MATCH:
            hit = db.scalars(
                select(TrainingSession)
                .where(
                    TrainingSession.deleted_at.is_(None),
                    TrainingSession.started_at_local >= start,
                    TrainingSession.started_at_local < end,
                )
                .order_by(TrainingSession.started_at_local)
            ).all()
            hit = [s for s in hit if s.session_kind in LIFT_MATCH[kind]]
            if hit:
                for s in hit:
                    s.planned_session_id = p.id
                p.status = "done"
                counts["done"] += 1
                if was_skipped:
                    healed.append(p.id)
        elif kind in ("easy_run", "intervals", "long_run"):
            runs = db.scalars(
                select(Run).where(
                    Run.deleted_at.is_(None),
                    Run.activity_type.in_(RUN_TYPES),
                    Run.started_at_local >= start,
                    Run.started_at_local < end,
                )
            ).all()
            if runs:
                for r in runs:
                    r.planned_session_id = p.id
                p.status = "done"
                counts["done"] += 1
                if was_skipped:
                    healed.append(p.id)
        elif kind == "rest" and p.day < today:
            p.status = "done"
            counts["done"] += 1
            continue
        if p.status in ("proposed", "approved") and p.day < today:
            p.status = "skipped"
            counts["skipped"] += 1
            newly_skipped.append(p)
        elif p.status == "skipped" and p.day < today:
            pass  # still unmatched: stays skipped
    db.flush()
    if healed:
        cancel_makeups(db, healed, today)
    return counts, newly_skipped


def cancel_makeups(db: Session, healed_ids: list[str], today: date) -> int:
    """The missed session turned out to have happened: undo the make-up placed for it."""
    from app.engine.schedule_edit import clear_lock

    n = 0
    rows = db.scalars(
        select(PlannedSession).where(
            PlannedSession.deleted_at.is_(None),
            PlannedSession.status.in_(["proposed", "approved"]),
            PlannedSession.day >= today,
            PlannedSession.schedule_lock.is_not(None),
        )
    )
    for r in rows:
        if (r.schedule_lock or {}).get("makeup_for") in healed_ids:
            log_decision(
                db,
                kind="scheduling",
                rule="makeup:cancelled",
                inputs={"healed_session": r.schedule_lock["makeup_for"]},
                output={"action": "cancelled", "slot": r.id, "day": r.day.isoformat()},
                rationale=(
                    f"The missed {r.kind} was done after all (late import): the make-up on "
                    f"{r.day:%a} is cancelled and the template slot returns"
                ),
                subject_type="planned_session",
                subject_id=r.id,
                planned_session_id=r.id,
            )
            clear_lock(r)
            n += 1
    db.flush()
    return n


ADAPTIVE_STATUSES = ("proposed", "approved", "preview")


def adaptive_fields(kind: str, status: str | None, prescription: dict | None = None) -> dict:
    """`adaptive` / `adapts_on` of a plan item: which open sessions will still change, and why.

    Open run sessions and open lifting targets are recomputed on every engine run (from the last
    quality session / long run, the training phase and VDOT, the lifts' reps at their loads,
    censored misses, the stall ladder and deload weeks, today's readiness, the weekly volume
    cap, the 24 h rule, missed sessions). Done, skipped and rest sessions are fixed."""
    if status not in ADAPTIVE_STATUSES or kind == "rest":
        return {"adaptive": False, "adapts_on": []}
    if kind == "intervals":
        on = [
            "pace of your last quality session",
            "training phase (quality cycle)",
            "VDOT updates",
            "today's readiness",
            "weekly volume cap",
            "recovery after lower-body days",
        ]
    elif kind == "long_run":
        on = [
            "your longest recent long run",
            "HR drift of the last long run",
            "step-back weeks",
            "your recent easy-run paces",
            "today's readiness",
            "weekly and single-run volume caps",
        ]
    elif kind == "easy_run":
        on = [
            "your recent easy-run paces",
            "today's readiness",
            "weekly and single-run volume caps",
        ]
    else:  # upper | lower
        on = [
            "your reps at each load",
            "today's readiness",
            "censored misses (readiness, hard runs, breaks)",
            "stall ladder and deload weeks",
        ]
    return {"adaptive": True, "adapts_on": on}


def planned_to_dict(p: PlannedSession, cfg: EngineConfig | None = None) -> dict[str, Any]:
    """API JSON of a planned session. With the active `cfg`, every lifting exercise gets its
    resolved `rest_s` (read time, so a changed rest time also reaches sessions already planned)."""
    pr = p.prescription or {}
    return {
        "id": p.id,
        "date": p.day.isoformat(),
        "kind": p.kind,
        "status": p.status,
        **adaptive_fields(p.kind, p.status, pr),
        "moved_from": p.moved_from.isoformat() if p.moved_from else None,
        "change_reason": p.change_reason,
        "title": pr.get("title"),
        "slot_id": pr.get("slot_id"),
        "template_kind": pr.get("template_kind"),
        "readiness": pr.get("readiness"),
        "adjustments": pr.get("adjustments", []),
        "notes": pr.get("notes", []),
        "session": annotate_session(pr.get("session"), cfg) if cfg else pr.get("session"),
        "config_version_id": p.config_version_id,
        "updated_at": iso_z(p.updated_at),
    }


__all__ = [
    "PersistResult",
    "adaptive_fields",
    "cancel_makeups",
    "persist_week",
    "planned_to_dict",
    "reconcile",
    "reconcile_detail",
    "utcnow",
]
