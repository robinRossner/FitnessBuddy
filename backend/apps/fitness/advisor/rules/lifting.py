"""Lifting rule family: rep ranges, per-exercise overrides (rep range, sets, variant swap,
rest), rest default, load increments, stall detection / reset and deload limits.

`CORE_SPECS` and `V2_SPECS` keep their place in the allowlist order (`allowlist.SPECS`), which
the system prompt prints verbatim.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from apps.fitness.advisor.rules.base import (
    Checked,
    Context,
    Invalid,
    Spec,
    choice,
    fmt,
    int_spec,
    num_spec,
    numeric,
    path_current,
    to_number,
)
from apps.fitness.advisor.rules.risk import HIGH, LOW, MEDIUM, always, increment_risk, reset_risk
from apps.fitness.db.models import Exercise
from apps.fitness.engine.config import REST_MAX_S, REST_MIN_S


def _rep_range_check(value: Any, cur: Any, _ctx: Context, _g: dict[str, str]) -> Checked:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise Invalid("a rep range must be [low, high]")
    lo, hi = (to_number(x, integer=True) for x in value)
    if lo < 3 or hi > 20:
        raise Invalid(f"rep range must stay within 3-20 reps (got {lo}-{hi})")
    if lo >= hi:
        raise Invalid(f"the low end must be below the high end (got {lo}-{hi})")
    cur_lo, cur_hi = (int(cur[0]), int(cur[1])) if cur else (lo, hi)
    shift = max(abs(lo - cur_lo), abs(hi - cur_hi))
    if shift == 0:
        raise Invalid(f"already {cur_lo}-{cur_hi}: nothing to change")
    if shift > 3:
        raise Invalid("one change may move each end of a rep range by at most 3 reps")
    return Checked([lo, hi], LOW if shift <= 1 else MEDIUM if shift <= 2 else HIGH)


def _slug_label(suffix: str) -> str:
    return "{name} " + suffix


def _ov_current(key: str, fallback: Callable[[Context, str], Any]):
    def cur(ctx: Context, g: dict[str, str]) -> Any:
        slug = g["slug"]
        ov = ctx.overrides(slug).get(key)
        return ov if ov is not None else fallback(ctx, slug)

    return cur


def _program_attr(attr: str, default: Callable[[Context], Any] | None = None):
    def get(ctx: Context, slug: str) -> Any:
        entries = ctx.program_entries(slug)
        val = getattr(entries[0][1], attr, None) if entries else None
        if isinstance(val, tuple):
            val = list(val)
        if val is None and default is not None:
            return default(ctx)
        return val

    return get


def _require_program_slug(ctx: Context, slug: str) -> None:
    if ctx.program is None:
        raise Invalid("no lifting program is loaded")
    if not ctx.program_entries(slug):
        raise Invalid(f"'{slug}' is not an active exercise of the lifting program")


def _ov_rep_range(value: Any, cur: Any, ctx: Context, g: dict[str, str]) -> Checked:
    _require_program_slug(ctx, g["slug"])
    return _rep_range_check(value, cur, ctx, g)


def _ov_sets(value: Any, cur: Any, ctx: Context, g: dict[str, str]) -> Checked:
    _require_program_slug(ctx, g["slug"])
    new = to_number(value, integer=True)
    if new < 1 or new > 6:
        raise Invalid(f"working sets must be between 1 and 6 (got {new})")
    if cur is not None and new == cur:
        raise Invalid(f"already {cur} sets: nothing to change")
    if cur is not None and abs(new - cur) > 2:
        raise Invalid("one change may add or remove at most 2 sets")
    return Checked(new, MEDIUM if cur is None or abs(new - cur) <= 1 else HIGH)


def _ov_rest(value: Any, cur: Any, ctx: Context, g: dict[str, str]) -> Checked:
    _require_program_slug(ctx, g["slug"])
    return _REST_CHECK(value, cur, ctx, g)


def _ov_variant(value: Any, cur: Any, ctx: Context, g: dict[str, str]) -> Checked:
    """Exercise swap: only to an active variant of the same movement pattern or body region at the
    current gym (or a generic one); never onto an exercise the day already has."""
    from apps.fitness.engine import data as D  # local: data imports the planner

    slug = g["slug"]
    _require_program_slug(ctx, slug)
    if not isinstance(value, str) or not value.strip():
        raise Invalid("the target must be a variant name (string)")
    if ctx.db is None:
        raise Invalid("exercise swaps cannot be checked without the catalog")
    name = value.strip()
    if cur is not None and name == cur:
        raise Invalid(f"already using '{cur}': nothing to change")
    target = D.resolve_variant(ctx.db, name, ctx.gym)
    if target is None or target.deleted_at is not None:
        raise Invalid(f"'{name}' is not a known variant at the current gym (or generic)")
    if not target.active:
        raise Invalid(f"'{name}' is marked inactive")
    if target.gym_id is not None and (ctx.gym is None or target.gym_id != ctx.gym.id):
        raise Invalid(f"'{name}' belongs to another gym")
    src = ctx.db.scalar(select(Exercise).where(Exercise.slug == slug))
    dst = ctx.db.get(Exercise, target.exercise_id)
    if src is None or dst is None:
        raise Invalid(f"unknown exercise '{slug}'")
    same_pattern = src.movement_pattern == dst.movement_pattern
    src_region, dst_region = ctx.regions.get(src.slug), ctx.regions.get(dst.slug)
    same_region = bool(src_region) and src_region == dst_region
    if not (same_pattern or same_region):
        raise Invalid(
            f"'{name}' ({dst.movement_pattern}) is neither the same movement pattern "
            f"({src.movement_pattern}) nor the same body region as '{slug}'"
        )
    for kind, _pe in ctx.program_entries(slug):
        for other in ctx.program.days.get(kind, []) if ctx.program else []:  # type: ignore[union-attr]
            if other.exercise != slug and other.active:
                eff = ctx.overrides(other.exercise).get("variant") or other.variant
                if eff == D.base_name_of(target):
                    raise Invalid(f"'{name}' is already part of the {kind} day")
    return Checked(D.base_name_of(target), MEDIUM)


def _increment_spec(key: str, label: str, options: tuple[float, ...]) -> Spec:
    opts = ", ".join(fmt(o) for o in options)
    return Spec(
        f"increments.{key}",
        label,
        f"kg, one of {opts}; a bigger step is high risk",
        path_current(f"increments.{key}"),
        choice(options, increment_risk),
    )


_REST_CHECK = numeric(
    REST_MIN_S,
    REST_MAX_S,
    integer=True,
    max_step=REST_MAX_S - REST_MIN_S,
    risk=always(LOW),
    unit=" s",
)


CORE_SPECS: tuple[Spec, ...] = (
    Spec(
        "strength.rep_range",
        "Rep range (all lifts)",
        "[low, high] ints within 3..20, low < high, each end moves <= 3; <= 1 per end is low risk",
        path_current("strength.rep_range"),
        _rep_range_check,
    ),
    Spec(
        "exercise_overrides.{slug}.rep_range",
        _slug_label("rep range"),
        "per program exercise (catalog slug): same rule as strength.rep_range",
        _ov_current(
            "rep_range", _program_attr("rep_range", lambda c: c.effective("strength.rep_range"))
        ),
        _ov_rep_range,
    ),
    Spec(
        "exercise_overrides.{slug}.sets",
        _slug_label("working sets"),
        "per program exercise: int 1..6, max +-2 per change (volume target)",
        _ov_current("sets", _program_attr("sets")),
        _ov_sets,
    ),
    Spec(
        "exercise_overrides.{slug}.variant",
        _slug_label("variant"),
        "per program exercise: swap to the name of an active variant with the same movement "
        "pattern or body region, at the current gym or generic",
        _ov_current("variant", _program_attr("variant")),
        _ov_variant,
    ),
    Spec(
        "exercise_overrides.{slug}.rest_s",
        _slug_label("rest time (s)"),
        "per program exercise: rest timer in seconds, int 30..600; always low risk",
        _ov_current("rest_s", lambda c, _slug: c.effective("lifting.rest_default_s")),
        _ov_rest,
    ),
    Spec(
        "lifting.rest_default_s",
        "Default rest time (s)",
        "rest timer for every exercise without its own, int 30..600; always low risk",
        path_current("lifting.rest_default_s"),
        numeric(
            REST_MIN_S,
            REST_MAX_S,
            integer=True,
            max_step=REST_MAX_S - REST_MIN_S,
            risk=always(LOW),
            unit=" s",
        ),
    ),
    _increment_spec("barbell_kg", "Barbell increment", (2.5, 5.0)),
    _increment_spec("smith_kg", "Smith machine increment", (2.5, 5.0)),
    _increment_spec("plate_loaded_kg", "Plate-loaded increment", (2.5, 5.0)),
    _increment_spec("dumbbell_increment_kg", "Dumbbell increment", (2.0, 2.5)),
    _increment_spec("default_stack_step_kg", "Machine / cable stack step", (2.5, 5.0)),
    _increment_spec("bodyweight_added_kg", "Added weight step (pull-ups, dips)", (1.25, 2.5)),
    _increment_spec("other_kg", "Other equipment increment", (1.25, 2.5)),
    Spec(
        "strength.stall_exposures",
        "Exposures before a stall",
        "int 2..6, max step 2",
        path_current("strength.stall_exposures"),
        numeric(2, 6, integer=True, max_step=2, risk=always(MEDIUM)),
    ),
    Spec(
        "strength.stall_reset_pct",
        "Stall reset (% load drop)",
        "number 5..15, max step 5",
        path_current("strength.stall_reset_pct"),
        numeric(5, 15, integer=False, max_step=5, risk=reset_risk, unit=" %"),
    ),
    Spec(
        "recovery.deload_red_days",
        "Red readiness days that trigger a deload",
        "int 2..7, max step 2",
        path_current("recovery.deload_red_days"),
        numeric(2, 7, integer=True, max_step=2, risk=always(MEDIUM)),
    ),
)


V2_SPECS: tuple[Spec, ...] = (
    # ---- progression engine v2 (docs/spec/decisions.md "Progression engine v2")
    int_spec(
        "strength.stall_exposures_2x",
        "Clean sessions before a stall (lifts trained twice a week)",
        2,
        8,
        2,
        doc="int 2..8, max step 2 (strength.stall_exposures is the once-a-week value)",
    ),
    int_spec("strength.stall_min_days", "Minimum days before a stall", 7, 28, 7),
    num_spec(
        "strength.stall_reset_small_pct",
        "First stall-ladder step (% load drop)",
        2.5,
        10,
        2.5,
        unit=" %",
    ),
    int_spec("strength.multi_stall_lifts", "Lifts stalled together for a soft deload", 2, 6, 2),
    int_spec("strength.big_miss_reps", "Rep drop that steps the load down", 2, 5, 1),
    int_spec("strength.rung_min_reps", "Predicted reps needed at the next load", 6, 10, 1),
    int_spec("strength.rep_ceiling_cap", "Highest temporary rep ceiling", 12, 20, 3),
    num_spec("strength.deload.sets_pct", "Deload: working sets cut (%)", 25, 60, 15, unit=" %"),
    num_spec(
        "strength.deload.fatigue_index_pct",
        "Fatigue index that triggers a deload (%)",
        25,
        60,
        10,
        unit=" %",
    ),
)
