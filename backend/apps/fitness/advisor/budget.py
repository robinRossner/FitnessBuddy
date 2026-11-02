"""Advisor call budget and cost: trigger + weekly calls per week (the weekly review keeps one call
back), and the price of a call from its token usage. "Ask coach" has its own daily limit
(`ask.py`).
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.fitness.advisor._common import (
    COUNTED,
    _local_midnight_utc,
    advisor_config,
    resolve_settings,
)
from apps.fitness.db.models import (
    AdvisorConsultation,
)
from apps.fitness.engine.core.config import AdvisorConfig
from apps.fitness.engine.store import service as engine_service
from apps.fitness.settings import Settings

log = logging.getLogger(__name__)


def calls_this_week(db: Session, today: date, settings: Settings | None = None) -> int:
    """Trigger + weekly calls since Monday (the budget; "Ask coach" is not counted)."""
    s = resolve_settings(settings)
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
    s = resolve_settings(settings)
    limit = advisor_config(db, s).max_calls_per_week
    used = calls_this_week(db, today, s)
    if kind == "trigger" and not _weekly_done(db, engine_service.monday(today)):
        limit -= 1
    if used >= limit:
        return False, f"{used} of {limit} advisor calls used this week"
    return True, ""


def cost_usd(usage: dict[str, int], cfg: AdvisorConfig) -> float:
    per = 1_000_000
    return round(
        usage.get("input_tokens", 0) * cfg.price_input_per_mtok / per
        + usage.get("output_tokens", 0) * cfg.price_output_per_mtok / per
        + usage.get("cache_read_input_tokens", 0) * cfg.price_cache_read_per_mtok / per
        + usage.get("cache_creation_input_tokens", 0) * cfg.price_cache_write_per_mtok / per,
        6,
    )
