"""Garmin endpoints (bearer-token protected): activities ("runs"), daily health, the manual sync
(`POST /garmin/sync`) and the push of planned runs to the watch (`POST /garmin/workouts/push`,
docs/api/garmin.md)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from apps.fitness.api.deps import get_db, get_settings_dep, service_errors
from apps.fitness.db.models import DailyHealth, Run
from apps.fitness.garmin import service
from apps.fitness.settings import Settings
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
    return service.list_runs(
        db, from_=from_, to=to, activity_type=activity_type, include_other=include_other,
        limit=limit,
    )  # fmt: skip


@router.get("/runs/{run_id}", response_model=RunDetailOut)
def get_run(run_id: str, db: Session = Depends(get_db)) -> Run:
    with service_errors():
        return service.get_run(db, run_id)


@router.get("/health/daily", response_model=list[DailyHealthOut])
def daily_health(
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    db: Session = Depends(get_db),
) -> list[DailyHealth]:
    return service.daily_health(db, from_, to)


class WorkoutPushIn(BaseModel):
    # also push this day's run (e.g. "Send to watch" on a later day); JSON key `date`
    day: date | None = Field(default=None, alias="date")
    dry_run: bool = False


@router.post("/garmin/workouts/push")
def push_workouts(
    body: WorkoutPushIn | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Upload / update / delete our Garmin workouts now (also runs every evening and early
    morning). 503: push disabled or no Garmin login configured; 502: Garmin refused or failed."""
    body = body or WorkoutPushIn()
    with service_errors():
        return service.push_now(db, body.day, body.dry_run, settings)


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


@router.post("/garmin/sync", response_model=ManualSyncOut)
def manual_sync(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)
) -> ManualSyncOut:
    """Pull new Garmin data now (activities, daily summary, sleep, HRV) and run the engine on
    anything that changed; returns when all of that is done (about 5-30 s with a new activity).
    Guarded by a cooldown and a daily cap (answered as 200 with `status`, zero Garmin calls); after
    a Garmin 429 every call answers 429 until Garmin's Retry-After has passed. 503: no usable Garmin
    source; 502: Garmin failed. The login-based fallback only resumes its cached tokens here."""
    with service_errors():
        return ManualSyncOut(**service.manual_sync(db, settings))
