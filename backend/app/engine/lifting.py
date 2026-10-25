"""Lifting progression for one exercise variant. Pure functions over plain data.

Double progression inside a fixed rep range (config `strength.rep_range`, per-exercise override).
The user trains every working set to 0-1 RIR, so the engine never prescribes RPE/RIR changes:

* all working sets of the last exposure reached the top of the range at the SAME load
  -> next load (`next_load`), every set targets the range bottom;
* the first working set fell below the range bottom (and the config flag is on)
  -> one step down, every set targets the range bottom;
* otherwise: same load, each set targets last reps + 1, capped at the top of the range.

Stall = no best-e1RM improvement over the last `stall_exposures` exposures -> load -10 %
(rounded down to an available load), rebuild from the range bottom, and an advisor trigger.
Deload (from the readiness streak) = fewer sets and a lighter load. The first prescription for
a variant without history has no weight (the app asks for one).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.engine.config import EngineConfig
from app.engine.increments import VariantLike, next_load, previous_load, round_down_load

_EPS = 1e-9


@dataclass(frozen=True)
class SetResult:
    weight: float | None
    reps: int | None
    duration_s: float | None = None


@dataclass(frozen=True)
class Exposure:
    """One session's working sets of one variant (warm-ups excluded), oldest first in history."""

    day: date
    sets: tuple[SetResult, ...]
    best_e1rm: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.day.isoformat(),
            "sets": [
                {"weight": s.weight, "reps": s.reps, "duration_s": s.duration_s} for s in self.sets
            ],
            "best_e1rm": self.best_e1rm,
        }


@dataclass(frozen=True)
class LiftSpec:
    variant: VariantLike
    rep_range: tuple[int, int]
    n_sets: int
    increment_kg: float | None = None  # per-exercise override of the load step
    timed: bool = False


@dataclass(frozen=True)
class StallReset:
    """The engine's last stall reset for this variant (from decisions_log)."""

    day: date
    load: float


@dataclass
class SetTarget:
    index: int
    weight: float | None
    reps: int | None
    set_type: str = "working"
    duration_s: float | None = None

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "index": self.index,
            "set_type": self.set_type,
            "target_weight": self.weight,
            "target_reps": self.reps,
        }
        if self.duration_s is not None:
            d["target_duration_s"] = self.duration_s
        return d


@dataclass
class LiftPrescription:
    sets: list[SetTarget]
    rule: str
    reason: str
    previous: Exposure | None = None
    flags: list[str] = field(default_factory=list)
    stall: bool = False
    inputs: dict[str, Any] = field(default_factory=dict)

    @property
    def load(self) -> float | None:
        return self.sets[0].weight if self.sets else None


# --------------------------------------------------------------------------- helpers


def detect_stall(e1rms: Sequence[float | None], n: int) -> bool:
    """No improvement of the best e1RM over the last `n` exposures vs. everything before them."""
    vals = [v for v in e1rms if v is not None]
    if n < 1 or len(vals) < n + 1:
        return False
    before, window = vals[:-n], vals[-n:]
    return max(window) <= max(before) + _EPS


def _step(spec: LiftSpec, cfg: EngineConfig, current: float, direction: int) -> float:
    if spec.increment_kg:
        return max(0.0, round(current + direction * spec.increment_kg, 3))
    if direction > 0:
        return next_load(spec.variant, current, cfg)
    return previous_load(spec.variant, current, cfg)


def _working(exposure: Exposure) -> list[SetResult]:
    return [s for s in exposure.sets if s.reps is not None or s.duration_s is not None]


def apply_deload(
    sets: list[SetTarget], spec: LiftSpec, cfg: EngineConfig
) -> tuple[list[SetTarget], str]:
    """Deload week: sets -`sets_pct` % (rounded, min 1) and load -`load_pct` % (rounded down)."""
    d = cfg.strength.deload
    n = max(1, round(len(sets) * (1 - d.sets_pct / 100)))
    out = [
        SetTarget(i + 1, s.weight, s.reps, s.set_type, s.duration_s) for i, s in enumerate(sets[:n])
    ]
    for t in out:
        if t.weight:
            t.weight = round_down_load(
                spec.variant, t.weight, t.weight * (1 - d.load_pct / 100), cfg
            )
    return out, f"deload: {len(sets)}->{n} sets, load -{d.load_pct:g}%"


def drop_sets(sets: list[SetTarget], n: int = 1) -> list[SetTarget]:
    """Yellow readiness: same effort, `n` fewer working sets (min 1)."""
    keep = max(1, len(sets) - n)
    return [
        SetTarget(i + 1, s.weight, s.reps, s.set_type, s.duration_s)
        for i, s in enumerate(sets[:keep])
    ]


# --------------------------------------------------------------------------- main rule


def prescribe(
    spec: LiftSpec,
    history: Sequence[Exposure],
    cfg: EngineConfig,
    *,
    deload: bool = False,
    stall_reset: StallReset | None = None,
) -> LiftPrescription:
    """Targets for the next exposure of `spec.variant`. `history` is oldest first."""
    lo, hi = spec.rep_range
    st = cfg.strength
    hist = [e for e in history if _working(e)]
    last = hist[-1] if hist else None
    inputs: dict[str, Any] = {
        "variant": getattr(spec.variant, "name", None),
        "rep_range": [lo, hi],
        "n_sets": spec.n_sets,
        "last": last.as_dict() if last else None,
        "exposures": len(hist),
        "recent_e1rm": [e.best_e1rm for e in hist[-(st.stall_exposures + 1) :]],
    }

    def done(p: LiftPrescription) -> LiftPrescription:
        p.previous, p.inputs = last, inputs
        if deload and p.rule != "no_history":
            p.sets, note = apply_deload(p.sets, spec, cfg)
            p.rule = f"{p.rule}+deload"
            p.reason = f"{p.reason}; {note}"
            p.flags.append("deload")
        return p

    if spec.timed:
        return done(_prescribe_timed(spec, last, cfg))

    if last is None:
        sets = [SetTarget(i + 1, None, lo) for i in range(spec.n_sets)]
        return done(
            LiftPrescription(
                sets, "no_history", f"no history for this variant: pick a load for {lo}-{hi} reps"
            )
        )

    ws = _working(last)
    top_weight = max((s.weight or 0.0) for s in ws)

    # A previous stall reset that has not been trained yet stays in force.
    if stall_reset is not None and last.day < stall_reset.day:
        sets = [SetTarget(i + 1, stall_reset.load, lo) for i in range(spec.n_sets)]
        return done(
            LiftPrescription(
                sets,
                "stall_reset",
                f"stall reset to {stall_reset.load:g} kg from {stall_reset.day} not trained yet",
                flags=["stall"],
            )
        )

    # ---- stall: only exposures since the last reset count
    window = hist
    if stall_reset is not None:
        window = [e for e in hist if e.day >= stall_reset.day]
    if detect_stall([e.best_e1rm for e in window], st.stall_exposures):
        target = top_weight * (1 - st.stall_reset_pct / 100)
        new_w = round_down_load(spec.variant, top_weight, target, cfg)
        if spec.increment_kg and spec.increment_kg > 0:
            k = math.ceil((top_weight - target) / spec.increment_kg - _EPS)
            new_w = max(0.0, round(top_weight - k * spec.increment_kg, 3))
        sets = [SetTarget(i + 1, new_w, lo) for i in range(spec.n_sets)]
        flags = ["stall"]
        recent = window[-st.stall_exposures :]
        if (
            len(recent) > 1
            and len({tuple((s.weight, s.reps) for s in e.sets) for e in recent}) == 1
        ):
            flags.append("identical_exposures")  # same sets every time: stall or unedited log?
        note = "" if new_w < top_weight else " (already the lowest available load)"
        p = LiftPrescription(
            sets,
            "stall_reset",
            f"no e1RM improvement in the last {st.stall_exposures} exposures: "
            f"{top_weight:g} -> {new_w:g} kg (-{st.stall_reset_pct:g}%, rounded down){note}, "
            f"rebuild from {lo} reps"
            + (", identical sets every time" if "identical_exposures" in flags else ""),
            flags=flags,
            stall=True,
        )
        return done(p)

    # ---- double progression
    src = [ws[i] if i < len(ws) else ws[-1] for i in range(spec.n_sets)]
    same_load = len({s.weight for s in ws}) == 1
    all_top = all((s.reps or 0) >= hi for s in ws)
    if same_load and all_top:
        new_w = _step(spec, cfg, ws[0].weight or 0.0, +1)
        sets = [SetTarget(i + 1, new_w, lo) for i in range(spec.n_sets)]
        return done(
            LiftPrescription(
                sets,
                "double_progression:increase_load",
                f"all {len(ws)} sets hit {hi} reps at {ws[0].weight:g} kg -> {new_w:g} kg, "
                f"back to {lo} reps",
            )
        )
    first = ws[0]
    if (
        st.decrease_load_if_first_set_below_range
        and (first.reps or 0) < lo
        and (first.weight or 0) > 0
    ):
        new_w = _step(spec, cfg, first.weight or 0.0, -1)
        sets = [SetTarget(i + 1, new_w, lo) for i in range(spec.n_sets)]
        return done(
            LiftPrescription(
                sets,
                "double_progression:decrease_load",
                f"first set only {first.reps} reps (< {lo}) at {first.weight:g} kg -> "
                f"{new_w:g} kg, target {lo} reps",
                flags=["load_down"],
            )
        )
    sets = [SetTarget(i + 1, s.weight, min((s.reps or 0) + 1, hi)) for i, s in enumerate(src)]
    return done(
        LiftPrescription(
            sets,
            "double_progression:add_reps",
            "same load, each set +1 rep (cap "
            f"{hi}): last {'/'.join(str(s.reps) for s in ws)} @ {top_weight:g} kg",
        )
    )


def _prescribe_timed(spec: LiftSpec, last: Exposure | None, cfg: EngineConfig) -> LiftPrescription:
    step = cfg.strength.timed_step_s
    if last is None:
        sets = [SetTarget(i + 1, None, None, duration_s=None) for i in range(spec.n_sets)]
        return LiftPrescription(sets, "no_history", "no history: hold as long as you can")
    best = max((s.duration_s or 0.0) for s in _working(last))
    sets = [SetTarget(i + 1, None, None, duration_s=best + step) for i in range(spec.n_sets)]
    return LiftPrescription(
        sets,
        "timed_progression",
        f"best hold last time {best:g}s -> target {best + step:g}s per set",
    )
