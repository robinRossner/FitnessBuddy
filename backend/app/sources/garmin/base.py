"""Abstract Garmin data source (implementations are a later step).

Like a persistence layer: the sync job depends only on this Protocol, so the primary
implementation (unofficial python-garminconnect) can be swapped for a HealthKit export /
iOS-pushed fallback without changing anything downstream.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ActivitySummary:
    external_id: str
    activity_type: str  # running | treadmill_running | trail_running | strength | ...
    started_at_utc: datetime  # aware
    duration_s: float
    distance_m: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    elevation_gain_m: float | None = None
    avg_cadence: float | None = None
    training_load: float | None = None
    time_in_zones_s: dict[str, float] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LapData:
    lap_index: int
    start_offset_s: float | None
    duration_s: float
    distance_m: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_cadence: float | None = None
    elevation_gain_m: float | None = None
    intensity: str | None = None


@dataclass(frozen=True)
class DailyHealthData:
    day: date
    resting_hr: float | None = None
    hrv_overnight_ms: float | None = None
    hrv_status: str | None = None
    sleep_s: int | None = None
    sleep_score: float | None = None
    body_battery_max: float | None = None
    body_battery_min: float | None = None
    stress_avg: float | None = None
    training_readiness: float | None = None
    body_weight_kg: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class GarminSource(Protocol):
    """Read-only access to Garmin data. Implementations must be idempotent and side-effect free."""

    name: str  # e.g. "garminconnect", "healthkit"

    def activities(self, start: date, end: date) -> Iterable[ActivitySummary]: ...

    def laps(self, external_id: str) -> list[LapData]: ...

    def daily_health(self, start: date, end: date) -> Iterable[DailyHealthData]: ...
