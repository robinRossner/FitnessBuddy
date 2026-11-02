"""Pace models: a swappable Protocol plus the default `VdotPaceModel` (Jack Daniels' VDOT).

The planner only talks to the `PaceModel` Protocol; `running.pace_model` in the engine config
selects the implementation (`make_pace_model`). A second model (e.g. an HR-pace regression)
only needs to implement the Protocol and be added to `MODELS`.

VDOT formulas (Daniels & Gilbert 1979; "Daniels' Running Formula"), velocity v in m/min, t in min:
    VO2(v)        = -4.60 + 0.182258 v + 0.000104 v^2
    %VO2max(t)    = 0.8 + 0.1894393 e^(-0.012778 t) + 0.2989558 e^(-0.1932605 t)
    VDOT(d, t)    = VO2(d / t) / %VO2max(t)
Training paces are the velocities at a fraction of VDOT: easy 59-74 %, threshold 88 %, interval
98 % (reps of 3-5 min), repetition 105 %. Check values: VDOT 40 = 5 km 24:08 / 10 km 50:03;
VDOT 50: threshold 4:15/km, interval 3:54/km.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from apps.fitness.engine.core.config import EngineConfig


@dataclass(frozen=True)
class PaceObservation:
    distance_m: float
    duration_s: float
    avg_hr: float | None = None
    elevation_gain_m: float | None = None


@dataclass(frozen=True)
class PaceRange:
    min_s_per_km: float  # faster bound
    max_s_per_km: float  # slower bound


@runtime_checkable
class PaceModel(Protocol):
    name: str

    def fit(self, history: Sequence[PaceObservation]) -> None:
        """(Re)fit from recent best efforts (contiguous stretches of recent runs)."""
        ...

    def easy_pace(self, hr_ceiling: float | None = None) -> PaceRange | None:
        """Pace range expected at or below the easy-run HR ceiling (HR stays the governor)."""
        ...

    def interval_pace(self, rep_distance_m: float) -> PaceRange | None:
        """Target pace for interval reps of the given length."""
        ...

    def faster_pace(self, pace_s_per_km: float, steps: float = 1.0) -> float:
        """`pace_s_per_km` made faster by `steps` progression steps of the model."""
        ...

    def describe(self) -> dict:
        """Estimate + provenance for the decision log."""
        ...


# --------------------------------------------------------------------------- VDOT maths


def vo2_cost(v_m_min: float) -> float:
    return -4.60 + 0.182258 * v_m_min + 0.000104 * v_m_min**2


def pct_vo2max(t_min: float) -> float:
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)


def vdot_from_race(distance_m: float, time_s: float) -> float:
    t = time_s / 60
    return vo2_cost(distance_m / t) / pct_vo2max(t)


def velocity_at_vo2(vo2: float) -> float:
    """Inverse of `vo2_cost` (m/min)."""
    a, b, c = 0.000104, 0.182258, -4.60 - vo2
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


def pace_at_fraction(vdot: float, fraction: float) -> float:
    """Pace (s/km) at `fraction` of VDOT."""
    return 60_000 / velocity_at_vo2(vdot * fraction)


def vdot_for_pace(pace_s_per_km: float, fraction: float) -> float:
    """The VDOT whose `fraction`-pace equals `pace_s_per_km`."""
    return vo2_cost(60_000 / pace_s_per_km) / fraction


def race_time_s(vdot: float, distance_m: float) -> float:
    """Predicted race time (s): the t at which VDOT(d, t) == vdot (bisection)."""
    lo, hi = 30.0, 6 * 3600.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if vdot_from_race(distance_m, mid) > vdot:  # too fast for this VDOT -> need more time
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


FRAC_EASY_FAST, FRAC_EASY_SLOW = 0.74, 0.59
FRAC_THRESHOLD, FRAC_INTERVAL, FRAC_REPETITION = 0.88, 0.98, 1.05
_BAND = 0.015  # +-1.5 % pace band around a point target


def easy_range_for_vdot(vdot: float) -> PaceRange:
    """Daniels' easy range (59-74 % of VDOT): fast end first."""
    return PaceRange(pace_at_fraction(vdot, FRAC_EASY_FAST), pace_at_fraction(vdot, FRAC_EASY_SLOW))


class VdotPaceModel:
    name = "vdot"

    def __init__(
        self,
        seed_distance_m: float = 10_000,
        seed_time_s: float = 3150,
        *,
        min_effort_m: float = 5_000,
        min_effort_hr: float | None = None,
        step: float = 0.5,
    ) -> None:
        self.seed_vdot = vdot_from_race(seed_distance_m, seed_time_s)
        self.min_effort_m = min_effort_m
        self.min_effort_hr = min_effort_hr
        self.step = step
        self.vdot = self.seed_vdot
        self.best: PaceObservation | None = None
        self.efforts_used = 0

    @classmethod
    def from_config(cls, cfg: EngineConfig) -> VdotPaceModel:
        r = cfg.running_cfg
        hr = r.heart_rate
        min_hr = hr.lthr * r.vdot.min_effort_hr_pct_lthr if hr.lthr else None
        return cls(
            r.vdot.seed_race.distance_m,
            r.vdot.seed_race.time_s,
            min_effort_m=r.vdot.min_effort_km * 1000,
            min_effort_hr=min_hr,
            step=r.vdot.pace_step,
        )

    def fit(self, history: Sequence[PaceObservation]) -> None:
        """Seed VDOT, raised by the best hard effort (>= min distance, avg HR >= min_effort_hr).

        Elevation gain is ignored (documented simplification). Efforts without HR count only if
        no HR threshold is set.
        """
        self.vdot, self.best, self.efforts_used = self.seed_vdot, None, 0
        for o in history:
            if o.distance_m < self.min_effort_m or o.duration_s <= 0:
                continue
            if self.min_effort_hr is not None and (
                o.avg_hr is None or o.avg_hr < self.min_effort_hr
            ):
                continue
            self.efforts_used += 1
            v = vdot_from_race(o.distance_m, o.duration_s)
            if v > self.vdot:
                self.vdot, self.best = v, o

    # ---- paces
    def easy_pace(self, hr_ceiling: float | None = None) -> PaceRange:
        return easy_range_for_vdot(self.vdot)

    def threshold_pace(self) -> float:
        return pace_at_fraction(self.vdot, FRAC_THRESHOLD)

    def interval_pace(self, rep_distance_m: float) -> PaceRange:
        frac = FRAC_REPETITION if rep_distance_m <= 400 else FRAC_INTERVAL
        p = pace_at_fraction(self.vdot, frac)
        return PaceRange(p * (1 - _BAND), p * (1 + _BAND))

    def faster_pace(self, pace_s_per_km: float, steps: float = 1.0) -> float:
        """One step = +`step` VDOT on the interval-pace scale (about 1 % per 0.5 VDOT)."""
        v = vdot_for_pace(pace_s_per_km, FRAC_INTERVAL) + steps * self.step
        return pace_at_fraction(v, FRAC_INTERVAL)

    def race_time(self, distance_m: float) -> float:
        return race_time_s(self.vdot, distance_m)

    def describe(self) -> dict:
        d = {
            "model": self.name,
            "vdot": round(self.vdot, 2),
            "seed_vdot": round(self.seed_vdot, 2),
            "source": "effort" if self.best else "seed",
            "efforts_considered": self.efforts_used,
        }
        if self.best:
            d["best_effort"] = {
                "distance_m": round(self.best.distance_m),
                "duration_s": round(self.best.duration_s),
                "avg_hr": self.best.avg_hr,
            }
        return d


MODELS: dict[str, Callable[[EngineConfig], PaceModel]] = {"vdot": VdotPaceModel.from_config}


def make_pace_model(cfg: EngineConfig) -> PaceModel:
    """The pace model selected by `running.pace_model` (default `vdot`)."""
    name = cfg.running_cfg.pace_model
    try:
        return MODELS[name](cfg)
    except KeyError:
        raise ValueError(f"unknown pace_model {name!r}; available: {sorted(MODELS)}") from None


def fmt_pace(s_per_km: float) -> str:
    s = round(s_per_km)
    return f"{s // 60}:{s % 60:02d}"
