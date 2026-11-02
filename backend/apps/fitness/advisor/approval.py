"""Approve / reject proposed changes and apply them: parameter changes become ONE new config
version (author advisor, `config_service.create_version(consultation_id=...)`), schedule changes
are written to the planned sessions with a targeted engine re-run. Also the approval mode setting
(manual / auto-low-risk / auto) and the Coach settings view.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from apps.fitness.advisor import validator as V
from apps.fitness.advisor._common import (
    Conflict,
    Unprocessable,
    _all_changes,
    advisor_config,
    get_consultation,
    program_raw,
    rerun_hook,
    resolve_settings,
    settle_status,
    user_today,
)
from apps.fitness.advisor.budget import calls_this_week
from apps.fitness.db.models import (
    AdvisorChange,
    AdvisorConsultation,
    AdvisorScheduleChange,
    ConfigVersion,
    PlannedSession,
)
from apps.fitness.db.types import utcnow
from apps.fitness.engine.store import config_service as cs
from apps.fitness.engine.store import schedule_edit as SE
from apps.fitness.engine.store import service as engine_service
from apps.fitness.engine.store.decisions import log_decision
from apps.fitness.settings import Settings

log = logging.getLogger(__name__)


def _reason(c: AdvisorConsultation, applied: list[AdvisorChange]) -> str:
    text = f"Advisor (consultation {c.id}): " + "; ".join(
        f"{x.label} {summary_value(x.old_value)} -> {summary_value(x.new_value)}: {x.reason}"
        for x in applied
    )
    return text[:900]


def summary_value(v: Any) -> str:
    if isinstance(v, list):
        return "-".join(str(x) for x in v)
    return str(v)


def apply_changes(
    db: Session,
    c: AdvisorConsultation,
    change_ids: list[int],
    *,
    settings: Settings | None = None,
) -> ConfigVersion | None:
    """Apply the given proposed changes as ONE new config version (author advisor).

    Each change is validated again against the config as it is now (it may have moved since the
    proposal); changes that no longer pass become `invalid`. Returns the new version or None.
    """
    s = resolve_settings(settings)
    wanted = set(change_ids)
    todo = [x for x in c.changes if x.id in wanted and x.status == "proposed"]
    if not todo:
        return None
    active = cs.get_active(db)
    ctx = V.build_context(db, active.content, program=program_raw(s), settings=s)
    accepted: list[tuple[AdvisorChange, V.Verdict]] = []
    for ch in todo:
        verdict = V.validate_change(ch.param, ch.new_value, ctx)
        if verdict.valid:
            V.accept(ch.param, verdict, ctx)
            accepted.append((ch, verdict))
        else:
            ch.status, ch.valid = "invalid", False
            ch.validation_error = f"no longer valid: {verdict.error}"
    if not accepted:
        settle_status(c)
        return None
    reason = _reason(c, [ch for ch, _v in accepted])
    try:
        version = cs.create_version(
            db,
            {ch.param: v.value for ch, v in accepted},
            reason=reason,
            author="advisor",
            consultation_id=c.id,
        )
    except (ValidationError, cs.ConfigError) as exc:
        for ch, _v in accepted:
            ch.status, ch.valid = "invalid", False
            ch.validation_error = f"the engine config rejected it: {exc}"[:300]
        settle_status(c)
        return None
    now = utcnow()
    for ch, v in accepted:
        ch.status, ch.applied_config_version_id, ch.applied_at = "applied", version.id, now
        ch.new_value = v.value
    c.applied_config_version_id = version.id
    c.accepted = True
    decision = log_decision(
        db,
        kind="config_update",
        rule="advisor_change",
        subject_type="config",
        subject_id=version.version,
        config_version_id=version.id,
        inputs={
            "consultation_id": c.id,
            "changes": [
                {"param": ch.param, "from": ch.old_value, "to": v.value} for ch, v in accepted
            ],
        },
        output={"new_version": version.version, "diff": version.diff},
        rationale=reason,
    )
    if decision is not None:
        c.decision_id = decision.id
    settle_status(c)
    log.info(
        "advisor consultation %s applied %d change(s) as config %s",
        c.id,
        len(accepted),
        version.version,
    )
    return version


def change_ref(x: AdvisorChange | AdvisorScheduleChange) -> str:
    """API id of a change: `p<id>` for a parameter change, `s<id>` for a schedule change (unique
    across both lists of a proposal)."""
    return f"{'s' if isinstance(x, AdvisorScheduleChange) else 'p'}{x.id}"


def _parse_ref(raw: Any) -> tuple[str, int]:
    text = str(raw).strip()
    kind, digits = (
        ("s", text[1:]) if text[:1] == "s" else ("p", text[1:] if text[:1] == "p" else text)
    )
    if not digits.isdigit():
        raise Unprocessable(f"change '{raw}' does not belong to this proposal")
    return kind, int(digits)


def approve(
    db: Session,
    c: AdvisorConsultation,
    change_ids: list[Any] | None,
    *,
    settings: Settings | None = None,
) -> ConfigVersion | None:
    """User approval (all valid changes, or the given subset; ids are `p…` / `s…`, a bare number
    counts as a parameter change). Idempotent: what is already applied is skipped. Returns the
    new config version, or None when no parameter change had to be applied (schedule changes are
    applied here, with a targeted engine re-run)."""
    if c.status in ("queued", "running", "awaiting_key", "dry_run", "failed", "rejected"):
        raise Conflict(f"this proposal is '{c.status}': there is nothing to approve")
    params = {x.id: x for x in c.changes}
    scheds = {x.id: x for x in c.schedule_changes}
    if change_ids is None:
        p_targets = [x for x in c.changes if x.valid and x.status == "proposed"]
        s_targets = [x for x in c.schedule_changes if x.valid and x.status == "proposed"]
    else:
        p_targets, s_targets = [], []
        for raw in change_ids:
            kind, num = _parse_ref(raw)
            x = (scheds if kind == "s" else params).get(num)
            if x is None:
                raise Unprocessable(f"change '{raw}' does not belong to this proposal")
            if not x.valid:
                raise Unprocessable(f"change '{raw}' is invalid: {x.validation_error}")
            if x.status == "rejected":
                raise Conflict(f"change '{raw}' was rejected")
            if x.status == "proposed":
                (s_targets if kind == "s" else p_targets).append(x)
    if not p_targets and not s_targets:
        return None
    s = resolve_settings(settings)
    version = apply_changes(db, c, [x.id for x in p_targets], settings=s)
    if s_targets:
        apply_schedule_changes(db, c, [x.id for x in s_targets], settings=s)
    db.commit()
    return version


def apply_schedule_changes(
    db: Session,
    c: AdvisorConsultation,
    change_ids: list[int],
    *,
    settings: Settings | None = None,
) -> int:
    """Apply the given proposed schedule changes (author advisor). Each is validated again against
    the plan as it is now; the placement is written to the planned sessions (a lock the engine
    keeps on every re-run), every change goes to decisions_log with the consultation id, and the
    engine re-plans the affected days only. Returns how many were applied."""
    s = resolve_settings(settings)
    wanted = set(change_ids)
    todo = [x for x in c.schedule_changes if x.id in wanted and x.status == "proposed"]
    if not todo:
        return 0
    cfg, row = cs.load_active(db)
    today = user_today(s)
    ctx = V.build_schedule_context(db, cfg, today)
    now = utcnow()
    days: set[date] = set()
    n = 0
    for x in todo:
        v = V.validate_schedule_change(
            ctx,
            day=x.day,
            action=x.action,
            target=x.target,
            to_day=x.to_day,
            new_kind=x.new_kind,
            distance_km=x.distance_km,
            reason=x.reason,
        )
        if not v.valid:
            x.status, x.valid = "invalid", False
            x.validation_error = f"no longer valid: {v.error}"
            continue
        V.accept_schedule(ctx, v)
        for e in v.edits:
            planned = db.get(PlannedSession, e.session_id)
            if planned is None:
                continue
            days.update({planned.day, *([e.day] if e.day else [])})
            SE.write_edit(planned, e, source="advisor", action=x.action, consultation_id=c.id)
        x.status, x.applied_at, x.label = "applied", now, v.label[:200]
        x.planned_session_id = v.session_id
        n += 1
        decision = log_decision(
            db,
            kind="scheduling",
            rule="advisor_schedule_change",
            subject_type="planned_session",
            subject_id=v.session_id,
            config_version_id=row.id,
            planned_session_id=v.session_id,
            inputs={
                "consultation_id": c.id,
                "change": {
                    "date": x.day.isoformat() if x.day else None,
                    "action": x.action,
                    "target": x.target,
                    "to_date": x.to_day.isoformat() if x.to_day else None,
                    "new_kind": x.new_kind,
                    "distance_km": x.distance_km,
                },
            },
            output={
                "action": x.action,
                "edits": [
                    {
                        "session": e.session_id,
                        "day": e.day.isoformat() if e.day else None,
                        "kind": e.kind,
                        "distance_km": e.distance_km,
                        "skip": e.skip,
                    }
                    for e in v.edits
                ],
            },
            rationale=f"Advisor (consultation {c.id}): {v.label}: {x.reason}"[:900],
        )
        if decision is not None and c.decision_id is None:
            c.decision_id = decision.id
    if n:
        c.accepted = True
    db.flush()
    settle_status(c)
    if n:
        _replan_days(db, days, today, s)
        log.info("advisor consultation %s applied %d schedule change(s)", c.id, n)
    return n


def _replan_days(db: Session, days: set[date], today: date, settings: Settings) -> None:
    """Re-run the engine for the affected days only; the placements are locks, so they stay."""
    by_week: dict[date, set[date]] = {}
    for d in days:
        by_week.setdefault(engine_service.monday(d), set()).add(d)
    program = program_raw(settings)
    if program is None:
        log.warning("schedule change applied without a lifting program: sessions not re-planned")
        return
    for ws, ds in sorted(by_week.items()):
        engine_service.apply_week(
            db, today=today, week_start=ws, settings=settings, program=program, only_days=ds
        )


def reject(db: Session, c: AdvisorConsultation, note: str | None) -> AdvisorConsultation:
    if c.status in ("queued", "running"):
        raise Conflict(f"this proposal is still '{c.status}'")
    if c.status == "rejected":
        return c
    for x in _all_changes(c):
        if x.status == "proposed":
            x.status = "rejected"
    if note:
        c.reject_note = note[:1000]
    if c.status in ("awaiting_key", "failed", "dry_run"):
        c.status = "rejected"
    else:
        settle_status(c)
    db.commit()
    return c


def approve_proposal(
    db: Session,
    ref: str | int,
    change_ids: list[Any] | None,
    *,
    settings: Settings | None = None,
) -> AdvisorConsultation:
    """POST /coach/proposals/{id}/approve: `approve`, then re-plan when a new config version was
    made. Returns the refreshed proposal. Raises NotFound / Conflict / Unprocessable."""
    c = get_consultation(db, ref)
    if approve(db, c, change_ids, settings=settings) is not None:
        rerun_hook()()
    db.refresh(c)
    return c


def reject_proposal(db: Session, ref: str | int, note: str | None) -> AdvisorConsultation:
    """POST /coach/proposals/{id}/reject. Raises NotFound / Conflict."""
    return reject(db, get_consultation(db, ref), note)


@dataclass
class CoachSettings:
    approval_mode: str
    api_key_configured: bool
    dry_run: bool
    calls_this_week: int
    max_calls_per_week: int


def coach_settings(db: Session, settings: Settings | None = None) -> CoachSettings:
    s = resolve_settings(settings)
    cfg = advisor_config(db, s)
    return CoachSettings(
        cfg.approval_mode,
        s.advisor_key is not None,
        s.advisor_dry_run,
        calls_this_week(db, user_today(s), s),
        cfg.max_calls_per_week,
    )


def set_approval_mode(db: Session, mode: str, settings: Settings | None = None) -> ConfigVersion:
    if mode not in ("manual", "auto-low-risk", "auto"):
        raise Unprocessable("approval_mode must be manual, auto-low-risk or auto")
    s = resolve_settings(settings)
    cs.ensure_seeded(db, s.engine_config_path)
    version = cs.create_version(
        db,
        {"advisor.approval_mode": mode},
        reason=f"Coach approval mode set to {mode}",
        author="user",
    )
    db.commit()
    return version
