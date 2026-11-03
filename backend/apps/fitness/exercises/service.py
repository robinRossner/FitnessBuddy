"""Exercises the user manages from the app (contract: docs/api/exercises.md).

* A new exercise is an `Exercise` row outside the catalog (slug `user_<name>`), so the catalog
  sync never touches it, plus its first variant.
* A variant of an existing exercise: gym-bound equipment (machine, cable, smith, plate loaded)
  becomes a machine at a gym (`gyms.service.create_machine`, same identity and revive rules);
  barbell / dumbbell / bodyweight / other variants are generic (`gym_id` null) with their own
  deterministic key, so adding one twice returns the same variant.
* Names may repeat when the equipment differs: creating "Row" as a cable when a barbell "Row"
  exists adds a variant to that exercise (only the same name AND equipment, at the same gym for
  machines, is a duplicate). Renaming an exercise renames its variants with it and keeps the
  plan pointing at them.
* Edits: the name, muscle and progression mode (load | reps) of an exercise (`edited_by_user`
  keeps them through catalog syncs), the equipment type and load step of a variant. `time` is set
  at creation only: timed exercises log durations, so switching would break their history.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.fitness.db.catalog import MUSCLE_REGION
from apps.fitness.db.models import Exercise, ExerciseVariant, Gym
from apps.fitness.db.types import EquipmentType, LoadConvention, Progression
from apps.fitness.gyms import service as gs
from apps.fitness.gyms.catalog import GYM_BOUND_EQUIPMENT
from apps.fitness.settings import Settings, get_settings
from apps.fitness.sync.pull import exercise_out, program_rep_ranges, variant_out

MUSCLES = tuple(MUSCLE_REGION)
EQUIPMENT = tuple(e.value for e in EquipmentType)
INCREMENT_MAX_KG = 50.0
PROGRESSIONS = tuple(p.value for p in Progression)
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


def resolve_progression(timed: bool | None, progression: str | None) -> str:
    """The mode of a new exercise from the request's `timed` and `progression` (both optional;
    `timed: true` alone means time, nothing means load). A contradiction is a 422."""
    if progression is not None and progression not in PROGRESSIONS:
        raise ExerciseError(422, f"progression must be one of {', '.join(PROGRESSIONS)}")
    if progression is None:
        return Progression.TIME.value if timed else Progression.LOAD.value
    if timed is not None and timed != (progression == Progression.TIME):
        raise ExerciseError(
            422, f"timed={str(timed).lower()} contradicts progression={progression}"
        )
    return progression


def _gym_for(db: Session, gym_id: int | None, settings: Settings) -> Gym:
    if gym_id is not None:
        return gs.find_gym(db, gym_id)
    gym = gs.effective_current_gym(db, settings)
    if gym is None:
        raise ExerciseError(422, "gym_id is required for machines (no current gym)")
    return gym


def _slot(db: Session, ex: Exercise, equipment_type: str, gym: Gym | None) -> str:
    """Can `ex` take a variant with this equipment (at `gym` for machines)? "same": it has one
    already; "taken": another machine type is set up at that gym (one machine per exercise and
    gym); "free": add it."""
    here = [
        v
        for v in db.scalars(
            select(ExerciseVariant).where(
                ExerciseVariant.exercise_id == ex.id,
                ExerciseVariant.gym_id == gym.id
                if gym is not None
                else ExerciseVariant.gym_id.is_(None),
                ExerciseVariant.deleted_at.is_(None),
                ExerciseVariant.active.is_(True),
            )
        )
    ]
    if any(gs._val(v.equipment_type) == equipment_type for v in here):
        return "same"
    return "taken" if gym is not None and here else "free"


def create_exercise(
    db: Session,
    *,
    name: str,
    muscle_group: str,
    equipment_type: str,
    progression: str,
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
    gym = _gym_for(db, gym_id, settings) if equipment_type in GYM_BOUND_EQUIPMENT else None
    same_name = list(
        db.scalars(
            select(Exercise).where(func.lower(Exercise.name) == name.lower()).order_by(Exercise.id)
        )
    )
    slots = [(e, _slot(db, e, equipment_type, gym)) for e in same_name]
    for e, slot in slots:
        if slot == "same":
            where = f" at {gym.name}" if gym is not None else ""
            raise ExerciseError(409, f"{e.name!r} ({equipment_type}) already exists{where}")
    # The same name with other equipment is the same movement: it gets a variant (history stays
    # grouped). Its muscle and progression mode win over the request's.
    ex = next((e for e, slot in slots if slot == "free"), None)
    if ex is None:  # no same-named exercise, or each already has another machine at this gym
        ex = Exercise(
            slug=_slug(db, name),
            name=name,
            primary_muscle=muscle_group,
            secondary_muscles=[],
            movement_pattern=_PATTERN.get(muscle_group, "other"),
            default_equipment_type=EquipmentType(equipment_type),
            e1rm_meaningful=progression == Progression.LOAD,
            is_timed=progression == Progression.TIME,
            progression=progression,
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


def update_exercise(
    db: Session, ex: Exercise, fields: dict[str, Any], settings: Settings
) -> tuple[Exercise, list[ExerciseVariant], bool]:
    """Apply PATCH fields. Returns (exercise, renamed variants, plan changed): the plan changed
    when a program variant got a new name or the progression mode changed, so the caller
    re-runs the engine."""
    if fields.get("muscle_group") is not None:
        _check_muscle(fields["muscle_group"])
    mode = fields.get("progression")
    if mode is not None:
        if mode not in PROGRESSIONS:
            raise ExerciseError(422, f"progression must be one of {', '.join(PROGRESSIONS)}")
        current = Progression.TIME.value if ex.is_timed else (ex.progression or "load")
        if mode != current and Progression.TIME.value in (mode, current):
            raise ExerciseError(
                422, "progression time can only be set when the exercise is created: "
                "timed exercises log durations"
            )  # fmt: skip
    renamed: list[ExerciseVariant] = []
    plan_changed = False
    if fields.get("name") is not None:
        name = " ".join(fields["name"].split())
        if not name:
            raise ExerciseError(422, "name must not be empty")
        if name != ex.name:
            renamed, plan_changed = _rename(db, ex, name, settings)
    if fields.get("muscle_group") is not None:
        ex.primary_muscle = fields["muscle_group"]
        ex.edited_by_user = True
    if mode is not None and mode != current:
        ex.progression = mode
        ex.edited_by_user = True  # the catalog sync keeps the user's choice
        plan_changed = True
    db.flush()
    return ex, renamed, plan_changed


def _rename(
    db: Session, ex: Exercise, name: str, settings: Settings
) -> tuple[list[ExerciseVariant], bool]:
    """Rename the exercise and all its live variants to "<name> (<equipment>)" (keeping the
    " @ gym" suffix), so the new name shows everywhere. Identity does not change: ids, keys and
    history stay. A program exercise's plan line finds its variant by name, so that line gets the
    new name as a config override (`exercise_overrides.<slug>.variant`, a "user" config version).
    `edited_by_user` makes the catalog sync keep the name."""
    from apps.fitness.engine.store import config_service as cs

    live = list(
        db.scalars(
            select(ExerciseVariant)
            .where(ExerciseVariant.exercise_id == ex.id, ExerciseVariant.deleted_at.is_(None))
            .order_by(ExerciseVariant.id)
        )
    )
    # a true duplicate: another exercise of that name already has this equipment (at this gym)
    for v in live:
        if not v.active:
            continue
        twin = db.scalar(
            select(Exercise.name)
            .join(ExerciseVariant, ExerciseVariant.exercise_id == Exercise.id)
            .where(
                func.lower(Exercise.name) == name.lower(),
                Exercise.id != ex.id,
                ExerciseVariant.gym_id == v.gym_id
                if v.gym_id is not None
                else ExerciseVariant.gym_id.is_(None),
                ExerciseVariant.equipment_type == v.equipment_type,
                ExerciseVariant.deleted_at.is_(None),
                ExerciseVariant.active.is_(True),
            )
        )
        if twin is not None:
            raise ExerciseError(
                409, f"{twin!r} ({gs._val(v.equipment_type)}) already exists: pick another name"
            )
    plan = gs.program_entries(db, settings).get(ex.slug)
    target = {  # variant id -> (new base, new full name)
        v.id: (
            variant_name(name, gs._val(v.equipment_type)),
            variant_name(name, gs._val(v.equipment_type)) + v.name[len(gs.base_name(v)) :],
        )
        for v in live
    }
    if len({(target[v.id][1], v.gym_id) for v in live}) < len(live):
        raise ExerciseError(409, "two variants would get the same name")
    changes: list[tuple[ExerciseVariant, str, str]] = []  # (variant, new base, new full name)
    for v in live:
        new_base, full = target[v.id]
        if full == v.name:
            continue
        clash = db.scalar(
            select(ExerciseVariant.id).where(
                ExerciseVariant.name == full,
                ExerciseVariant.gym_id == v.gym_id
                if v.gym_id is not None
                else ExerciseVariant.gym_id.is_(None),
                ExerciseVariant.id.not_in(list(target)),
            )
        )
        if clash is not None:
            raise ExerciseError(409, f"a variant named {full!r} already exists")
        changes.append((v, new_base, full))

    plan_changed = False
    if plan is not None:
        hit = next((new for v, new, _ in changes if gs.base_name(v) == plan.variant), None)
        if hit is not None:
            cs.ensure_seeded(db, settings.engine_config_path)
            cs.create_version(
                db, {f"exercise_overrides.{ex.slug}.variant": hit}, author="user",
                reason=f"{ex.name} renamed to {name}: the plan follows",
            )  # fmt: skip
            plan_changed = True
    for v, _, full in changes:
        v.name = full
    ex.name = name
    ex.edited_by_user = True
    db.flush()
    return [v for v, _, _ in changes], plan_changed


def check_increment(value: float | None) -> float | None:
    if value is None:
        return None
    if not 0 < value <= INCREMENT_MAX_KG:
        raise ExerciseError(422, f"increment_kg must be above 0 and at most {INCREMENT_MAX_KG:g}")
    return round(float(value) / 0.25) * 0.25 or 0.25


def active_variants(db: Session) -> list[ExerciseVariant]:
    """Every variant that is not tombstoned, by name (`GET /api/v1/variants`)."""
    return list(
        db.scalars(
            select(ExerciseVariant)
            .where(ExerciseVariant.deleted_at.is_(None))
            .order_by(ExerciseVariant.name)
        )
    )


# --------------------------------------------------------------------------- operations
# One committed transaction each (what an API request does), answering in the pull shape.
# `replan` runs after the commit when the plan may change.


def _exercise(db: Session, exercise_id: int) -> Exercise:
    ex = db.get(Exercise, exercise_id)
    if ex is None:
        raise ExerciseError(404, "exercise not found")
    return ex


def new_exercise(
    db: Session,
    *,
    name: str,
    muscle_group: str,
    equipment_type: str,
    timed: bool | None = None,
    progression: str | None = None,
    gym_id: int | None = None,
    increment_kg: float | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """`POST /exercises`: the exercise and its first variant."""
    s = settings or get_settings()
    mode = resolve_progression(timed, progression)
    ex, v = create_exercise(
        db, name=name, muscle_group=muscle_group, equipment_type=equipment_type, progression=mode,
        gym_id=gym_id, increment_kg=check_increment(increment_kg), settings=s,
    )  # fmt: skip
    db.commit()
    return {"exercise": exercise_out(ex), "variant": variant_out(v, program_rep_ranges(s, db))}


def new_variant(
    db: Session,
    exercise_id: int,
    *,
    equipment_type: str,
    gym_id: int | None = None,
    settings: Settings | None = None,
    replan: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """`POST /exercises/{id}/variants`; a machine may stop a "set up this machine" plan item."""
    s = settings or get_settings()
    ex = _exercise(db, exercise_id)
    v = add_variant(db, ex, equipment_type=equipment_type, gym_id=gym_id, settings=s)
    db.commit()
    if v.gym_id is not None and replan is not None:
        replan("machine set up")
    return variant_out(v, program_rep_ranges(s, db))


def edit_exercise(
    db: Session,
    exercise_id: int,
    fields: dict[str, Any],
    settings: Settings | None = None,
    *,
    replan: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """`PATCH /exercises/{id}`: name, muscle group and / or progression mode; a rename or a mode
    change re-plans, and a rename answers with the renamed variants too."""
    s = settings or get_settings()
    ex = _exercise(db, exercise_id)
    _, renamed, plan_changed = update_exercise(db, ex, fields, s)
    db.commit()
    if plan_changed and replan is not None:
        replan("exercise edited")  # a new variant name in the plan line, or a new progression mode
    out = exercise_out(ex)
    if renamed:  # additive: the variants that got the new name (pull shape)
        rep_ranges = program_rep_ranges(s, db)
        out["variants"] = [variant_out(v, rep_ranges) for v in renamed]
    return out
