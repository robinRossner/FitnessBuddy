"""Stable identifiers and names the engine derives without the database: planned-session ids,
template slot ids, the gym-free base name of an exercise variant."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Protocol

from apps.fitness.engine.core.scheduling import WEEKDAYS

PLAN_NS = uuid.UUID("5f0c3b52-71f1-4a6e-9c3e-3d1d0a7b9e11")


class _Named(Protocol):
    @property
    def name(self) -> str: ...


class GymVariantLike(Protocol):
    """What `base_name_of` reads from an exercise variant (an `ExerciseVariant` row fits)."""

    @property
    def name(self) -> str: ...

    @property
    def gym_id(self) -> int | None: ...

    @property
    def gym(self) -> _Named | None: ...


def planned_id(week_start: date, slot_id: str) -> str:
    """Stable client-visible id of a planned session: one per (week, template slot)."""
    return str(uuid.uuid5(PLAN_NS, f"{week_start.isoformat()}|{slot_id}"))


def template_slot_ids(template: dict[str, str]) -> list[str]:
    return [f"{wd}_{template[wd]}" for wd in WEEKDAYS if template.get(wd)]


def base_name_of(variant: GymVariantLike) -> str:
    if variant.gym_id is not None and variant.gym is not None:
        suffix = f" @ {variant.gym.name}"
        if variant.name.endswith(suffix):
            return variant.name[: -len(suffix)]
    return variant.name
