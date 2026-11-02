"""Running evaluation and progression. Pure functions over plain data (laps, runs, HR zones).

* classification of a recorded run (intervals / easy / long) from its laps,
* easy-run evaluation: pass = avg HR <= easy ceiling and >= X % of the time at/below it,
  aerobic efficiency (speed / HR) and decoupling (HR drift between first and second half),
* interval evaluation: work vs recovery laps (FIT lap intensity if it distinguishes them, else
  a pace heuristic), judged by PACE (share of reps on pace, fade); HR is a safety ceiling and a
  multi-week trend only (docs/research/running-progression.md section 4),
* long-run distance progression (step-back weeks, drift gate), the weekly-volume cap from the
  4-week actual mean, the single-run cap and "metric not improving" detection.
The Friday quality cycle and its levers live in `quality.py`, VDOT updates in `vdot.py`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import fmean, median
from typing import Any

from apps.fitness.engine.core.config import EasyRunConfig, IntervalConfig, LongRunConfig
from apps.fitness.engine.core.hr import HrZones
from apps.fitness.engine.core.pace import PaceObservation

WORK_INTENSITIES = {"active", "interval"}
REST_INTENSITIES = {"recovery", "rest"}
SKIP_INTENSITIES = {"warmup", "cooldown"}
EASY_GARMIN_ZONES = 3  # Garmin z1..z3 count as easy in the easy-run time share


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
    elevation_gain_m: float | None = None

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


def easy_time_fraction(
    cap: float,
    time_in_zones_s: dict[str, float] | None,
    laps: Sequence[LapData] = (),
) -> tuple[float | None, str]:
    """Share (0..1) of the time that counts as easy, and how it was measured.

    Prefers Garmin's own seconds in z1..EASY_GARMIN_ZONES, so it follows the zones set on Garmin
    (resting / max HR) without any bpm conversion here; else the laps (a lap counts fully if its
    avg HR is <= cap), else None.
    """
    if time_in_zones_s:
        secs = [float(time_in_zones_s.get(f"z{i}", 0.0)) for i in range(1, 6)]
        total = sum(secs)
        if total > 0:
            return sum(secs[:EASY_GARMIN_ZONES]) / total, "garmin_zones"
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
    # v2: judged by pace, HR is a safety ceiling only
    target_pace_s_per_km: float | None = None
    reps_on_pace: int | None = None
    pace_ok_share: float | None = None
    last_rep_peak_hr: float | None = None
    hr_ceiling_bpm: float | None = None
    hr_safety_exceeded: bool | None = None
    green: bool = False  # passed easily (the last rep's peak HR below the green line)
    verdict: str = ""  # green | pass | pass_hard | fail | no_target
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def lap_on_pace(pace: float | None, target: float | None, tol_pct: float) -> bool | None:
    """A rep is on pace unless slower than the target by more than `tol_pct` % (faster is fine)."""
    if pace is None or target is None:
        return None
    return pace <= target * (1 + tol_pct / 100) + 1e-9


def evaluate_intervals(
    split: IntervalSplit,
    zones: HrZones,
    cfg: IntervalConfig,
    *,
    prescribed_reps: int | None = None,
    target_pace: float | None = None,
    pace_tol_pct: float = 3.0,
    hr_ceiling: float | None = None,
    green_hr: float | None = None,
) -> IntervalEval:
    """Pass = >= `pass_rep_share` of the work reps on pace (within `pace_tol_pct` % of
    `target_pace`, faster is fine) and the last rep at most `fade_max_pct` % slower than the
    first, with every prescribed rep done. HR never fails a session: the last rep's peak HR (the
    lap max HR, the closest a lap file gets to "the final 60 s") above `hr_ceiling` makes it
    `pass_hard` (hold, no progression); below `green_hr` it is `green` (passed easily). The
    recovery-lap HR drop and the work HR vs the zone are reported for the multi-week trend only.
    Without a target pace only the fade is judged (verdict `no_target`)."""
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
    notes: list[str] = []
    in_zone = None
    too_high = below = False
    if work_hr is not None:
        in_zone = zones.interval_low <= work_hr <= zones.interval_high
        too_high = work_hr > zones.interval_high
        below = work_hr < zones.interval_low
    drops = [
        w.avg_hr - r.avg_hr
        for w, r in zip(work, split.rest, strict=False)
        if r is not None and w.avg_hr and r.avg_hr
    ]
    drop = fmean(drops) if drops else None
    recovery_ok = None if drop is None else drop >= cfg.hr_recovery_min_bpm
    if recovery_ok is False:
        notes.append(
            f"HR drop in the recovery laps {drop:.0f} bpm (< {cfg.hr_recovery_min_bpm:g}): "
            "watched as a multi-week trend, not a fail"
        )
    fade = None
    if len(work) >= 2 and work[0].pace and work[-1].pace:
        fade = (work[-1].pace - work[0].pace) / work[0].pace * 100
    on_pace = (
        sum(1 for lap in work if lap_on_pace(lap.pace, target_pace, pace_tol_pct))
        if target_pace
        else None
    )
    share = on_pace / len(work) if on_pace is not None and work else None
    short = prescribed_reps is not None and len(work) < prescribed_reps
    if short:
        reasons.append(f"only {len(work)} of {prescribed_reps} reps")
    pace_pass = True
    if share is not None and share < cfg.pass_rep_share - 1e-9:
        pace_pass = False
        reasons.append(
            f"{on_pace} of {len(work)} reps on pace (need {cfg.pass_rep_share * 100:.0f}%)"
        )
    if fade is not None and fade > cfg.fade_max_pct:
        pace_pass = False
        reasons.append(f"last rep {fade:.1f}% slower than the first (max {cfg.fade_max_pct:g}%)")
    passed = pace_pass and not short
    last = work[-1] if work else None
    peak = (last.max_hr or last.avg_hr) if last else None
    safety = None if peak is None or hr_ceiling is None else peak > hr_ceiling
    green = bool(passed and peak is not None and green_hr is not None and peak < green_hr)
    if not passed:
        verdict = "fail"
    elif target_pace is None:
        verdict = "no_target"
    elif safety:
        verdict = "pass_hard"
        notes.append(
            f"last rep peaked at {peak:.0f} bpm (> {hr_ceiling:.0f}): on pace but hard, hold"
        )
    elif green:
        verdict = "green"
    else:
        verdict = "pass"
    if peak is None:
        notes.append("no heart rate in the laps: judged by pace only")
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
        target_pace_s_per_km=target_pace,
        reps_on_pace=on_pace,
        pace_ok_share=share,
        last_rep_peak_hr=peak,
        hr_ceiling_bpm=hr_ceiling,
        hr_safety_exceeded=safety,
        green=green,
        verdict=verdict,
        notes=notes,
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
    exempt: bool = False  # the day after a lower-body session: an HR fail is only a note
    notes: list[str] = field(default_factory=list)
    hr_ceiling: int | None = None  # the ceiling this run was judged by

    def as_dict(self) -> dict[str, Any]:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def easy_share_label(source: str, cap: float) -> str:
    """What the time share of `easy_time_fraction` measures, for messages."""
    if source == "garmin_zones":
        return f"in Garmin Z1-Z{EASY_GARMIN_ZONES}"
    return f"at or below {cap}"


def evaluate_easy_run(
    run: RunData, zones: HrZones, cfg: EasyRunConfig, *, after_lower: bool = False
) -> EasyEval:
    """Pass = avg HR <= the easy ceiling and >= X % of the time at/below it. `zones` are those in
    force for this run (`ZoneBook.for_run`), not necessarily today's. With
    `after_lower` (the day after a lower-body session, `cfg.exempt_after_lower`) an HR fail is
    turned into a note: the legs are pre-fatigued, so HR runs high for the same pace."""
    cap = zones.easy_high
    reasons: list[str] = []
    ok = True
    if run.avg_hr is None:
        ok = False
        reasons.append("no average HR")
    elif run.avg_hr > cap:
        ok = False
        reasons.append(f"avg HR {run.avg_hr:.0f} above the easy ceiling {cap}")
    frac, src = easy_time_fraction(cap, run.time_in_zones_s, run.laps)
    pct = None if frac is None else frac * 100
    if pct is not None and pct < cfg.pass_pct_time_at_or_below_cap:
        ok = False
        reasons.append(
            f"only {pct:.0f}% of the time {easy_share_label(src, cap)} (need {cfg.pass_pct_time_at_or_below_cap:g}%)"  # noqa: E501
        )
    dec = decoupling_pct(run.laps)
    flag = dec is not None and dec > cfg.decoupling_flag_pct
    if flag:
        reasons.append(f"HR drift {dec:.1f}% (> {cfg.decoupling_flag_pct:g}%)")
    exempt = False
    notes: list[str] = []
    if not ok and after_lower and cfg.exempt_after_lower and run.avg_hr is not None:
        exempt, ok = True, True
        notes = [f"{r} (day after lower body: a note, not a fail)" for r in reasons]
        reasons = []
    return EasyEval(
        passed=ok,
        avg_hr=run.avg_hr,
        pct_at_or_below_cap=pct,
        pct_source=src,
        efficiency=efficiency(run.distance_m, run.duration_s, run.avg_hr),
        decoupling_pct=dec,
        decoupling_flag=flag,
        reasons=reasons,
        exempt=exempt,
        notes=notes,
        hr_ceiling=cap,
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
    abs_km: float = 2.0,
    hold_km: float | None = None,
) -> float | None:
    """Max km for the week starting `week_start`: the mean of the last `weeks_avg` weeks of
    ACTUAL km (weeks without runs count as 0), then max(mean x (1 + cap %), mean + abs_km,
    hold_km). `hold_km` is the volume of the last completed week that may be repeated
    (`hold_volume`). No run in those weeks: nothing to cap against (None)."""
    prev = [weekly.get(week_start - timedelta(days=7 * i), 0.0) for i in range(1, weeks_avg + 1)]
    if not any(prev):
        return None
    mean = fmean(prev)
    return round(max(mean * (1 + cap_pct / 100), mean + abs_km, hold_km or 0.0), 2)


@dataclass
class VolumeHold:
    """Whether the weekly cap may hold the last completed week's km (see `hold_volume`)."""

    last_week_km: float
    limit_km: float  # limit_factor x the highest week of the 4 weeks before the last one
    km: float | None  # the hold fed into the cap; None = no hold
    blocked: str | None = None  # why there is no hold

    def as_dict(self) -> dict[str, Any]:
        return {
            "last_week_km": round(self.last_week_km, 2),
            "limit_km": round(self.limit_km, 2),
            "hold_km": None if self.km is None else round(self.km, 2),
            "blocked": self.blocked,
        }


def hold_volume(
    weekly: dict[date, float],
    runs: Sequence[RunData],
    week_start: date,
    *,
    limit_factor: float,
    flagged: Callable[[RunData], bool],
) -> VolumeHold | None:
    """Holding a volume you just completed is low risk: the km of the last completed week
    (Mon-Sun before `week_start`), at most `limit_factor` x the highest weekly km of the 4 weeks
    before that week, and only when none of its easy / long runs (`flagged`: an HR fail or a drift
    flag; interval sessions are not judged) was flagged. None when that week had no runs."""
    last = week_start - timedelta(days=7)
    km = weekly.get(last, 0.0)
    if km <= 0:
        return None
    limit = limit_factor * max(weekly.get(last - timedelta(days=7 * i), 0.0) for i in range(1, 5))
    bad = [
        r
        for r in runs
        if iso_week_start(r.day) == last and split_intervals(r.laps) is None and flagged(r)
    ]
    if bad:
        why = f"the easy / long run of {bad[-1].day} had an HR fail or HR drift"
        return VolumeHold(km, limit, None, why)
    return VolumeHold(km, limit, round(min(km, limit), 2))


def mean_km(weekly: dict[date, float], week_start: date, weeks: int = 4) -> float:
    prev = [weekly.get(week_start - timedelta(days=7 * i), 0.0) for i in range(1, weeks + 1)]
    return round(fmean(prev), 2) if prev else 0.0


def single_run_cap_km(
    runs: Sequence[RunData], today: date, factor: float, abs_km: float = 0.0, days: int = 30
) -> float | None:
    """A single run may be at most `factor` x the longest run of the last `days` days (or that
    run + `abs_km`, whichever is larger). None without runs in the window."""
    since = today - timedelta(days=days)
    longest = max((r.km for r in runs if since <= r.day <= today), default=0)
    if longest <= 0:
        return None
    return round(max(longest * factor, longest + abs_km), 1)


def fit_to_cap(
    planned: dict[str, float],
    cap_km: float | None,
    long_min_km: float,
    easy_min_km: float = 3.0,
) -> tuple[dict[str, float], list[str]]:
    """Scale the planned km down to `cap_km`: the easy run first (to `easy_min_km`), then the
    long run (to `long_min_km`). Interval sessions keep their structure (the planner already
    shrank warm-up / cool-down). Returns (km by slot, notes)."""
    out = dict(planned)
    notes: list[str] = []
    if cap_km is None or sum(out.values()) <= cap_km:
        return out, notes
    for slot, floor in (("easy_run", easy_min_km), ("long_run", long_min_km)):
        excess = sum(out.values()) - cap_km
        if excess <= 0 or slot not in out:
            continue
        new = math.floor((out[slot] - excess) * 2 + 1e-9) / 2
        new = max(floor, new)
        if new < out[slot]:
            notes.append(f"{slot} {out[slot]:g} -> {new:g} km (weekly cap {cap_km:g} km)")
            out[slot] = new
    return out, notes


@dataclass
class LongRunPlan:
    km: float
    reason: str
    step_back: bool = False
    drift: str | None = None  # hold | cut | None
    capped_by: str | None = None


def next_long_run_km(
    recent_long: float | None,
    cfg: LongRunConfig,
    *,
    decoupling: float | None = None,
    duration_min: float | None = None,
    step_back: bool = False,
    single_cap: float | None = None,
) -> LongRunPlan:
    """Next long run: the longest recent long run + `step_km`; on a step-back week -
    `step_back_pct` %; drift gate on the last long run (steady and >= `drift_min_minutes`):
    decoupling > `drift_hold_pct` holds, > `drift_cut_pct` cuts by `drift_cut_by_pct` %. Never
    above `cap_km` or the single-run cap (1.1 x the longest run of the last 30 days)."""
    if recent_long is None:
        plan = LongRunPlan(cfg.start_km, f"no recent long run: start at {cfg.start_km:g} km")
    else:
        base = recent_long
        drift = None
        if decoupling is not None and (duration_min or 0) >= cfg.drift_min_minutes:
            if decoupling > cfg.drift_cut_pct:
                drift = "cut"
            elif decoupling > cfg.drift_hold_pct:
                drift = "hold"
        if step_back:
            km = base * (1 - cfg.step_back_pct / 100)
            why = f"step-back week: -{cfg.step_back_pct:g}% of {base:.1f} km"
        elif drift == "cut":
            km = base * (1 - cfg.drift_cut_by_pct / 100)
            why = (
                f"HR drift {decoupling:.1f}% (> {cfg.drift_cut_pct:g}%) on the last long run: "
                f"-{cfg.drift_cut_by_pct:g}%"
            )
        elif drift == "hold":
            km = base
            why = (
                f"HR drift {decoupling:.1f}% (> {cfg.drift_hold_pct:g}%) on the last long run: "
                f"hold {base:.1f} km"
            )
        else:
            km = base + cfg.step_km
            why = f"last {base:.1f} km, +{cfg.step_km:g} km"
        plan = LongRunPlan(km, why, step_back=step_back, drift=drift)
    km = plan.km
    if km > cfg.cap_km:
        km, plan.capped_by = cfg.cap_km, f"long-run cap {cfg.cap_km:g} km"
    if single_cap is not None and km > single_cap:
        km, plan.capped_by = single_cap, f"single-run cap {single_cap:g} km"
    plan.km = max(math.floor(km * 2 + 1e-9) / 2, 1.0)
    if plan.capped_by:
        plan.reason += f"; capped by the {plan.capped_by}"
    return plan


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
