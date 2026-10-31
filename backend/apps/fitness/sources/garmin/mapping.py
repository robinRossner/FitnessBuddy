"""Pure functions: raw Garmin Connect JSON -> normalised dataclasses.

Shared by every source: the Personal API serves the same DTOs as Garmin Connect, so
python-garminconnect responses are adapted to the same shapes first. No I/O here, so a stored
`garmin_raw` payload can always be re-parsed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from apps.fitness.sources.garmin.base import (
    DAILY_SUMMARY,
    HRV,
    SLEEP,
    ActivitySummary,
    AthleteMetricValue,
    DailyHealthData,
    WeightEntry,
)

# Garmin typeKeys that count as "runs" for run-specific consumers (engine, /runs).
RUN_TYPES = frozenset(
    {
        "running",
        "treadmill_running",
        "trail_running",
        "track_running",
        "indoor_running",
        "virtual_run",
        "ultra_run",
    }
)

MIN_PACE_DISTANCE_M = 100.0


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    x = _f(v)
    return None if x is None else round(x)


def parse_garmin_ts(value: str) -> datetime:
    """'2026-10-22 05:27:22' / '2026-10-22T05:27:22.0' -> naive datetime."""
    return datetime.fromisoformat(value.replace(" ", "T")).replace(microsecond=0)


def pace_s_per_km(duration_s: float | None, distance_m: float | None) -> float | None:
    if not duration_s or not distance_m or distance_m < MIN_PACE_DISTANCE_M:
        return None
    return round(duration_s / (distance_m / 1000.0), 2)


def activity_from_raw(item: dict[str, Any]) -> ActivitySummary:
    """One element of /activitylist-service/activities/search/activities."""
    atype = (item.get("activityType") or {}).get("typeKey") or "unknown"
    utc = parse_garmin_ts(item["startTimeGMT"]).replace(tzinfo=UTC)
    local = parse_garmin_ts(item.get("startTimeLocal") or item["startTimeGMT"])
    duration = _f(item.get("duration")) or 0.0
    distance = _f(item.get("distance"))
    zones = {
        f"z{n}": _f(item.get(f"hrTimeInZone_{n}")) or 0.0
        for n in range(1, 6)
        if item.get(f"hrTimeInZone_{n}") is not None
    }
    cadence = _f(item.get("averageRunningCadenceInStepsPerMinute"))
    if cadence is None:
        cadence = _f(item.get("averageBikingCadenceInRevPerMinute"))
    return ActivitySummary(
        external_id=str(item["activityId"]),
        activity_type=atype,
        started_at_utc=utc,
        started_at_local=local,
        title=item.get("activityName"),
        duration_s=duration,
        moving_time_s=_f(item.get("movingDuration")),
        distance_m=distance,
        avg_hr=_f(item.get("averageHR")),
        max_hr=_f(item.get("maxHR")),
        elevation_gain_m=_f(item.get("elevationGain")),
        avg_cadence=cadence,
        avg_pace_s_per_km=pace_s_per_km(duration, distance),
        training_load=_f(item.get("activityTrainingLoad")),
        training_effect_aerobic=_f(item.get("aerobicTrainingEffect")),
        training_effect_anaerobic=_f(item.get("anaerobicTrainingEffect")),
        vo2max=_f(item.get("vO2MaxValue")),
        avg_power_w=_f(item.get("avgPower")),
        calories=_f(item.get("calories")),
        time_in_zones_s=zones or None,
        raw=item,
    )


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def daily_summary_from_raw(entry: dict[str, Any]) -> DailyHealthData | None:
    """UserDailySummaryV2DTO (Personal API / `dailysummaries`) or the older v1 shape."""
    day = _day(entry.get("calendarDate"))
    if day is None:
        return None
    if "heartRate" in entry or "bodyBattery" in entry or "stress" in entry:  # v2
        hr, st, bb = (
            entry.get("heartRate") or {},
            entry.get("stress") or {},
            entry.get("bodyBattery") or {},
        )
        steps = ((entry.get("movement") or {}).get("steps") or {}).get("value")
        return DailyHealthData(
            day=day,
            kind=DAILY_SUMMARY,
            resting_hr=_f(hr.get("restingValue")),
            stress_avg=_f(st.get("avgLevel")),
            body_battery_max=_f(bb.get("maxValue")),
            body_battery_min=_f(bb.get("minValue")),
            body_battery_charged=_f(bb.get("chargedValue")),
            body_battery_drained=_f(bb.get("drainedValue")),
            steps=_i(steps),
            raw=entry,
        )
    return DailyHealthData(  # v1 (UserDailySummaryDto, python-garminconnect get_stats)
        day=day,
        kind=DAILY_SUMMARY,
        resting_hr=_f(entry.get("restingHeartRate")),
        stress_avg=_f(entry.get("averageStressLevel")),
        body_battery_max=_f(entry.get("bodyBatteryHighestValue")),
        body_battery_min=_f(entry.get("bodyBatteryLowestValue")),
        body_battery_charged=_f(entry.get("bodyBatteryChargedValue")),
        body_battery_drained=_f(entry.get("bodyBatteryDrainedValue")),
        steps=_i(entry.get("totalSteps")),
        raw=entry,
    )


def sleep_from_raw(entry: dict[str, Any]) -> DailyHealthData | None:
    """DailySleepDTO. `calendarDate` is the wake-up day."""
    day = _day(entry.get("calendarDate"))
    sleep_s = _i(entry.get("sleepTimeSeconds"))
    if day is None or not sleep_s:
        return None
    overall = ((entry.get("sleepScores") or {}).get("overall") or {}).get("value")
    return DailyHealthData(
        day=day,
        kind=SLEEP,
        sleep_s=sleep_s,
        sleep_score=_f(overall),
        sleep_deep_s=_i(entry.get("deepSleepSeconds")),
        sleep_light_s=_i(entry.get("lightSleepSeconds")),
        sleep_rem_s=_i(entry.get("remSleepSeconds")),
        sleep_awake_s=_i(entry.get("awakeSleepSeconds")),
        raw=entry,
    )


def hrv_from_raw(entry: dict[str, Any]) -> DailyHealthData | None:
    """HrvSummaryDTO (one element of hrvSummaries)."""
    day = _day(entry.get("calendarDate"))
    if day is None or entry.get("lastNightAvg") is None:
        return None
    base = entry.get("baseline") or {}
    return DailyHealthData(
        day=day,
        kind=HRV,
        hrv_overnight_ms=_f(entry.get("lastNightAvg")),
        hrv_status=entry.get("status"),
        hrv_weekly_avg_ms=_f(entry.get("weeklyAvg")),
        hrv_baseline_low_ms=_f(base.get("balancedLow")),
        hrv_baseline_high_ms=_f(base.get("balancedUpper")),
        raw=entry,
    )


DAILY_PARSERS = {DAILY_SUMMARY: daily_summary_from_raw, SLEEP: sleep_from_raw, HRV: hrv_from_raw}


def weights_from_range(payload: dict[str, Any] | None) -> list[WeightEntry]:
    """WeightRangeDTO -> the latest weigh-in of each day (Garmin reports grams)."""
    out: list[WeightEntry] = []
    for summary in (payload or {}).get("dailyWeightSummaries") or []:
        entry = summary.get("latestWeight") or {}
        grams = _f(entry.get("weight"))
        day = _day(entry.get("calendarDate") or summary.get("summaryDate"))
        if grams is None or day is None:
            continue
        kg = round(grams / 1000.0, 2)
        if not 20 < kg < 400:
            continue
        ms = entry.get("timestampGMT") or entry.get("date")
        when = (
            datetime.fromtimestamp(ms / 1000.0, UTC)
            if ms
            else datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
        )
        out.append(
            WeightEntry(
                day=day,
                measured_at_utc=when,
                weight_kg=kg,
                source_type=entry.get("sourceType"),
                raw=entry,
            )
        )
    return out


def hr_zone_metrics(payload: Any) -> list[AthleteMetricValue]:
    """/biometric-service/heartRateZones/v2 -> max HR, LTHR, resting HR, zone floors.

    The list holds one entry per sport profile; DEFAULT (or the first) is used.
    """
    entries = payload if isinstance(payload, list) else ([payload] if payload else [])
    if not entries:
        return []
    chosen = next((e for e in entries if e.get("sport") == "DEFAULT"), entries[0])
    mapping = {
        "max_hr": "maxHeartRateUsed",
        "lthr": "lactateThresholdHeartRateUsed",
        "resting_hr_setting": "restingHeartRateUsed",
        **{f"hr_zone{n}_floor": f"zone{n}Floor" for n in range(1, 6)},
    }
    out = []
    for metric, key in mapping.items():
        value = _f(chosen.get(key))
        if value:
            out.append(AthleteMetricValue(metric=metric, value=value, raw=chosen))
    return out
