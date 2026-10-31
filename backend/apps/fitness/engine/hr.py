"""Heart-rate zones derived from LTHR / max HR (pure functions).

Zones are never stored: they are computed from the active config's `running.heart_rate`, so a new
LTHR (e.g. from Garmin) changes every zone, the easy-run ceiling and the interval target at once.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from apps.fitness.engine.config import EngineConfig, HeartRateConfig

ZONE_ORDER = ("z1", "z2", "z3", "z4", "z5a", "z5b", "z5c")


def _round_half_up(x: float) -> int:
    return math.floor(x + 0.5)


@dataclass(frozen=True)
class Zone:
    name: str
    low: int  # inclusive bpm
    high: int  # inclusive bpm


@dataclass(frozen=True)
class HrZones:
    zones: tuple[Zone, ...]
    max_hr: int
    lthr: int | None
    easy_low: int  # lower end of the useful easy range
    easy_high: int  # easy-run ceiling (top of Friel Z2 once LTHR is known)
    easy_basis: str  # "lthr" | "hrmax"
    interval_low: int
    interval_high: int

    def zone(self, name: str) -> Zone:
        for z in self.zones:
            if z.name == name:
                return z
        raise KeyError(name)

    def zone_of(self, bpm: float) -> str:
        for z in self.zones:
            if bpm <= z.high:
                return z.name
        return self.zones[-1].name

    def as_dict(self) -> dict:
        return {
            "max_hr": self.max_hr,
            "lthr": self.lthr,
            "zones": {z.name: [z.low, z.high] for z in self.zones},
            "easy": {"low": self.easy_low, "high": self.easy_high, "basis": self.easy_basis},
            "intervals": {"low": self.interval_low, "high": self.interval_high},
        }


def compute_zones(hr: HeartRateConfig) -> HrZones:
    """Friel run zones from LTHR (inclusive upper bounds = round-half-up(pct * LTHR)).

    Without LTHR the zones fall back to the same percentages of an LTHR assumed to be 91 % of max
    HR (a common rule of thumb), and the easy ceiling to `easy_ceiling.pct_hrmax` of max HR.
    """
    if hr.max_hr is None and hr.lthr is None:
        raise ValueError("running.heart_rate needs max_hr or lthr")
    lthr = hr.lthr
    max_hr = hr.max_hr if hr.max_hr is not None else (lthr or 0) / 0.91
    basis_lthr = lthr if lthr is not None else 0.91 * max_hr
    uppers = [(name, _round_half_up(pct * basis_lthr)) for name, pct in hr.zones_pct_lthr.items()]
    uppers.sort(key=lambda t: t[1])
    zones: list[Zone] = []
    low = 0
    for name, high in uppers:
        zones.append(Zone(name, low, high))
        low = high + 1
    zones.append(Zone("z5c", low, max(round(max_hr), low)))
    by_name = {z.name: z for z in zones}

    if lthr is not None:
        easy_high = _round_half_up(hr.easy_ceiling.pct_lthr * lthr)
        basis = "lthr"
    else:
        easy_high = math.floor(hr.easy_ceiling.pct_hrmax * max_hr)
        basis = "hrmax"
    easy_low = _round_half_up(hr.easy_low_pct_hrmax * max_hr)
    first, last = hr.interval_zones
    return HrZones(
        zones=tuple(zones),
        max_hr=round(max_hr),
        lthr=round(lthr) if lthr is not None else None,
        easy_low=min(easy_low, easy_high),
        easy_high=easy_high,
        easy_basis=basis,
        interval_low=by_name[first].low,
        interval_high=by_name[last].high,
    )


def zones_from_config(cfg: EngineConfig) -> HrZones:
    return compute_zones(cfg.running_cfg.heart_rate)
