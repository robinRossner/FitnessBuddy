"""Consultation lifecycle: create a consultation, run it, store + validate the answer, settle its
status, auto-apply per approval mode, and recover / retry consultations that did not finish.

Status of a consultation (`advisor_consultations.status`, the `Proposal.status` of the API):

    queued -> running -> pending ........ valid changes wait for the user (approval mode manual)
                      -> applied ........ every valid change applied (or nothing to decide)
                      -> partially_applied  some applied, the rest still proposed or rejected
                      -> rejected ....... nothing valid, or the user rejected everything
                      -> failed ......... API error, refusal, cut-off answer (error says which)
    awaiting_key ........................ no ANTHROPIC_API_KEY yet (input is stored, nothing called)
    dry_run ............................. ADVISOR_DRY_RUN=true: input stored, nothing called
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.advisor import claude, summary
from apps.fitness.advisor import validator as V
from apps.fitness.advisor._common import (
    MAX_CHANGES,
    MAX_SCHEDULE_CHANGES,
    RUNNABLE,
    STALE_RUNNING_MIN,
    USAGE_LIMIT_MESSAGE,
    _all_changes,
    advisor_config,
    program_raw,
    rerun_hook,
    resolve_settings,
    settle_status,
    user_today,
)
from apps.fitness.advisor.approval import apply_changes, apply_schedule_changes
from apps.fitness.advisor.budget import budget_allows, cost_usd
from apps.fitness.db.models import (
    AdvisorChange,
    AdvisorConsultation,
    AdvisorScheduleChange,
)
from apps.fitness.db.types import utcnow
from apps.fitness.engine.store import config_service as cs
from apps.fitness.settings import Settings
from core.redact import redact

log = logging.getLogger(__name__)


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
    s = resolve_settings(settings)
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


def _fail(db: Session, c: AdvisorConsultation, message: str) -> AdvisorConsultation:
    c.status = "failed"
    c.error = redact(message)[:1000]
    db.commit()
    log.warning("advisor consultation %s failed: %s", c.id, c.error)
    return c


def _call_failed(
    db: Session, c: AdvisorConsultation, exc: claude.AdvisorCallError
) -> AdvisorConsultation:
    """Map a failed provider call to a consultation status by `exc.kind`:

    - `auth` (the subscription login is missing / expired): `awaiting_key`, message kept; it runs
      again once the credential is fixed (`retry_awaiting_key`);
    - `usage_limit` (subscription limit reached): `failed` with a fixed message; an ask / weekly
      consultation is flagged `usage.requeue` and re-run by the next daily trigger job
      (`retry_usage_limited`), a trigger batch gives its triggers back to `open` so the next
      daily batch picks them up;
    - everything else (timeout, cli_missing, cli_error, invalid_output, API errors): `failed`.
    """
    if exc.kind == "auth":
        c.status = "awaiting_key"
        c.error = redact(exc.message)[:1000]
        db.commit()
        log.warning("advisor consultation %s waits for a working login: %s", c.id, c.error)
        return c
    if exc.kind == "usage_limit":
        if c.kind in ("ask", "weekly"):
            c.usage = {**(c.usage or {}), "requeue": True}
        return _fail(db, c, USAGE_LIMIT_MESSAGE)
    return _fail(db, c, exc.message)


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
    s = resolve_settings(settings)
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
        return _call_failed(db, c, exc)
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
        result.usage.get("input_tokens", 0)
        + result.usage.get("cache_read_input_tokens", 0)
        + result.usage.get("cache_creation_input_tokens", 0)
    )
    c.tokens_out = result.usage.get("output_tokens", 0)
    # a subscription run has no per-token price: the cost is unknown, not zero
    c.cost_usd = (
        None if result.usage.get("billing") == "subscription" else cost_usd(result.usage, cfg)
    )
    log.info(
        "advisor consultation %s: in=%d (cache read %d, write %d) out=%d cost=%s",
        c.id,
        result.usage.get("input_tokens", 0),
        result.usage.get("cache_read_input_tokens", 0),
        result.usage.get("cache_creation_input_tokens", 0),
        result.usage.get("output_tokens", 0),
        "subscription" if c.cost_usd is None else f"${c.cost_usd:.4f}",
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
    _store_schedule_changes(db, c, out, settings)
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


def _store_schedule_changes(db: Session, c: AdvisorConsultation, out, settings: Settings) -> None:
    """Validate every schedule change on its own, in order, on a working copy of the plan."""
    items = list(getattr(out, "schedule_changes", None) or [])
    if not items:
        return
    cfg, _row = cs.load_active(db)
    ctx = V.build_schedule_context(db, cfg, user_today(settings))
    for i, ch in enumerate(items):
        if i >= MAX_SCHEDULE_CHANGES:
            verdict = V.ScheduleVerdict(
                False,
                f"too many schedule changes in one answer (max {MAX_SCHEDULE_CHANGES})",
                None,
                V.schedule_label(
                    ch.action,
                    ch.target,
                    _day(ch.date),
                    _day(ch.to_date),
                    ch.new_kind,
                    ch.distance_km,
                ),
                None,
            )
        else:
            verdict = V.validate_schedule_change(
                ctx,
                day=ch.date,
                action=ch.action,
                target=ch.target,
                to_day=ch.to_date,
                new_kind=ch.new_kind,
                distance_km=ch.distance_km,
                reason=ch.reason,
            )
        if verdict.valid:
            V.accept_schedule(ctx, verdict)
        else:
            log.warning("advisor schedule change rejected: %s: %s", verdict.label, verdict.error)
        db.add(
            AdvisorScheduleChange(
                consultation_id=c.id,
                position=i,
                day=_day(ch.date),
                action=ch.action,
                target=ch.target[:24],
                to_day=_day(ch.to_date),
                new_kind=ch.new_kind,
                distance_km=ch.distance_km,
                reason=ch.reason[:1500],
                label=verdict.label[:200],
                risk=verdict.risk or "medium",
                valid=verdict.valid,
                validation_error=verdict.error,
                status="proposed" if verdict.valid else "invalid",
                planned_session_id=verdict.session_id,
            )
        )


def _day(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def _auto_apply(
    db: Session, c: AdvisorConsultation, settings: Settings, on_applied: Callable[[], None] | None
) -> None:
    mode = advisor_config(db, settings).approval_mode
    if mode == "manual":
        return
    ids = [
        x.id for x in c.changes if x.status == "proposed" and (mode == "auto" or x.risk == "low")
    ]
    sched = [
        x.id
        for x in c.schedule_changes
        if x.status == "proposed" and x.valid and (mode == "auto" or x.risk == "low")
    ]
    if not ids and not sched:
        return
    version = apply_changes(db, c, ids, settings=settings) if ids else None
    if sched:
        apply_schedule_changes(db, c, sched, settings=settings)
    db.commit()
    if version is not None:
        (on_applied or rerun_hook())()


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
    s = resolve_settings(settings)
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


def retry_usage_limited(
    db: Session,
    settings: Settings | None = None,
    *,
    client: Any | None = None,
    on_applied: Callable[[], None] | None = None,
    max_age_days: int = 2,
    limit: int = 3,
) -> int:
    """Run again the ask / weekly consultations that stopped on the subscription usage limit."""
    s = resolve_settings(settings)
    if s.advisor_key is None or s.advisor_dry_run:
        return 0
    since = utcnow() - timedelta(days=max_age_days)
    rows = [
        c
        for c in db.scalars(
            select(AdvisorConsultation)
            .where(AdvisorConsultation.status == "failed", AdvisorConsultation.created_at >= since)
            .order_by(AdvisorConsultation.id)
        )
        if (c.usage or {}).get("requeue")
    ][:limit]
    for c in rows:
        c.status, c.error, c.usage = "queued", None, None
        db.commit()
        execute(db, c.id, settings=s, client=client, on_applied=on_applied)
    return len(rows)


def pending_summary(db: Session) -> dict[str, Any]:
    """`coach` block of GET /plan/today: proposals that wait for a decision."""
    pending = pending_proposals(db)
    return {
        "pending_count": len(pending),
        "latest_pending_id": str(pending[0].id) if pending else None,
    }


def list_proposals(db: Session, status: str = "all", limit: int = 50) -> list[AdvisorConsultation]:
    """Proposals of GET /coach/proposals, newest first: `pending` (changes still to decide),
    `applied` (incl. partially applied), `rejected` or `all`."""
    if status == "pending":
        return pending_proposals(db)[:limit]
    q = select(AdvisorConsultation).order_by(AdvisorConsultation.id.desc())
    if status == "applied":
        q = q.where(AdvisorConsultation.status.in_(("applied", "partially_applied")))
    elif status == "rejected":
        q = q.where(AdvisorConsultation.status == "rejected")
    return list(db.scalars(q.limit(limit)))


def pending_proposals(db: Session) -> list[AdvisorConsultation]:
    rows = db.scalars(
        select(AdvisorConsultation)
        .where(AdvisorConsultation.status.in_(("pending", "partially_applied")))
        .order_by(AdvisorConsultation.id.desc())
    )
    return [c for c in rows if any(x.status == "proposed" for x in _all_changes(c))]
