"""Next-load computation per equipment type. Pure functions, no DB access."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from apps.fitness.db.types import EquipmentType, LoadConvention
from apps.fitness.engine.config import EngineConfig, IncrementConfig

_EPS = 1e-6


class VariantLike(Protocol):
    """Anything shaped like an ExerciseVariant (ORM row or a plain dataclass)."""

    @property
    def equipment_type(self) -> str: ...
    @property
    def load_convention(self) -> str: ...
    @property
    def weight_ladder(self) -> Sequence[float] | None: ...


def next_rung(ladder: Sequence[float] | None, current: float, fallback_step: float) -> float:
    """Smallest ladder weight strictly above `current`; above the top rung add `fallback_step`."""
    if ladder:
        for w in sorted(float(x) for x in ladder):
            if w > current + _EPS:
                return w
    return round(current + fallback_step, 3)


def next_load(
    variant: VariantLike, current: float, config: EngineConfig | IncrementConfig
) -> float:
    """The next heavier load to prescribe for `variant`, starting from `current` (kg, as logged).

    - barbell / smith / plate_loaded: + fixed step (2 x 2.5 kg by default)
    - dumbbell: + `dumbbell_increment_kg` (2.0 or 2.5) per dumbbell; doubled if the variant logs
      the total of both dumbbells (load_convention=total)
    - machine_stack / cable: next rung on the variant's weight_ladder, else + default step
    - bodyweight: + smallest added plate (weight is the added load)
    - assistance (assisted pull-up): progression means *less* assistance -> previous rung / -step
    """
    inc = config.increments if isinstance(config, EngineConfig) else config
    eq = EquipmentType(variant.equipment_type)
    conv = LoadConvention(variant.load_convention)

    if conv is LoadConvention.ASSISTANCE:
        lower = [float(w) for w in (variant.weight_ladder or []) if float(w) < current - _EPS]
        if lower:
            return max(lower)
        return max(0.0, round(current - inc.default_stack_step_kg, 3))

    match eq:
        case EquipmentType.BARBELL:
            step = inc.barbell_kg
        case EquipmentType.SMITH:
            step = inc.smith_kg
        case EquipmentType.PLATE_LOADED:
            step = inc.plate_loaded_kg
        case EquipmentType.DUMBBELL:
            step = inc.dumbbell_increment_kg * (2 if conv is LoadConvention.TOTAL else 1)
        case EquipmentType.MACHINE_STACK | EquipmentType.CABLE:
            return next_rung(variant.weight_ladder, current, inc.default_stack_step_kg)
        case EquipmentType.BODYWEIGHT:
            step = inc.bodyweight_added_kg
        case _:
            step = inc.other_kg
    return round(current + step, 3)


def _step_for(variant: VariantLike, inc: IncrementConfig) -> float:
    """Fixed per-step load change for equipment without a ladder."""
    eq = EquipmentType(variant.equipment_type)
    conv = LoadConvention(variant.load_convention)
    match eq:
        case EquipmentType.BARBELL:
            return inc.barbell_kg
        case EquipmentType.SMITH:
            return inc.smith_kg
        case EquipmentType.PLATE_LOADED:
            return inc.plate_loaded_kg
        case EquipmentType.DUMBBELL:
            return inc.dumbbell_increment_kg * (2 if conv is LoadConvention.TOTAL else 1)
        case EquipmentType.MACHINE_STACK | EquipmentType.CABLE:
            return inc.default_stack_step_kg
        case EquipmentType.BODYWEIGHT:
            return inc.bodyweight_added_kg
        case _:
            return inc.other_kg


def previous_load(
    variant: VariantLike, current: float, config: EngineConfig | IncrementConfig
) -> float:
    """One step easier than `current` (inverse of `next_load`; never below 0).

    Assistance variants get *more* assistance (the next rung up / + step).
    """
    inc = config.increments if isinstance(config, EngineConfig) else config
    conv = LoadConvention(variant.load_convention)
    eq = EquipmentType(variant.equipment_type)
    ladder = sorted(float(w) for w in (variant.weight_ladder or []))
    if conv is LoadConvention.ASSISTANCE:
        return next_rung(ladder, current, inc.default_stack_step_kg)
    if eq in (EquipmentType.MACHINE_STACK, EquipmentType.CABLE):
        lower = [w for w in ladder if w < current - _EPS]
        if lower:
            return lower[-1]
    return max(0.0, round(current - _step_for(variant, inc), 3))


def round_down_load(
    variant: VariantLike,
    current: float,
    target: float,
    config: EngineConfig | IncrementConfig,
) -> float:
    """The highest *available* load that is <= `target`, staying on the grid of `current`.

    Stack/cable with a ladder: the largest rung <= target. Otherwise `current` minus the fewest
    whole increments that get to or below target (so 52.5 kg barbell loads stay on their grid),
    never below 0. Always at least one step below `current` when target < current. Assistance
    variants round the assistance UP (more help = easier).
    """
    inc = config.increments if isinstance(config, EngineConfig) else config
    conv = LoadConvention(variant.load_convention)
    eq = EquipmentType(variant.equipment_type)
    ladder = sorted(float(w) for w in (variant.weight_ladder or []))
    step = _step_for(variant, inc)
    if conv is LoadConvention.ASSISTANCE:
        above = [w for w in ladder if w >= target - _EPS]
        if above:
            return above[0]
        k = math.ceil((target - current) / step - _EPS)
        return round(current + max(k, 0) * step, 3)
    if eq in (EquipmentType.MACHINE_STACK, EquipmentType.CABLE) and ladder:
        below = [w for w in ladder if w <= target + _EPS]
        return below[-1] if below else ladder[0]
    if target >= current - _EPS:
        return round(current, 3)
    k = math.ceil((current - target) / step - _EPS)
    return max(0.0, round(current - k * step, 3))
