"""Allowlist + hard limits for what the Claude advisor may change, and the risk classification.

Claude can only propose *parameters* (a dotted path into the engine config and a new value); it
never sets weights or paces, the engine recomputes those from the parameters. Everything that is
not in `SPECS` is rejected. Each change is validated on its own (`validate_change`) against the
config content as it would be after the earlier valid changes of the same proposal, and gets:

- `valid` / `error`: the type, range, step and cross-field checks below,
- `risk`: low | medium | high, decided HERE (never by Claude). `low` is deliberately narrow:
  rep-range shifts of at most 1 rep per end and interval rep counts of +-1. The
  `auto-low-risk` approval mode only applies those.

Schedule changes (move / skip / swap / shorten / replace of planned sessions) have their own
validation below (`validate_schedule_change`).

Hard limits (see `describe_allowlist()`, which also feeds the system prompt):
rep ranges 3-20 (low < high), stall exposures 2-6 (2-8 twice a week), stall reset 5-15 % (first
ladder step 2.5-10 %), deload red days 2-7, interval reps 3-10, rep distance 200-2000 m, jog
recovery 100-800 m, weekly volume cap 0-15 % (+0-4 km), weekly volume target 10-60 km, long run
cap <= 25 km, VDOT steps <= 1.0 (<= 2.0 per 4 weeks), increments only from the options of their
equipment, exercise swaps only to active variants of the same movement pattern or body region
at the current gym (or generic). Every change also has a maximum step ("max load jump" /
"volume ramp"): a single proposal cannot move a limit across its whole range.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Exercise, Gym
from app.engine import config_service as cs
from app.engine import data as D
from app.engine import schedule_edit as SE
from app.engine import schedule_rules as SR
from app.engine.config import REST_MAX_S, REST_MIN_S, EngineConfig
from app.engine.program import Program

LOW, MEDIUM, HIGH = "low", "medium", "high"
RISKS = (LOW, MEDIUM, HIGH)

SLUG = r"(?P<slug>[a-z0-9_]{1,60})"


class Invalid(ValueError):
    """The change breaks a limit; the message is shown to the user."""


# --------------------------------------------------------------------------- value helpers


def to_number(value: Any, *, integer: bool) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise Invalid(f"must be a number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise Invalid("must be a finite number")
    if integer:
        if float(value) != int(value):
            raise Invalid("must be a whole number")
        return int(value)
    return round(float(value), 3)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, int | float) and isinstance(b, int | float) and not isinstance(a, bool):
        return abs(float(a) - float(b)) < 1e-9
    return a == b


def _fmt(v: Any) -> str:
    if isinstance(v, list | tuple):
        return "-".join(_fmt(x) for x in v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


# --------------------------------------------------------------------------- context


@dataclass
class Context:
    """What a change is validated against. `content` is the (working) engine config content."""

    content: dict[str, Any]
    program: Program | None = None
    db: Session | None = None
    gym: Gym | None = None
    regions: dict[str, str] = field(default_factory=dict)  # exercise slug -> body region
    _eff: dict[str, Any] | None = field(default=None, repr=False)

    def effective(self, path: str) -> Any:
        """Value of a dotted path including the engine's defaults for keys the content lacks."""
        if self._eff is None:
            cfg = EngineConfig.model_validate(self.content)
            self._eff = {
                "strength": cfg.strength.model_dump(),
                "increments": cfg.increments.model_dump(),
                "recovery": cfg.recovery_cfg.model_dump(),
                "running": cfg.running_cfg.model_dump(),
                "lifting": cfg.lifting_cfg.model_dump(),
            }
        node: Any = self._eff
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return list(node) if isinstance(node, tuple) else node

    def overrides(self, slug: str) -> dict[str, Any]:
        return (self.content.get("exercise_overrides") or {}).get(slug) or {}

    def program_entries(self, slug: str) -> list[tuple[str, Any]]:
        if self.program is None:
            return []
        return [
            (kind, pe)
            for kind, items in self.program.days.items()
            for pe in items
            if pe.exercise == slug and pe.active
        ]

    def apply(self, param: str, value: Any) -> None:
        cs.set_path(self.content, param, value)
        self._eff = None

    def fork(self) -> Context:
        return Context(copy.deepcopy(self.content), self.program, self.db, self.gym, self.regions)


# --------------------------------------------------------------------------- specs


@dataclass(frozen=True)
class Checked:
    value: Any
    risk: str


@dataclass(frozen=True)
class Spec:
    template: str  # e.g. "running.intervals.reps_max" or "exercise_overrides.{slug}.rep_range"
    label: str  # "{name}" is replaced by the exercise's variant name
    doc: str
    current: Callable[[Context, dict[str, str]], Any]
    check: Callable[[Any, Any, Context, dict[str, str]], Checked]
    after: Callable[[Context], str | None] | None = None  # cross-field check on the new content
    regex: re.Pattern[str] = field(init=False)

    def __post_init__(self) -> None:
        pattern = re.escape(self.template).replace(r"\{slug\}", SLUG)
        object.__setattr__(self, "regex", re.compile(rf"^{pattern}$"))


def _path_current(path: str) -> Callable[[Context, dict[str, str]], Any]:
    return lambda ctx, _g: ctx.effective(path)


def _numeric(
    lo: float,
    hi: float,
    *,
    integer: bool,
    max_step: float,
    risk: Callable[[float, float], str],
    multiple: float | None = None,
    unit: str = "",
) -> Callable[[Any, Any, Context, dict[str, str]], Checked]:
    def check(value: Any, cur: Any, _ctx: Context, _g: dict[str, str]) -> Checked:
        new = to_number(value, integer=integer)
        if new < lo or new > hi:
            raise Invalid(f"must be between {_fmt(lo)} and {_fmt(hi)}{unit} (got {_fmt(new)})")
        if multiple and abs(new / multiple - round(new / multiple)) > 1e-9:
            raise Invalid(f"must be a multiple of {_fmt(multiple)}{unit}")
        if cur is not None and _same(new, cur):
            raise Invalid(f"already {_fmt(cur)}{unit}: nothing to change")
        if cur is not None and abs(new - cur) > max_step + 1e-9:
            raise Invalid(
                f"one change may move this by at most {_fmt(max_step)}{unit} "
                f"(from {_fmt(cur)} to {_fmt(new)})"
            )
        return Checked(new, risk(float(cur if cur is not None else new), float(new)))

    return check


def _choice(
    options: tuple[float, ...], risk: Callable[[float, float], str]
) -> Callable[[Any, Any, Context, dict[str, str]], Checked]:
    def check(value: Any, cur: Any, _ctx: Context, _g: dict[str, str]) -> Checked:
        new = to_number(value, integer=False)
        if not any(abs(new - o) < 1e-9 for o in options):
            raise Invalid(f"must be one of {', '.join(_fmt(o) for o in options)} kg (got {new:g})")
        if cur is not None and _same(new, cur):
            raise Invalid(f"already {_fmt(cur)} kg: nothing to change")
        return Checked(float(new), risk(float(cur if cur is not None else new), float(new)))

    return check


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
    from app.engine import data as D  # local: data imports the planner

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


def _risk_by_step(low: float, medium: float):
    """Risk from the absolute size of the move: <= low -> low, <= medium -> medium, else high."""

    def risk(cur: float, new: float) -> str:
        step = abs(new - cur)
        return LOW if step <= low else MEDIUM if step <= medium else HIGH

    return risk


def _always(level: str):
    return lambda _c, _n: level


def _cap_risk(cur: float, new: float) -> str:
    return HIGH if new > cur and new > 10 else MEDIUM


def _floor_risk(cur: float, new: float) -> str:
    if cur <= 0:  # switching the floor on (it is off by default)
        return MEDIUM if new <= 25 else HIGH
    return HIGH if new - cur > 5 else MEDIUM


def _long_cap_risk(cur: float, new: float) -> str:
    return HIGH if new > cur and new > 21 else MEDIUM


def _step_risk(cur: float, new: float) -> str:
    return HIGH if new > 1.0 else MEDIUM


def _increment_risk(cur: float, new: float) -> str:
    return HIGH if new >= cur * 2 - 1e-9 else MEDIUM


def _reset_risk(cur: float, new: float) -> str:
    return HIGH if abs(new - cur) >= 4 else MEDIUM


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


def _up_risk(threshold: float):
    """High when the value goes up past `threshold`, medium otherwise."""

    def risk(cur: float, new: float) -> str:
        return HIGH if new > cur and new > threshold + 1e-9 else MEDIUM

    return risk


def _int_spec(path: str, label: str, lo: int, hi: int, step: int, risk=None, doc: str = "") -> Spec:
    return Spec(
        path,
        label,
        doc or f"int {lo}..{hi}, max step {step}",
        _path_current(path),
        _numeric(lo, hi, integer=True, max_step=step, risk=risk or _always(MEDIUM)),
    )


def _num_spec(
    path: str, label: str, lo: float, hi: float, step: float, risk=None, unit="", doc=""
) -> Spec:
    return Spec(
        path,
        label,
        doc or f"number {_fmt(lo)}..{_fmt(hi)}, max step {_fmt(step)}",
        _path_current(path),
        _numeric(lo, hi, integer=False, max_step=step, risk=risk or _always(MEDIUM), unit=unit),
    )


def _bool_check(value: Any, cur: Any, _ctx: Context, _g: dict[str, str]) -> Checked:
    if not isinstance(value, bool):
        raise Invalid("must be true or false")
    if cur is not None and value == cur:
        raise Invalid(f"already {str(cur).lower()}: nothing to change")
    return Checked(value, LOW)


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
        _numeric(lo, hi, integer=True, max_step=step, risk=_always(MEDIUM)),
    )


def _long_run_after(ctx: Context) -> str | None:
    cap, mn = ctx.effective("running.long_run.cap_km"), ctx.effective("running.long_run.min_km")
    if cap is not None and mn is not None and cap < mn:
        return f"the long run cap ({_fmt(cap)} km) cannot be below its minimum ({_fmt(mn)} km)"
    return None


def _increment_spec(key: str, label: str, options: tuple[float, ...]) -> Spec:
    opts = ", ".join(_fmt(o) for o in options)
    return Spec(
        f"increments.{key}",
        label,
        f"kg, one of {opts}; a bigger step is high risk",
        _path_current(f"increments.{key}"),
        _choice(options, _increment_risk),
    )


def _reps_spec(key: str, label: str) -> Spec:
    return Spec(
        f"running.intervals.{key}",
        label,
        "int 3..10, +-1 is low risk, max step 3; reps_min <= reps_start <= reps_max",
        _path_current(f"running.intervals.{key}"),
        _numeric(3, 10, integer=True, max_step=3, risk=_risk_by_step(1, 2)),
        after=_intervals_after,
    )


_REST_CHECK = _numeric(
    REST_MIN_S,
    REST_MAX_S,
    integer=True,
    max_step=REST_MAX_S - REST_MIN_S,
    risk=_always(LOW),
    unit=" s",
)


SPECS: tuple[Spec, ...] = (
    Spec(
        "strength.rep_range",
        "Rep range (all lifts)",
        "[low, high] ints within 3..20, low < high, each end moves <= 3; <= 1 per end is low risk",
        _path_current("strength.rep_range"),
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
        _path_current("lifting.rest_default_s"),
        _numeric(
            REST_MIN_S,
            REST_MAX_S,
            integer=True,
            max_step=REST_MAX_S - REST_MIN_S,
            risk=_always(LOW),
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
        _path_current("strength.stall_exposures"),
        _numeric(2, 6, integer=True, max_step=2, risk=_always(MEDIUM)),
    ),
    Spec(
        "strength.stall_reset_pct",
        "Stall reset (% load drop)",
        "number 5..15, max step 5",
        _path_current("strength.stall_reset_pct"),
        _numeric(5, 15, integer=False, max_step=5, risk=_reset_risk, unit=" %"),
    ),
    Spec(
        "recovery.deload_red_days",
        "Red readiness days that trigger a deload",
        "int 2..7, max step 2",
        _path_current("recovery.deload_red_days"),
        _numeric(2, 7, integer=True, max_step=2, risk=_always(MEDIUM)),
    ),
    Spec(
        "running.weekly_volume_cap_pct",
        "Weekly run volume cap (%)",
        "number 0..15, max step 5; raising above 10 is high risk",
        _path_current("running.weekly_volume_cap_pct"),
        _numeric(0, 15, integer=False, max_step=5, risk=_cap_risk, unit=" %"),
    ),
    Spec(
        "running.weekly_volume_floor_km",
        "Weekly run volume target (km)",
        "number 10..60 (0 = off), max step 20; a target the cap grows toward, never a jump; "
        "raising by more than 5 is high risk",
        _path_current("running.weekly_volume_floor_km"),
        _numeric(10, 60, integer=False, max_step=20, risk=_floor_risk, unit=" km"),
    ),
    Spec(
        "running.long_run.cap_km",
        "Long run maximum (km)",
        "number 8..25, max step 3; never above 25",
        _path_current("running.long_run.cap_km"),
        _numeric(8, 25, integer=False, max_step=3, risk=_long_cap_risk, unit=" km"),
        after=_long_run_after,
    ),
    Spec(
        "running.long_run.step_km",
        "Long run weekly step (km)",
        "number 0.5..2, max step 1; above 1 is high risk",
        _path_current("running.long_run.step_km"),
        _numeric(0.5, 2, integer=False, max_step=1, risk=_step_risk, unit=" km"),
    ),
    # ---- progression engine v2 (docs/spec/decisions.md "Progression engine v2")
    _int_spec(
        "strength.stall_exposures_2x",
        "Clean sessions before a stall (lifts trained twice a week)",
        2,
        8,
        2,
        doc="int 2..8, max step 2 (strength.stall_exposures is the once-a-week value)",
    ),
    _int_spec("strength.stall_min_days", "Minimum days before a stall", 7, 28, 7),
    _num_spec(
        "strength.stall_reset_small_pct",
        "First stall-ladder step (% load drop)",
        2.5,
        10,
        2.5,
        unit=" %",
    ),
    _int_spec("strength.multi_stall_lifts", "Lifts stalled together for a soft deload", 2, 6, 2),
    _int_spec("strength.big_miss_reps", "Rep drop that steps the load down", 2, 5, 1),
    _int_spec("strength.rung_min_reps", "Predicted reps needed at the next load", 6, 10, 1),
    _int_spec("strength.rep_ceiling_cap", "Highest temporary rep ceiling", 12, 20, 3),
    _num_spec("strength.deload.sets_pct", "Deload: working sets cut (%)", 25, 60, 15, unit=" %"),
    _num_spec(
        "strength.deload.fatigue_index_pct",
        "Fatigue index that triggers a deload (%)",
        25,
        60,
        10,
        unit=" %",
    ),
    _num_spec(
        "running.interval_pace_tolerance_pct",
        "Interval rep pace tolerance (%)",
        1,
        6,
        2,
        unit=" %",
    ),
    _num_spec(
        "running.volume.cap_abs_km",
        "Weekly cap: absolute allowance over the 4-week mean (km)",
        0,
        4,
        1,
        risk=_up_risk(2),
        unit=" km",
        doc="number 0..4, max step 1; raising above 2 is high risk",
    ),
    _num_spec(
        "running.volume.single_run_factor",
        "Single run vs the longest of 30 days (x)",
        1.0,
        1.3,
        0.1,
        risk=_up_risk(1.15),
        doc="number 1.0..1.3, max step 0.1; raising above 1.15 is high risk",
    ),
    Spec(
        "running.volume.hold_last_week",
        "Weekly cap may hold last week's completed km",
        "true or false (low risk: repeating a volume just completed)",
        _path_current("running.volume.hold_last_week"),
        _bool_check,
    ),
    _num_spec(
        "running.volume.hold_limit_factor",
        "Held volume vs the highest of the 4 weeks before it (x)",
        1.0,
        1.2,
        0.1,
        risk=_up_risk(1.1),
        doc="number 1.0..1.2, max step 0.1; raising above 1.1 is high risk",
    ),
    _int_spec("running.long_run.step_back_every", "Long run step-back every N weeks", 3, 6, 1),
    _num_spec("running.long_run.step_back_pct", "Long run step-back (%)", 10, 30, 10, unit=" %"),
    _num_spec(
        "running.long_run.drift_hold_pct", "Long run drift that holds (%)", 3, 10, 2, unit=" %"
    ),
    _num_spec(
        "running.vdot.max_step",
        "VDOT: largest single update",
        0.25,
        1.0,
        0.5,
        risk=_up_risk(0.5),
        doc="number 0.25..1.0, max step 0.5; raising above 0.5 is high risk",
    ),
    _num_spec(
        "running.vdot.max_per_4w",
        "VDOT: largest rise per 4 weeks",
        0.5,
        2.0,
        0.5,
        risk=_up_risk(1.0),
        doc="number 0.5..2.0, max step 0.5; raising above 1.0 is high risk",
    ),
    _int_spec(
        "running.easy.strides.reps",
        "Strides on the easy run",
        0,
        8,
        2,
        risk=_always(LOW),
        doc="int 0..8, max step 2; always low risk",
    ),
    _int_spec(
        "running.threshold.reps_max",
        "Threshold reps (maximum)",
        3,
        8,
        2,
        risk=_risk_by_step(1, 2),
        doc="int 3..8, max step 2; +-1 is low risk",
    ),
    _num_spec(
        "running.threshold.max_work_min", "Threshold minutes per session", 15, 45, 10, unit=" min"
    ),
    _num_spec(
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
        _path_current("running.intervals.rep_distance_m"),
        _numeric(
            200,
            2000,
            integer=False,
            max_step=500,
            risk=_risk_by_step(0, 300),
            multiple=50,
            unit=" m",
        ),
    ),
    Spec(
        "running.intervals.recovery_distance_m",
        "Interval jog recovery (m)",
        "number 100..800 in steps of 50, max step 300; > 200 is high risk",
        _path_current("running.intervals.recovery_distance_m"),
        _numeric(
            100,
            800,
            integer=False,
            max_step=300,
            risk=_risk_by_step(0, 200),
            multiple=50,
            unit=" m",
        ),
    ),
)


def find_spec(param: str) -> tuple[Spec, dict[str, str]] | None:
    if not isinstance(param, str):
        return None
    for spec in SPECS:
        m = spec.regex.match(param)
        if m:
            return spec, m.groupdict()
    return None


def describe_allowlist() -> str:
    """Deterministic text for the system prompt (the single source of truth is SPECS)."""
    return "\n".join(f"- {s.template}: {s.doc}" for s in SPECS)


# --------------------------------------------------------------------------- verdicts


@dataclass(frozen=True)
class Verdict:
    valid: bool
    error: str | None
    risk: str | None  # the validator's classification; None for invalid changes
    value: Any  # normalised new value (valid changes)
    current: Any  # what the engine uses today
    label: str


def label_for(spec: Spec, groups: dict[str, str], ctx: Context) -> str:
    if "{name}" not in spec.label:
        return spec.label
    slug = groups.get("slug", "")
    name = None
    entries = ctx.program_entries(slug)
    if entries:
        name = ctx.overrides(slug).get("variant") or entries[0][1].variant
    return spec.label.replace("{name}", name or slug.replace("_", " ").title())


def validate_change(param: str, new_value: Any, ctx: Context) -> Verdict:
    """Validate one proposed change against `ctx` (not modified; use `accept` to chain changes)."""
    found = find_spec(param)
    if found is None:
        return Verdict(
            False,
            "param is not on the allowlist (only engine parameters can be changed, never "
            "weights, paces or heart-rate numbers)",
            None,
            None,
            None,
            str(param)[:120],
        )
    spec, groups = found
    label = label_for(spec, groups, ctx)
    cur = None
    try:
        cur = spec.current(ctx, groups)
        checked = spec.check(new_value, cur, ctx, groups)
        if spec.after is not None:
            trial = ctx.fork()
            trial.apply(param, checked.value)
            problem = spec.after(trial)
            if problem:
                raise Invalid(problem)
    except Invalid as exc:
        return Verdict(False, str(exc), None, None, cur, label)
    return Verdict(True, None, checked.risk, checked.value, cur, label)


def accept(param: str, verdict: Verdict, ctx: Context) -> None:
    """Fold a valid change into `ctx` so the next change is validated against it."""
    ctx.apply(param, verdict.value)


# --------------------------------------------------------------------------- schedule changes
#
# `schedule_changes` of the advisor output: move / skip / swap / shorten / replace a planned
# session of the current or next week. Each is validated on its own against the same hard rules
# the engine's missed-session policy uses (app/engine/schedule_rules.py), in the order given, on a
# working layout that already contains the earlier valid changes. Claude never sets a weight or
# a pace: a change only re-places or shortens a session, the engine recomputes its content.
#
# Rules: only the current and next week and never the past; the target must exist (a planned,
# still open session of that kind on that date); a session is changed at most once per proposal;
# move / swap stay inside one week; max 1 hard session per day, never two hard days in a row, the
# 24 h rule after lower, max 2 sessions per day; `shorten` only cuts runs, by at most
# `schedule.shorten_max_pct` (50 %); `replace` only goes to an equal or easier kind
# (`schedule.kind_load`) unless it makes up a missed session of that kind; the week's runs must
# stay under the weekly volume cap.
# Risk: skip, shorten and replace-by-an-easier-kind are low; move, swap and any other replace
# are medium.

SCHEDULE_ACTIONS = ("move", "skip", "swap", "shorten", "replace")


@dataclass(frozen=True)
class ScheduleVerdict:
    valid: bool
    error: str | None
    risk: str | None
    label: str
    session_id: str | None  # the planned session the change is about
    edits: tuple[SR.Edit, ...] = ()
    touched: tuple[str, ...] = ()  # planned session ids the change moves / alters
    makeup_for: str | None = None


@dataclass
class ScheduleContext:
    """The schedule a change is validated against (`work` accumulates the accepted changes)."""

    cfg: EngineConfig
    today: date
    rules: SR.Rules
    base: SR.Layout  # the plan as Claude saw it
    work: SR.Layout  # base + accepted changes
    week_start: date  # Monday of the current week
    rooms: dict[date, float | None] = field(default_factory=dict)  # volume room per week
    touched: set[str] = field(default_factory=set)

    @property
    def horizon_end(self) -> date:
        return self.week_start + timedelta(weeks=self.cfg.schedule_cfg.horizon_weeks)


def build_schedule_context(db: Session, cfg: EngineConfig, today: date) -> ScheduleContext:
    ws = today - timedelta(days=today.weekday())
    weeks = tuple(ws + timedelta(weeks=i) for i in range(cfg.schedule_cfg.horizon_weeks))
    rows = [
        r
        for w in weeks
        for r in SE.load_rows(db, w)
        if r.day >= ws - timedelta(days=SE.WINDOW_BEFORE)
    ]
    seen: dict[str, Any] = {r.id: r for r in rows}
    layout = SR.Layout(
        [SE.sess_from_row(r) for r in seen.values()],
        D.lower_days(db, ws - timedelta(days=SE.WINDOW_BEFORE), today),
    )
    ctx = ScheduleContext(
        cfg,
        today,
        SR.Rules.from_config(cfg),
        layout,
        layout.copy(),
        ws,
    )
    for w in weeks:
        ctx.rooms[w] = SE.volume_room(db, cfg, today, w)
    return ctx


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _fmt_day(d: date) -> str:
    return SR.weekday(d)


def _pretty(kind: str) -> str:
    return SR.PRETTY.get(kind, kind)


def schedule_label(
    action: str,
    target: str,
    d: date | None,
    to_d: date | None,
    new_kind: str | None,
    distance: float | None,
    partner_kind: str | None = None,
) -> str:
    t = _pretty(SR.normalize_kind(target) or target)
    day = _fmt_day(d) if d else "?"
    if action == "move":
        return f"Move {t} {day} → {_fmt_day(to_d) if to_d else '?'}"
    if action == "swap":
        other = f"{_pretty(partner_kind)} " if partner_kind else ""
        return f"Swap {t} {day} ↔ {other}{_fmt_day(to_d) if to_d else '?'}"
    if action == "skip":
        return f"Skip {t} {day}"
    if action == "shorten":
        km = f" to {_fmt(distance)} km" if distance is not None else ""
        return f"Shorten {t} {day}{km}"
    if action == "replace":
        nk = _pretty(SR.normalize_kind(new_kind) or new_kind or "?")
        return f"Replace {t} {day} with {nk}"
    return f"{action} {t} {day}"


def _bad(label: str, error: str) -> ScheduleVerdict:
    return ScheduleVerdict(False, error, None, label, None)


def _find(layout: SR.Layout, d: date, kind: str) -> SR.Sess | None:
    return next((s for s in layout.on(d) if s.kind == kind and s.is_open), None)


def _risk_for(action: str, *, easier: bool = False) -> str:
    if action in ("skip", "shorten") or (action == "replace" and easier):
        return LOW
    return MEDIUM


def _parse_day(raw: Any) -> date | None:
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def validate_schedule_change(
    ctx: ScheduleContext,
    *,
    day: Any,
    action: str,
    target: str,
    to_day: Any = None,
    new_kind: str | None = None,
    distance_km: float | None = None,
    reason: str = "",
) -> ScheduleVerdict:
    """Validate one schedule change. Pure over `ctx` (use `accept_schedule` to fold it in)."""
    d = _parse_day(day)
    to_d = _parse_day(to_day) if to_day not in (None, "") else None
    kind = SR.normalize_kind(target)
    nk = SR.normalize_kind(new_kind) if new_kind else None
    label = schedule_label(action, target, d, to_d, new_kind, distance_km)
    cfg = ctx.cfg
    sc = cfg.schedule_cfg
    if action not in SCHEDULE_ACTIONS:
        return _bad(label, f"unknown action '{action}' (use one of {', '.join(SCHEDULE_ACTIONS)})")
    if d is None:
        return _bad(label, "date must be YYYY-MM-DD")
    if kind is None:
        return _bad(label, f"unknown session kind '{target}'")
    if d < ctx.today:
        return _bad(label, f"{d.isoformat()} is in the past: only today and later can change")
    if d >= ctx.horizon_end:
        return _bad(label, "only the current and the next week can change")
    problem = override_problem(reason or "")
    if problem:
        return _bad(label, f"the reason {problem}: schedule changes never set weights or paces")
    # fields must match the action
    if action in ("move", "swap") and to_d is None:
        return _bad(label, f"{action} needs a valid to_date")
    if action not in ("move", "swap") and to_day not in (None, ""):
        return _bad(label, "to_date only applies to move and swap")
    if action == "replace" and not nk:
        return _bad(label, "replace needs a valid new_kind")
    if action != "replace" and new_kind:
        return _bad(label, "new_kind only applies to replace")
    if action == "shorten" and (distance_km is None or not math.isfinite(distance_km)):
        return _bad(label, "shorten needs distance_km")
    if action != "shorten" and distance_km is not None:
        return _bad(label, "distance_km only applies to shorten (changes never set paces)")

    sess = _find(ctx.base, d, kind)
    if sess is None:
        return _bad(label, f"no open planned {_pretty(kind)} on {d.isoformat()}")
    if sess.id in ctx.touched:
        return _bad(label, "this session is already changed by an earlier change in the proposal")
    if kind == SR.REST and action not in ("swap", "replace"):
        return _bad(label, "a rest day can only be swapped with a session or replaced")
    ws = _monday(d)
    edits: list[SR.Edit] = []
    touched = [sess.id]
    risk = MEDIUM
    makeup_for: str | None = None
    partner: SR.Sess | None = None

    if action == "skip":
        edits = [SR.Edit(sess.id, skip=True, reason=reason)]
        risk = LOW
    elif action == "shorten":
        if kind not in (SR.EASY, SR.LONG):
            return _bad(label, "only easy and long runs can be shortened")
        if sess.km is None:
            return _bad(label, "this run has no planned distance to shorten")
        new = round(float(distance_km), 1)  # type: ignore[arg-type]
        floor = sess.km * (1 - sc.shorten_max_pct / 100)
        if new >= sess.km - 1e-9:
            return _bad(label, f"shorten must reduce the distance (planned {_fmt(sess.km)} km)")
        if new < floor - 1e-9:
            return _bad(
                label,
                f"shorten may cut at most {_fmt(sc.shorten_max_pct)} % "
                f"(planned {_fmt(sess.km)} km, minimum {_fmt(round(floor, 1))} km)",
            )
        edits = [SR.Edit(sess.id, distance_km=new, reason=reason)]
        risk = LOW
    elif action == "replace":
        assert nk is not None
        if nk == kind:
            return _bad(label, f"already a {_pretty(kind)}: nothing to replace")
        missed = [
            m
            for m in ctx.base.sessions
            if m.status == "skipped"
            and m.kind == nk
            and _monday(m.day) == ws
            and m.day < ctx.today
            and not any(o.makeup_for == m.id for o in ctx.work.sessions)
        ]
        if ctx.rules.load(nk) <= ctx.rules.load(kind):
            easier = ctx.rules.load(nk) < ctx.rules.load(kind)
            risk = _risk_for("replace", easier=easier)
            edits = [SR.Edit(sess.id, kind=nk, reason=reason)]
        elif missed:
            m = missed[0]
            makeup_for = m.id
            risk = MEDIUM
            edits = [SR.Edit(sess.id, kind=nk, moved_from=m.day, makeup_for=m.id, reason=reason)]
        else:
            return _bad(
                label,
                f"replace may only go to an equal or easier kind: {_pretty(nk)} is harder than "
                f"{_pretty(kind)} (only the make-up of a missed {_pretty(nk)} may)",
            )
    else:  # move | swap
        assert to_d is not None
        if to_d == d:
            return _bad(label, "to_date is the same day")
        if to_d < ctx.today:
            return _bad(label, f"{to_d.isoformat()} is in the past: nothing can be placed there")
        if to_d >= ctx.horizon_end:
            return _bad(label, "only the current and the next week can change")
        if _monday(to_d) != ws:
            return _bad(label, "a session can only move inside its own week")
        there = [s for s in ctx.work.on(to_d) if s.is_open]
        if action == "move":
            rest = next((s for s in there if s.kind == SR.REST and not s.locked), None)
            edits = [SR.Edit(sess.id, day=to_d, moved_from=d, reason=reason)]
            if rest is not None and sess.kind != SR.REST:
                edits.append(
                    SR.Edit(
                        rest.id, day=d, moved_from=to_d, reason=f"made room for a move: {reason}"
                    )
                )
                touched.append(rest.id)
                partner = rest
        else:
            if len(there) != 1:
                return _bad(
                    label,
                    f"swap needs exactly one open session on {to_d.isoformat()} "
                    f"(found {len(there)})",
                )
            partner = there[0]
            if partner.id in ctx.touched:
                return _bad(label, "the other session is already changed by an earlier change")
            edits = [
                SR.Edit(sess.id, day=to_d, moved_from=d, reason=reason),
                SR.Edit(partner.id, day=d, moved_from=to_d, reason=f"swapped: {reason}"),
            ]
            touched.append(partner.id)
        risk = MEDIUM
    if partner is not None and action == "swap":
        label = schedule_label(action, target, d, to_d, new_kind, distance_km, partner.kind)

    # hard rules on the working layout
    after = SR.apply_edits(ctx.work, edits)
    moved = [x for x in (after.get(e.session_id) for e in edits if not e.skip) if x]
    problem = SR.past_problem(moved, ctx.today)
    if problem is None:
        found = SR.new_violations(ctx.work, after, ctx.rules, today=ctx.today)
        problem = found[0] if found else None
    if problem is None and action != "skip":
        problem = SR.volume_problem(
            ctx.work, after, cfg, today=ctx.today, week_start=ws, room_km=ctx.rooms.get(ws)
        )
    if problem:
        return _bad(label, problem)
    return ScheduleVerdict(
        True, None, risk, label, sess.id, tuple(edits), tuple(touched), makeup_for
    )


def accept_schedule(ctx: ScheduleContext, verdict: ScheduleVerdict) -> None:
    """Fold a valid schedule change into the working layout (later changes see it)."""
    ctx.work = SR.apply_edits(ctx.work, verdict.edits)
    ctx.touched.update(verdict.touched)


# --------------------------------------------------------------------------- session overrides

_WEIGHT_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:kg|kgs|lb|lbs)\b", re.I)
_PACE_RE = re.compile(r"\d{1,2}:\d{2}\s*(?:/|per)\s*(?:km|mi)|\d+(?:[.,]\d+)?\s*min\s*/\s*km", re.I)


def override_problem(text: str) -> str | None:
    """Session overrides are advisory notes: they may not prescribe a weight or a pace."""
    if _WEIGHT_RE.search(text):
        return "names a weight"
    if _PACE_RE.search(text):
        return "names a pace"
    return None


def build_context(
    db: Session,
    content: dict[str, Any],
    *,
    program: Program | None,
    settings=None,
) -> Context:
    """Context with the catalog lookups the swap check needs."""
    from app.db.catalog import load_catalog
    from app.engine import data as D
    from app.settings import get_settings

    s = settings or get_settings()
    regions: dict[str, str] = {}
    if s.catalog_path.exists():
        regions = {slug: c.body_region for slug, c in load_catalog(s.catalog_path).items()}
    gym = D.current_gym(db, s.gyms_path)
    return Context(copy.deepcopy(content), program, db, gym, regions)


__all__ = [
    "SCHEDULE_ACTIONS",
    "SPECS",
    "Context",
    "Invalid",
    "ScheduleContext",
    "ScheduleVerdict",
    "Verdict",
    "accept",
    "accept_schedule",
    "build_context",
    "build_schedule_context",
    "describe_allowlist",
    "find_spec",
    "override_problem",
    "validate_change",
    "validate_schedule_change",
]
