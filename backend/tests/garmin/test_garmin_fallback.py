"""python-garminconnect fallback (mocked library object; never logs in) and the `auto` source."""

from __future__ import annotations

import sys
import types
from datetime import UTC, date, datetime

import pytest

from apps.fitness.settings import Settings
from apps.fitness.sources.garmin.base import DAILY_SUMMARY, HRV, SLEEP, GarminSource
from apps.fitness.sources.garmin.composite import CompositeSource
from apps.fitness.sources.garmin.errors import (
    NotFoundError,
    RateLimitedError,
    ServerError,
    SourceUnavailable,
)
from apps.fitness.sources.garmin.factory import build_source
from apps.fitness.sources.garmin.python_garminconnect import PythonGarminConnectSource
from tests.support.garmin_fakes import (
    SAMPLE_LAPS,
    FakeSource,
    activity_raw,
    build_fit,
    hr_zones_raw,
    hrv_raw,
    sleep_raw,
    weight_range,
    zip_bytes,
)

D1, D2 = date(2026, 10, 1), date(2026, 10, 2)


class GarminConnectTooManyRequestsError(Exception):
    pass


class GarminConnectAuthenticationError(Exception):
    pass


class GarminConnectConnectionError(Exception):
    pass


class GarminConnectNotFoundError(Exception):
    pass


class FakeLib:
    """Stands in for garminconnect.Garmin; records calls."""

    login_error: Exception | None = None

    def __init__(self, email=None, password=None):
        self.credentials = (email, password)
        self.calls: list[tuple] = []
        self.logins = 0

    def login(self, tokenstore=None):
        self.logins += 1
        if self.login_error:
            raise self.login_error
        return None, None

    def get_activities_by_date(self, start, end, sortorder=None):
        self.calls.append(("acts", start, end, sortorder))
        return [activity_raw(7, "2026-10-01")]

    def download_activity(self, activity_id, fmt):
        self.calls.append(("download", activity_id))
        if activity_id == "404":
            raise GarminConnectNotFoundError()
        return zip_bytes("a.fit", build_fit(datetime(2026, 10, 1, 5, 30, tzinfo=UTC), SAMPLE_LAPS))

    def get_activity_splits(self, activity_id):
        self.calls.append(("splits", activity_id))
        return {"lapDTOs": [{"distance": 1000, "duration": 300, "intensityType": "ACTIVE"}]}

    def get_stats(self, iso):
        return {"calendarDate": iso, "restingHeartRate": 45, "totalSteps": 4000}

    def get_sleep_data(self, iso):
        return {"dailySleepDTO": sleep_raw(iso)}

    def get_hrv_data_range(self, start, end):
        return {"hrvSummaries": [hrv_raw(start), hrv_raw(end, 90)]}

    def get_weigh_ins(self, start, end):
        return weight_range(start)

    def get_heart_rate_zones(self):
        return hr_zones_raw()


@pytest.fixture
def lib_module(monkeypatch):
    """Fake `garminconnect` module so the lazy import in the source never touches the network."""
    mod = types.ModuleType("garminconnect")
    mod.Garmin = FakeLib
    mod.Garmin.ActivityDownloadFormat = types.SimpleNamespace(ORIGINAL="original")
    monkeypatch.setitem(sys.modules, "garminconnect", mod)
    FakeLib.login_error = None
    return mod


def make(tmp_path, factory=FakeLib, **kw):
    pauses: list[float] = []
    src = PythonGarminConnectSource(
        "me@example.test",
        "pw-not-real",
        tmp_path / "tokens",
        pace_s=1.0,
        client_factory=factory,
        sleep=pauses.append,
        **kw,
    )
    src.pauses = pauses  # type: ignore[attr-defined]
    return src


def test_satisfies_protocol_and_windows(tmp_path):
    src = make(tmp_path)
    assert isinstance(src, GarminSource)
    assert src.max_window_days(SLEEP) == 7 and src.max_window_days(HRV) == 90


def test_maps_activities_daily_weight_and_metrics(tmp_path, lib_module):
    src = make(tmp_path)
    [a] = list(src.activities(D1, D2))
    assert a.external_id == "7" and a.avg_pace_s_per_km == 300.0
    assert src._api.calls[0] == ("acts", "2026-10-01", "2026-10-02", "asc")
    summary = list(src.daily_health(DAILY_SUMMARY, D1, D2))
    assert [(r.day, r.resting_hr, r.steps) for r in summary] == [(D1, 45, 4000), (D2, 45, 4000)]
    sleep = list(src.daily_health(SLEEP, D1, D2))
    assert [r.sleep_score for r in sleep] == [81, 81]
    hrv = list(src.daily_health(HRV, D1, D2))
    assert [r.hrv_overnight_ms for r in hrv] == [66, 90]
    assert [w.weight_kg for w in src.weights(D1, D2)] == [76.5]
    assert {m.metric for m in src.athlete_metrics()} >= {"max_hr", "lthr"}
    assert src.pauses == [1.0] * 8  # paced: one pause per library call


def test_detail_prefers_fit_then_falls_back_to_splits(tmp_path, lib_module):
    src = make(tmp_path)
    d = src.activity_detail("7")
    assert len(d.laps) == 3 and d.fit_bytes
    d = src.activity_detail("404")  # no original file -> laps from splits
    assert len(d.laps) == 1 and d.fit_bytes is None and d.laps[0].intensity == "active"


def test_login_is_lazy_once_and_failure_is_never_retried(tmp_path, lib_module):
    created: list[FakeLib] = []

    def factory(email, password):
        lib = FakeLib(email, password)
        created.append(lib)
        return lib

    src = make(tmp_path, factory)
    assert created == []  # nothing happens at construction
    list(src.activities(D1, D2))
    list(src.activities(D1, D2))
    assert len(created) == 1 and created[0].logins == 1

    FakeLib.login_error = GarminConnectAuthenticationError("bad creds pw-not-real")
    created.clear()
    bad = make(tmp_path, factory)
    for _ in range(3):
        with pytest.raises(SourceUnavailable) as err:
            list(bad.activities(D1, D2))
    assert len(created) == 1 and created[0].logins == 1  # exactly one login attempt
    assert "pw-not-real" not in str(err.value)


def test_library_errors_are_translated(tmp_path, lib_module):
    src = make(tmp_path)
    src._client()
    for exc, expected in [
        (GarminConnectTooManyRequestsError(), RateLimitedError),
        (GarminConnectAuthenticationError(), SourceUnavailable),
        (GarminConnectNotFoundError(), NotFoundError),
        (GarminConnectConnectionError(), ServerError),
    ]:

        def boom(*a, _e=exc, **k):
            raise _e

        src._api.get_heart_rate_zones = boom
        with pytest.raises(expected):
            src.athlete_metrics()


# --------------------------------------------------------------------------- composite / factory


class DownSource(FakeSource):
    def _down(self, *a, **k):
        raise SourceUnavailable("key expired")

    activities = daily_health = weights = athlete_metrics = activity_detail = _down  # type: ignore


def test_auto_uses_primary_when_healthy():
    primary, fb = FakeSource(), FakeSource()
    primary.daily_items[SLEEP] = [sleep_raw("2026-10-01")]
    fb.daily_items[SLEEP] = [sleep_raw("2026-10-01", score=10)]
    auto = CompositeSource(primary, fb, today=lambda: D2)
    assert [r.sleep_score for r in auto.daily_health(SLEEP, D1, D2)] == [81]
    assert fb.calls == []


def test_auto_falls_back_when_primary_unavailable_and_stays_there():
    fb = FakeSource()
    fb.activity_items = [activity_raw(5, "2026-10-01")]
    fb.windows[SLEEP] = 7
    auto = CompositeSource(DownSource(), fb, today=lambda: D2)
    assert [a.external_id for a in auto.activities(D1, D2)] == ["5"]
    assert auto.max_window_days(SLEEP) == 7  # now reflects the fallback
    list(auto.athlete_metrics())
    assert ("hr_zones",) in fb.calls


def test_auto_without_fallback_reraises_unavailable():
    with pytest.raises(SourceUnavailable):
        CompositeSource(DownSource(), None).activities(D1, D2)


def test_auto_fills_recent_gaps_but_not_old_history():
    primary, fb = FakeSource(), FakeSource()
    fb.daily_items[SLEEP] = [sleep_raw("2026-10-01"), sleep_raw("2023-02-01")]
    auto = CompositeSource(primary, fb, today=lambda: D2, gap_days=45)
    assert len(list(auto.daily_health(SLEEP, D1, D2))) == 1  # recent window: filled
    fb.calls.clear()
    assert list(auto.daily_health(SLEEP, date(2023, 1, 1), date(2023, 3, 1))) == []  # old: no
    assert fb.calls == []


def test_factory_modes(tmp_path):
    base = {"_env_file": None, "garmin_token_cache_path": tmp_path / "t.json"}
    key = Settings(**base, garmin_personal_api_key="k" * 20)
    assert build_source(key, "personal_api").name == "personal_api"
    assert build_source(key).name == "auto"  # default mode, key present
    with pytest.raises(SourceUnavailable):
        build_source(key, "garminconnect")  # no email/password
    both = Settings(
        **base, garmin_personal_api_key="k" * 20, garmin_email="a@b.test", garmin_password="x"
    )
    assert build_source(both, "garminconnect").name == "garminconnect"
    assert isinstance(build_source(both), CompositeSource)
    only_login = Settings(**base, garmin_email="a@b.test", garmin_password="x")
    assert build_source(only_login).name == "garminconnect"
    with pytest.raises(SourceUnavailable):
        build_source(Settings(**base))


def test_cached_tokens_only_never_gives_the_library_credentials(tmp_path, lib_module):
    """Manual syncs: the login-based source may resume cached tokens but cannot SSO-login."""
    s = Settings(
        _env_file=None,
        garmin_token_cache_path=tmp_path / "t.json",
        garmin_connect_token_dir=tmp_path / "tokens",
        garmin_email="a@b.test",
        garmin_password="x",
    )
    src = build_source(s, cached_tokens_only=True)
    list(src.activities(D1, D2))
    assert src._api.credentials == (None, None) and src._api.logins == 1
    normal = build_source(s)  # scheduled syncs keep their credentials
    list(normal.activities(D1, D2))
    assert normal._api.credentials == ("a@b.test", "x")


def test_failed_gap_fill_login_keeps_the_primary_result():
    primary = FakeSource()

    class NoLogin(FakeSource):
        def daily_health(self, kind, start, end):
            raise SourceUnavailable("login failed")

    auto = CompositeSource(primary, NoLogin(), today=lambda: D2, gap_days=45)
    assert list(auto.daily_health(HRV, D1, D2)) == []
