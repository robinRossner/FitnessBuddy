"""Raw Garmin JSON -> normalised models; FIT laps; PersonalApiSource over a mocked HTTP layer."""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from apps.fitness.garmin import mapping
from apps.fitness.garmin.errors import BadRequestError, NotFoundError
from apps.fitness.garmin.fit import FitError, laps_from_splits, parse_laps, unzip_fit
from apps.fitness.garmin.personal_api import PersonalApiSource
from apps.fitness.garmin.personal_client import TOKEN_PATH, PersonalApiClient
from tests.garmin.test_garmin_client import KEY, Clock, make_jwt
from tests.support.garmin_fakes import (
    SAMPLE_LAPS,
    activity_raw,
    build_fit,
    hr_zones_raw,
    hrv_raw,
    sleep_raw,
    summary_v2,
    weight_range,
    zip_bytes,
)


def test_activity_mapping():
    a = mapping.activity_from_raw(activity_raw(123, "2026-10-01"))
    assert a.external_id == "123" and a.activity_type == "running"
    assert a.started_at_utc == datetime(2026, 10, 1, 5, 30, tzinfo=UTC)
    assert a.started_at_local == datetime(2026, 10, 1, 7, 30)
    assert a.distance_m == 5000 and a.duration_s == 1500 and a.moving_time_s == 1480
    assert a.avg_pace_s_per_km == 300.0
    assert (a.avg_hr, a.max_hr, a.avg_cadence, a.elevation_gain_m) == (150, 170, 176, 40)
    assert (a.training_effect_aerobic, a.training_effect_anaerobic) == (3.2, 0.4)
    assert a.training_load == 90.5 and a.avg_power_w == 250 and a.vo2max is None
    assert a.time_in_zones_s == {"z1": 10, "z2": 100, "z3": 1000, "z4": 300, "z5": 0}


def test_activity_mapping_sparse_non_running():
    raw = {
        "activityId": 9,
        "startTimeGMT": "2026-10-02 10:00:00",
        "activityType": {"typeKey": "strength_training"},
        "duration": 3000.0,
    }
    a = mapping.activity_from_raw(raw)
    assert a.activity_type == "strength_training" and a.distance_m is None
    assert a.avg_pace_s_per_km is None and a.time_in_zones_s is None
    assert a.training_effect_aerobic is None and a.started_at_local == datetime(2026, 10, 2, 10)


def test_pace_ignores_tiny_distances():
    assert mapping.pace_s_per_km(600, 50) is None
    assert mapping.pace_s_per_km(None, 1000) is None


def test_daily_summary_v2_and_v1_shapes():
    d = mapping.daily_summary_from_raw(summary_v2("2026-10-03"))
    assert (d.resting_hr, d.stress_avg, d.steps) == (53, 31, 9876)
    assert (d.body_battery_max, d.body_battery_min) == (98, 12)
    assert (d.body_battery_charged, d.body_battery_drained) == (80, 85)
    v1 = mapping.daily_summary_from_raw(
        {
            "calendarDate": "2026-10-03",
            "restingHeartRate": 46,
            "totalSteps": 5000,
            "averageStressLevel": 28,
            "bodyBatteryHighestValue": 90,
            "bodyBatteryLowestValue": 10,
        }
    )
    assert (v1.resting_hr, v1.steps, v1.body_battery_max) == (46, 5000, 90)
    assert mapping.daily_summary_from_raw({"calendarDate": "garbage"}) is None


def test_sleep_and_hrv_mapping():
    s = mapping.sleep_from_raw(sleep_raw("2026-10-03"))
    assert (s.sleep_s, s.sleep_score, s.sleep_deep_s, s.sleep_rem_s) == (28800, 81, 5400, 7200)
    assert mapping.sleep_from_raw(sleep_raw("2026-10-03", seconds=0)) is None
    assert mapping.sleep_from_raw(sleep_raw("2026-10-03", score=None)).sleep_score is None
    h = mapping.hrv_from_raw(hrv_raw("2026-10-03", 77))
    assert (h.hrv_overnight_ms, h.hrv_status, h.hrv_weekly_avg_ms) == (77, "BALANCED", 70)
    assert (h.hrv_baseline_low_ms, h.hrv_baseline_high_ms) == (62, 90)
    assert mapping.hrv_from_raw({"calendarDate": "2026-10-03", "lastNightAvg": None}) is None


def test_weight_mapping_uses_grams_and_latest_entry():
    [w] = mapping.weights_from_range(weight_range("2026-10-04", 76500.0))
    assert w.weight_kg == 76.5 and w.day == date(2026, 10, 4) and w.source_type == "INDEX_SCALE"
    assert w.measured_at_utc == datetime(2026, 10, 4, 6, tzinfo=UTC)
    assert mapping.weights_from_range(weight_range("2026-10-04", 5000.0)) == []  # implausible
    assert mapping.weights_from_range({"dailyWeightSummaries": []}) == []
    assert mapping.weights_from_range(None) == []


def test_hr_zone_metrics():
    m = {x.metric: x.value for x in mapping.hr_zone_metrics(hr_zones_raw(190, 171))}
    assert m["max_hr"] == 190 and m["lthr"] == 171 and m["resting_hr_setting"] == 60
    assert m["hr_zone1_floor"] == 98 and m["hr_zone5_floor"] == 176
    assert mapping.hr_zone_metrics([]) == [] and mapping.hr_zone_metrics(None) == []


def test_fit_laps_roundtrip():
    start = datetime(2026, 10, 1, 5, 30, tzinfo=UTC)
    fit = build_fit(start, SAMPLE_LAPS)
    laps = parse_laps(unzip_fit(zip_bytes("1_ACTIVITY.fit", fit)))
    assert [lap.lap_index for lap in laps] == [1, 2, 3]
    assert laps[1].start_offset_s == 300 and laps[1].duration_s == 270
    assert laps[1].distance_m == 1000 and laps[1].avg_pace_s_per_km == 270.0
    assert (laps[1].avg_hr, laps[1].max_hr, laps[1].elevation_gain_m) == (160, 172, 8)
    assert [lap.intensity for lap in laps] == ["warmup", "active", "rest"]


def test_fit_errors():
    with pytest.raises(FitError):
        unzip_fit(zip_bytes("readme.txt", b"x"))
    with pytest.raises(FitError):
        unzip_fit(b"PK\x03\x04garbage")
    with pytest.raises(FitError):
        parse_laps(b"not a fit file at all")


def test_laps_from_splits_fallback_shape():
    laps = laps_from_splits(
        {
            "lapDTOs": [
                {
                    "distance": 1000,
                    "duration": 300,
                    "averageHR": 150,
                    "maxHR": 160,
                    "intensityType": "ACTIVE",
                    "averageRunCadence": 170,
                    "elevationGain": 4,
                }
            ]
        }
    )
    assert laps[0].avg_pace_s_per_km == 300.0 and laps[0].intensity == "active"
    assert laps_from_splits(None) == []


# --------------------------------------------------------------------------- PersonalApiSource


def make_source(handler, tmp_path) -> PersonalApiSource:
    clock = Clock()

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            return httpx.Response(200, text=make_jwt(clock.now + 86400))
        return handler(request)

    client = PersonalApiClient(
        KEY,
        base_url="https://x.test",
        token_cache_path=None,
        interval_s=0,
        transport=httpx.MockTransport(route),
        clock=clock.time,
        sleep=clock.sleep,
    )
    return PersonalApiSource(client)


def test_activities_paginate_and_pass_date_window(tmp_path):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        q = dict(req.url.params)
        seen.append(q)
        assert req.url.path == "/activitylist-service/activities/search/activities"
        assert q["startDate"] == "2026-01-01" and q["endDate"] == "2026-03-31"
        offset = int(q["start"])
        n = 99 if offset == 0 else 3
        return httpx.Response(
            200, json=[activity_raw(offset + i + 1, "2026-02-01") for i in range(n)]
        )

    src = make_source(handler, tmp_path)
    got = list(src.activities(date(2026, 1, 1), date(2026, 3, 31)))
    assert len(got) == 102 and [q["start"] for q in seen] == ["0", "99"]


def test_daily_endpoints_and_204(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/usersummary-service/v2/dailysummaries":
            assert req.url.params["startDate"] == "2026-10-01"
            return httpx.Response(200, json={"data": [summary_v2("2026-10-01")]})
        if path == "/sleep-service/sleep/dailySleepSummariesByDate":
            return httpx.Response(200, json=[sleep_raw("2026-10-01"), sleep_raw("2026-10-02", 0)])
        if path.startswith("/hrv-service/hrv/daily/2026-10-01/2026-10-02"):
            return httpx.Response(204)
        if path.startswith("/weight-service/weight/range/"):
            assert req.url.params["includeAll"] == "true"
            return httpx.Response(200, json=weight_range("2026-10-01"))
        if path == "/biometric-service/heartRateZones/v2":
            return httpx.Response(200, json=hr_zones_raw())
        raise AssertionError(path)

    src = make_source(handler, tmp_path)
    d1, d2 = date(2026, 10, 1), date(2026, 10, 2)
    assert [r.steps for r in src.daily_health("daily_summary", d1, d2)] == [9876]
    assert [r.day for r in src.daily_health("sleep", d1, d2)] == [d1]  # 0 s sleep dropped
    assert list(src.daily_health("hrv", d1, d2)) == []
    assert [w.weight_kg for w in src.weights(d1, d2)] == [76.5]
    assert {m.metric for m in src.athlete_metrics()} >= {"max_hr", "lthr"}
    assert src.max_window_days("sleep") == 31 and src.max_window_days("daily_summary") == 365


def test_window_too_large_is_a_bad_request(tmp_path):
    src = make_source(lambda req: httpx.Response(400, json={}), tmp_path)
    with pytest.raises(BadRequestError):
        list(src.daily_health("sleep", date(2026, 1, 1), date(2026, 3, 1)))


def test_activity_detail_unzips_and_parses(tmp_path):
    fit = build_fit(datetime(2026, 10, 1, 5, 30, tzinfo=UTC), SAMPLE_LAPS)
    payload = zip_bytes("1_ACTIVITY.fit", fit)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/1"):
            return httpx.Response(200, content=payload)
        if req.url.path.endswith("/2"):
            return httpx.Response(200, content=zip_bytes("x.fit", b"broken"))
        return httpx.Response(404, json={"error": "NOT_FOUND"})

    src = make_source(handler, tmp_path)
    detail = src.activity_detail("1")
    assert len(detail.laps) == 3 and detail.fit_bytes == fit
    with pytest.raises(NotFoundError):
        src.activity_detail("2")  # corrupt FIT
    with pytest.raises(NotFoundError):
        src.activity_detail("3")  # no file
