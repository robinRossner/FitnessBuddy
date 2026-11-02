"""The Friday quality session: phase cycle, session types and the one-lever-per-week progression.

Spec: docs/research/running-progression.md (sections 2-3) with the decisions in
docs/spec/decisions.md ("Progression engine v2"). Pure functions over plain data.

* Cycle (config `running.quality.phases`): about 12 weeks threshold, then threshold / VO2max
  alternating, then 5k-specific, ending with a 5 km time trial; then the cycle repeats. A phase
  week only counts when its quality session was actually run, so a missed Friday repeats the
  same session next week and the phase does not advance.
* Session types: `threshold` (T pace), `vo2max` (I pace, the legacy `running.intervals`
  settings), `race_5k` (5k race pace), `time_trial` (5 km all-out, re-anchors VDOT). Paces always
  come from the CURRENT VDOT.
* Levers, one per week, in order: reps -> rep length -> shorter recovery (threshold only) ->
  pace. A session passed on pace advances one lever; "passed hard" (HR above the ceiling) holds;
  a fail repeats, and `fails_to_step_back` fails of the same prescription within 3 weeks step one
  rep back. The pace lever is a VDOT step and only fires with the VDOT evidence (green sessions);
  otherwise the session holds at its maximum structure.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from apps.fitness.engine.config import QualityConfig, RunningConfig
from apps.fitness.engine.hr import HrZones
from apps.fitness.engine.pace import FRAC_INTERVAL, FRAC_THRESHOLD, pace_at_fraction, race_time_s

TYPES = ("threshold", "vo2max", "race_5k", "time_trial")
LABELS = {
    "threshold": "Threshold",
    "vo2max": "Intervals",
    "race_5k": "5k reps",
    "time_trial": "Time trial",
}


@dataclass(frozen=True)
class QualityRecord:
    """A past quality session as it was prescribed (from the stored plan)."""

    day: date
    session_type: str
    reps: int
    rep_m: float
    recovery_s: float | None = None
    pace_s_per_km: float | None = None


@dataclass
class PhaseState:
    cycle_start: date
    completed_weeks: int  # weeks with a quality session done since the cycle start
    cycle: int  # 1-based
    phase: str
    phase_week: int
    phase_weeks: int
    session_type: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "cycle_start": self.cycle_start.isoformat(),
            "completed_weeks": self.completed_weeks,
            "cycle": self.cycle,
            "phase": self.phase,
            "phase_week": self.phase_week,
            "phase_weeks": self.phase_weeks,
            "session_type": self.session_type,
        }


def phase_state(
    cfg: QualityConfig, cycle_start: date, week_start: date, done_weeks: Iterable[date]
) -> PhaseState:
    completed = len({w for w in done_weeks if cycle_start <= w < week_start})
    total = sum(cfg.weeks_of(p) for p in cfg.phases) or 1
    cyc, idx = divmod(completed, total)
    phase = cfg.phases[-1]
    for p in cfg.phases:
        n = cfg.weeks_of(p)
        if idx < n:
            phase = p
            break
        idx -= n
    pw = idx + 1
    sessions = phase.sessions or ("threshold",)
    return PhaseState(
        cycle_start,
        completed,
        cyc + 1,
        phase.name,
        pw,
        cfg.weeks_of(phase),
        sessions[(pw - 1) % len(sessions)],
    )


@dataclass(frozen=True)
class TypeSpec:
    name: str
    reps_start: int
    reps_min: int
    reps_max: int
    rep_ladder: tuple[float, ...]
    start_rep_m: float
    recovery_ladder_s: tuple[float, ...]  # empty: distance-based jog recovery
    recovery_m: float | None
    max_work_min: float
    hr_ceiling_pct_lthr: float
    green_hr_pct_lthr: float


def hr_lines(zones: HrZones, spec: TypeSpec) -> tuple[float | None, float | None]:
    """(green line, safety ceiling) in bpm for a rep of `spec`: % of LTHR (None without LTHR)."""
    if not zones.lthr:
        return None, None
    return zones.lthr * spec.green_hr_pct_lthr, zones.lthr * spec.hr_ceiling_pct_lthr


def type_spec(rc: RunningConfig, t: str) -> TypeSpec:
    ic = rc.intervals
    if t == "threshold":
        c = rc.threshold
        return TypeSpec(
            t,
            c.reps_start,
            c.reps_min,
            c.reps_max,
            tuple(sorted(c.rep_ladder_m)),
            sorted(c.rep_ladder_m)[0],
            tuple(sorted(c.recovery_ladder_s, reverse=True)),
            None,
            c.max_work_min,
            c.hr_ceiling_pct_lthr,
            c.green_hr_pct_lthr,
        )
    if t == "race_5k":
        c5 = rc.race_5k
        return TypeSpec(
            t,
            c5.reps_start,
            c5.reps_min,
            c5.reps_max,
            tuple(sorted(c5.rep_ladder_m)),
            sorted(c5.rep_ladder_m)[0],
            (c5.recovery_s,),
            None,
            c5.max_work_min,
            c5.hr_ceiling_pct_lthr,
            c5.green_hr_pct_lthr,
        )
    if t == "time_trial":
        tt = rc.time_trial
        return TypeSpec(t, 1, 1, 1, (tt.distance_m,), tt.distance_m, (), None, 60, 9.9, 0.0)
    ladder = tuple(sorted({float(ic.rep_distance_m), *[float(x) for x in ic.rep_ladder_m]}))
    return TypeSpec(
        "vo2max",
        ic.reps_start,
        ic.reps_min,
        ic.reps_max,
        ladder,
        float(ic.rep_distance_m),
        (),
        ic.recovery_distance_m,
        ic.max_work_min,
        ic.hr_ceiling_pct_lthr,
        ic.green_hr_pct_lthr,
    )


def target_pace(vdot: float, t: str) -> float:
    """Target pace (s/km) of a session type from the current VDOT."""
    if t == "threshold":
        return pace_at_fraction(vdot, FRAC_THRESHOLD)
    if t in ("race_5k", "time_trial"):
        return race_time_s(vdot, 5000) / 5
    return pace_at_fraction(vdot, FRAC_INTERVAL)


@dataclass
class QualityPlan:
    session_type: str
    reps: int
    rep_m: float
    recovery_s: float | None
    recovery_m: float | None
    pace_s_per_km: float
    rule: str
    reason: str
    lever: str | None = None
    note: str = ""
    vdot_step: bool = False  # the pace lever asks for a VDOT step

    def work_min(self) -> float:
        return self.reps * self.rep_m / 1000 * self.pace_s_per_km / 60

    def as_dict(self) -> dict[str, Any]:
        return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def _work_min(reps: int, rep_m: float, pace: float) -> float:
    return reps * rep_m / 1000 * pace / 60


def _fmt_m(m: float) -> str:
    return f"{m:g} m"


def start_plan(spec: TypeSpec, pace: float, rule: str, reason: str) -> QualityPlan:
    rec_s = spec.recovery_ladder_s[0] if spec.recovery_ladder_s else None
    reps = spec.reps_start
    while reps > spec.reps_min and _work_min(reps, spec.start_rep_m, pace) > spec.max_work_min:
        reps -= 1
    return QualityPlan(
        spec.name,
        reps,
        spec.start_rep_m,
        rec_s,
        spec.recovery_m,
        pace,
        rule,
        reason,
        note=reason,
    )


def next_quality(
    spec: TypeSpec,
    last: QualityRecord | None,
    verdict: str | None,
    *,
    pace: float,
    fails_same: int = 0,
    fails_to_step_back: int = 2,
    vdot_step_ok: bool = False,
) -> QualityPlan:
    """The next session of type `spec.name` from the last one of that type and its verdict
    (green | pass | pass_hard | fail | no_target | None = no usable lap data)."""
    label = LABELS.get(spec.name, spec.name)
    if spec.name == "time_trial":
        return QualityPlan(
            "time_trial",
            1,
            spec.start_rep_m,
            None,
            None,
            pace,
            "quality:time_trial",
            f"end of the cycle: {spec.start_rep_m / 1000:g} km time trial, all-out (re-anchors "
            "VDOT)",
            note="time trial: run it all-out, it sets the next cycle's paces",
        )
    if last is None:
        return start_plan(
            spec, pace, "quality:start", f"first {label.lower()} session of the cycle"
        )
    reps, rep_m = last.reps, last.rep_m
    rec_s = last.recovery_s if spec.recovery_ladder_s else None
    if rec_s is None and spec.recovery_ladder_s:
        rec_s = spec.recovery_ladder_s[0]

    def plan(r: int, m: float, rs: float | None, rule: str, why: str, lever=None, step=False):
        return QualityPlan(spec.name, r, m, rs, spec.recovery_m, pace, rule, why, lever, why, step)

    if verdict is None:
        why = "no lap data from the last session: same session"
        return plan(reps, rep_m, rec_s, "quality:repeat", why)
    if verdict == "fail":
        if fails_same >= fails_to_step_back and reps > spec.reps_min:
            return plan(
                reps - 1,
                rep_m,
                rec_s,
                "quality:step_back",
                f"{fails_same} misses of this session in 3 weeks: one rep less",
            )
        why = "last session missed the pace: repeat it"
        return plan(reps, rep_m, rec_s, "quality:repeat", why)
    if verdict == "pass_hard":
        return plan(
            reps,
            rep_m,
            rec_s,
            "quality:hold",
            "last session on pace but the HR peaked above the ceiling: same session",
        )
    # passed: one lever
    if reps < spec.reps_max and _work_min(reps + 1, rep_m, pace) <= spec.max_work_min + 1e-9:
        return plan(
            reps + 1, rep_m, rec_s, "quality:add_rep", "+1 rep: last session passed on pace", "reps"
        )
    longer = [m for m in spec.rep_ladder if m > rep_m + 1e-6]
    for m in longer:
        r = max(spec.reps_min, min(spec.reps_max, math.floor(reps * rep_m / m + 0.5)))
        if _work_min(r, m, pace) <= spec.max_work_min + 1e-9:
            return plan(
                r,
                m,
                rec_s,
                "quality:longer_reps",
                f"longer reps ({_fmt_m(rep_m)} -> {_fmt_m(m)}): last session passed at max reps",
                "rep_length",
            )
    if spec.recovery_ladder_s and rec_s is not None:
        shorter = [s for s in spec.recovery_ladder_s if s < rec_s - 1e-6]
        if shorter:
            new = max(shorter)
            return plan(
                reps,
                rep_m,
                new,
                "quality:shorter_recovery",
                f"shorter recovery ({rec_s:g} -> {new:g} s): reps and length at their maximum",
                "recovery",
            )
    if vdot_step_ok:
        return plan(
            spec.reps_min,
            rep_m,
            rec_s,
            "quality:faster_pace",
            f"all levers at their maximum: faster pace (VDOT step), back to {spec.reps_min} reps",
            "pace",
            True,
        )
    return plan(
        reps,
        rep_m,
        rec_s,
        "quality:hold_max",
        "all levers at their maximum: the pace waits for VDOT evidence (easy passes)",
    )


def recent_of_type(records: list[QualityRecord], t: str, before: date) -> QualityRecord | None:
    rs = [r for r in records if r.session_type == t and r.day < before]
    return max(rs, key=lambda r: r.day) if rs else None


def same_prescription(a: QualityRecord, b: QualityRecord) -> bool:
    return a.session_type == b.session_type and a.reps == b.reps and abs(a.rep_m - b.rep_m) < 1


def weeks_between(a: date, b: date) -> int:
    return (b - a).days // 7


def week_of(d: date) -> date:
    return d - timedelta(days=d.weekday())
