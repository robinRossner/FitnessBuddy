"""`auto` source: Personal API first; python-garminconnect only as a safety net.

The fallback is consulted when
- the primary raises `SourceUnavailable` (expired / revoked key, blocked), or
- a *recent* daily-kind / weight window (ending within `gap_days` of today) comes back empty
  from the primary while the fallback exists ("fill gaps").
Old history is never pulled from the login-based source implicitly (many requests, 429 risk);
use `garmin backfill --source garminconnect` for that, deliberately.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import date, timedelta

from app.sources.garmin.base import (
    DAILY_KINDS,
    ActivityDetail,
    ActivitySummary,
    AthleteMetricValue,
    DailyHealthData,
    GarminSource,
    WeightEntry,
)
from app.sources.garmin.errors import SourceUnavailable

log = logging.getLogger(__name__)


class CompositeSource:
    name = "auto"

    def __init__(
        self,
        primary: GarminSource,
        fallback: GarminSource | None,
        *,
        today: Callable[[], date] = date.today,
        gap_days: int = 45,
    ) -> None:
        self.primary, self.fallback = primary, fallback
        self._today, self._gap_days = today, gap_days
        self._primary_down = False

    def max_window_days(self, kind: str) -> int:
        src = self.fallback if self._primary_down and self.fallback else self.primary
        return src.max_window_days(kind)

    def _try(self, fn_name: str, *args):
        if not self._primary_down:
            try:
                return getattr(self.primary, fn_name)(*args)
            except SourceUnavailable as exc:
                if self.fallback is None:
                    raise
                log.warning("primary Garmin source unavailable (%s); using fallback", exc)
                self._primary_down = True
        assert self.fallback is not None
        return getattr(self.fallback, fn_name)(*args)

    def _recent(self, end: date) -> bool:
        return end >= self._today() - timedelta(days=self._gap_days)

    def activities(self, start: date, end: date) -> Iterable[ActivitySummary]:
        return list(self._try("activities", start, end))

    def activity_detail(self, external_id: str) -> ActivityDetail:
        return self._try("activity_detail", external_id)

    def daily_health(self, kind: str, start: date, end: date) -> Iterable[DailyHealthData]:
        rows = list(self._try("daily_health", kind, start, end))
        gap = not rows and self.fallback and not self._primary_down and kind in DAILY_KINDS
        if gap and self._recent(end):
            log.info("filling %s gap %s..%s from fallback", kind, start, end)
            rows = list(self.fallback.daily_health(kind, start, end))
        return rows

    def weights(self, start: date, end: date) -> Iterable[WeightEntry]:
        rows = list(self._try("weights", start, end))
        if not rows and self.fallback and not self._primary_down and self._recent(end):
            rows = list(self.fallback.weights(start, end))
        return rows

    def athlete_metrics(self) -> list[AthleteMetricValue]:
        return self._try("athlete_metrics")
