"""Building blocks of the parameter allowlist: the `Invalid` error (its message is shown to the
user), value helpers, the validation `Context`, `Spec` / `Checked`, and the generic number /
choice / switch checks every rule family builds its specs from.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from apps.fitness.advisor.rules.risk import LOW, MEDIUM, always
from apps.fitness.db.models import Gym
from apps.fitness.engine import config_service as cs
from apps.fitness.engine.config import EngineConfig
from apps.fitness.engine.program import Program

SLUG = r"(?P<slug>[a-z0-9_]{1,60})"


class Invalid(ValueError):
    """The change breaks a limit; the message is shown to the user."""


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


def fmt(v: Any) -> str:
    if isinstance(v, list | tuple):
        return "-".join(fmt(x) for x in v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


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


def path_current(path: str) -> Callable[[Context, dict[str, str]], Any]:
    return lambda ctx, _g: ctx.effective(path)


def numeric(
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
            raise Invalid(f"must be between {fmt(lo)} and {fmt(hi)}{unit} (got {fmt(new)})")
        if multiple and abs(new / multiple - round(new / multiple)) > 1e-9:
            raise Invalid(f"must be a multiple of {fmt(multiple)}{unit}")
        if cur is not None and _same(new, cur):
            raise Invalid(f"already {fmt(cur)}{unit}: nothing to change")
        if cur is not None and abs(new - cur) > max_step + 1e-9:
            raise Invalid(
                f"one change may move this by at most {fmt(max_step)}{unit} "
                f"(from {fmt(cur)} to {fmt(new)})"
            )
        return Checked(new, risk(float(cur if cur is not None else new), float(new)))

    return check


def choice(
    options: tuple[float, ...], risk: Callable[[float, float], str]
) -> Callable[[Any, Any, Context, dict[str, str]], Checked]:
    def check(value: Any, cur: Any, _ctx: Context, _g: dict[str, str]) -> Checked:
        new = to_number(value, integer=False)
        if not any(abs(new - o) < 1e-9 for o in options):
            raise Invalid(f"must be one of {', '.join(fmt(o) for o in options)} kg (got {new:g})")
        if cur is not None and _same(new, cur):
            raise Invalid(f"already {fmt(cur)} kg: nothing to change")
        return Checked(float(new), risk(float(cur if cur is not None else new), float(new)))

    return check


def int_spec(path: str, label: str, lo: int, hi: int, step: int, risk=None, doc: str = "") -> Spec:
    return Spec(
        path,
        label,
        doc or f"int {lo}..{hi}, max step {step}",
        path_current(path),
        numeric(lo, hi, integer=True, max_step=step, risk=risk or always(MEDIUM)),
    )


def num_spec(
    path: str, label: str, lo: float, hi: float, step: float, risk=None, unit="", doc=""
) -> Spec:
    return Spec(
        path,
        label,
        doc or f"number {fmt(lo)}..{fmt(hi)}, max step {fmt(step)}",
        path_current(path),
        numeric(lo, hi, integer=False, max_step=step, risk=risk or always(MEDIUM), unit=unit),
    )


def bool_check(value: Any, cur: Any, _ctx: Context, _g: dict[str, str]) -> Checked:
    if not isinstance(value, bool):
        raise Invalid("must be true or false")
    if cur is not None and value == cur:
        raise Invalid(f"already {str(cur).lower()}: nothing to change")
    return Checked(value, LOW)


def switch_check(on_risk: str, off_risk: str):
    """A true / false key whose risk depends on the direction (switching on vs off)."""

    def check(value: Any, cur: Any, ctx: Context, g: dict[str, str]) -> Checked:
        bool_check(value, cur, ctx, g)
        return Checked(value, on_risk if value else off_risk)

    return check
