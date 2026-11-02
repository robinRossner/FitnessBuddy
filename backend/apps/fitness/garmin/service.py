"""What the Garmin endpoints do (docs/api/garmin.md): read activities ("runs") and daily health,
push the planned runs to the watch now, and the guarded manual sync. Errors are `ServiceError`s
carrying the HTTP status the API answers with."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from apps.fitness.db.models import DailyHealth, Run, SyncState
from apps.fitness.errors import ServiceError
from apps.fitness.garmin import workouts
from apps.fitness.garmin.errors import GarminError, RateLimitedError, SourceUnavailable
from apps.fitness.garmin.manual import manual_sync_guard
from apps.fitness.garmin.mapping import RUN_TYPES
from apps.fitness.jobs.garmin_jobs import LIGHT_KINDS, run_sync
from apps.fitness.settings import Settings, get_settings

BUSY_RETRY_S = 5
BLOCKED_DETAIL = "Garmin rate limit: manual syncs are paused, try again later"


# --------------------------------------------------------------------------- reads


def list_runs(
    db: Session,
    *,
    from_: date | None = None,
    to: date | None = None,
    activity_type: str | None = None,
    include_other: bool = False,
    limit: int = 200,
) -> list[Run]:
    """Newest first; running types only unless `activity_type` or `include_other`."""
    q = select(Run).where(Run.deleted_at.is_(None))
    if activity_type:
        q = q.where(Run.activity_type == activity_type)
    elif not include_other:
        q = q.where(Run.activity_type.in_(RUN_TYPES))
    if from_:
        q = q.where(Run.started_at_local >= datetime.combine(from_, time.min))
    if to:
        q = q.where(Run.started_at_local < datetime.combine(to + timedelta(days=1), time.min))
    return list(db.scalars(q.order_by(Run.started_at_utc.desc()).limit(limit)))


def get_run(db: Session, run_id: str) -> Run:
    """One activity with its laps in order (404 if unknown or deleted)."""
    run = db.scalar(
        select(Run)
        .where(Run.id == run_id, Run.deleted_at.is_(None))
        .options(selectinload(Run.laps))
    )
    if run is None:
        raise ServiceError(404, "run not found")
    run.laps.sort(key=lambda lap: lap.lap_index)
    return run


def daily_health(
    db: Session, from_: date | None = None, to: date | None = None
) -> list[DailyHealth]:
    q = select(DailyHealth)
    if from_:
        q = q.where(DailyHealth.day >= from_)
    if to:
        q = q.where(DailyHealth.day <= to)
    return list(db.scalars(q.order_by(DailyHealth.day)))


def local_coverage(db: Session) -> dict[str, Any]:
    """What is stored locally (the CLI's `garmin check`): activities and daily-health rows with
    their date range, and the sync cursor of each kind."""
    runs = db.execute(
        select(func.count(Run.id), func.min(Run.started_at_local), func.max(Run.started_at_local))
        .where(Run.deleted_at.is_(None))
    ).one()  # fmt: skip
    days = db.execute(
        select(func.count(DailyHealth.id), func.min(DailyHealth.day), func.max(DailyHealth.day))
    ).one()
    return {
        "activities": tuple(runs),
        "daily_health": tuple(days),
        "cursors": [
            (st.kind, st.cursor_date, st.last_error)
            for st in db.scalars(select(SyncState).order_by(SyncState.kind))
        ],
    }


# --------------------------------------------------------------------------- workout push


def push_now(
    db: Session,
    day: date | None = None,
    dry_run: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """`POST /garmin/workouts/push`: bring our Garmin workouts in line now (`day` = also push
    that day's run). 422 bad day, 503 disabled / no login, 502 Garmin failed."""
    s = settings or get_settings()
    today = workouts.user_today(s)
    if day is not None and not (today <= day <= today + timedelta(days=workouts.HORIZON_DAYS)):
        raise ServiceError(
            422, f"date must be between today and {workouts.HORIZON_DAYS} days ahead"
        )
    if not dry_run and not s.garmin_push_workouts:
        raise ServiceError(503, "pushing workouts to Garmin is disabled (GARMIN_PUSH_WORKOUTS)")
    try:
        report = workouts.push_workouts(db, s, today=today, extra_day=day, dry_run=dry_run)
    except SourceUnavailable as exc:
        raise ServiceError(503, str(exc)) from None
    except GarminError as exc:
        raise ServiceError(502, str(exc)) from None
    return report.as_dict()


# --------------------------------------------------------------------------- manual sync


def _manual_sync_out(
    db: Session,
    s: Settings,
    today: date,
    status: str,
    retry_after_s: int | None = None,
    new_activities: int = 0,
) -> dict[str, Any]:
    db.rollback()  # fresh snapshot: the sync wrote through its own sessions
    latest = db.scalar(
        select(Run).where(Run.deleted_at.is_(None)).order_by(Run.started_at_utc.desc()).limit(1)
    )
    hrv = db.scalar(
        select(DailyHealth.id)
        .where(DailyHealth.day == today, DailyHealth.hrv_overnight_ms.is_not(None))
        .limit(1)
    )
    return {
        "status": status,
        "retry_after_s": retry_after_s,
        "new_activities": new_activities,
        "latest_activity": None
        if latest is None
        else {
            "title": latest.title,
            "activity_type": latest.activity_type,
            "started_at_local": latest.started_at_local.isoformat(timespec="seconds")
            if latest.started_at_local
            else None,
        },
        "hrv_today": hrv is not None,
        "manual_syncs_today": manual_sync_guard.count(today),
        "manual_syncs_limit": s.garmin_manual_sync_daily_max,
    }


def manual_sync(db: Session, settings: Settings | None = None) -> dict[str, Any]:
    """`POST /garmin/sync`: pull the light kinds now (cooldown / daily cap answer with a `status`
    and no Garmin call; after a Garmin 429 every call is a 429 until its Retry-After).
    503 no usable source, 502 Garmin failed. The login fallback only resumes cached tokens."""
    s = settings or get_settings()
    today = workouts.user_today(s)
    verdict = manual_sync_guard.reserve(
        today, s.user_tz, s.garmin_manual_sync_cooldown_s, s.garmin_manual_sync_daily_max
    )
    if verdict.status == "blocked":
        raise ServiceError(429, BLOCKED_DETAIL, {"Retry-After": str(verdict.retry_after_s)})
    if verdict.status != "go":
        return _manual_sync_out(db, s, today, verdict.status, verdict.retry_after_s)
    try:
        report = run_sync(LIGHT_KINDS, settings=s, cached_tokens_only=True)
    except RateLimitedError as exc:
        manual_sync_guard.block(exc.retry_after)
        headers = {"Retry-After": str(math.ceil(exc.retry_after))} if exc.retry_after else None
        raise ServiceError(429, BLOCKED_DETAIL, headers) from None
    except SourceUnavailable as exc:
        raise ServiceError(503, str(exc)) from None
    except GarminError as exc:
        raise ServiceError(502, str(exc)) from None
    if report is None:  # a scheduled / CLI sync holds the lock; it never reached Garmin
        manual_sync_guard.refund(verdict)
        return _manual_sync_out(db, s, today, "busy", BUSY_RETRY_S)
    return _manual_sync_out(db, s, today, "synced", new_activities=report.activities_inserted)
