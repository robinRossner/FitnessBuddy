"""Abstract Garmin data source.

The sync job depends only on this Protocol, so the primary implementation (official Personal
API) can be swapped for / complemented by python-garminconnect (login based fallback) or a
HealthKit-style source without touching anything downstream.

Sources return *normalised* dataclasses plus the raw JSON (`raw`) they were parsed from; the
sync layer stores the raw JSON (`garmin_raw`) so parsers can be re-run without refetching.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

# Data kinds a source can serve; also the keys of `sync_state`.
ACTIVITIES = "activities"
DAILY_SUMMARY = "daily_summary"  # resting HR, stress, body battery, steps
SLEEP = "sleep"
HRV = "hrv"
WEIGHT = "weight"
HR_ZONES = "hr_zones"  # point-in-time athlete parameters (max HR, LTHR, zone floors)
ALL_KINDS = (ACTIVITIES, DAILY_SUMMARY, SLEEP, HRV, WEIGHT, HR_ZONES)
DAILY_KINDS = (DAILY_SUMMARY, SLEEP, HRV)  # merged into one `daily_health` row per day


@dataclass(frozen=True)
class ActivitySummary:
    external_id: str
    activity_type: (
        str  # Garmin typeKey: running | treadmill_running | hiking | strength_training ...
    )
    started_at_utc: datetime  # aware
    started_at_local: datetime  # naive wall clock
    duration_s: float
    title: str | None = None
    moving_time_s: float | None = None
    distance_m: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    elevation_gain_m: float | None = None
    avg_cadence: float | None = None  # steps per minute
    avg_pace_s_per_km: float | None = None
    training_load: float | None = None
    training_effect_aerobic: float | None = None
    training_effect_anaerobic: float | None = None
    vo2max: float | None = None
    avg_power_w: float | None = None
    calories: float | None = None
    time_in_zones_s: dict[str, float] | None = None  # z1..z5 -> seconds (Garmin's zones)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LapData:
    lap_index: int  # 1-based
    start_offset_s: float | None
    duration_s: float | None
    distance_m: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_pace_s_per_km: float | None = None
    avg_cadence: float | None = None
    elevation_gain_m: float | None = None
    intensity: str | None = None  # active | rest | warmup | cooldown | recovery | interval


@dataclass(frozen=True)
class ActivityDetail:
    laps: list[LapData]
    fit_bytes: bytes | None = None  # the unzipped original .FIT, when available


@dataclass(frozen=True)
class DailyHealthData:
    """Partial per-day health data from one `kind`; the sync layer merges non-None fields."""

    day: date
    kind: str = DAILY_SUMMARY
    resting_hr: float | None = None
    hrv_overnight_ms: float | None = None
    hrv_status: str | None = None
    hrv_weekly_avg_ms: float | None = None
    hrv_baseline_low_ms: float | None = None
    hrv_baseline_high_ms: float | None = None
    sleep_s: int | None = None
    sleep_score: float | None = None
    sleep_deep_s: int | None = None
    sleep_light_s: int | None = None
    sleep_rem_s: int | None = None
    sleep_awake_s: int | None = None
    body_battery_max: float | None = None
    body_battery_min: float | None = None
    body_battery_charged: float | None = None
    body_battery_drained: float | None = None
    stress_avg: float | None = None
    steps: int | None = None
    training_readiness: float | None = None  # not offered by the Personal API -> usually None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WeightEntry:
    day: date  # local calendar day
    measured_at_utc: datetime  # aware
    weight_kg: float
    source_type: str | None = None  # Garmin's sourceType (INDEX_SCALE, USER_SETTING, ...)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AthleteMetricValue:
    metric: str  # max_hr | lthr | resting_hr_setting | hr_zone1_floor .. hr_zone5_floor | vo2max
    value: float
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class GarminSource(Protocol):
    """Read-only access to Garmin data. Implementations must be idempotent and side-effect free."""

    name: str  # "personal_api" | "garminconnect" | "auto"

    def max_window_days(self, kind: str) -> int:
        """Largest inclusive date span one request/chunk of `kind` may cover."""
        ...

    def activities(self, start: date, end: date) -> Iterable[ActivitySummary]: ...

    def activity_detail(self, external_id: str) -> ActivityDetail:
        """Laps + original .FIT. Raises NotFoundError when the activity has no file."""
        ...

    def daily_health(self, kind: str, start: date, end: date) -> Iterable[DailyHealthData]: ...

    def weights(self, start: date, end: date) -> Iterable[WeightEntry]: ...

    def athlete_metrics(self) -> list[AthleteMetricValue]: ...
