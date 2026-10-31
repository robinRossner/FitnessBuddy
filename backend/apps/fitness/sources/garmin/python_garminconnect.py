"""GarminSource backed by python-garminconnect (login based fallback).

Used only when GARMIN_EMAIL / GARMIN_PASSWORD are set and GARMIN_SOURCE selects it (or `auto`
and the Personal API failed). `GarminConnectLogin` is also the login of the workout push
(`apps.fitness.garmin_workouts`). Garmin blocks/429s repeated SSO logins, so:
- login happens lazily, at most once per process; a failure is remembered and re-raised
  without trying again;
- tokens are persisted in `garmin_connect_token_dir` (python-garminconnect's own token file),
  so later runs resume without a fresh login;
- one request per `pace_s` seconds.

Responses are adapted to the Personal API shapes and parsed by `mapping`, so both sources
produce identical rows.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from pathlib import Path
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
from apps.fitness.sources.garmin.errors import (
    GarminError,
    NotFoundError,
    RateLimitedError,
    ServerError,
    SourceUnavailable,
)
from apps.fitness.sources.garmin.fit import FitError, laps_from_splits, parse_laps, unzip_fit
from apps.fitness.sources.garmin.redact import install_log_redaction, register_secret

log = logging.getLogger(__name__)

# Per-day endpoints => small chunks keep resumable progress granular.
WINDOWS = {ACTIVITIES: 90, DAILY_SUMMARY: 7, SLEEP: 7, HRV: 90, WEIGHT: 90}


def _default_factory(email: str, password: str):
    from garminconnect import Garmin  # imported lazily: only needed for this fallback

    return Garmin(email, password)


def translate_error(exc: Exception) -> Exception:
    """python-garminconnect exception -> our GarminError family (type names only, no secrets)."""
    name = type(exc).__name__
    if "TooManyRequests" in name:
        return RateLimitedError("python-garminconnect: rate limited (429)")
    if "Authentication" in name:
        return SourceUnavailable("python-garminconnect: authentication failed")
    if "NotFound" in name:
        return NotFoundError("python-garminconnect: not found")
    if "Connection" in name:
        return ServerError(f"python-garminconnect: connection error ({name})")
    return GarminError(f"python-garminconnect: {name}")


class GarminConnectLogin:
    """The one python-garminconnect login path (sync fallback and workout push).

    Lazy: the first `client()` call logs in (resuming the token file in `token_dir` when it is
    still valid, so usually no SSO round trip). A failed login is remembered and re-raised
    without contacting Garmin again: forever (`retry_after_s=None`, one sync run) or until
    `retry_after_s` has passed (the long-lived shared login of the workout push), because Garmin
    SSO answers repeated logins with 429.
    """

    def __init__(
        self,
        email: str,
        password: str,
        token_dir: Path,
        *,
        client_factory: Callable[[str, str], Any] = _default_factory,
        retry_after_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        install_log_redaction()
        register_secret(password)
        self._email, self._password = email, password
        self._token_dir = token_dir
        self._factory = client_factory
        self._retry_after_s = retry_after_s
        self._clock = clock
        self._lock = threading.Lock()
        self.api: Any = None
        self._error: SourceUnavailable | None = None
        self._failed_at: float | None = None

    def client(self) -> Any:
        with self._lock:
            if self._error is not None:
                expired = (
                    self._retry_after_s is not None
                    and self._failed_at is not None
                    and self._clock() - self._failed_at >= self._retry_after_s
                )
                if not expired:
                    raise self._error  # never hammer Garmin SSO after a failed login
                self._error = self._failed_at = None
            if self.api is None:
                try:
                    api = self._factory(self._email, self._password)
                    self._token_dir.mkdir(parents=True, exist_ok=True)
                    api.login(str(self._token_dir))
                except Exception as exc:
                    self._error = SourceUnavailable(
                        f"python-garminconnect login failed ({type(exc).__name__}); "
                        "not retrying for now"
                    )
                    self._failed_at = self._clock()
                    raise self._error from None
                self.api = api
            return self.api

    def forget(self) -> None:
        """Drop the logged-in client (its tokens stopped working); the next call logs in again."""
        with self._lock:
            self.api = None


_shared: dict[tuple[str, str], GarminConnectLogin] = {}
_shared_lock = threading.Lock()


def shared_login(
    email: str,
    password: str,
    token_dir: Path,
    *,
    retry_after_s: float = 6 * 3600,
    client_factory: Callable[[str, str], Any] = _default_factory,
) -> GarminConnectLogin:
    """Process-wide login for long-lived callers (the workout push runs several times a day in
    the server process): one client, and a failed login blocks new attempts for `retry_after_s`."""
    key = (email, str(token_dir))
    with _shared_lock:
        login = _shared.get(key)
        if login is None:
            login = GarminConnectLogin(
                email,
                password,
                token_dir,
                client_factory=client_factory,
                retry_after_s=retry_after_s,
            )
            _shared[key] = login
        return login


def reset_shared_logins() -> None:
    """Tests: forget every process-wide login."""
    with _shared_lock:
        _shared.clear()


class PythonGarminConnectSource:
    name = "garminconnect"

    def __init__(
        self,
        email: str,
        password: str,
        token_dir: Path,
        *,
        pace_s: float = 1.0,
        client_factory: Callable[[str, str], Any] = _default_factory,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._login = GarminConnectLogin(email, password, token_dir, client_factory=client_factory)
        self._pace_s = pace_s
        self._sleep = sleep

    @property
    def _api(self) -> Any:
        return self._login.api

    def max_window_days(self, kind: str) -> int:
        return WINDOWS[kind]

    # ------------------------------------------------------------------ plumbing

    def _client(self) -> Any:
        return self._login.client()

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        api = self._client()
        self._sleep(self._pace_s)
        try:
            return getattr(api, method)(*args, **kwargs)
        except Exception as exc:
            raise self._translate(exc) from None

    @staticmethod
    def _translate(exc: Exception) -> Exception:
        return translate_error(exc)

    # ------------------------------------------------------------------ activities

    def activities(self, start: date, end: date) -> Iterable[ActivitySummary]:
        items = self._call(
            "get_activities_by_date", start.isoformat(), end.isoformat(), sortorder="asc"
        )
        for item in items or []:
            yield mapping.activity_from_raw(item)

    def activity_detail(self, external_id: str) -> ActivityDetail:
        from garminconnect import Garmin

        try:
            content = self._call(
                "download_activity", external_id, Garmin.ActivityDownloadFormat.ORIGINAL
            )
            fit = unzip_fit(content)
            return ActivityDetail(laps=parse_laps(fit), fit_bytes=fit)
        except (NotFoundError, FitError):
            splits = self._call("get_activity_splits", external_id)  # laps without a FIT
            laps = laps_from_splits(splits)
            if not laps:
                raise NotFoundError("no FIT file and no splits") from None
            return ActivityDetail(laps=laps, fit_bytes=None)

    # ------------------------------------------------------------------ daily health

    def _days(self, start: date, end: date) -> Iterable[date]:
        d = start
        while d <= end:
            yield d
            d += timedelta(days=1)

    def daily_health(self, kind: str, start: date, end: date) -> Iterable[DailyHealthData]:
        if kind == HRV:
            payload = self._call("get_hrv_data_range", start.isoformat(), end.isoformat())
            for entry in (payload or {}).get("hrvSummaries") or []:
                row = mapping.hrv_from_raw(entry)
                if row:
                    yield row
            return
        for day in self._days(start, end):
            iso = day.isoformat()
            if kind == DAILY_SUMMARY:
                entry = self._call("get_stats", iso) or {}
                entry = {**entry, "calendarDate": entry.get("calendarDate") or iso}
                row = mapping.daily_summary_from_raw(entry)
            elif kind == SLEEP:
                data = self._call("get_sleep_data", iso) or {}
                entry = data.get("dailySleepDTO") or {}
                entry = {**entry, "calendarDate": entry.get("calendarDate") or iso}
                row = mapping.sleep_from_raw(entry)
            else:
                raise ValueError(f"unknown daily kind {kind!r}")
            if row:
                yield row

    # ------------------------------------------------------------------ weight / athlete

    def weights(self, start: date, end: date) -> Iterable[WeightEntry]:
        payload = self._call("get_weigh_ins", start.isoformat(), end.isoformat())
        return mapping.weights_from_range(payload)

    def athlete_metrics(self) -> list[AthleteMetricValue]:
        return mapping.hr_zone_metrics(self._call("get_heart_rate_zones"))
