"""Garmin endpoints (bearer-token protected): activities ("runs"), daily health, the manual sync
(`POST /garmin/sync`) and the push of planned runs to the watch (`POST /garmin/workouts/push`,
docs/api/garmin.md)."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from apps.fitness import garmin_workouts
from apps.fitness.api.routes import get_db
from apps.fitness.db.models import DailyHealth, Run
from apps.fitness.jobs.garmin_jobs import LIGHT_KINDS, run_sync
from apps.fitness.manual_sync import Verdict, manual_sync_guard
from apps.fitness.settings import Settings, get_settings
from apps.fitness.sources.garmin.errors import GarminError, RateLimitedError, SourceUnavailable
from apps.fitness.sources.garmin.mapping import RUN_TYPES
from core.auth import require_token

router = APIRouter(dependencies=[Depends(require_token)])


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LapOut(_Out):
    lap_index: int
    start_offset_s: float | None
    duration_s: float | None
    distance_m: float | None
    avg_hr: float | None
    max_hr: float | None
    avg_pace_s_per_km: float | None
    avg_cadence: float | None
    elevation_gain_m: float | None
    intensity: str | None


class RunOut(_Out):
    id: str
    external_id: str | None
    source: str
    activity_type: str | None
    title: str | None
    started_at_utc: datetime
    started_at_local: datetime | None
    duration_s: float | None
    moving_time_s: float | None
    distance_m: float | None
    avg_hr: float | None
    max_hr: float | None
    avg_pace_s_per_km: float | None
    avg_cadence: float | None
    elevation_gain_m: float | None
    training_load: float | None
    training_effect_aerobic: float | None
    training_effect_anaerobic: float | None
    vo2max: float | None
    avg_power_w: float | None
    calories: float | None
    time_in_zones_s: dict[str, Any] | None


class RunDetailOut(RunOut):
    laps: list[LapOut]


class DailyHealthOut(_Out):
    day: date
    source: str
    resting_hr: float | None
    hrv_overnight_ms: float | None
    hrv_status: str | None
    hrv_weekly_avg_ms: float | None
    hrv_baseline_low_ms: float | None
    hrv_baseline_high_ms: float | None
    sleep_s: int | None
    sleep_score: float | None
    sleep_deep_s: int | None
    sleep_light_s: int | None
    sleep_rem_s: int | None
    sleep_awake_s: int | None
    body_battery_max: float | None
    body_battery_min: float | None
    body_battery_charged: float | None
    body_battery_drained: float | None
    stress_avg: float | None
    steps: int | None
    training_readiness: float | None


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    activity_type: Annotated[
        str | None, Query(description="Garmin typeKey, e.g. running, hiking, strength_training")
    ] = None,
    include_other: Annotated[
        bool, Query(description="include non-running activities (default: running types only)")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    db: Session = Depends(get_db),
) -> list[Run]:
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


@router.get("/runs/{run_id}", response_model=RunDetailOut)
def get_run(run_id: str, db: Session = Depends(get_db)) -> Run:
    run = db.scalar(
        select(Run)
        .where(Run.id == run_id, Run.deleted_at.is_(None))
        .options(selectinload(Run.laps))
    )
    if run is None:
        raise HTTPException(404, "run not found")
    run.laps.sort(key=lambda lap: lap.lap_index)
    return run


@router.get("/health/daily", response_model=list[DailyHealthOut])
def daily_health(
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    db: Session = Depends(get_db),
) -> list[DailyHealth]:
    q = select(DailyHealth)
    if from_:
        q = q.where(DailyHealth.day >= from_)
    if to:
        q = q.where(DailyHealth.day <= to)
    return list(db.scalars(q.order_by(DailyHealth.day)))


class WorkoutPushIn(BaseModel):
    # also push this day's run (e.g. "Send to watch" on a later day); JSON key `date`
    day: date | None = Field(default=None, alias="date")
    dry_run: bool = False


@router.post("/garmin/workouts/push")
def push_workouts(body: WorkoutPushIn | None = None, db: Session = Depends(get_db)) -> dict:
    """Upload / update / delete our Garmin workouts now (also runs every evening and early
    morning). 503: push disabled or no Garmin login configured; 502: Garmin refused or failed."""
    body = body or WorkoutPushIn()
    s = get_settings()
    today = garmin_workouts.user_today(s)
    if body.day is not None and not (
        today <= body.day <= today + timedelta(days=garmin_workouts.HORIZON_DAYS)
    ):
        raise HTTPException(
            422, f"date must be between today and {garmin_workouts.HORIZON_DAYS} days ahead"
        )
    if not body.dry_run and not s.garmin_push_workouts:
        raise HTTPException(503, "pushing workouts to Garmin is disabled (GARMIN_PUSH_WORKOUTS)")
    try:
        report = garmin_workouts.push_workouts(
            db, s, today=today, extra_day=body.day, dry_run=body.dry_run
        )
    except SourceUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except GarminError as exc:
        raise HTTPException(502, str(exc)) from None
    return report.as_dict()


class LatestActivityOut(BaseModel):
    title: str | None
    activity_type: str | None
    started_at_local: str | None  # naive local time, "2026-11-01T07:12:03"


class ManualSyncOut(BaseModel):
    status: Literal["synced", "cooldown", "busy", "limit_reached"]
    retry_after_s: int | None
    new_activities: int
    latest_activity: LatestActivityOut | None
    hrv_today: bool
    manual_syncs_today: int
    manual_syncs_limit: int


BUSY_RETRY_S = 5
BLOCKED_DETAIL = "Garmin rate limit: manual syncs are paused, try again later"


def _manual_sync_out(
    db: Session,
    s: Settings,
    today: date,
    status: str,
    retry_after_s: int | None = None,
    new_activities: int = 0,
) -> ManualSyncOut:
    db.rollback()  # fresh snapshot: the sync wrote through its own sessions
    latest = db.scalar(
        select(Run).where(Run.deleted_at.is_(None)).order_by(Run.started_at_utc.desc()).limit(1)
    )
    hrv = db.scalar(
        select(DailyHealth.id)
        .where(DailyHealth.day == today, DailyHealth.hrv_overnight_ms.is_not(None))
        .limit(1)
    )
    return ManualSyncOut(
        status=status,  # type: ignore[arg-type]
        retry_after_s=retry_after_s,
        new_activities=new_activities,
        latest_activity=None
        if latest is None
        else LatestActivityOut(
            title=latest.title,
            activity_type=latest.activity_type,
            started_at_local=latest.started_at_local.isoformat(timespec="seconds")
            if latest.started_at_local
            else None,
        ),
        hrv_today=hrv is not None,
        manual_syncs_today=manual_sync_guard.count(today),
        manual_syncs_limit=s.garmin_manual_sync_daily_max,
    )


def _blocked(verdict: Verdict) -> HTTPException:
    return HTTPException(429, BLOCKED_DETAIL, {"Retry-After": str(verdict.retry_after_s)})


@router.post("/garmin/sync", response_model=ManualSyncOut)
def manual_sync(db: Session = Depends(get_db)) -> ManualSyncOut:
    """Pull new Garmin data now (activities, daily summary, sleep, HRV) and run the engine on
    anything that changed; returns when all of that is done (about 5-30 s with a new activity).
    Guarded by a cooldown and a daily cap (answered as 200 with `status`, zero Garmin calls); after
    a Garmin 429 every call answers 429 until Garmin's Retry-After has passed. 503: no usable Garmin
    source; 502: Garmin failed. The login-based fallback only resumes its cached tokens here."""
    s = get_settings()
    today = garmin_workouts.user_today(s)
    verdict = manual_sync_guard.reserve(
        today, s.user_tz, s.garmin_manual_sync_cooldown_s, s.garmin_manual_sync_daily_max
    )
    if verdict.status == "blocked":
        raise _blocked(verdict)
    if verdict.status != "go":
        return _manual_sync_out(db, s, today, verdict.status, verdict.retry_after_s)
    try:
        report = run_sync(LIGHT_KINDS, settings=s, cached_tokens_only=True)
    except RateLimitedError as exc:
        manual_sync_guard.block(exc.retry_after)
        headers = {"Retry-After": str(math.ceil(exc.retry_after))} if exc.retry_after else None
        raise HTTPException(429, BLOCKED_DETAIL, headers) from None
    except SourceUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except GarminError as exc:
        raise HTTPException(502, str(exc)) from None
    if report is None:  # a scheduled / CLI sync holds the lock; it never reached Garmin
        manual_sync_guard.refund(verdict)
        return _manual_sync_out(db, s, today, "busy", BUSY_RETRY_S)
    return _manual_sync_out(db, s, today, "synced", new_activities=report.activities_inserted)
