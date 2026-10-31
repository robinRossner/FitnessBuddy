"""Garmin endpoints (bearer-token protected): activities ("runs"), daily health, and the push of
planned runs to the watch (`POST /garmin/workouts/push`, docs/api/garmin.md)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from apps.fitness import garmin_workouts
from apps.fitness.api.routes import get_db
from apps.fitness.db.models import DailyHealth, Run
from apps.fitness.settings import get_settings
from apps.fitness.sources.garmin.errors import GarminError, SourceUnavailable
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
