"""Advisor lifecycle: create a consultation, run it, store + validate the answer, approve / reject
changes, budget guards, the trigger batch and the weekly review.

Status of a consultation (`advisor_consultations.status`, the `Proposal.status` of the API):

    queued -> running -> pending ........ valid changes wait for the user (approval mode manual)
                      -> applied ........ every valid change applied (or nothing to decide)
                      -> partially_applied  some applied, the rest still proposed or rejected
                      -> rejected ....... nothing valid, or the user rejected everything
                      -> failed ......... API error, refusal, cut-off answer (error says which)
    awaiting_key ........................ no ANTHROPIC_API_KEY yet (input is stored, nothing called)
    dry_run ............................. ADVISOR_DRY_RUN=true: input stored, nothing called

The model only ever proposes parameters; every applied change goes through
`config_service.create_version(author="advisor", consultation_id=...)`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.advisor import claude, summary
from app.advisor import validator as V
from app.analytics import common
from app.db.models import (
    AdvisorChange,
    AdvisorConsultation,
    AdvisorTrigger,
    ConfigVersion,
    ExerciseVariant,
    Run,
)
from app.db.types import utcnow
from app.engine import config_service as cs
from app.engine import service as engine_service
from app.engine.config import AdvisorConfig
from app.engine.decisions import log_decision
from app.settings import Settings, get_settings
from app.sources.garmin.redact import redact

log = logging.getLogger(__name__)

# trigger / proposal types
LIFT_STALL, RULE_CONFLICT, RUNNING_STAGNATION = "lift_stall", "rule_conflict", "running_stagnation"
DATA_ANOMALY, WEEKLY_REVIEW, ASK = "data_anomaly", "weekly_review", "ask"
# engine trigger type -> proposal trigger type (a deload is a recovery anomaly for the advisor)
TRIGGER_MAP = {
    "lift_stall": LIFT_STALL,
    "rule_conflict": RULE_CONFLICT,
    "running_stagnation": RUNNING_STAGNATION,
    "data_anomaly": DATA_ANOMALY,
    "deload": DATA_ANOMALY,
}
PRIORITY = (LIFT_STALL, RULE_CONFLICT, RUNNING_STAGNATION, DATA_ANOMALY)

RUNNABLE = ("queued", "awaiting_key", "dry_run")
COUNTED = ("running", "pending", "applied", "partially_applied", "rejected", "failed")
MAX_CHANGES = 8
STALE_RUNNING_MIN = 30
COVER_DAYS = 14  # a stalled lift / stagnating run metric is not consulted again within this time


class AdvisorError(Exception):
    """Base class; `status_code` is what the API answers."""

    status_code = 400


class NotFound(AdvisorError):
    status_code = 404


class Conflict(AdvisorError):
    status_code = 409


class Unprocessable(AdvisorError):
    status_code = 422


class AskLimit(AdvisorError):
    status_code = 429


# --------------------------------------------------------------------------- config + budget


def advisor_config(db: Session, settings: Settings | None = None) -> AdvisorConfig:
    s = settings or get_settings()
    cs.ensure_seeded(db, s.engine_config_path)
    return cs.load_active(db)[0].advisor_cfg


def _tz(settings: Settings) -> ZoneInfo:
    return ZoneInfo(settings.user_tz)


def _local_midnight_utc(day: date, settings: Settings) -> datetime:
    return datetime.combine(day, time.min, tzinfo=_tz(settings)).astimezone(UTC)


def user_today(settings: Settings | None = None) -> date:
    return common.today()


def calls_this_week(db: Session, today: date, settings: Settings | None = None) -> int:
    """Trigger + weekly calls since Monday (the budget; "Ask coach" is not counted)."""
    s = settings or get_settings()
    since = _local_midnight_utc(engine_service.monday(today), s)
    return (
        db.scalar(
            select(func.count())
            .select_from(AdvisorConsultation)
            .where(
                AdvisorConsultation.kind.in_(("weekly", "trigger")),
                AdvisorConsultation.status.in_(COUNTED),
                AdvisorConsultation.created_at >= since,
            )
        )
        or 0
    )


def asks_today(db: Session, today: date, settings: Settings | None = None) -> int:
    s = settings or get_settings()
    since = _local_midnight_utc(today, s)
    return (
        db.scalar(
            select(func.count())
            .select_from(AdvisorConsultation)
            .where(AdvisorConsultation.kind == "ask", AdvisorConsultation.created_at >= since)
        )
        or 0
    )


def _weekly_done(db: Session, week_start: date) -> bool:
    return (
        db.scalar(
            select(AdvisorConsultation.id).where(
                AdvisorConsultation.kind == "weekly",
                AdvisorConsultation.week_start == week_start,
                AdvisorConsultation.status != "failed",
            )
        )
        is not None
    )


def budget_allows(
    db: Session, kind: str, today: date, settings: Settings | None = None
) -> tuple[bool, str]:
    """Weekly review: at most `max_calls_per_week` calls. Trigger batches keep one call back for
    the weekly review while it has not run this week."""
    s = settings or get_settings()
    limit = advisor_config(db, s).max_calls_per_week
    used = calls_this_week(db, today, s)
    if kind == "trigger" and not _weekly_done(db, engine_service.monday(today)):
        limit -= 1
    if used >= limit:
        return False, f"{used} of {limit} advisor calls used this week"
    return True, ""


def check_ask_budget(db: Session, today: date, settings: Settings | None = None) -> None:
    s = settings or get_settings()
    limit = advisor_config(db, s).max_ask_per_day
    if asks_today(db, today, s) >= limit:
        raise AskLimit(f"Daily limit of {limit} coach questions reached. Try again tomorrow.")


# --------------------------------------------------------------------------- cost


def cost_usd(usage: dict[str, int], cfg: AdvisorConfig) -> float:
    per = 1_000_000
    return round(
        usage.get("input_tokens", 0) * cfg.price_input_per_mtok / per
        + usage.get("output_tokens", 0) * cfg.price_output_per_mtok / per
        + usage.get("cache_read_input_tokens", 0) * cfg.price_cache_read_per_mtok / per
        + usage.get("cache_creation_input_tokens", 0) * cfg.price_cache_write_per_mtok / per,
        6,
    )


# --------------------------------------------------------------------------- trigger info


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
        return (
            f"Stalled lift: {p.get('variant', 'lift')}",
            str(p.get("reason") or "No improvement of the best e1RM over the stall window."),
            {"kind": "lift", "id": str(p.get("variant_id") or row.subject_id)},
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
    if t == "deload":
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


def ask_trigger_info(db: Session, scope: str, ref_id: str | None, question: str) -> dict[str, Any]:
    """Validate the target the user asks about and describe it. Raises Unprocessable."""
    if scope == "general":
        return {
            "type": ASK,
            "title": "Question to the coach",
            "detail": question[:200],
            "ref": {"kind": None, "id": None},
        }
    if not ref_id:
        raise Unprocessable(f"ref_id is required for scope '{scope}'")
    if scope == "lift":
        try:
            variant = db.get(ExerciseVariant, int(ref_id))
        except ValueError:
            variant = None
        if variant is None:
            raise Unprocessable(f"unknown lift (variant id) '{ref_id}'")
        title = f"Question about {variant.name}"
        ref_id = str(variant.id)
    elif scope == "run":
        run = db.get(Run, ref_id)
        if run is None:
            raise Unprocessable(f"unknown run '{ref_id}'")
        title = f"Question about the run on {(run.started_at_local or run.started_at_utc).date()}"
    elif scope == "week":
        try:
            ws = engine_service.monday(date.fromisoformat(ref_id))
        except ValueError:
            raise Unprocessable("ref_id must be a date (YYYY-MM-DD) for scope 'week'") from None
        ref_id = ws.isoformat()
        title = f"Question about the week of {ref_id}"
    else:
        raise Unprocessable("scope must be one of lift, run, week, general")
    return {
        "type": ASK,
        "title": title,
        "detail": question[:200],
        "ref": {"kind": scope, "id": ref_id},
    }


# --------------------------------------------------------------------------- create + execute


def create_consultation(
    db: Session,
    *,
    kind: str,
    trigger_info: dict[str, Any],
    question: str | None = None,
    status: str = "queued",
    week_start: date | None = None,
    settings: Settings | None = None,
) -> AdvisorConsultation:
    s = settings or get_settings()
    c = AdvisorConsultation(
        kind=kind,
        status=status,
        trigger=trigger_info["type"],
        trigger_info=trigger_info,
        question=question,
        week_start=week_start,
        model=s.advisor_model,
        request={},
        dry_run=s.advisor_dry_run,
    )
    db.add(c)
    db.flush()
    return c


def rerun_engine_after_change() -> None:
    """Re-plan after the config changed (the same hook the iOS push uses)."""
    from app.jobs.engine_jobs import rerun_engine

    rerun_engine("advisor change")


def program_raw(settings: Settings):
    try:
        return engine_service.get_program(settings)
    except (OSError, ValueError):
        return None


def _fail(db: Session, c: AdvisorConsultation, message: str) -> AdvisorConsultation:
    c.status = "failed"
    c.error = redact(message)[:1000]
    db.commit()
    log.warning("advisor consultation %s failed: %s", c.id, c.error)
    return c


def execute(
    db: Session,
    consultation_id: int,
    *,
    settings: Settings | None = None,
    client: Any | None = None,
    on_applied: Callable[[], None] | None = None,
) -> AdvisorConsultation | None:
    """Build the input, store it, and (unless dry run / no key) call Claude and store the answer.

    Safe to call twice: only queued / awaiting_key / dry_run consultations run. Never raises for
    API problems; they end up in `status` / `error`.
    """
    s = settings or get_settings()
    c = db.get(AdvisorConsultation, consultation_id)
    if c is None or c.status not in RUNNABLE:
        return c
    c.model = s.advisor_model
    try:
        doc = summary.build_input(db, trigger=c.trigger_info or {}, question=c.question, settings=s)
    except Exception as exc:
        log.exception("advisor input could not be built")
        return _fail(db, c, f"Could not build the advisor input: {exc}")
    c.request = doc
    c.dry_run = s.advisor_dry_run
    if s.advisor_dry_run:
        c.status = "dry_run"
        db.commit()
        log.info(
            "advisor dry run %s: input stored (%d tokens), Claude not called",
            c.id,
            summary.estimate_tokens(doc),
        )
        return c
    if s.advisor_key is None:
        c.status = "awaiting_key"
        db.commit()
        log.info("advisor consultation %s waits for ANTHROPIC_API_KEY", c.id)
        return c

    c.status = "running"
    c.error = None
    db.commit()
    try:
        result = claude.consult(s, summary.user_message(doc), client=client)
    except claude.AdvisorCallError as exc:
        return _fail(db, c, exc.message)
    except Exception as exc:  # a bug in our code must not leave the row in `running`
        log.exception("advisor call crashed")
        return _fail(db, c, f"Unexpected error: {type(exc).__name__}")

    cfg = advisor_config(db, s)
    c.usage = {
        **result.usage,
        "stop_reason": result.stop_reason,
        "request_id": result.request_id,
        "fell_back": result.fell_back,
        "served_by": result.model,
    }
    c.tokens_in = (
        result.usage["input_tokens"]
        + result.usage["cache_read_input_tokens"]
        + result.usage["cache_creation_input_tokens"]
    )
    c.tokens_out = result.usage["output_tokens"]
    c.cost_usd = cost_usd(result.usage, cfg)
    log.info(
        "advisor consultation %s: in=%d (cache read %d, write %d) out=%d cost=$%.4f",
        c.id,
        result.usage["input_tokens"],
        result.usage["cache_read_input_tokens"],
        result.usage["cache_creation_input_tokens"],
        result.usage["output_tokens"],
        c.cost_usd,
    )
    if result.outcome == "refusal":
        category = (result.stop_details or {}).get("category")
        return _fail(
            db,
            c,
            "Claude declined to answer this request"
            + (f" (category: {category})" if category else "")
            + ". Nothing was proposed.",
        )
    if result.outcome == "max_tokens":
        return _fail(
            db,
            c,
            f"Claude's answer was cut off at {s.advisor_max_tokens} tokens. Nothing was proposed.",
        )
    if result.output is None:
        return _fail(db, c, f"No usable answer from Claude (stop reason: {result.stop_reason}).")

    try:
        store_output(db, c, result.output, s)
    except (ValidationError, cs.ConfigError, ValueError) as exc:
        db.rollback()
        c = db.get(AdvisorConsultation, consultation_id)
        return _fail(db, c, f"Could not validate Claude's answer: {exc}")
    db.commit()
    _auto_apply(db, c, s, on_applied)
    return c


def store_output(db: Session, c: AdvisorConsultation, out, settings: Settings) -> None:
    """Validate every proposed change on its own and store the answer."""
    cfg_row = cs.get_active(db)
    ctx = V.build_context(db, cfg_row.content, program=program_raw(settings), settings=settings)
    seen: set[str] = set()
    for i, ch in enumerate(out.proposed_changes):
        verdict: V.Verdict
        if i >= MAX_CHANGES:
            verdict = V.Verdict(
                False,
                f"too many changes in one answer (max {MAX_CHANGES})",
                None,
                None,
                None,
                ch.param[:120],
            )
        elif ch.param in seen:
            verdict = V.Verdict(
                False, "the same parameter was proposed twice", None, None, None, ch.param[:120]
            )
        else:
            verdict = V.validate_change(ch.param, ch.to, ctx)
        seen.add(ch.param)
        if verdict.valid:
            V.accept(ch.param, verdict, ctx)
        else:
            log.warning("advisor change rejected: %s -> %s: %s", ch.param, ch.to, verdict.error)
        db.add(
            AdvisorChange(
                consultation_id=c.id,
                position=i,
                param=ch.param[:160],
                label=verdict.label[:200],
                old_value=verdict.current if verdict.current is not None else ch.from_,
                new_value=verdict.value if verdict.valid else ch.to,
                reason=ch.reason[:1500],
                risk=(verdict.risk if verdict.valid else ch.risk),
                claude_risk=ch.risk,
                valid=verdict.valid,
                validation_error=verdict.error,
                status="proposed" if verdict.valid else "invalid",
            )
        )
    kept, dropped = [], []
    for o in out.session_overrides:
        problem = V.override_problem(f"{o.change} {o.reason}")
        try:
            date.fromisoformat(o.date)
        except ValueError:
            problem = problem or "date is not YYYY-MM-DD"
        (dropped if problem else kept).append(
            {**o.model_dump(), **({"rejected": problem} if problem else {})}
        )
    stored = out.model_dump(by_alias=True)
    stored["session_overrides"] = kept
    if dropped:
        stored["rejected_session_overrides"] = dropped
        log.warning("advisor session overrides dropped: %s", [d["rejected"] for d in dropped])
    c.response = stored
    db.flush()
    db.refresh(c)
    settle_status(c)


def settle_status(c: AdvisorConsultation) -> None:
    changes = list(c.changes)
    proposed = [x for x in changes if x.status == "proposed"]
    applied = [x for x in changes if x.status == "applied"]
    if proposed:
        c.status = "partially_applied" if applied else "pending"
    elif applied:
        c.status = (
            "applied"
            if all(x.status in ("applied", "invalid") for x in changes)
            else "partially_applied"
        )
    elif not changes:
        c.status = "applied"  # an answer without changes: nothing left to decide
    else:
        c.status = "rejected"


def _auto_apply(
    db: Session, c: AdvisorConsultation, settings: Settings, on_applied: Callable[[], None] | None
) -> None:
    mode = advisor_config(db, settings).approval_mode
    if mode == "manual":
        return
    ids = [
        x.id for x in c.changes if x.status == "proposed" and (mode == "auto" or x.risk == "low")
    ]
    if not ids:
        return
    version = apply_changes(db, c, ids, settings=settings)
    db.commit()
    if version is not None:
        (on_applied or rerun_engine_after_change)()


# --------------------------------------------------------------------------- apply / approve


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
    s = settings or get_settings()
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


def get_consultation(db: Session, ref: str | int) -> AdvisorConsultation:
    try:
        c = db.get(AdvisorConsultation, int(ref))
    except (TypeError, ValueError):
        c = None
    if c is None:
        raise NotFound(f"proposal '{ref}' not found")
    return c


def approve(
    db: Session,
    c: AdvisorConsultation,
    change_ids: list[Any] | None,
    *,
    settings: Settings | None = None,
) -> ConfigVersion | None:
    """User approval (all valid changes, or the given subset). Idempotent: what is already applied
    is skipped. Returns the new config version, or None when nothing had to be applied."""
    if c.status in ("queued", "running", "awaiting_key", "dry_run", "failed", "rejected"):
        raise Conflict(f"this proposal is '{c.status}': there is nothing to approve")
    by_id = {str(x.id): x for x in c.changes}
    if change_ids is None:
        targets = [x for x in c.changes if x.valid and x.status == "proposed"]
    else:
        targets = []
        for raw in change_ids:
            x = by_id.get(str(raw))
            if x is None:
                raise Unprocessable(f"change '{raw}' does not belong to this proposal")
            if not x.valid:
                raise Unprocessable(f"change '{raw}' is invalid: {x.validation_error}")
            if x.status == "rejected":
                raise Conflict(f"change '{raw}' was rejected")
            if x.status == "proposed":
                targets.append(x)
    if not targets:
        return None
    version = apply_changes(db, c, [x.id for x in targets], settings=settings)
    db.commit()
    return version


def reject(db: Session, c: AdvisorConsultation, note: str | None) -> AdvisorConsultation:
    if c.status in ("queued", "running"):
        raise Conflict(f"this proposal is still '{c.status}'")
    if c.status == "rejected":
        return c
    for x in c.changes:
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


# --------------------------------------------------------------------------- triggers + weekly


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
            if item.get("engine_type") in ("lift_stall", "running_stagnation"):
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
    if c is not None and c.status == "failed":
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


def start_ask(
    db: Session,
    *,
    scope: str,
    ref_id: str | None,
    question: str,
    settings: Settings | None = None,
) -> AdvisorConsultation:
    """Create the consultation of an "Ask coach" tap (checks the daily limit). Does not run it."""
    s = settings or get_settings()
    question = (question or "").strip()
    if not question:
        raise Unprocessable("question must not be empty")
    if len(question) > 2000:
        raise Unprocessable("question is too long (max 2000 characters)")
    info = ask_trigger_info(db, scope, ref_id, question)
    check_ask_budget(db, user_today(s), s)
    status = "queued" if (s.advisor_key or s.advisor_dry_run) else "awaiting_key"
    c = create_consultation(
        db, kind="ask", trigger_info=info, question=question, status=status, settings=s
    )
    db.commit()
    return c


def recover_stale(db: Session, settings: Settings | None = None) -> int:
    """A consultation that stayed queued / running (server restarted mid-call) is marked failed."""
    cutoff = utcnow() - timedelta(minutes=STALE_RUNNING_MIN)
    n = 0
    for c in db.scalars(
        select(AdvisorConsultation).where(
            AdvisorConsultation.status.in_(("queued", "running")),
            AdvisorConsultation.updated_at < cutoff,
        )
    ):
        c.status = "failed"
        c.error = "Interrupted before it finished (the server restarted?). Ask again."
        n += 1
    if n:
        db.commit()
    return n


def retry_awaiting_key(
    db: Session,
    settings: Settings | None = None,
    *,
    client: Any | None = None,
    on_applied: Callable[[], None] | None = None,
    max_age_days: int = 8,
    limit: int = 3,
) -> int:
    """Once a key exists, run the consultations that were waiting for it."""
    s = settings or get_settings()
    if s.advisor_key is None or s.advisor_dry_run:
        return 0
    since = utcnow() - timedelta(days=max_age_days)
    today = user_today(s)
    n = 0
    for c in list(
        db.scalars(
            select(AdvisorConsultation)
            .where(
                AdvisorConsultation.status == "awaiting_key",
                AdvisorConsultation.created_at >= since,
            )
            .order_by(AdvisorConsultation.id)
        )
    ):
        if n >= limit:
            break
        if c.kind != "ask" and not budget_allows(db, "weekly", today, s)[0]:
            continue
        execute(db, c.id, settings=s, client=client, on_applied=on_applied)
        n += 1
    return n


@dataclass
class CoachSettings:
    approval_mode: str
    api_key_configured: bool
    dry_run: bool
    calls_this_week: int
    max_calls_per_week: int


def coach_settings(db: Session, settings: Settings | None = None) -> CoachSettings:
    s = settings or get_settings()
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
    s = settings or get_settings()
    cs.ensure_seeded(db, s.engine_config_path)
    version = cs.create_version(
        db,
        {"advisor.approval_mode": mode},
        reason=f"Coach approval mode set to {mode}",
        author="user",
    )
    db.commit()
    return version


def pending_summary(db: Session) -> dict[str, Any]:
    """`coach` block of GET /plan/today: proposals that wait for a decision."""
    pending = pending_proposals(db)
    return {
        "pending_count": len(pending),
        "latest_pending_id": str(pending[0].id) if pending else None,
    }


def pending_proposals(db: Session) -> list[AdvisorConsultation]:
    rows = db.scalars(
        select(AdvisorConsultation)
        .where(AdvisorConsultation.status.in_(("pending", "partially_applied")))
        .order_by(AdvisorConsultation.id.desc())
    )
    return [c for c in rows if any(x.status == "proposed" for x in c.changes)]
