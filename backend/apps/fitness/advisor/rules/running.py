"""Running rule family: weekly volume cap / target, long run, easy pace calibration, VDOT
updates (incl. from interval reps), threshold, interval and quality-phase limits, with their
cross-field checks.

`CORE_SPECS` and `V2_SPECS` keep their place in the allowlist order (`allowlist.SPECS`), which
the system prompt prints verbatim.
"""

from __future__ import annotations

from typing import Any

from apps.fitness.advisor.rules.base import (
    Context,
    Spec,
    bool_check,
    fmt,
    int_spec,
    num_spec,
    numeric,
    path_current,
    switch_check,
)
from apps.fitness.advisor.rules.risk import (
    LOW,
    MEDIUM,
    always,
    cap_risk,
    down_risk,
    floor_risk,
    long_cap_risk,
    risk_by_step,
    step_risk,
    up_risk,
)


def _intervals_after(ctx: Context) -> str | None:
    s, lo, hi = (
        ctx.effective(f"running.intervals.{k}") for k in ("reps_start", "reps_min", "reps_max")
    )
    if None in (s, lo, hi):
        return None
    if not (lo <= s <= hi):
        return (
            f"interval reps must satisfy reps_min <= reps_start <= reps_max (got {lo}, {s}, {hi})"
        )
    return None


def _phase_spec(name: str, label: str, lo: int, hi: int, step: int) -> Spec:
    """Weeks of one quality-cycle phase: the override, else the phase's configured weeks."""

    def cur(ctx: Context, _g: dict[str, str]) -> Any:
        ov = ctx.effective(f"running.quality.phase_weeks.{name}")
        if ov is not None:
            return ov
        for ph in ctx.effective("running.quality.phases") or []:
            if ph.get("name") == name:
                return ph.get("weeks")
        return None

    return Spec(
        f"running.quality.phase_weeks.{name}",
        label,
        f"int {lo}..{hi}, max step {step}; a phase week counts only when its session was run",
        cur,
        numeric(lo, hi, integer=True, max_step=step, risk=always(MEDIUM)),
    )


def _long_run_after(ctx: Context) -> str | None:
    cap, mn = ctx.effective("running.long_run.cap_km"), ctx.effective("running.long_run.min_km")
    if cap is not None and mn is not None and cap < mn:
        return f"the long run cap ({fmt(cap)} km) cannot be below its minimum ({fmt(mn)} km)"
    return None


def _reps_spec(key: str, label: str) -> Spec:
    return Spec(
        f"running.intervals.{key}",
        label,
        "int 3..10, +-1 is low risk, max step 3; reps_min <= reps_start <= reps_max",
        path_current(f"running.intervals.{key}"),
        numeric(3, 10, integer=True, max_step=3, risk=risk_by_step(1, 2)),
        after=_intervals_after,
    )


CORE_SPECS: tuple[Spec, ...] = (
    Spec(
        "running.weekly_volume_cap_pct",
        "Weekly run volume cap (%)",
        "number 0..15, max step 5; raising above 10 is high risk",
        path_current("running.weekly_volume_cap_pct"),
        numeric(0, 15, integer=False, max_step=5, risk=cap_risk, unit=" %"),
    ),
    Spec(
        "running.weekly_volume_floor_km",
        "Weekly run volume target (km)",
        "number 10..60 (0 = off), max step 20; a target the cap grows toward, never a jump; "
        "raising by more than 5 is high risk",
        path_current("running.weekly_volume_floor_km"),
        numeric(10, 60, integer=False, max_step=20, risk=floor_risk, unit=" km"),
    ),
    Spec(
        "running.long_run.cap_km",
        "Long run maximum (km)",
        "number 8..25, max step 3; never above 25",
        path_current("running.long_run.cap_km"),
        numeric(8, 25, integer=False, max_step=3, risk=long_cap_risk, unit=" km"),
        after=_long_run_after,
    ),
    Spec(
        "running.long_run.step_km",
        "Long run weekly step (km)",
        "number 0.5..2, max step 1; above 1 is high risk",
        path_current("running.long_run.step_km"),
        numeric(0.5, 2, integer=False, max_step=1, risk=step_risk, unit=" km"),
    ),
)


V2_SPECS: tuple[Spec, ...] = (
    num_spec(
        "running.interval_pace_tolerance_pct",
        "Interval rep pace tolerance (%)",
        1,
        6,
        2,
        unit=" %",
    ),
    num_spec(
        "running.volume.cap_abs_km",
        "Weekly cap: absolute allowance over the 4-week mean (km)",
        0,
        4,
        1,
        risk=up_risk(2),
        unit=" km",
        doc="number 0..4, max step 1; raising above 2 is high risk",
    ),
    num_spec(
        "running.volume.single_run_factor",
        "Single run vs the longest of 30 days (x)",
        1.0,
        1.3,
        0.1,
        risk=up_risk(1.15),
        doc="number 1.0..1.3, max step 0.1; raising above 1.15 is high risk",
    ),
    Spec(
        "running.volume.hold_last_week",
        "Weekly cap may hold last week's completed km",
        "true or false (low risk: repeating a volume just completed)",
        path_current("running.volume.hold_last_week"),
        bool_check,
    ),
    num_spec(
        "running.volume.hold_limit_factor",
        "Held volume vs the highest of the 4 weeks before it (x)",
        1.0,
        1.2,
        0.1,
        risk=up_risk(1.1),
        doc="number 1.0..1.2, max step 0.1; raising above 1.1 is high risk",
    ),
    # ---- easy pace calibration (docs/spec/decisions.md "Easy pace calibration (2026-10-28)")
    Spec(
        "running.easy_calibration.enabled",
        "Easy pace range calibrated from your easy runs",
        "true or false (low risk: easy / long runs only, HR still leads, capped by max_vdot_gain)",
        path_current("running.easy_calibration.enabled"),
        bool_check,
    ),
    num_spec(
        "running.easy_calibration.max_vdot_gain",
        "Easy pace calibration: largest gain over the quality VDOT",
        0,
        6,
        2,
        risk=up_risk(4),
        doc="number 0..6, max step 2; raising above 4 is high risk",
    ),
    int_spec(
        "running.easy_calibration.window_days",
        "Easy pace calibration: window (days)",
        14,
        56,
        14,
    ),
    int_spec(
        "running.easy_calibration.min_runs",
        "Easy pace calibration: easy runs needed",
        2,
        8,
        2,
        risk=down_risk(3),
        doc="int 2..8, max step 2; lowering below 3 is high risk",
    ),
    num_spec(
        "running.easy_calibration.hr_margin_bpm",
        "Easy pace calibration: HR margin below the easy ceiling (bpm)",
        0,
        8,
        2,
        unit=" bpm",
    ),
    # ---- VDOT from interval reps (docs/spec/decisions.md "VDOT from interval reps (2026-10-28)")
    Spec(
        "running.vdot.from_intervals.enabled",
        "Quality VDOT may rise to the value measured from interval reps",
        "true or false (switching off is low risk: VDOT then rises only by races and inference; "
        "switching on is medium: faster quality paces)",
        path_current("running.vdot.from_intervals.enabled"),
        switch_check(MEDIUM, LOW),
    ),
    num_spec(
        "running.vdot.from_intervals.max_step",
        "VDOT from interval reps: largest single rise",
        0.5,
        4,
        1,
        risk=up_risk(3),
        doc="number 0.5..4, max step 1; raising above 3 is high risk",
    ),
    int_spec(
        "running.vdot.from_intervals.window_days",
        "VDOT from interval reps: window (days)",
        28,
        84,
        14,
        risk=up_risk(70),
        doc="int 28..84, max step 14; raising above 70 is high risk",
    ),
    int_spec(
        "running.vdot.from_intervals.min_reps",
        "VDOT from interval reps: work reps needed per session",
        3,
        8,
        2,
        risk=down_risk(4),
        doc="int 3..8, max step 2; lowering below 4 is high risk",
    ),
    int_spec(
        "running.vdot.from_intervals.age_free_days",
        "VDOT from interval reps: days before evidence loses value",
        14,
        42,
        7,
        risk=up_risk(28),
        doc="int 14..42, max step 7; raising above 28 is high risk",
    ),
    num_spec(
        "running.vdot.from_intervals.age_discount_per_week",
        "VDOT from interval reps: VDOT lost per week of age",
        0,
        1,
        0.25,
        risk=down_risk(0.25),
        doc="number 0..1, max step 0.25; lowering below 0.25 is high risk",
    ),
    int_spec("running.long_run.step_back_every", "Long run step-back every N weeks", 3, 6, 1),
    num_spec("running.long_run.step_back_pct", "Long run step-back (%)", 10, 30, 10, unit=" %"),
    num_spec(
        "running.long_run.drift_hold_pct", "Long run drift that holds (%)", 3, 10, 2, unit=" %"
    ),
    num_spec(
        "running.vdot.max_step",
        "VDOT: largest single update",
        0.25,
        1.0,
        0.5,
        risk=up_risk(0.5),
        doc="number 0.25..1.0, max step 0.5; raising above 0.5 is high risk",
    ),
    num_spec(
        "running.vdot.max_per_4w",
        "VDOT: largest rise per 4 weeks",
        0.5,
        2.0,
        0.5,
        risk=up_risk(1.0),
        doc="number 0.5..2.0, max step 0.5; raising above 1.0 is high risk",
    ),
    int_spec(
        "running.easy.strides.reps",
        "Strides on the easy run",
        0,
        8,
        2,
        risk=always(LOW),
        doc="int 0..8, max step 2; always low risk",
    ),
    int_spec(
        "running.threshold.reps_max",
        "Threshold reps (maximum)",
        3,
        8,
        2,
        risk=risk_by_step(1, 2),
        doc="int 3..8, max step 2; +-1 is low risk",
    ),
    num_spec(
        "running.threshold.max_work_min", "Threshold minutes per session", 15, 45, 10, unit=" min"
    ),
    num_spec(
        "running.intervals.max_work_min", "VO2max minutes per session", 10, 30, 6, unit=" min"
    ),
    _phase_spec("threshold", "Weeks of the threshold phase", 4, 16, 4),
    _phase_spec("threshold_vo2max", "Weeks of the threshold / VO2max phase", 2, 12, 4),
    _phase_spec("5k_specific", "Weeks of the 5k-specific phase", 2, 10, 4),
    _reps_spec("reps_start", "Interval reps (first session)"),
    _reps_spec("reps_min", "Interval reps after a pace step"),
    _reps_spec("reps_max", "Interval reps (maximum)"),
    Spec(
        "running.intervals.rep_distance_m",
        "Interval rep distance (m)",
        "number 200..2000 in steps of 50, max step 500; > 300 is high risk",
        path_current("running.intervals.rep_distance_m"),
        numeric(
            200,
            2000,
            integer=False,
            max_step=500,
            risk=risk_by_step(0, 300),
            multiple=50,
            unit=" m",
        ),
    ),
    Spec(
        "running.intervals.recovery_distance_m",
        "Interval jog recovery (m)",
        "number 100..800 in steps of 50, max step 300; > 200 is high risk",
        path_current("running.intervals.recovery_distance_m"),
        numeric(
            100,
            800,
            integer=False,
            max_step=300,
            risk=risk_by_step(0, 200),
            multiple=50,
            unit=" m",
        ),
    ),
)
