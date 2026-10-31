"""A measured VDOT from completed interval sessions (config `running.vdot.from_intervals`).

The quality VDOT otherwise rises only through races / time trials (a >= 5 km stretch at a hard
HR) or by inference (green quality sessions + an efficiency trend, +0.5 at a time). Interval reps
are a direct measurement, though: 5 x 1000 m at 4:22/km means the athlete's I pace (Daniels: 98 %
of VDOT, reps of 3-5 min) is at least that fast. This module turns the work reps of the recent
interval sessions into such an estimate:

* reps = the session's work laps (FIT intensity, else the pace heuristic of
  `running.split_intervals`) of `rep_min_s`..`rep_max_s` and >= `min_rep_m`; at least
  `min_reps` of them;
* run evenly: slowest - fastest <= `max_spread_pct` of the median, last rep at most
  `max_fade_pct` slower than the first (a fading or erratic session is no clean measurement);
* HR sanity (wrist HR lags, so it never adds anything): the median rep peak HR must reach
  `min_peak_hr_pct_lthr` x LTHR (a "4:22/km" at an easy HR is a GPS error, not fitness); reps with
  an average HR below the Z5 floor were sub-maximal, so the median pace is a lower bound of the I
  pace (not discounted), reps at Z5 are taken as I pace exactly - same number, the label says which;
* estimate = the VDOT whose I pace is the median rep pace, minus `age_discount_per_week` per week
  beyond `age_free_days`; sessions older than `window_days` (or before the last VDOT drop) do not
  count. The best session wins.

Pure functions, no state: the same runs give the same estimate on every re-run.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any

from apps.fitness.engine import running as R
from apps.fitness.engine.config import VdotFromIntervalsConfig
from apps.fitness.engine.hr import HrZones
from apps.fitness.engine.pace import FRAC_INTERVAL, fmt_pace, vdot_for_pace


@dataclass(frozen=True)
class RepSession:
    """One interval session whose reps measure the I pace."""

    day: date
    reps: int
    rep_m: float  # median rep distance
    median_pace_s_per_km: float
    avg_hr: float  # median of the reps' average HR
    peak_hr: float  # median of the reps' max HR
    effort: str  # submaximal | maximal
    raw_vdot: float  # VDOT whose I pace is the median rep pace
    age_days: int
    discount: float
    vdot: float  # raw_vdot - discount

    def label(self) -> str:
        return (
            f"{self.reps} x {self.rep_m:.0f} m on {self.day.isoformat()} at "
            f"{fmt_pace(self.median_pace_s_per_km)}/km"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.day.isoformat(),
            "reps": self.reps,
            "rep_m": round(self.rep_m),
            "median_pace_s_per_km": round(self.median_pace_s_per_km),
            "pace_label": fmt_pace(self.median_pace_s_per_km),
            "avg_hr": round(self.avg_hr),
            "peak_hr": round(self.peak_hr),
            "effort": self.effort,
            "raw_vdot": round(self.raw_vdot, 2),
            "age_days": self.age_days,
            "discount": round(self.discount, 2),
            "vdot": round(self.vdot, 2),
        }


@dataclass(frozen=True)
class RepEstimate:
    best: RepSession | None
    sessions: tuple[RepSession, ...] = ()
    skipped: dict[str, int] = field(default_factory=dict)  # why sessions did not count
    reason: str = ""

    @property
    def vdot(self) -> float | None:
        return self.best.vdot if self.best is not None else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "vdot": round(self.vdot, 2) if self.vdot is not None else None,
            "best": self.best.as_dict() if self.best is not None else None,
            "sessions": [s.as_dict() for s in self.sessions],
            "skipped": dict(sorted(self.skipped.items())),
            "reason": self.reason,
        }


def work_reps(run: R.RunData, cfg: VdotFromIntervalsConfig) -> list[R.LapData]:
    """The run's work laps that are usable reps (length / duration window, no GPS junk)."""
    sp = R.split_intervals(run.laps)
    if sp is None:
        return []
    return [
        lap
        for lap in sp.work
        if lap.pace
        and cfg.rep_min_s <= lap.duration_s <= cfg.rep_max_s
        and lap.distance_m >= cfg.min_rep_m
    ]


def session_from_run(
    run: R.RunData, zones: HrZones, cfg: VdotFromIntervalsConfig, *, today: date
) -> RepSession | str:
    """The run's measurement, or why it does not count (a skip key)."""
    reps = work_reps(run, cfg)
    if len(reps) < cfg.min_reps:
        return "few_reps"
    paces = [lap.pace or 0.0 for lap in reps]
    med = median(paces)
    if (max(paces) - min(paces)) / med * 100 > cfg.max_spread_pct + 1e-9:
        return "uneven"
    if (paces[-1] - paces[0]) / paces[0] * 100 > cfg.max_fade_pct + 1e-9:
        return "fade"
    avg = [lap.avg_hr for lap in reps if lap.avg_hr]
    if not avg:
        return "no_hr"
    peak = median([lap.max_hr or lap.avg_hr or 0.0 for lap in reps if lap.avg_hr])
    lthr = zones.lthr or 0.91 * zones.max_hr
    if peak < cfg.min_peak_hr_pct_lthr * lthr - 1e-9:
        return "hr_low"
    avg_hr = median(avg)
    effort = "maximal" if avg_hr >= zones.zone("z5a").low else "submaximal"
    raw = vdot_for_pace(med, FRAC_INTERVAL)
    age = (today - run.day).days
    discount = max(0, age - cfg.age_free_days) / 7 * cfg.age_discount_per_week
    return RepSession(
        run.day,
        len(reps),
        median([lap.distance_m for lap in reps]),
        med,
        avg_hr,
        peak,
        effort,
        raw,
        age,
        discount,
        raw - discount,
    )


def estimate_from_reps(
    runs: Sequence[R.RunData],
    zones: HrZones,
    cfg: VdotFromIntervalsConfig,
    *,
    today: date,
    after: date | None = None,
) -> RepEstimate:
    """The best age-discounted estimate of the window (see the module docstring).

    `after`: only sessions after this day count (the last VDOT drop: evidence from before a
    layoff or a fail-drop must not undo it)."""
    if not cfg.enabled:
        return RepEstimate(None, reason="VDOT from interval reps is off")
    since = today - timedelta(days=cfg.window_days)
    if after is not None and after > since:
        since = after
    out: list[RepSession] = []
    skipped: dict[str, int] = {}
    for r in sorted(runs, key=lambda x: (x.day, x.id)):
        if not (since < r.day <= today) or R.split_intervals(r.laps) is None:
            continue
        s = session_from_run(r, zones, cfg, today=today)
        if isinstance(s, str):
            skipped[s] = skipped.get(s, 0) + 1
        else:
            out.append(s)
    if not out:
        why = f"no interval session in {cfg.window_days} days measures the I pace"
        if skipped:
            why += " (" + ", ".join(f"{k}: {v}" for k, v in sorted(skipped.items())) + ")"
        return RepEstimate(None, (), skipped, why)
    best = max(out, key=lambda s: (round(s.vdot, 6), s.day))
    z5 = zones.zone("z5a").low
    hr_note = (
        f"avg rep HR {best.avg_hr:.0f} below Z5 {z5}: sub-maximal, so your I pace is at least this"
        if best.effort == "submaximal"
        else f"avg rep HR {best.avg_hr:.0f} at Z5: taken as I pace"
    )
    age_note = (
        f", -{best.discount:.1f} for its age ({best.age_days} days)" if best.discount > 0 else ""
    )
    reason = (
        f"{best.label()} ({hr_note}) = I pace of VDOT {best.raw_vdot:.1f}{age_note}: "
        f"VDOT {best.vdot:.1f}"
    )
    return RepEstimate(best, tuple(out), skipped, reason)
