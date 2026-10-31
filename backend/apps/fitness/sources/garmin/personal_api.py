"""GarminSource backed by the official Garmin Connect Personal API (primary source)."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

from apps.fitness.sources.garmin import mapping
from apps.fitness.sources.garmin.base import (
    ACTIVITIES,
    DAILY_SUMMARY,
    HRV,
    SLEEP,
    WEIGHT,
    ActivityDetail,
    ActivitySummary,
    AthleteMetricValue,
    DailyHealthData,
    WeightEntry,
)
from apps.fitness.sources.garmin.errors import BadRequestError, NotFoundError
from apps.fitness.sources.garmin.fit import FitError, parse_laps, unzip_fit
from apps.fitness.sources.garmin.personal_client import PersonalApiClient

log = logging.getLogger(__name__)

# Inclusive spans per request. sleep / body battery are capped at 31 days by Garmin, steps and
# stress at 28, daily summaries at one year (verified: 32-33 day sleep ranges return HTTP 400).
# HRV and weight accepted >= 365 days in practice; smaller chunks keep the backfill resumable.
WINDOWS = {ACTIVITIES: 90, DAILY_SUMMARY: 365, SLEEP: 31, HRV: 90, WEIGHT: 365}
ACTIVITY_PAGE = 99  # API default / maximum seen; pagination is by offset


class PersonalApiSource:
    name = "personal_api"

    def __init__(self, client: PersonalApiClient) -> None:
        self.client = client

    def max_window_days(self, kind: str) -> int:
        return WINDOWS[kind]

    # ------------------------------------------------------------------ activities

    def activities(self, start: date, end: date) -> Iterable[ActivitySummary]:
        offset = 0
        while True:
            page = self.client.get(
                "/activitylist-service/activities/search/activities",
                {
                    "start": offset,
                    "limit": ACTIVITY_PAGE,
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "sortOrder": "asc",
                },
            )
            if not page:
                return
            for item in page:
                yield mapping.activity_from_raw(item)
            if len(page) < ACTIVITY_PAGE:
                return
            offset += len(page)

    def activity_detail(self, external_id: str) -> ActivityDetail:
        content = self.client.get_bytes(f"/download-service/files/activity/{external_id}")
        try:
            fit = unzip_fit(content)
            laps = parse_laps(fit)
        except FitError as exc:
            log.warning("activity %s: unusable FIT file (%s)", external_id, exc)
            raise NotFoundError(f"unusable FIT file: {exc}") from exc
        return ActivityDetail(laps=laps, fit_bytes=fit)

    # ------------------------------------------------------------------ daily health

    def daily_health(self, kind: str, start: date, end: date) -> Iterable[DailyHealthData]:
        s, e = start.isoformat(), end.isoformat()
        if kind == DAILY_SUMMARY:
            payload = self.client.get(
                "/usersummary-service/v2/dailysummaries", {"startDate": s, "endDate": e}
            )
            entries: list[dict[str, Any]] = (payload or {}).get("data") or []
        elif kind == SLEEP:
            entries = (
                self.client.get(
                    "/sleep-service/sleep/dailySleepSummariesByDate",
                    {"startDate": s, "endDate": e},
                )
                or []
            )
        elif kind == HRV:
            payload = self.client.get(f"/hrv-service/hrv/daily/{s}/{e}")  # 204 -> None
            entries = (payload or {}).get("hrvSummaries") or []
        else:
            raise ValueError(f"unknown daily kind {kind!r}")
        parse = mapping.DAILY_PARSERS[kind]
        for entry in entries:
            row = parse(entry)
            if row is not None:
                yield row

    # ------------------------------------------------------------------ weight / athlete

    def weights(self, start: date, end: date) -> Iterable[WeightEntry]:
        payload = self.client.get(
            f"/weight-service/weight/range/{start.isoformat()}/{end.isoformat()}",
            {"includeAll": "true"},
        )
        return mapping.weights_from_range(payload)

    def athlete_metrics(self) -> list[AthleteMetricValue]:
        return mapping.hr_zone_metrics(self.client.get("/biometric-service/heartRateZones/v2"))

    # ------------------------------------------------------------------ `garmin check`

    def probe(self, today: date) -> dict[str, dict[str, Any]]:
        """One cheap request per data kind: {kind: {"ok": bool, "rows": int, "error": str}}."""
        week = today - timedelta(days=7)
        out: dict[str, dict[str, Any]] = {}

        def run(kind: str, fn) -> None:
            try:
                out[kind] = {"ok": True, "rows": len(list(fn()))}
            except (BadRequestError, NotFoundError) as exc:
                out[kind] = {"ok": False, "rows": 0, "error": str(exc)}

        run(
            ACTIVITIES,
            lambda: list(
                self.client.get(
                    "/activitylist-service/activities/search/activities", {"start": 0, "limit": 5}
                )
                or []
            ),
        )
        for kind in (DAILY_SUMMARY, SLEEP, HRV):
            run(kind, lambda k=kind: list(self.daily_health(k, week, today)))
        run(WEIGHT, lambda: list(self.weights(today - timedelta(days=90), today)))
        run("hr_zones", lambda: self.athlete_metrics())
        return out
