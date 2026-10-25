"""Running evaluation and progression. Pure functions over plain data (laps, runs, HR zones).

* classification of a recorded run (intervals / easy / long) from its laps,
* easy-run evaluation: pass = avg HR <= easy ceiling and >= X % of the time at/below it,
  aerobic efficiency (speed / HR) and decoupling (HR drift between first and second half),
* interval evaluation: work vs recovery laps (FIT lap intensity if it distinguishes them, else
  a pace heuristic), work HR vs the target zone, HR drop in the recovery laps, and the
  progression rule (+1 rep up to max reps, then a faster pace and back to the minimum reps),
* long-run distance progression, the weekly-volume cap and "metric not improving" detection.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import fmean, median
from typing import Any

from app.engine.config import EasyRunConfig, IntervalConfig, LongRunConfig
from app.engine.hr import HrZones
from app.engine.pace import PaceModel, PaceObservation

WORK_INTENSITIES = {"active", "interval"}
REST_INTENSITIES = {"recovery", "rest"}
SKIP_INTENSITIES = {"warmup", "cooldown"}


@dataclass(frozen=True)
class LapData:
    duration_s: float
    distance_m: float
    avg_hr: float | None = None
    max_hr: float | None = None
    intensity: str | None = None

    @property
    def pace(self) -> float | None:
        return self.duration_s / self.distance_m * 1000 if self.distance_m > 0 else None


@dataclass(frozen=True)
class RunData:
    id: str
    day: date
    distance_m: float
    duration_s: float
    avg_hr: float | None = None
    max_hr: float | None = None
    laps: tuple[LapData, ...] = ()
    time_in_zones_s: dict[str, float] | None = None
    zone_floors: tuple[float, ...] | None = None  # Garmin z1..z5 floors valid for this run
    title: str | None = None

    @property
    def km(self) -> float:
        return self.distance_m / 1000

    @property
    def pace(self) -> float | None:
        return self.duration_s / self.distance_m * 1000 if self.distance_m > 0 else None


# --------------------------------------------------------------------------- generic metrics


def efficiency(distance_m: float, duration_s: float, avg_hr: float | None) -> float | None:
    """Aerobic efficiency = speed (m/min) per heartbeat-rate (bpm); higher = fitter."""
    if not avg_hr or duration_s <= 0 or distance_m <= 0:
        return None
    return round((distance_m / (duration_s / 60)) / avg_hr, 4)


def decoupling_pct(laps: Sequence[LapData]) -> float | None:
    """Pa:HR decoupling: (EF first half - EF second half) / EF first half in %. Positive = drift.

    Halves are split by elapsed time (a lap belongs to the half its midpoint falls in); needs
    at least 4 laps with HR.
    """
    ls = [lap for lap in laps if lap.avg_hr and lap.duration_s > 0 and lap.distance_m > 0]
    if len(ls) < 4:
        return None
    total = sum(lap.duration_s for lap in ls)
    t = 0.0
    first: list[LapData] = []
    second: list[LapData] = []
    for lap in ls:
        mid = t + lap.duration_s / 2
        (first if mid < total / 2 else second).append(lap)
        t += lap.duration_s
    if not first or not second:
        return None

    def ef(group: list[LapData]) -> float:
        dur = sum(lap.duration_s for lap in group)
        dist = sum(lap.distance_m for lap in group)
        hr = sum(lap.avg_hr * lap.duration_s for lap in group) / dur
        return (dist / (dur / 60)) / hr

    e1, e2 = ef(first), ef(second)
    return round((e1 - e2) / e1 * 100, 2)


def fraction_at_or_below(
    cap: float,
    time_in_zones_s: dict[str, float] | None,
    floors: Sequence[float] | None,
    laps: Sequence[LapData] = (),
) -> tuple[float | None, str]:
    """Share (0..1) of the time at or below HR `cap`, and how it was estimated.

    Prefers Garmin's zone seconds (zone floors from athlete_metrics; a zone straddling the cap is
    split linearly), else the laps (a lap counts fully if its avg HR is <= cap), else None.
    """
    if time_in_zones_s and floors and len(floors) >= 2:
        secs = [float(time_in_zones_s.get(f"z{i + 1}", 0.0)) for i in range(len(floors))]
        total = sum(secs)
        if total > 0:
            below = 0.0
            for i, s in enumerate(secs):
                lo = float(floors[i]) if i > 0 else 0.0
                hi = float(floors[i + 1]) - 1 if i + 1 < len(floors) else 250.0
                if hi <= cap:
                    below += s
                elif lo <= cap:
                    below += s * (cap - lo + 1) / (hi - lo + 1)
            return below / total, "garmin_zones"
    ls = [lap for lap in laps if lap.avg_hr and lap.duration_s > 0]
    if ls:
        total = sum(lap.duration_s for lap in ls)
        below = sum(lap.duration_s for lap in ls if lap.avg_hr <= cap)
        return below / total, "laps"
    return None, "none"


# --------------------------------------------------------------------------- interval laps


@dataclass
class IntervalSplit:
    work: list[LapData]
    rest: list[LapData | None]  # rest[i] = the recovery lap right after work[i] (None if none)
    method: str  # "intensity" | "pace"


def split_intervals(laps: Sequence[LapData]) -> IntervalSplit | None:
    """Find work and recovery laps; None if the run does not look like an interval session."""
    ls = [lap for lap in laps if lap.pace]
    if len(ls) < 3:
        return None
    # 1) FIT intensity, if it separates work from recovery (all-"interval" laps of an easy run don't)  # noqa: E501
    work = [lap for lap in ls if (lap.intensity or "").lower() in WORK_INTENSITIES]
    rest = [lap for lap in ls if (lap.intensity or "").lower() in REST_INTENSITIES]
    if len(work) >= 2 and rest:
        return IntervalSplit(work, _align_rest(ls, work, rest), "intensity")
    # 2) pace heuristic: clearly faster laps with slower laps between them
    paces = [lap.pace for lap in ls if lap.pace]
    med = median(paces)
    fast = [lap for lap in ls if lap.pace and lap.pace < med * 0.92]
    if len(fast) < 2:
        return None
    idx = [i for i, lap in enumerate(ls) if lap in fast]
    inner = ls[idx[0] : idx[-1] + 1]
    gaps = [lap for lap in inner if lap not in fast]
    if not gaps:
        return None
    return IntervalSplit(fast, _align_rest(ls, fast, gaps), "pace")


def _align_rest(
    ls: list[LapData], work: list[LapData], rest: list[LapData]
) -> list[LapData | None]:
    """For each work lap the recovery lap right after it, or None."""
    rest_ids = {id(r) for r in rest}
    pos = {id(lap): i for i, lap in enumerate(ls)}
    out: list[LapData | None] = []
    for w in work:
        i = pos[id(w)]
        nxt = ls[i + 1] if i + 1 < len(ls) else None
        out.append(nxt if nxt is not None and id(nxt) in rest_ids else None)
    return out


@dataclass
class IntervalEval:
    reps: int
    work_pace_s_per_km: float
    work_hr: float | None
    hr_in_zone: bool | None
    hr_too_high: bool
    hr_below_zone: bool
    recovery_drop_bpm: float | None
    recovery_ok: bool | None
    pace_fade_pct: float | None
    passed: bool
    reasons: list[str] = field(default_factory=list)
    method: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def evaluate_intervals(
    split: IntervalSplit,
    zones: HrZones,
    cfg: IntervalConfig,
    *,
    prescribed_reps: int | None = None,
) -> IntervalEval:
    work = split.work
    dur = sum(lap.duration_s for lap in work)
    dist = sum(lap.distance_m for lap in work)
    pace = dur / dist * 1000
    hrs = [lap for lap in work if lap.avg_hr]
    work_hr = (
        sum(lap.avg_hr * lap.duration_s for lap in hrs) / sum(lap.duration_s for lap in hrs)
        if hrs
        else None
    )
    reasons: list[str] = []
    in_zone = None
    too_high = below = False
    if work_hr is not None:
        in_zone = zones.interval_low <= work_hr <= zones.interval_high
        too_high = work_hr > zones.interval_high
        below = work_hr < zones.interval_low
        if too_high:
            reasons.append(f"work HR {work_hr:.0f} above the target zone ({zones.interval_high})")
        elif below:
            reasons.append(f"work HR {work_hr:.0f} below the target zone ({zones.interval_low})")
    drops = [
        w.avg_hr - r.avg_hr
        for w, r in zip(work, split.rest, strict=False)
        if r is not None and w.avg_hr and r.avg_hr
    ]
    drop = fmean(drops) if drops else None
    recovery_ok = None if drop is None else drop >= cfg.hr_recovery_min_bpm
    if recovery_ok is False:
        reasons.append(
            f"HR drop in the recovery laps only {drop:.0f} bpm (< {cfg.hr_recovery_min_bpm:g})"
        )
    fade = None
    if len(work) >= 2 and work[0].pace and work[-1].pace:
        fade = (work[-1].pace - work[0].pace) / work[0].pace * 100
    short = prescribed_reps is not None and len(work) < prescribed_reps
    if short:
        reasons.append(f"only {len(work)} of {prescribed_reps} reps")
    passed = not too_high and recovery_ok is not False and not short
    return IntervalEval(
        reps=len(work),
        work_pace_s_per_km=pace,
        work_hr=work_hr,
        hr_in_zone=in_zone,
        hr_too_high=too_high,
        hr_below_zone=below,
        recovery_drop_bpm=drop,
        recovery_ok=recovery_ok,
        pace_fade_pct=fade,
        passed=passed,
        reasons=reasons,
        method=split.method,
    )


@dataclass
class IntervalPlan:
    reps: int
    pace_s_per_km: float
    rule: str
    reason: str


def next_interval_plan(
    last: IntervalEval | None, model: PaceModel, cfg: IntervalConfig
) -> IntervalPlan:
    """Progression: pass -> +1 rep (max `reps_max`), at max reps -> faster pace (VDOT step) and
    back to `reps_min`; fail -> repeat. No history -> `reps_start` at the model's interval pace."""
    if last is None:
        rng = model.interval_pace(cfg.rep_distance_m)
        pace = (rng.min_s_per_km + rng.max_s_per_km) / 2
        return IntervalPlan(
            cfg.reps_start,
            round(pace),
            "intervals:start",
            f"no earlier interval session: {cfg.reps_start} reps at the model's interval pace",
        )
    pace = round(last.work_pace_s_per_km)
    if not last.passed:
        why = "; ".join(last.reasons) or "criteria not met"
        return IntervalPlan(last.reps, pace, "intervals:repeat", f"repeat {last.reps} reps ({why})")
    if last.reps < cfg.reps_max:
        return IntervalPlan(
            last.reps + 1,
            pace,
            "intervals:add_rep",
            f"HR in range and recovery fine -> {last.reps + 1} reps at the same pace",
        )
    new_pace = round(model.faster_pace(pace, 1.0))
    return IntervalPlan(
        cfg.reps_min,
        min(new_pace, pace - 1),
        "intervals:faster_pace",
        f"{last.reps} reps done cleanly -> faster pace and back to {cfg.reps_min} reps",
    )


# --------------------------------------------------------------------------- easy runs


@dataclass
class EasyEval:
    passed: bool
    avg_hr: float | None
    pct_at_or_below_cap: float | None
    pct_source: str
    efficiency: float | None
    decoupling_pct: float | None
    decoupling_flag: bool
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def evaluate_easy_run(run: RunData, zones: HrZones, cfg: EasyRunConfig) -> EasyEval:
    cap = zones.easy_high
    reasons: list[str] = []
    ok = True
    if run.avg_hr is None:
        ok = False
        reasons.append("no average HR")
    elif run.avg_hr > cap:
        ok = False
        reasons.append(f"avg HR {run.avg_hr:.0f} above the easy ceiling {cap}")
    frac, src = fraction_at_or_below(cap, run.time_in_zones_s, run.zone_floors, run.laps)
    pct = None if frac is None else frac * 100
    if pct is not None and pct < cfg.pass_pct_time_at_or_below_cap:
        ok = False
        reasons.append(
            f"only {pct:.0f}% of the time at/below {cap} (need {cfg.pass_pct_time_at_or_below_cap:g}%)"  # noqa: E501
        )
    dec = decoupling_pct(run.laps)
    flag = dec is not None and dec > cfg.decoupling_flag_pct
    if flag:
        reasons.append(f"HR drift {dec:.1f}% (> {cfg.decoupling_flag_pct:g}%)")
    return EasyEval(
        passed=ok,
        avg_hr=run.avg_hr,
        pct_at_or_below_cap=pct,
        pct_source=src,
        efficiency=efficiency(run.distance_m, run.duration_s, run.avg_hr),
        decoupling_pct=dec,
        decoupling_flag=flag,
        reasons=reasons,
    )


def is_interval_run(run: RunData) -> bool:
    if "interval" in (run.title or "").lower():
        return True
    return split_intervals(run.laps) is not None


# --------------------------------------------------------------------------- best efforts


def best_effort(run: RunData, min_m: float) -> PaceObservation | None:
    """Fastest contiguous stretch of >= `min_m` metres (by laps), else the whole run."""
    laps = [lap for lap in run.laps if lap.distance_m > 0 and lap.duration_s > 0]
    best: tuple[float, list[LapData]] | None = None
    for i in range(len(laps)):
        dist = dur = 0.0
        for j in range(i, len(laps)):
            dist += laps[j].distance_m
            dur += laps[j].duration_s
            if dist >= min_m:
                pace = dur / dist
                if best is None or pace < best[0]:
                    best = (pace, laps[i : j + 1])
                break
    if best is not None:
        group = best[1]
        dur = sum(lap.duration_s for lap in group)
        dist = sum(lap.distance_m for lap in group)
        hr_laps = [lap for lap in group if lap.avg_hr]
        hr = (
            sum(lap.avg_hr * lap.duration_s for lap in hr_laps)
            / sum(lap.duration_s for lap in hr_laps)
            if hr_laps
            else None
        )
        return PaceObservation(dist, dur, hr)
    if run.distance_m >= min_m and run.duration_s > 0:
        return PaceObservation(run.distance_m, run.duration_s, run.avg_hr)
    return None


# --------------------------------------------------------------------------- volume + long run


def iso_week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weekly_km(runs: Sequence[RunData]) -> dict[date, float]:
    out: dict[date, float] = {}
    for r in runs:
        w = iso_week_start(r.day)
        out[w] = out.get(w, 0.0) + r.km
    return out


def volume_cap_km(
    weekly: dict[date, float],
    week_start: date,
    cap_pct: float,
    *,
    weeks_avg: int = 4,
    floor_km: float = 0.0,
) -> float | None:
    """Max km for the week starting `week_start`:
    max(floor, (1 + cap) x max(last week, 4-week average)).

    The floor stops a tiny running base from producing nonsense caps. With no history and no
    floor there is nothing to cap against (None).
    """
    prev = [weekly.get(week_start - timedelta(days=7 * i), 0.0) for i in range(1, weeks_avg + 1)]
    if not any(prev):
        return floor_km or None
    base = max(prev[0], fmean(prev))
    return round(max(floor_km, base * (1 + cap_pct / 100)), 2)


def fit_to_cap(
    planned: dict[str, float], cap_km: float | None, long_min_km: float, easy_min_km: float = 3.0
) -> tuple[dict[str, float], list[str]]:
    """Scale the planned km down to `cap_km`: long run first (to `long_min_km`), then easy runs
    (to `easy_min_km`). Interval sessions keep their structure. Returns (km by slot, notes)."""
    out = dict(planned)
    notes: list[str] = []
    if cap_km is None or sum(out.values()) <= cap_km:
        return out, notes
    for slot, floor in (("long_run", long_min_km), ("easy_run", easy_min_km)):
        excess = sum(out.values()) - cap_km
        if excess <= 0 or slot not in out:
            continue
        new = max(floor, out[slot] - excess)
        new = round(new * 2) / 2
        if new < out[slot]:
            notes.append(f"{slot} {out[slot]:g} -> {new:g} km (10% weekly cap {cap_km:g} km)")
            out[slot] = new
    return out, notes


def next_long_run_km(
    recent_long: float | None, passed: bool, cfg: LongRunConfig
) -> tuple[float, str]:
    """Distance for the next long run: last long run + step if it was easy/clean, else hold."""
    if recent_long is None:
        return cfg.start_km, f"no recent long run: start at {cfg.start_km:g} km"
    base = recent_long
    km = base + cfg.step_km if passed else base
    km = min(round(km * 2) / 2, cfg.cap_km)
    km = max(km, cfg.min_km)
    why = (
        f"+{cfg.step_km:g} km after a clean long run" if passed else "hold: last long run not clean"
    )
    return km, f"last {base:.1f} km, {why}"


# --------------------------------------------------------------------------- stagnation


def stagnation(
    series: Sequence[tuple[date, float]], end: date, weeks: int, min_improvement_pct: float
) -> dict[str, Any] | None:
    """Mean of the last `weeks` weeks vs the `weeks` before; higher is better.

    Returns details if the improvement is below `min_improvement_pct` (needs >= 2 points in each
    window), else None.
    """
    recent_from = end - timedelta(days=7 * weeks - 1)
    prior_from = recent_from - timedelta(days=7 * weeks)
    recent = [v for d, v in series if recent_from <= d <= end]
    prior = [v for d, v in series if prior_from <= d < recent_from]
    if len(recent) < 2 or len(prior) < 2:
        return None
    a, b = fmean(recent), fmean(prior)
    change = (a - b) / b * 100
    if change >= min_improvement_pct:
        return None
    return {
        "weeks": weeks,
        "recent_mean": round(a, 4),
        "prior_mean": round(b, 4),
        "change_pct": round(change, 2),
        "n_recent": len(recent),
        "n_prior": len(prior),
    }
