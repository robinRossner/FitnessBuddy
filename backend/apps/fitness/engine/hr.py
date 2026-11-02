"""Heart-rate zones from Garmin's zone floors, else derived from LTHR / max HR (pure functions).

Zones are never stored. With `running.heart_rate.source: garmin` and valid floors in
`athlete_metrics` they are Garmin's own zones (`garmin_zones`), so targets and watch workouts match
what the watch shows. Otherwise they are computed from the active config's `running.heart_rate`
(`compute_zones`), so a new LTHR changes every zone, the easy-run ceiling and the interval target
at once.

A stored run is judged by the zones in force when it was run, not today's: see `ZoneBook`.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from typing import Protocol

from apps.fitness.engine.config import EngineConfig, HeartRateConfig

log = logging.getLogger(__name__)

ZONE_ORDER = ("z1", "z2", "z3", "z4", "z5a", "z5b", "z5c")
# The engine's targets came from LTHR until this day, so earlier runs keep that ceiling. Garmin's
# floors of that time (zones from % of max HR) described the watch, they were never the target.
GARMIN_ZONES_SINCE = date(2026, 11, 2)
_warned_floors: set[tuple[float, ...]] = set()


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
    easy_basis: str  # "lthr" | "hrmax" | "garmin"
    interval_low: int
    interval_high: int
    source: str = "lthr"  # "lthr" | "garmin"
    threshold_low: int | None = None  # Garmin Z4; None: % of LTHR from the threshold config
    threshold_high: int | None = None
    easy_label: str = "Z1-Z2"
    interval_label: str = "Z4-Z5a"

    @property
    def z5_low(self) -> int:
        """Where Z5 starts (`z5` from Garmin, `z5a` from LTHR)."""
        return next(z.low for z in self.zones if z.name in ("z5", "z5a"))

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
            "source": self.source,
            "max_hr": self.max_hr,
            "lthr": self.lthr,
            "zones": {z.name: [z.low, z.high] for z in self.zones},
            "easy": {"low": self.easy_low, "high": self.easy_high, "basis": self.easy_basis},
            "intervals": {"low": self.interval_low, "high": self.interval_high},
            "threshold": {"low": self.threshold_low, "high": self.threshold_high},
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


def _max_hr(hr: HeartRateConfig) -> float | None:
    return hr.max_hr if hr.max_hr is not None else (hr.lthr / 0.91 if hr.lthr else None)


def valid_floors(floors: Sequence[float] | None, max_hr: float | None) -> bool:
    """Five strictly ascending values that look like bpm. Garmin's floors have only been seen with
    `trainingMethod: HR_MAX`; percentages (possible under HR_RESERVE) must not pass as bpm."""
    if floors is None or len(floors) != 5:
        return False
    if any(b <= a for a, b in pairwise(floors)):
        return False
    return floors[0] >= 50 and floors[4] > 100 and (max_hr is None or floors[4] <= max_hr)


def garmin_zones(hr: HeartRateConfig, floors: Sequence[float]) -> HrZones:
    """Garmin's five zones as engine zones (`floors` already passed `valid_floors`).

    Easy = Z2-Z3, threshold = Z4, VO2max / 5k = Z5 up to max HR. Z1 is open at the bottom like the
    LTHR zones, so every HR has a zone. LTHR stays as configured: VO2max / 5k reps' green line and
    safety ceiling (`quality.hr_lines`) and the VDOT effort checks are still % of LTHR.
    """
    f = [_round_half_up(x) for x in floors]
    max_hr = _max_hr(hr)
    top = max(_round_half_up(max_hr), f[4]) if max_hr is not None else f[4]
    zones = tuple(
        Zone(f"z{i + 1}", 0 if i == 0 else f[i], f[i + 1] - 1 if i < 4 else top) for i in range(5)
    )
    return HrZones(
        zones=zones,
        max_hr=top,
        lthr=round(hr.lthr) if hr.lthr is not None else None,
        easy_low=f[1],
        easy_high=f[3] - 1,
        easy_basis="garmin",
        interval_low=f[4],
        interval_high=top,
        source="garmin",
        threshold_low=f[3],
        threshold_high=f[4] - 1,
        easy_label="Z2-Z3",
        interval_label="Z5",
    )


def _usable_floors(hr: HeartRateConfig, floors: Sequence[float] | None) -> tuple[float, ...] | None:
    """`floors` if the config follows Garmin and they are valid; warns once about invalid ones."""
    if hr.source != "garmin" or floors is None:
        return None
    key = tuple(floors)
    if valid_floors(key, _max_hr(hr)):
        return key
    if key not in _warned_floors:
        _warned_floors.add(key)
        log.warning("ignoring Garmin zone floors %s (not bpm?): using the LTHR zones", key)
    return None


def zones_from_config(cfg: EngineConfig, floors: Sequence[float] | None = None) -> HrZones:
    """The current zones: Garmin's `floors` (the latest ones) when usable, else from LTHR."""
    hr = cfg.running_cfg.heart_rate
    usable = _usable_floors(hr, floors)
    return garmin_zones(hr, usable) if usable else compute_zones(hr)


class _Run(Protocol):
    day: date
    zone_floors: tuple[float, ...] | None


@dataclass
class ZoneBook:
    """Today's zones for what is planned, and for a stored run the zones in force when it was run.

    So a zone change on Garmin never re-judges a past run. A run's floors are the newest
    `athlete_metrics` row dated on or before it (`RunData.zone_floors`); runs before
    `GARMIN_ZONES_SINCE` keep the LTHR zones the engine targeted then.
    """

    hr: HeartRateConfig
    current: HrZones
    lthr_zones: HrZones
    _by_floors: dict[tuple[float, ...], HrZones] = field(default_factory=dict)

    def for_run(self, run: _Run) -> HrZones:
        usable = _usable_floors(self.hr, run.zone_floors) if run.day >= GARMIN_ZONES_SINCE else None
        if usable is None:
            return self.lthr_zones
        if usable not in self._by_floors:
            self._by_floors[usable] = garmin_zones(self.hr, usable)
        return self._by_floors[usable]


def zone_book(cfg: EngineConfig, latest_floors: Sequence[float] | None) -> ZoneBook:
    hr = cfg.running_cfg.heart_rate
    return ZoneBook(hr, zones_from_config(cfg, latest_floors), compute_zones(hr))
