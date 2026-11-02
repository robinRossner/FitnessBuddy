"""Original activity files: unzip, decode laps with fitdecode.

Only lap/session messages are read; the file's `user_profile` message (name, weight, ...) is
ignored on purpose. Corrupt files raise `FitError` and the caller falls back to no laps.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime
from typing import Any

import fitdecode

from apps.fitness.garmin.base import LapData
from apps.fitness.garmin.mapping import pace_s_per_km


class FitError(Exception):
    pass


def unzip_fit(content: bytes) -> bytes:
    """The Personal API / Connect serves originals as a ZIP holding one .fit."""
    if content[:2] != b"PK":  # already a bare FIT
        return content
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".fit")]
            if not names:
                raise FitError("zip contains no .fit file")
            return zf.read(names[0])
    except zipfile.BadZipFile as exc:
        raise FitError("not a valid zip file") from exc


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def parse_laps(fit_bytes: bytes) -> list[LapData]:
    laps_raw: list[dict[str, Any]] = []
    session_start: datetime | None = None
    try:
        with fitdecode.FitReader(io.BytesIO(fit_bytes)) as reader:
            for frame in reader:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name == "lap":
                    laps_raw.append({f.name: f.value for f in frame.fields if f.value is not None})
                elif frame.name == "session" and session_start is None:
                    session_start = frame.get_value("start_time", fallback=None)
    except (fitdecode.FitError, ValueError, EOFError) as exc:
        raise FitError(f"cannot decode FIT: {exc}") from exc

    laps: list[LapData] = []
    for i, lap in enumerate(laps_raw, start=1):
        duration = _num(lap.get("total_timer_time")) or _num(lap.get("total_elapsed_time"))
        distance = _num(lap.get("total_distance"))
        start = lap.get("start_time")
        offset = (
            (start - session_start).total_seconds()
            if isinstance(start, datetime) and isinstance(session_start, datetime)
            else None
        )
        cad = _num(lap.get("avg_running_cadence"))
        if cad is not None:  # running cadence is per leg (+ fractional part); steps/min = x2
            cadence = (cad + (_num(lap.get("avg_fractional_cadence")) or 0.0)) * 2
        else:
            cadence = _num(lap.get("avg_cadence"))
        intensity = lap.get("intensity")
        laps.append(
            LapData(
                lap_index=i,
                start_offset_s=round(offset, 1) if offset is not None else None,
                duration_s=duration,
                distance_m=distance,
                avg_hr=_num(lap.get("avg_heart_rate")),
                max_hr=_num(lap.get("max_heart_rate")),
                avg_pace_s_per_km=pace_s_per_km(duration, distance),
                avg_cadence=round(cadence, 1) if cadence is not None else None,
                elevation_gain_m=_num(lap.get("total_ascent")),
                intensity=str(intensity).lower() if intensity else None,
            )
        )
    return laps


def laps_from_splits(payload: dict[str, Any] | None) -> list[LapData]:
    """python-garminconnect `get_activity_splits` (lapDTOs) when no FIT is available."""
    out: list[LapData] = []
    for i, lap in enumerate((payload or {}).get("lapDTOs") or [], start=1):
        duration, distance = _num(lap.get("duration")), _num(lap.get("distance"))
        intensity = lap.get("intensityType")
        out.append(
            LapData(
                lap_index=i,
                start_offset_s=None,
                duration_s=duration,
                distance_m=distance,
                avg_hr=_num(lap.get("averageHR")),
                max_hr=_num(lap.get("maxHR")),
                avg_pace_s_per_km=pace_s_per_km(duration, distance),
                avg_cadence=_num(lap.get("averageRunCadence")),
                elevation_gain_m=_num(lap.get("elevationGain")),
                intensity=str(intensity).lower() if intensity else None,
            )
        )
    return out
