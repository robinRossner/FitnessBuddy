"""Gyms and machines as a user-managed thing (contract: docs/api/gyms.md).

* The current gym is stored through the versioned engine config as `gym.current_id` (author
  "user"), so every plan can say which gym it was made for. Unset = the old behaviour (latest entry
  of gyms.yaml, else the gym of the newest session).
* A machine is a gym-bound `ExerciseVariant` ("<program variant name> @ <gym name>"). The engine
  resolves program exercises to variants by that name, so a variant made here uses the program's
  name for its exercise.
* Identity: `ExerciseVariant.key`. Variants made here get a deterministic uuid5 over
  (gym id, exercise id) in their own namespace, so creating the same machine again never
  duplicates it; the Strong importer adopts such a variant by (name, gym) when the same machine
  shows up in an import, and never tombstones one (`is_user_variant`).
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.fitness.db.models import (
    Exercise,
    ExerciseVariant,
    Gym,
    TrainingSession,
    WorkoutSet,
)
from apps.fitness.db.types import EquipmentType, LoadConvention, iso_z, utcnow
from apps.fitness.engine import config_service as cs
from apps.fitness.gyms import GYM_BOUND_EQUIPMENT
from apps.fitness.settings import Settings

CONFIG_PATH = "gym.current_id"
MACHINE_EQUIPMENT = ("machine_stack", "cable", "smith", "plate_loaded", "other")
LADDER_MAX_LEN = 60
LADDER_MAX_KG = 500.0
LADDER_STEP = 0.25
_NS = uuid.UUID("5d0b6d52-7f0e-4b7e-9d0f-6f1d1a0e2c11")  # user-made variants


class GymError(Exception):
    """An error with an HTTP status (the API maps it to HTTPException)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------- current gym


def configured_gym_id(db: Session) -> int | None:
    """`gym.current_id` of the active engine config, None if unset."""
    try:
        content = cs.get_active(db).content
    except cs.ConfigError:
        return None
    node = content.get("gym")
    value = node.get("current_id") if isinstance(node, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def configured_current_gym(db: Session) -> Gym | None:
    """The configured current gym if it still exists (the engine's first choice)."""
    gid = configured_gym_id(db)
    gym = db.get(Gym, gid) if gid is not None else None
    return gym if gym is not None and gym.deleted_at is None else None


def effective_current_gym(db: Session, settings: Settings) -> Gym | None:
    """The gym the engine plans for: the configured one, else the old default."""
    gym = configured_current_gym(db)
    if gym is not None:
        return gym
    from apps.fitness.engine import data

    return data.current_gym(db, settings.gyms_path)


def set_current_gym(db: Session, gym: Gym, settings: Settings) -> bool:
    """Store the current gym as a config version. Returns False if it already was current."""
    cs.ensure_seeded(db, settings.engine_config_path)
    if configured_gym_id(db) == gym.id:
        return False
    cs.create_version(
        db,
        {CONFIG_PATH: gym.id},
        reason=f"Current gym set to {gym.name}",
        author="user",
    )
    return True


# --------------------------------------------------------------------------- ladders


def normalize_ladder(values: list[float]) -> list[float]:
    """Validate a weight ladder: round to 0.25 kg, 0 < w <= 500, strictly ascending, 1..60."""
    if not values:
        raise ValueError("weight_ladder must have at least one entry")
    if len(values) > LADDER_MAX_LEN:
        raise ValueError(f"weight_ladder has more than {LADDER_MAX_LEN} entries")
    out: list[float] = []
    for raw in values:
        w = round(float(raw) / LADDER_STEP) * LADDER_STEP
        if not 0 < w <= LADDER_MAX_KG:
            raise ValueError(f"weight {raw:g} kg must be above 0 and at most {LADDER_MAX_KG:g}")
        out.append(float(w))
    for a, b in itertools.pairwise(out):
        if b <= a:
            raise ValueError("weight_ladder must be sorted ascending without duplicates")
    return out


def preview_ladder(start: float, step: float, count: int, extra: list[float] | None) -> list[float]:
    """start, start+step, ... (`count` weights) plus the `extra` weights, sorted and unique."""
    weights = {round((start + i * step) / LADDER_STEP) * LADDER_STEP for i in range(count)}
    weights |= {round(float(x) / LADDER_STEP) * LADDER_STEP for x in extra or []}
    return normalize_ladder(sorted(float(w) for w in weights))


# --------------------------------------------------------------------------- variants


def base_name(variant: ExerciseVariant) -> str:
    return variant.name.split(" @ ")[0]


def user_variant_key(gym_id: int, exercise_id: int) -> str:
    return str(uuid.uuid5(_NS, f"user:variant:{gym_id}|{exercise_id}"))


def is_user_variant(v: ExerciseVariant) -> bool:
    """Made through the gyms API (not by the importer): the importer must not tombstone it."""
    return v.gym_id is not None and v.key == user_variant_key(v.gym_id, v.exercise_id)


def is_bound(v: ExerciseVariant) -> bool:
    return v.gym_id is not None or _val(v.equipment_type) in GYM_BOUND_EQUIPMENT


def _val(x: Any) -> str:
    return x.value if hasattr(x, "value") else str(x)


@dataclass
class Usage:
    sessions: int = 0
    last_used: Any = None  # date | None


def usage_by_variant(db: Session) -> dict[int, Usage]:
    q = (
        select(
            WorkoutSet.variant_id,
            func.count(func.distinct(WorkoutSet.session_id)),
            func.max(TrainingSession.started_at_local),
        )
        .join(TrainingSession, TrainingSession.id == WorkoutSet.session_id)
        .where(WorkoutSet.deleted_at.is_(None), TrainingSession.deleted_at.is_(None))
        .group_by(WorkoutSet.variant_id)
    )
    return {
        vid: Usage(int(n), last.date() if last else None) for vid, n, last in db.execute(q).all()
    }


def last_top_set(db: Session, variant_id: int) -> dict[str, Any] | None:
    """Heaviest working set of the variant's latest session."""
    row = db.execute(
        select(WorkoutSet.weight_kg, WorkoutSet.reps)
        .join(TrainingSession, TrainingSession.id == WorkoutSet.session_id)
        .where(
            WorkoutSet.variant_id == variant_id,
            WorkoutSet.deleted_at.is_(None),
            TrainingSession.deleted_at.is_(None),
            WorkoutSet.set_type.in_(("working", "drop", "failure")),
            WorkoutSet.weight_kg.is_not(None),
            WorkoutSet.weight_kg > 0,
            WorkoutSet.reps.is_not(None),
        )
        .order_by(
            TrainingSession.started_at_local.desc(),
            WorkoutSet.weight_kg.desc(),
            WorkoutSet.reps.desc(),
        )
        .limit(1)
    ).first()
    return {"weight_kg": float(row[0]), "reps": int(row[1])} if row else None


def previous_variant(
    variants: list[ExerciseVariant], gym_id: int, usage: dict[int, Usage]
) -> ExerciseVariant | None:
    """The most recently used variant of the exercise at another gym (a hint when setting up)."""
    cands = [
        v
        for v in variants
        if v.gym_id is not None
        and v.gym_id != gym_id
        and v.deleted_at is None
        and usage.get(v.id, Usage()).sessions > 0
    ]
    return max(cands, key=lambda v: (usage[v.id].last_used, v.id), default=None)


def previous_hint(db: Session, variant: ExerciseVariant) -> dict[str, Any] | None:
    """For a gym-bound variant without history: the last top set of the same exercise at another
    gym, `{gym_name, weight_kg, reps}` (the planner shows it as a hint, never as a target)."""
    if variant.gym_id is None:
        return None
    siblings = list(
        db.scalars(
            select(ExerciseVariant).where(
                ExerciseVariant.exercise_id == variant.exercise_id,
                ExerciseVariant.deleted_at.is_(None),
            )
        )
    )
    prev = previous_variant(siblings, variant.gym_id, usage_by_variant(db))
    top = last_top_set(db, prev.id) if prev is not None else None
    gym = db.get(Gym, prev.gym_id) if prev is not None else None
    if top is None or gym is None:
        return None
    return {"gym_name": gym.name, "weight_kg": top["weight_kg"], "reps": top["reps"]}


def machine_count(db: Session, gym_id: int) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(ExerciseVariant)
            .where(
                ExerciseVariant.gym_id == gym_id,
                ExerciseVariant.deleted_at.is_(None),
                ExerciseVariant.active.is_(True),
            )
        )
        or 0
    )


def gym_dict(db: Session, gym: Gym, current_id: int | None) -> dict[str, Any]:
    return {
        "id": gym.id,
        "name": gym.name,
        "created_at": iso_z(gym.created_at),
        "machine_count": machine_count(db, gym.id),
        "is_current": gym.id == current_id,
        "source": "import" if gym.key else "user",
    }


def list_gyms(db: Session, settings: Settings) -> dict[str, Any]:
    current = effective_current_gym(db, settings)
    cid = current.id if current else None
    gyms = db.scalars(select(Gym).where(Gym.deleted_at.is_(None)).order_by(Gym.id)).all()
    return {"current_gym_id": cid, "gyms": [gym_dict(db, g, cid) for g in gyms]}


def find_gym(db: Session, gym_id: int) -> Gym:
    gym = db.get(Gym, gym_id)
    if gym is None or gym.deleted_at is not None:
        raise GymError(404, "gym not found")
    return gym


def name_taken(db: Session, name: str, *, except_id: int | None = None) -> Gym | None:
    q = select(Gym).where(func.lower(Gym.name) == name.lower())
    return next((g for g in db.scalars(q) if g.id != except_id and g.deleted_at is None), None)


def create_gym(db: Session, name: str) -> Gym:
    name = name.strip()
    if not name:
        raise GymError(422, "name must not be empty")
    if name_taken(db, name):
        raise GymError(409, f"a gym named {name!r} already exists")
    # a deleted gym with this name comes back (the name is unique in the table)
    old = db.scalar(select(Gym).where(func.lower(Gym.name) == name.lower()))
    if old is not None:
        old.deleted_at = None
        old.name = name
        db.flush()
        return old
    gym = Gym(name=name)
    db.add(gym)
    db.flush()
    return gym


def rename_gym(db: Session, gym: Gym, name: str) -> Gym:
    name = name.strip()
    if not name:
        raise GymError(422, "name must not be empty")
    if name_taken(db, name, except_id=gym.id):
        raise GymError(409, f"a gym named {name!r} already exists")
    if db.scalar(
        select(Gym.id).where(func.lower(Gym.name) == name.lower(), Gym.id != gym.id).limit(1)
    ):
        raise GymError(409, f"a gym named {name!r} already exists (deleted); pick another name")
    old_suffix, new_suffix = f" @ {gym.name}", f" @ {name}"
    gym.name = name
    # the engine finds a machine by "<program variant> @ <gym name>": names follow the gym
    for v in db.scalars(select(ExerciseVariant).where(ExerciseVariant.gym_id == gym.id)):
        if v.name.endswith(old_suffix):
            v.name = v.name[: -len(old_suffix)] + new_suffix
    db.flush()
    return gym


def delete_gym(db: Session, gym: Gym, settings: Settings) -> None:
    current = effective_current_gym(db, settings)
    if current is not None and current.id == gym.id:
        raise GymError(409, "this is the current gym: switch to another gym first")
    used = db.scalar(select(TrainingSession.id).where(TrainingSession.gym_id == gym.id).limit(1))
    if used is not None:
        raise GymError(409, "this gym has sessions and cannot be deleted")
    gym.deleted_at = utcnow()
    db.flush()


# --------------------------------------------------------------------------- machines


def program_entries(db: Session, settings: Settings) -> dict[str, Any]:
    """Active program entries by exercise slug (config overrides applied); {} without a program."""
    from apps.fitness.engine import service
    from apps.fitness.engine.program import apply_overrides

    try:
        program = service.get_program(settings)
    except (OSError, ValueError):
        return {}
    cfg = service.active_config(db, settings)
    if cfg is not None:
        program = apply_overrides(program, cfg.exercise_overrides)
    return program.by_exercise()


def variants_by_exercise(db: Session) -> dict[int, list[ExerciseVariant]]:
    out: dict[int, list[ExerciseVariant]] = {}
    for v in db.scalars(select(ExerciseVariant).where(ExerciseVariant.deleted_at.is_(None))):
        out.setdefault(v.exercise_id, []).append(v)
    return out


def entry_is_bound(entry: Any, variants: list[ExerciseVariant]) -> bool:
    """Does the program line need a machine of its own at each gym?"""
    named = [v for v in variants if base_name(v) == entry.variant]
    if named:
        return any(is_bound(v) for v in named)
    return any(is_bound(v) for v in variants)


def variant_at_gym(
    variants: list[ExerciseVariant], gym_id: int, entry: Any | None, usage: dict[int, Usage]
) -> ExerciseVariant | None:
    """The variant of an exercise at a gym (not tombstoned): the program's one, active first."""
    here = [v for v in variants if v.gym_id == gym_id and v.deleted_at is None]
    return max(
        here,
        key=lambda v: (
            v.active,
            entry is not None and base_name(v) == entry.variant,
            usage.get(v.id, Usage()).sessions,
            -v.id,
        ),
        default=None,
    )


def unmapped_count(db: Session, gym: Gym, settings: Settings) -> int:
    """Program exercises that need a machine at `gym` and have none (yet)."""
    entries = program_entries(db, settings)
    by_ex = variants_by_exercise(db)
    usage: dict[int, Usage] = {}
    n = 0
    for slug, entry in entries.items():
        ex_id = db.scalar(select(Exercise.id).where(Exercise.slug == slug))
        variants = by_ex.get(ex_id, [])
        if not entry_is_bound(entry, variants):
            continue
        v = variant_at_gym(variants, gym.id, entry, usage)
        if v is None or not v.active:
            n += 1
    return n


def _equipment_hint(entry: Any | None, variants: list[ExerciseVariant], ex: Exercise) -> str:
    named = [v for v in variants if entry is not None and base_name(v) == entry.variant]
    for v in named + [x for x in variants if x not in named]:
        if _val(v.equipment_type) in MACHINE_EQUIPMENT:
            return _val(v.equipment_type)
    default = _val(ex.default_equipment_type)
    return default if default in MACHINE_EQUIPMENT else "other"


def machine_items(db: Session, gym: Gym, settings: Settings) -> list[dict[str, Any]]:
    entries = program_entries(db, settings)
    by_ex = variants_by_exercise(db)
    usage = usage_by_variant(db)
    exercises = {e.id: e for e in db.scalars(select(Exercise))}
    slug_to_id = {e.slug: e.id for e in exercises.values()}

    ordered: list[tuple[Exercise, Any | None]] = []
    seen: set[int] = set()
    for slug, entry in entries.items():
        ex_id = slug_to_id.get(slug)
        if ex_id is None or not entry_is_bound(entry, by_ex.get(ex_id, [])):
            continue
        ordered.append((exercises[ex_id], entry))
        seen.add(ex_id)
    extra = []
    for ex_id, variants in by_ex.items():
        if ex_id in seen or ex_id not in exercises:
            continue
        has_history = any(
            v.gym_id is not None and usage.get(v.id, Usage()).sessions > 0 for v in variants
        )
        here = any(v.gym_id == gym.id for v in variants)
        if has_history or here:
            extra.append(exercises[ex_id])
    extra.sort(key=lambda e: e.name.lower())
    ordered += [(e, None) for e in extra]
    return [machine_item(db, gym, ex, entry, by_ex.get(ex.id, []), usage) for ex, entry in ordered]


def machine_item(
    db: Session,
    gym: Gym,
    ex: Exercise,
    entry: Any | None,
    variants: list[ExerciseVariant],
    usage: dict[int, Usage],
) -> dict[str, Any]:
    v = variant_at_gym(variants, gym.id, entry, usage)
    prev = previous_variant(variants, gym.id, usage)
    previous = None
    if prev is not None:
        prev_gym = db.get(Gym, prev.gym_id)
        previous = {
            "gym_name": prev_gym.name if prev_gym else None,
            "variant_id": prev.id,
            "last_top_set": last_top_set(db, prev.id),
            "weight_ladder": prev.weight_ladder,
        }
    variant = None
    if v is not None:
        u = usage.get(v.id, Usage())
        variant = {
            "id": v.id,
            "name": v.name,
            "equipment_type": _val(v.equipment_type),
            "weight_ladder": v.weight_ladder,
            "notes": v.notes,
            "active": bool(v.active),
            "sessions": u.sessions,
            "last_used": u.last_used.isoformat() if u.last_used else None,
        }
    equipment = (
        _val(v.equipment_type)
        if v is not None
        else _val(prev.equipment_type)
        if prev is not None and _val(prev.equipment_type) in MACHINE_EQUIPMENT
        else _equipment_hint(entry, variants, ex)
    )
    return {
        "exercise_id": ex.id,
        "exercise_name": ex.name,
        "body_region": ex.primary_muscle,
        "in_program": entry is not None,
        "equipment_type": equipment,
        "variant": variant,
        "previous": previous,
    }


def machine_item_for(db: Session, gym: Gym, ex: Exercise, settings: Settings) -> dict[str, Any]:
    entries = program_entries(db, settings)
    by_ex = variants_by_exercise(db)
    return machine_item(
        db, gym, ex, entries.get(ex.slug), by_ex.get(ex.id, []), usage_by_variant(db)
    )


def create_machine(
    db: Session,
    gym: Gym,
    ex: Exercise,
    *,
    name: str | None,
    equipment_type: str,
    weight_ladder: list[float] | None,
    notes: str | None,
    settings: Settings,
) -> ExerciseVariant:
    """Set up `ex` at `gym`: a new variant, or the revived inactive / tombstoned one."""
    if equipment_type not in MACHINE_EQUIPMENT:
        raise GymError(422, f"equipment_type must be one of {', '.join(MACHINE_EQUIPMENT)}")
    ladder = normalize_ladder(weight_ladder) if weight_ladder is not None else None
    entries = program_entries(db, settings)
    entry = entries.get(ex.slug)
    all_rows = list(db.scalars(select(ExerciseVariant).where(ExerciseVariant.exercise_id == ex.id)))
    here = [v for v in all_rows if v.gym_id == gym.id]
    if any(v.deleted_at is None and v.active for v in here):
        raise GymError(409, "this exercise is already set up at this gym: update the variant")
    live = [v for v in all_rows if v.deleted_at is None]
    usage = usage_by_variant(db)
    prev = previous_variant(live, gym.id, usage)
    if name is not None and name.strip():
        full = name.strip()
    else:
        base = entry.variant if entry is not None else (base_name(prev) if prev else ex.name)
        full = f"{base} @ {gym.name}"
    clash = db.scalar(
        select(ExerciseVariant).where(
            ExerciseVariant.name == full, ExerciseVariant.gym_id == gym.id
        )
    )
    reviving = max(here, key=lambda v: (usage.get(v.id, Usage()).sessions, v.id), default=None)
    if clash is not None and clash is not reviving:
        raise GymError(409, f"a variant named {full!r} already exists at this gym")
    if reviving is not None:
        v = reviving
        v.deleted_at = None
        v.active = True
        if name is not None and name.strip():
            v.name = full
        v.equipment_type = EquipmentType(equipment_type)
        if ladder is not None:
            v.weight_ladder, v.ladder_inferred = ladder, False
        if notes is not None:
            v.notes = notes
    else:
        v = ExerciseVariant(
            exercise_id=ex.id,
            gym_id=gym.id,
            key=user_variant_key(gym.id, ex.id),
            name=full,
            equipment_type=EquipmentType(equipment_type),
            load_convention=prev.load_convention if prev is not None else LoadConvention.TOTAL,
            weight_ladder=ladder,
            ladder_inferred=False,
            active=True,
            notes=notes,
        )
        db.add(v)
    db.flush()
    return v


def update_variant(
    db: Session, v: ExerciseVariant, fields: dict[str, Any]
) -> tuple[ExerciseVariant, bool]:
    """Apply PATCH fields; returns (variant, engine_inputs_changed)."""
    changed = False
    if "name" in fields:
        name = (fields["name"] or "").strip()
        if not name:
            raise GymError(422, "name must not be empty")
        if name != v.name:
            clash = db.scalar(
                select(ExerciseVariant.id).where(
                    ExerciseVariant.name == name,
                    ExerciseVariant.gym_id == v.gym_id
                    if v.gym_id is not None
                    else ExerciseVariant.gym_id.is_(None),
                    ExerciseVariant.id != v.id,
                )
            )
            if clash is not None:
                raise GymError(409, f"a variant named {name!r} already exists at this gym")
            v.name = name
    if "weight_ladder" in fields:
        ladder = fields["weight_ladder"]
        if ladder is None:
            if v.weight_ladder is not None:
                changed = True
            v.weight_ladder, v.ladder_inferred = None, True
        else:
            new = normalize_ladder(ladder)
            if new != v.weight_ladder:
                changed = True
            v.weight_ladder, v.ladder_inferred = new, False
    if "notes" in fields:
        v.notes = fields["notes"]
    if "active" in fields and fields["active"] is not None and bool(fields["active"]) != v.active:
        v.active = bool(fields["active"])
        changed = True
    if fields.get("equipment_type") is not None and fields["equipment_type"] != _val(
        v.equipment_type
    ):
        v.equipment_type = EquipmentType(fields["equipment_type"])
        changed = True
    if "increment_kg" in fields:
        inc = fields["increment_kg"]
        inc = round(float(inc) / LADDER_STEP) * LADDER_STEP or LADDER_STEP if inc else None
        if inc != v.increment_kg:
            v.increment_kg = inc
            changed = True
    db.flush()
    return v, changed
