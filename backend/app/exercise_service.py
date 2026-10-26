"""Exercises the user manages from the app (contract: docs/api/exercises.md).

* A new exercise is an `Exercise` row outside the catalog (slug `user_<name>`), so the catalog
  sync never touches it, plus its first variant.
* A variant of an existing exercise: gym-bound equipment (machine, cable, smith, plate loaded)
  becomes a machine at a gym (`gym_service.create_machine`, same identity and revive rules);
  barbell / dumbbell / bodyweight / other variants are generic (`gym_id` null) with their own
  deterministic key, so adding one twice returns the same variant.
* Edits: the muscle of an exercise (`edited_by_user` keeps it through catalog syncs), the
  equipment type and load step of a variant.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import gym_service as gs
from app.db.catalog import MUSCLE_REGION
from app.db.models import Exercise, ExerciseVariant, Gym
from app.db.types import EquipmentType, LoadConvention
from app.gyms import GYM_BOUND_EQUIPMENT
from app.settings import Settings

MUSCLES = tuple(MUSCLE_REGION)
EQUIPMENT = tuple(e.value for e in EquipmentType)
INCREMENT_MAX_KG = 50.0
_NS = uuid.UUID("0c7f4f8e-4f86-4d0a-9a43-3f0f5b3f6a21")  # user-made generic variants

# muscle -> movement pattern of a new exercise (only used for grouping and the coach summary)
_PATTERN = {
    "chest": "horizontal_push",
    "shoulders": "vertical_push",
    "rear_delts": "horizontal_abduction",
    "triceps": "elbow_extension",
    "back": "horizontal_pull",
    "lats": "vertical_pull",
    "traps": "scapular_elevation",
    "biceps": "elbow_flexion",
    "forearms": "wrist_flexion",
    "quads": "squat",
    "hamstrings": "hinge",
    "glutes": "hinge",
    "adductors": "hip_adduction",
    "calves": "calf_raise",
    "abs": "trunk_flexion",
}

EQUIPMENT_LABEL = {
    "barbell": "Barbell",
    "dumbbell": "Dumbbell",
    "machine_stack": "Machine",
    "plate_loaded": "Plate Loaded",
    "cable": "Cable",
    "bodyweight": "Bodyweight",
    "smith": "Smith Machine",
    "other": None,
}


class ExerciseError(gs.GymError):
    """Same shape as GymError: an HTTP status plus a message."""


def generic_variant_key(exercise_id: int, equipment_type: str) -> str:
    return str(uuid.uuid5(_NS, f"user:generic:{exercise_id}|{equipment_type}"))


def is_user_generic_variant(v: ExerciseVariant) -> bool:
    return v.gym_id is None and v.key == generic_variant_key(
        v.exercise_id, gs._val(v.equipment_type)
    )


def variant_name(base: str, equipment_type: str) -> str:
    label = EQUIPMENT_LABEL.get(equipment_type)
    return f"{base} ({label})" if label else base


def _slug(db: Session, name: str) -> str:
    base = "user_" + (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "exercise")[:60]
    slug, n = base, 2
    while db.scalar(select(Exercise.id).where(Exercise.slug == slug)) is not None:
        slug, n = f"{base}_{n}", n + 1
    return slug


def _check_muscle(muscle: str) -> None:
    if muscle not in MUSCLES:
        raise ExerciseError(422, f"muscle_group must be one of {', '.join(MUSCLES)}")


def _check_equipment(equipment_type: str) -> None:
    if equipment_type not in EQUIPMENT:
        raise ExerciseError(422, f"equipment_type must be one of {', '.join(EQUIPMENT)}")


def _gym_for(db: Session, gym_id: int | None, settings: Settings) -> Gym:
    if gym_id is not None:
        return gs.find_gym(db, gym_id)
    gym = gs.effective_current_gym(db, settings)
    if gym is None:
        raise ExerciseError(422, "gym_id is required for machines (no current gym)")
    return gym


def create_exercise(
    db: Session,
    *,
    name: str,
    muscle_group: str,
    equipment_type: str,
    timed: bool,
    gym_id: int | None,
    increment_kg: float | None,
    settings: Settings,
) -> tuple[Exercise, ExerciseVariant]:
    """A new exercise outside the catalog with its first variant."""
    name = " ".join(name.split())
    if not name:
        raise ExerciseError(422, "name must not be empty")
    _check_muscle(muscle_group)
    _check_equipment(equipment_type)
    clash = db.scalar(select(Exercise).where(func.lower(Exercise.name) == name.lower()))
    if clash is not None:
        raise ExerciseError(409, f"an exercise named {clash.name!r} already exists: add a variant")
    ex = Exercise(
        slug=_slug(db, name),
        name=name,
        primary_muscle=muscle_group,
        secondary_muscles=[],
        movement_pattern=_PATTERN.get(muscle_group, "other"),
        default_equipment_type=EquipmentType(equipment_type),
        e1rm_meaningful=not timed,
        is_timed=timed,
        bodyweight_loaded=equipment_type == "bodyweight",
        edited_by_user=True,
    )
    db.add(ex)
    db.flush()
    v = add_variant(db, ex, equipment_type=equipment_type, gym_id=gym_id, settings=settings)
    if increment_kg is not None:
        v.increment_kg = increment_kg
    db.flush()
    return ex, v


def add_variant(
    db: Session,
    ex: Exercise,
    *,
    equipment_type: str,
    gym_id: int | None,
    settings: Settings,
) -> ExerciseVariant:
    """The variant of `ex` with this equipment (at the gym for machines): an existing live one,
    else a revived or new one. Idempotent."""
    _check_equipment(equipment_type)
    if equipment_type in GYM_BOUND_EQUIPMENT:
        gym = _gym_for(db, gym_id, settings)
        here = list(
            db.scalars(
                select(ExerciseVariant).where(
                    ExerciseVariant.exercise_id == ex.id,
                    ExerciseVariant.gym_id == gym.id,
                    ExerciseVariant.deleted_at.is_(None),
                    ExerciseVariant.active.is_(True),
                )
            )
        )
        same = [v for v in here if gs._val(v.equipment_type) == equipment_type]
        if same or here:
            return (same or here)[0]
        # program exercises keep the program's name (the plan finds the machine by it)
        in_program = ex.slug in gs.program_entries(db, settings)
        return gs.create_machine(
            db,
            gym,
            ex,
            name=None if in_program else f"{variant_name(ex.name, equipment_type)} @ {gym.name}",
            equipment_type=equipment_type,
            weight_ladder=None,
            notes=None,
            settings=settings,
        )
    generic = list(
        db.scalars(
            select(ExerciseVariant).where(
                ExerciseVariant.exercise_id == ex.id,
                ExerciseVariant.gym_id.is_(None),
                ExerciseVariant.equipment_type == EquipmentType(equipment_type),
            )
        )
    )
    live = [v for v in generic if v.deleted_at is None]
    if live:
        v = max(live, key=lambda v: (v.active, -v.id))
        v.active = True
        db.flush()
        return v
    key = generic_variant_key(ex.id, equipment_type)
    v = next((x for x in generic if x.key == key), None) or (generic[0] if generic else None)
    if v is not None:  # revive (same id, history kept)
        v.deleted_at = None
        v.active = True
        db.flush()
        return v
    name = variant_name(ex.name, equipment_type)
    if db.scalar(
        select(ExerciseVariant.id).where(
            ExerciseVariant.name == name, ExerciseVariant.gym_id.is_(None)
        )
    ):
        raise ExerciseError(409, f"a variant named {name!r} already exists")
    v = ExerciseVariant(
        exercise_id=ex.id,
        gym_id=None,
        key=key,
        name=name,
        equipment_type=EquipmentType(equipment_type),
        load_convention=LoadConvention.PER_HAND
        if equipment_type == "dumbbell"
        else LoadConvention.TOTAL,
        weight_ladder=None,
        ladder_inferred=False,
        active=True,
    )
    db.add(v)
    db.flush()
    return v


def update_exercise(db: Session, ex: Exercise, fields: dict[str, Any]) -> Exercise:
    if fields.get("muscle_group") is not None:
        _check_muscle(fields["muscle_group"])
        ex.primary_muscle = fields["muscle_group"]
        ex.edited_by_user = True
    db.flush()
    return ex


def check_increment(value: float | None) -> float | None:
    if value is None:
        return None
    if not 0 < value <= INCREMENT_MAX_KG:
        raise ExerciseError(422, f"increment_kg must be above 0 and at most {INCREMENT_MAX_KG:g}")
    return round(float(value) / 0.25) * 0.25 or 0.25
