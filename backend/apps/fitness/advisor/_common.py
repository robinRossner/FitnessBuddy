"""Shared pieces of the advisor lifecycle: trigger / proposal types, limits, the API errors, the
active advisor config, the user's day, the engine re-run hook and the consultation status rule
(`settle_status`). Imports nothing from the other lifecycle modules.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from apps.fitness.analytics import common
from apps.fitness.db.models import (
    AdvisorConsultation,
)
from apps.fitness.engine import config_service as cs
from apps.fitness.engine import service as engine_service
from apps.fitness.engine.config import AdvisorConfig
from apps.fitness.settings import Settings, get_settings

log = logging.getLogger(__name__)


# trigger / proposal types
LIFT_STALL, RULE_CONFLICT, RUNNING_STAGNATION = "lift_stall", "rule_conflict", "running_stagnation"
DATA_ANOMALY, WEEKLY_REVIEW, ASK = "data_anomaly", "weekly_review", "ask"
MISSED_SESSION = "missed_session"
# engine trigger type -> proposal trigger type (a deload is a recovery anomaly for the advisor).
# v2 engine types map onto the existing proposal types, so the coach API (and the iOS app) see
# no new values: a multi-lift stall is a lift stall, a possible 4th run is a running topic.
TRIGGER_MAP = {
    "lift_stall": LIFT_STALL,
    "multi_lift_stall": LIFT_STALL,
    "rule_conflict": RULE_CONFLICT,
    "running_stagnation": RUNNING_STAGNATION,
    "fourth_run": RUNNING_STAGNATION,
    "data_anomaly": DATA_ANOMALY,
    "deload": DATA_ANOMALY,
    "missed_session": MISSED_SESSION,
}
PRIORITY = (LIFT_STALL, MISSED_SESSION, RULE_CONFLICT, RUNNING_STAGNATION, DATA_ANOMALY)

RUNNABLE = ("queued", "awaiting_key", "dry_run")
COUNTED = ("running", "pending", "applied", "partially_applied", "rejected", "failed")
MAX_CHANGES = 8
MAX_SCHEDULE_CHANGES = 6
STALE_RUNNING_MIN = 30
COVER_DAYS = 14  # a stalled lift / stagnating run metric is not consulted again within this time


USAGE_LIMIT_MESSAGE = "Subscription usage limit reached, retry later"


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


def rerun_engine_after_change() -> None:
    """Re-plan after the config changed (the same hook the iOS push uses)."""
    from apps.fitness.jobs.engine_jobs import rerun_engine

    rerun_engine("advisor change")


def rerun_hook() -> Callable[[], None]:
    """`rerun_engine_after_change` as the `service` facade exposes it, looked up at call time: the
    facade attribute is the one patch point (tests' `no_rerun` fixture) for every caller."""
    from apps.fitness.advisor import service

    return service.rerun_engine_after_change


def program_raw(settings: Settings):
    try:
        return engine_service.get_program(settings)
    except (OSError, ValueError):
        return None


def _all_changes(c: AdvisorConsultation) -> list[Any]:
    return [*c.changes, *c.schedule_changes]


def settle_status(c: AdvisorConsultation) -> None:
    changes = _all_changes(c)
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
