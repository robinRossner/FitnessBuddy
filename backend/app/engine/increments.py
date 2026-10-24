"""Next-load computation per equipment type. Pure functions, no DB access."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.db.types import EquipmentType, LoadConvention
from app.engine.config import EngineConfig, IncrementConfig

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
