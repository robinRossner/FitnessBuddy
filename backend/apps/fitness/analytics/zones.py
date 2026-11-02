"""Time in heart-rate zones on the engine's current zones, always five.

The engine's zones are Garmin's z1..z5 (their latest floors), or without usable floors Friel's seven
from LTHR, collapsed: z1 -> z1, z2 -> z2, z3 -> z3, z4 -> z4, z5a + z5b + z5c -> z5.

Sources, best first:
  garmin_zones  Garmin's per-second zone seconds (the floors valid for that run,
                `athlete_metrics`) re-binned onto the engine zones, assuming the time inside a
                Garmin zone is spread evenly over its bpm range. With the same floors that is
                Garmin's seconds as they are; older runs show on today's zones, so bars compare.
  laps          each lap's duration goes to the zone of its average HR.
  none          no HR data: all zeros.
"""

from __future__ import annotations

from collections.abc import Sequence

from apps.fitness.engine.core.hr import HrZones, valid_floors
from apps.fitness.engine.core.running import LapData, RunData

ZONES5 = ("z1", "z2", "z3", "z4", "z5")
TOP_BPM = 250  # open upper end, same as the engine


def collapse(name: str) -> str:
    """'z5a' / 'z5b' / 'z5c' -> 'z5'; 'z1'..'z4' unchanged."""
    return name[:2]


def bounds5(zones: HrZones) -> dict[str, tuple[int, int]]:
    """Inclusive (low, high) bpm of the 5 collapsed zones."""
    out: dict[str, tuple[int, int]] = {}
    for z in zones.zones:
        key = collapse(z.name)
        lo, hi = out.get(key, (z.low, z.high))
        out[key] = (min(lo, z.low), max(hi, z.high))
    last = ZONES5[-1]
    out[last] = (out[last][0], TOP_BPM)
    return out


def _from_garmin(secs: Sequence[float], floors: Sequence[float], b5: dict) -> dict[str, float]:
    out = dict.fromkeys(ZONES5, 0.0)
    for i, s in enumerate(secs):
        if not s:
            continue
        lo = int(floors[i]) if i > 0 else 0
        hi = int(floors[i + 1]) - 1 if i + 1 < len(floors) else TOP_BPM
        width = hi - lo + 1
        for z, (clo, chi) in b5.items():
            overlap = min(hi, chi) - max(lo, clo) + 1
            if overlap > 0:
                out[z] += s * overlap / width
    return out


def _from_laps(laps: Sequence[LapData], zones: HrZones) -> dict[str, float]:
    out = dict.fromkeys(ZONES5, 0.0)
    for lap in laps:
        if lap.avg_hr and lap.duration_s > 0:
            out[collapse(zones.zone_of(lap.avg_hr))] += lap.duration_s
    return out


def time_in_zones(run: RunData, zones: HrZones) -> tuple[dict[str, int], str]:
    """(seconds per z1..z5, source) for one run."""
    secs: dict[str, float] | None = None
    source = "none"
    if run.time_in_zones_s and valid_floors(run.zone_floors, zones.max_hr):
        raw = [
            float(run.time_in_zones_s.get(f"z{i + 1}", 0.0)) for i in range(len(run.zone_floors))
        ]
        if sum(raw) > 0:
            secs, source = _from_garmin(raw, run.zone_floors, bounds5(zones)), "garmin_zones"
    if secs is None and any(lap.avg_hr for lap in run.laps):
        secs, source = _from_laps(run.laps, zones), "laps"
    if secs is None:
        secs = dict.fromkeys(ZONES5, 0.0)
    return {z: round(v) for z, v in secs.items()}, source
