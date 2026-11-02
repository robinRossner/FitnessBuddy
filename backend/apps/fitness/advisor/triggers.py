"""Engine triggers -> consultations: describe open triggers for the model, pick the worthwhile
ones, and run the daily trigger batch and the Sunday weekly review (both within the budget).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.advisor._common import (
    COVER_DAYS,
    PRIORITY,
    TRIGGER_MAP,
    WEEKLY_REVIEW,
    advisor_config,
    user_today,
)
from apps.fitness.advisor.budget import _weekly_done, budget_allows
from apps.fitness.advisor.consultations import create_consultation, execute
from apps.fitness.db.models import (
    AdvisorConsultation,
    AdvisorTrigger,
)
from apps.fitness.db.types import utcnow
from apps.fitness.engine import schedule_rules as SR
from apps.fitness.engine import service as engine_service
from apps.fitness.settings import Settings, get_settings

log = logging.getLogger(__name__)


def _compact(payload: dict[str, Any], limit: int = 400) -> dict[str, Any]:
    text = json.dumps(payload, sort_keys=True, default=str)
    if len(text) <= limit:
        return payload
    return {k: v for k, v in list(payload.items())[:6] if len(json.dumps(v, default=str)) < 120}


def _describe(row: AdvisorTrigger) -> tuple[str, str, dict[str, Any]]:
    """(title, detail, ref) of one engine trigger."""
    p = row.payload or {}
    t = row.type
    week = engine_service.monday(row.day).isoformat() if row.day else None
    if t == "lift_stall":
        ask = {
            "rep_range_or_variation": " The engine already tried a -5% reset: propose a rep "
            "range change or a close variation.",
            "smaller_increment_or_variation": " The next load step is too big a jump: a smaller "
            "increment or a variation is needed.",
            "ladder_exhausted": " The stall ladder is used up: decide on a variation, the rep "
            "range or the sets.",
        }.get(str(p.get("proposal")), "")
        return (
            f"Stalled lift: {p.get('variant', 'lift')}",
            str(p.get("reason") or "No progress over the stall window.") + ask,
            {"kind": "lift", "id": str(p.get("variant_id") or row.subject_id)},
        )
    if t == "multi_lift_stall":
        lifts = p.get("lifts") or []
        win = p.get("window") or {}
        return (
            f"{len(lifts)} lifts stalled together",
            f"{', '.join(lifts[:8])}. No per-lift resets: a soft deload week "
            f"({win.get('start')} to {win.get('until')}, sets halved, loads kept), then a retest "
            "of the old loads. Look for the shared cause (running load, sleep, food, stress, "
            "copied or prefilled logs).",
            {"kind": "week", "id": week},
        )
    if t == "fourth_run":
        return (
            "Ready for a 4th run?",
            f"{p.get('weeks_consistent', 8)} consistent weeks, {p.get('mean_km_4w')} km/week, "
            f"longest run {p.get('longest_km_4w')} km. Suggest an easy 30-40 min run (Thursday "
            "after upper or Monday, never Tuesday) only if lifting does not suffer.",
            {"kind": "week", "id": week},
        )
    if t == "running_stagnation":
        change = p.get("change_pct")
        return (
            "Running progress has stalled",
            f"{p.get('metric', 'aerobic efficiency')} changed {change:+.1f}% vs the previous "
            f"{p.get('weeks', '?')} weeks."
            if isinstance(change, int | float)
            else "Running efficiency is not improving.",
            {"kind": "week", "id": week},
        )
    if t == "rule_conflict":
        return (
            "Two rules disagree",
            str(p.get("conflict") or "The engine's rules conflict."),
            {"kind": "week", "id": week},
        )
    if t == "missed_session":
        missed = p.get("missed") or {}
        return (
            f"Missed {SR.PRETTY.get(missed.get('kind'), 'session')}",
            str(p.get("reason") or "A planned session was missed and could not be made up."),
            {"kind": "week", "id": p.get("week_start") or week},
        )
    if t == "deload":
        if p.get("reason") == "fatigue_index":
            return (
                "Deload week triggered",
                "Many lifting sessions missed their reps two weeks in a row (fatigue index).",
                {"kind": "week", "id": week},
            )
        return (
            "Deload week triggered",
            f"{p.get('streak', 'Several')} red readiness days in a row.",
            {"kind": "week", "id": week},
        )
    return (
        "Recovery data looks unusual",
        f"{p.get('signal', 'HRV')} stayed far below baseline for several days.",
        {"kind": "week", "id": week},
    )


def trigger_info_from_rows(rows: list[AdvisorTrigger]) -> dict[str, Any]:
    """One batched `trigger` for several engine triggers: the highest-priority type leads."""
    mapped = [(TRIGGER_MAP[r.type], r) for r in rows if r.type in TRIGGER_MAP]
    mapped.sort(key=lambda m: (PRIORITY.index(m[0]), m[1].id))
    ptype, lead = mapped[0]
    title, detail, ref = _describe(lead)
    if len(mapped) > 1:
        title += f" (+{len(mapped) - 1} more)"
        detail = " | ".join(_describe(r)[0] + ": " + _describe(r)[1] for _t, r in mapped)[:900]
    return {
        "type": ptype,
        "title": title,
        "detail": detail,
        "ref": ref,
        "items": [
            {
                "type": TRIGGER_MAP[r.type],
                "engine_type": r.type,
                "id": r.id,
                "day": r.day.isoformat() if r.day else None,
                "subject": r.subject_id,
                "payload": _compact(r.payload or {}),
            }
            for _t, r in mapped
        ],
    }


def weekly_trigger_info(week_start: date, rows: list[AdvisorTrigger]) -> dict[str, Any]:
    items = trigger_info_from_rows(rows)["items"] if rows else []
    detail = "Scheduled weekly review of lifting, running and recovery."
    if items:
        detail += f" {len(items)} engine trigger(s) were open and are included."
    return {
        "type": WEEKLY_REVIEW,
        "title": f"Weekly review, week of {week_start.isoformat()}",
        "detail": detail,
        "ref": {"kind": "week", "id": week_start.isoformat()},
        "items": items,
    }


def open_triggers(db: Session, settings: Settings, today: date) -> list[AdvisorTrigger]:
    """Open engine triggers worth a consultation. Old ones are dismissed, and so is a stalled
    lift / stagnating metric that was consulted within the last `COVER_DAYS` days."""
    cfg = advisor_config(db, settings)
    cutoff = utcnow() - timedelta(days=cfg.trigger_max_age_days)
    covered = _covered_subjects(db)
    keep: list[AdvisorTrigger] = []
    for t in db.scalars(
        select(AdvisorTrigger).where(AdvisorTrigger.status == "open").order_by(AdvisorTrigger.id)
    ):
        if t.type not in TRIGGER_MAP:
            continue
        if t.created_at < cutoff or (t.type, t.subject_id) in covered:
            t.status = "dismissed"
            continue
        keep.append(t)
    return keep


def _covered_subjects(db: Session) -> set[tuple[str, str | None]]:
    since = utcnow() - timedelta(days=COVER_DAYS)
    out: set[tuple[str, str | None]] = set()
    for c in db.scalars(
        select(AdvisorConsultation).where(
            AdvisorConsultation.created_at >= since, AdvisorConsultation.status != "failed"
        )
    ):
        for item in (c.trigger_info or {}).get("items") or []:
            if item.get("engine_type") in ("lift_stall", "running_stagnation", "fourth_run"):
                out.add((item["engine_type"], item.get("subject")))
    return out


def _consume(db: Session, rows: list[AdvisorTrigger], c: AdvisorConsultation) -> None:
    for t in rows:
        t.status, t.consultation_id = "consulted", c.id


def run_trigger_batch(
    db: Session,
    settings: Settings | None = None,
    *,
    today: date | None = None,
    client: Any | None = None,
    on_applied: Callable[[], None] | None = None,
) -> AdvisorConsultation | None:
    """Batch every worthwhile open trigger into ONE consultation (within the weekly budget)."""
    s = settings or get_settings()
    today = today or user_today(s)
    rows = open_triggers(db, s, today)
    db.commit()
    if not rows:
        return None
    ok, why = budget_allows(db, "trigger", today, s)
    if not ok:
        log.info("advisor trigger batch postponed: %s (%d trigger(s) stay open)", why, len(rows))
        return None
    c = create_consultation(
        db, kind="trigger", trigger_info=trigger_info_from_rows(rows), settings=s
    )
    _consume(db, rows, c)
    db.commit()
    return _release_if_failed(
        db, rows, execute(db, c.id, settings=s, client=client, on_applied=on_applied)
    )


def _release_if_failed(
    db: Session, rows: list[AdvisorTrigger], c: AdvisorConsultation | None
) -> AdvisorConsultation | None:
    """A failed call did not answer anything: its triggers go back to `open` for the next run."""
    if c is not None and c.status == "failed" and not (c.usage or {}).get("requeue"):
        for t in rows:
            t.status, t.consultation_id = "open", None
        db.commit()
    return c


def run_weekly_review(
    db: Session,
    settings: Settings | None = None,
    *,
    today: date | None = None,
    client: Any | None = None,
    on_applied: Callable[[], None] | None = None,
) -> AdvisorConsultation | None:
    """The Sunday review: one call per week, including whatever triggers are still open."""
    s = settings or get_settings()
    today = today or user_today(s)
    week = engine_service.monday(today)
    if _weekly_done(db, week):
        log.info("weekly advisor review for %s already exists", week)
        return None
    ok, why = budget_allows(db, "weekly", today, s)
    if not ok:
        log.warning("weekly advisor review skipped: %s", why)
        return None
    rows = open_triggers(db, s, today)
    c = create_consultation(
        db, kind="weekly", trigger_info=weekly_trigger_info(week, rows), week_start=week, settings=s
    )
    _consume(db, rows, c)
    db.commit()
    return _release_if_failed(
        db, rows, execute(db, c.id, settings=s, client=client, on_applied=on_applied)
    )
