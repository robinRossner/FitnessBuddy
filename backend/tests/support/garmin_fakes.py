"""Synthetic Garmin payloads, a tiny FIT encoder and a fake GarminSource (no real data)."""

from __future__ import annotations

import io
import struct
import zipfile
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from apps.fitness.garmin import mapping
from apps.fitness.garmin.base import (
    ACTIVITIES,
    DAILY_SUMMARY,
    HRV,
    SLEEP,
    WEIGHT,
    ActivityDetail,
    ActivitySummary,
    AthleteMetricValue,
    DailyHealthData,
    LapData,
    WeightEntry,
)
from apps.fitness.garmin.errors import NotFoundError


def activity_raw(aid: int, day: str = "2026-10-01", typ: str = "running", **over) -> dict:
    item = {
        "activityId": aid,
        "activityName": f"Test {typ}",
        "startTimeLocal": f"{day} 07:30:00",
        "startTimeGMT": f"{day} 05:30:00",
        "activityType": {"typeId": 1, "typeKey": typ, "parentTypeId": 17},
        "distance": 5000.0,
        "duration": 1500.0,
        "movingDuration": 1480.0,
        "elevationGain": 40.0,
        "averageHR": 150.0,
        "maxHR": 170.0,
        "averageRunningCadenceInStepsPerMinute": 176.0,
        "aerobicTrainingEffect": 3.2,
        "anaerobicTrainingEffect": 0.4,
        "activityTrainingLoad": 90.5,
        "avgPower": 250.0,
        "calories": 400.0,
        "hrTimeInZone_1": 10.0,
        "hrTimeInZone_2": 100.0,
        "hrTimeInZone_3": 1000.0,
        "hrTimeInZone_4": 300.0,
        "hrTimeInZone_5": 0.0,
        "lapCount": 5,
    }
    item.update(over)
    return item


def summary_v2(day: str, **over) -> dict:
    d = {
        "calendarDate": day,
        "heartRate": {"minValue": 40, "maxValue": 150, "restingValue": 53},
        "stress": {"avgLevel": 31},
        "bodyBattery": {"minValue": 12, "maxValue": 98, "chargedValue": 80, "drainedValue": 85},
        "movement": {"steps": {"goal": 10000, "value": 9876, "distanceInMeters": 7000.0}},
    }
    d.update(over)
    return d


def sleep_raw(day: str, seconds: int = 28800, score: int | None = 81) -> dict:
    return {
        "calendarDate": day,
        "sleepTimeSeconds": seconds,
        "deepSleepSeconds": 5400,
        "lightSleepSeconds": 15000,
        "remSleepSeconds": 7200,
        "awakeSleepSeconds": 600,
        "sleepScores": {"overall": {"value": score, "qualifierKey": "GOOD"}} if score else {},
    }


def hrv_raw(day: str, last: int = 66) -> dict:
    return {
        "calendarDate": day,
        "weeklyAvg": 70,
        "lastNightAvg": last,
        "status": "BALANCED",
        "baseline": {"lowUpper": 56, "balancedLow": 62, "balancedUpper": 90},
    }


def weight_range(day: str, grams: float = 76500.0) -> dict:
    entry = {
        "calendarDate": day,
        "weight": grams,
        "timestampGMT": int(datetime.fromisoformat(f"{day}T06:00:00+00:00").timestamp() * 1000),
        "sourceType": "INDEX_SCALE",
    }
    return {"dailyWeightSummaries": [{"summaryDate": day, "latestWeight": entry}]}


def hr_zones_raw(max_hr: int = 190, lthr: int = 171) -> list[dict]:
    return [
        {
            "sport": "DEFAULT",
            "maxHeartRateUsed": max_hr,
            "lactateThresholdHeartRateUsed": lthr,
            "restingHeartRateUsed": 60,
            "zone1Floor": 98,
            "zone2Floor": 117,
            "zone3Floor": 137,
            "zone4Floor": 156,
            "zone5Floor": 176,
        }
    ]


# --------------------------------------------------------------------------- FIT encoder


def _crc16(data: bytes) -> int:
    table = [
        0x0000,
        0xCC01,
        0xD801,
        0x1400,
        0xF001,
        0x3C00,
        0x2800,
        0xE401,
        0xA001,
        0x6C00,
        0x7800,
        0xB401,
        0x5000,
        0x9C01,
        0x8801,
        0x4400,
    ]
    crc = 0
    for byte in data:
        tmp = table[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ table[byte & 0xF]
        tmp = table[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ table[(byte >> 4) & 0xF]
    return crc


_FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)

# (field number, size, base type, struct format)
_U32, _U16, _U8, _ENUM = (134, 4, "I"), (132, 2, "H"), (2, 1, "B"), (0, 1, "B")


def _msg(local: int, global_num: int, fields: list[tuple[int, tuple[int, int, str], int]]) -> bytes:
    definition = bytes([0x40 | local, 0, 0]) + struct.pack("<H", global_num)
    definition += bytes([len(fields)])
    for num, (base, size, _fmt), _v in fields:
        definition += bytes([num, size, base])
    data = bytes([local])
    for _n, (_b, _s, fmt), value in fields:
        data += struct.pack("<" + fmt, value)
    return definition + data


def build_fit(start: datetime, laps: list[dict]) -> bytes:
    """Minimal valid FIT file: one session + the given laps.

    Lap dict keys: offset_s, timer_s, distance_m, avg_hr, max_hr, ascent_m, intensity (0 active,
    1 rest, 2 warmup, 3 cooldown).
    """
    ts = int((start - _FIT_EPOCH).total_seconds())
    body = _msg(0, 18, [(2, _U32, ts)])  # session: start_time
    for lap in laps:
        body += _msg(
            1,
            19,
            [
                (2, _U32, ts + int(lap["offset_s"])),
                (8, _U32, int(lap["timer_s"] * 1000)),
                (9, _U32, int(lap["distance_m"] * 100)),
                (15, _U8, lap["avg_hr"]),
                (16, _U8, lap["max_hr"]),
                (21, _U16, lap["ascent_m"]),
                (23, _ENUM, lap["intensity"]),
            ],
        )
    header = struct.pack("<BBHI4s", 14, 0x20, 2100, len(body), b".FIT")
    header += struct.pack("<H", _crc16(header))
    return header + body + struct.pack("<H", _crc16(header + body))


def zip_bytes(name: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, content)
    return buf.getvalue()


SAMPLE_LAPS = [
    {
        "offset_s": 0,
        "timer_s": 300.0,
        "distance_m": 1000.0,
        "avg_hr": 140,
        "max_hr": 150,
        "ascent_m": 5,
        "intensity": 2,
    },
    {
        "offset_s": 300,
        "timer_s": 270.0,
        "distance_m": 1000.0,
        "avg_hr": 160,
        "max_hr": 172,
        "ascent_m": 8,
        "intensity": 0,
    },
    {
        "offset_s": 570,
        "timer_s": 120.0,
        "distance_m": 200.0,
        "avg_hr": 120,
        "max_hr": 140,
        "ascent_m": 0,
        "intensity": 1,
    },
]


# --------------------------------------------------------------------------- fake source


class FakeSource:
    """In-memory GarminSource; records every call so tests can assert chunking."""

    name = "fake"

    def __init__(self) -> None:
        self.activity_items: list[dict] = []
        self.daily_items: dict[str, list[dict]] = {DAILY_SUMMARY: [], SLEEP: [], HRV: []}
        self.weight_payloads: list[dict] = []
        self.zones: list[dict] = hr_zones_raw()
        self.windows = {ACTIVITIES: 90, DAILY_SUMMARY: 30, SLEEP: 10, HRV: 20, WEIGHT: 50}
        self.calls: list[tuple] = []
        self.fail_on: dict[tuple, Exception] = {}
        self.details: dict[str, ActivityDetail | Exception] = {}

    def max_window_days(self, kind: str) -> int:
        return self.windows[kind]

    def _maybe_fail(self, key: tuple) -> None:
        exc = self.fail_on.get(key)
        if exc is not None:
            raise exc

    def activities(self, start: date, end: date) -> Iterable[ActivitySummary]:
        self.calls.append((ACTIVITIES, start, end))
        self._maybe_fail((ACTIVITIES, start))
        for item in self.activity_items:
            day = date.fromisoformat(item["startTimeLocal"][:10])
            if start <= day <= end:
                yield mapping.activity_from_raw(item)

    def activity_detail(self, external_id: str) -> ActivityDetail:
        self.calls.append(("detail", external_id))
        res = self.details.get(external_id)
        if isinstance(res, Exception):
            raise res
        if res is None:
            raise NotFoundError("no file")
        return res

    def daily_health(self, kind: str, start: date, end: date) -> Iterable[DailyHealthData]:
        self.calls.append((kind, start, end))
        self._maybe_fail((kind, start))
        parse = mapping.DAILY_PARSERS[kind]
        for item in self.daily_items[kind]:
            if start <= date.fromisoformat(item["calendarDate"]) <= end:
                row = parse(item)
                if row:
                    yield row

    def weights(self, start: date, end: date) -> Iterable[WeightEntry]:
        self.calls.append((WEIGHT, start, end))
        self._maybe_fail((WEIGHT, start))
        out = []
        for p in self.weight_payloads:
            for w in mapping.weights_from_range(p):
                if start <= w.day <= end:
                    out.append(w)
        return out

    def athlete_metrics(self) -> list[AthleteMetricValue]:
        self.calls.append(("hr_zones",))
        return mapping.hr_zone_metrics(self.zones)


def days(start: str, n: int) -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


__all__ = ["LapData"]
