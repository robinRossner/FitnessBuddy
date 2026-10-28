"""Easy-pace calibration: the easy / long run pace range from the athlete's own easy runs.

Daniels' E pace (59-74 % of VDOT) comes from the quality VDOT, which only moves through races,
time trials and quality sessions. A runner whose aerobic base is ahead of that number runs his
easy runs faster than the range at an easy HR. This module looks at the recent steady runs that
were genuinely easy by HR (config `running.easy_calibration`, see `EasyCalibrationConfig`) and,
with enough of them, sets an "easy VDOT": the VDOT whose fast end of the easy range (74 %) is
their median pace. It is clamped to [quality VDOT, quality VDOT + max_vdot_gain]: calibration
only ever speeds the easy range up, and HR stays the governor on the day. Interval, threshold,
5k and time-trial paces (and the quality VDOT itself) are untouched. Pure functions, no state:
the same runs give the same result.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any

from app.engine import running as R
from app.engine.config import EasyCalibrationConfig, EasyRunConfig
from app.engine.hr import HrZones
from app.engine.pace import (
    FRAC_EASY_FAST,
    PaceRange,
    easy_range_for_vdot,
    fmt_pace,
    vdot_for_pace,
)


@dataclass(frozen=True)
class EasySample:
    """One qualifying run."""

    day: date
    km: float
    pace_s_per_km: float
    avg_hr: float
    decoupling_pct: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.day.isoformat(),
            "km": round(self.km, 1),
            "pace_s_per_km": round(self.pace_s_per_km),
            "avg_hr": round(self.avg_hr),
            "decoupling_pct": round(self.decoupling_pct, 1),
        }


@dataclass(frozen=True)
class EasyCalibration:
    vdot: float  # the VDOT the easy / long ranges use
    quality_vdot: float
    calibrated: bool
    reason: str  # one human sentence for the plan
    hr_ceiling: int | None
    samples: tuple[EasySample, ...] = ()
    median_pace_s_per_km: float | None = None
    estimate_vdot: float | None = None  # uncapped VDOT of the median pace (None: too few runs)
    capped: bool = False
    skipped: dict[str, int] = field(default_factory=dict)  # why runs did not count -> how many

    @property
    def basis(self) -> str:
        return "calibrated" if self.calibrated else "daniels"

    def easy_range(self) -> PaceRange:
        return easy_range_for_vdot(self.vdot)

    def as_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis,
            "vdot": round(self.vdot, 2),
            "quality_vdot": round(self.quality_vdot, 2),
            "reason": self.reason,
            "hr_ceiling": self.hr_ceiling,
            "runs": [s.as_dict() for s in self.samples],
            "median_pace_s_per_km": (
                round(self.median_pace_s_per_km) if self.median_pace_s_per_km else None
            ),
            "estimate_vdot": round(self.estimate_vdot, 2) if self.estimate_vdot else None,
            "capped": self.capped,
            "skipped": dict(sorted(self.skipped.items())),
        }


def _skip_reason(
    run: R.RunData,
    zones: HrZones,
    easy: EasyRunConfig,
    cfg: EasyCalibrationConfig,
    exclude_days: Collection[date],
) -> str | None:
    """Why `run` does not count as a calibration sample (None: it counts)."""
    if run.day in exclude_days or R.is_interval_run(run):
        return "quality"
    if run.distance_m <= 0 or run.duration_s < cfg.min_minutes * 60:
        return "short"
    if run.avg_hr is None:
        return "no_hr"
    if run.avg_hr > zones.easy_high - cfg.hr_margin_bpm + 1e-9:
        return "hr_high"
    climb = run.elevation_gain_m / run.km if run.elevation_gain_m is not None else 0.0
    if climb > cfg.max_elevation_m_per_km + 1e-9:
        return "hilly"
    ev = R.evaluate_easy_run(run, zones, easy)  # no day-after-lower exemption: a genuine pass
    if not ev.passed:
        return "hr_rule"
    if ev.decoupling_pct is None:
        return "no_laps"
    if ev.decoupling_pct > easy.decoupling_flag_pct + 1e-9:
        return "drift"
    return None


def qualifying_runs(
    runs: Sequence[R.RunData],
    zones: HrZones,
    easy: EasyRunConfig,
    cfg: EasyCalibrationConfig,
    *,
    today: date,
    exclude_days: Collection[date] = (),
) -> tuple[list[EasySample], dict[str, int]]:
    """The steady, genuinely easy runs of the window (oldest first) and the skip counts."""
    since = today - timedelta(days=cfg.window_days)
    out: list[EasySample] = []
    skipped: dict[str, int] = {}
    for r in sorted(runs, key=lambda x: (x.day, x.id)):
        if not (since < r.day <= today):
            continue
        why = _skip_reason(r, zones, easy, cfg, exclude_days)
        if why is not None:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        dec = R.decoupling_pct(r.laps)
        out.append(EasySample(r.day, r.km, r.pace or 0.0, float(r.avg_hr or 0), float(dec or 0)))
    return out, skipped


def calibrate_easy(
    runs: Sequence[R.RunData],
    zones: HrZones,
    easy: EasyRunConfig,
    cfg: EasyCalibrationConfig,
    quality_vdot: float,
    *,
    today: date,
    exclude_days: Collection[date] = (),
) -> EasyCalibration:
    """The VDOT for the easy / long run pace range (see the module docstring)."""
    q = quality_vdot
    ceiling = zones.easy_high

    def daniels(why: str, **kw: Any) -> EasyCalibration:
        return EasyCalibration(
            q, q, False, f"Daniels easy pace for VDOT {q:.1f}: {why}", ceiling, **kw
        )

    if not cfg.enabled:
        return daniels("calibration from your easy runs is off")
    samples, skipped = qualifying_runs(
        runs, zones, easy, cfg, today=today, exclude_days=exclude_days
    )
    n = len(samples)
    hr_limit = ceiling - cfg.hr_margin_bpm
    if n < cfg.min_runs:
        return daniels(
            f"only {n} of {n + sum(skipped.values())} runs in {cfg.window_days} days count as "
            f"genuinely easy (easy HR rule passed, avg HR <= {hr_limit:g}, no drift); "
            f"need {cfg.min_runs}",
            samples=tuple(samples),
            skipped=skipped,
        )
    paces = [s.pace_s_per_km for s in samples]
    med = median(paces)
    est = vdot_for_pace(med, FRAC_EASY_FAST)
    span = f"{fmt_pace(min(paces))}-{fmt_pace(max(paces))}/km, median {fmt_pace(med)}"
    common = {"samples": tuple(samples), "median_pace_s_per_km": med, "estimate_vdot": est}
    if est <= q + 1e-9:
        return daniels(
            f"your {n} easy runs of {cfg.window_days} days at HR <= {hr_limit:g} ({span}) fit it",
            skipped=skipped,
            **common,
        )
    v = min(est, q + cfg.max_vdot_gain)
    capped = est > v + 1e-9
    reason = (
        f"Easy pace from your {n} easy runs of {cfg.window_days} days at HR <= {hr_limit:g} "
        f"({span}): "
        f"easy VDOT {v:.1f} vs {q:.1f} for quality sessions"
        + (f" (capped at +{cfg.max_vdot_gain:g})" if capped else "")
    )
    return EasyCalibration(v, q, True, reason, ceiling, capped=capped, skipped=skipped, **common)
