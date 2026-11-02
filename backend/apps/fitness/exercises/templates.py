"""Exercise settings the user changes from the app that live in the engine config or the program
file (contract: docs/api/exercises.md):

* the rest timer of an exercise (`exercise_overrides.<slug>.rest_s`, a config version authored
  "user"; `None` resets it to `lifting.rest_default_s`);
* the lifting template of a day kind (`days.<kind>` of the live program file, backup first).

Each function is one committed transaction and answers in the API's shape.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from apps.fitness.db.models import Exercise, ExerciseVariant, PlannedSession
from apps.fitness.db.types import utcnow
from apps.fitness.engine import config_service as cs
from apps.fitness.engine import rest
from apps.fitness.engine import service as engine
from apps.fitness.engine.config import EngineConfig
from apps.fitness.engine.program import KINDS, apply_overrides, set_kind_slots
from apps.fitness.exercises.service import ExerciseError
from apps.fitness.settings import Settings, get_settings

CARRY = ("rep_range", "increment_kg", "note")  # template fields that survive a re-pick


def clock(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def _live_variant(db: Session, variant_id: int, message: str) -> tuple[ExerciseVariant, Exercise]:
    variant = db.get(ExerciseVariant, variant_id)
    if variant is None or variant.deleted_at is not None:
        raise ExerciseError(404, message)
    exercise = db.get(Exercise, variant.exercise_id)
    if exercise is None:  # pragma: no cover - FK guarantees it
        raise ExerciseError(404, "exercise not found")
    return variant, exercise


def set_rest(
    db: Session, variant_id: int, rest_s: int | None, settings: Settings | None = None
) -> dict[str, Any]:
    """Set (or with `None` reset) the rest time of the variant's exercise. Idempotent: the same
    value again creates no new config version. Open lifting plans are re-sent on the next pull."""
    s = settings or get_settings()
    variant, exercise = _live_variant(db, variant_id, "variant not found")
    cs.ensure_seeded(db, s.engine_config_path)
    cfg, active = cs.load_active(db)
    path = f"exercise_overrides.{exercise.slug}.rest_s"
    name = variant.name.split(" @ ")[0]
    default = cfg.lifting_cfg.rest_default_s
    if rest_s is None:
        row = cs.create_version(
            db, {}, unset=(path,), author="user",
            reason=f"Rest time for {name} reset to the default ({clock(default)})",
        )  # fmt: skip
    else:
        row = cs.create_version(
            db, {path: rest_s}, author="user",
            reason=f"Rest time for {name} set to {clock(rest_s)}",
        )  # fmt: skip
    if row.id != active.id:
        # open lifting plans are re-sent with the new rest_s on the next pull
        db.execute(
            update(PlannedSession)
            .where(
                PlannedSession.kind.in_(("upper", "lower")),
                PlannedSession.deleted_at.is_(None),
                PlannedSession.day >= engine.user_today(s),
            )
            .values(updated_at=utcnow())
        )
    db.commit()
    new_cfg = EngineConfig.model_validate(row.content)
    return {
        "variant_id": variant.id,
        "rest_s": rest.resolve_rest_s(new_cfg, exercise.slug),
        "is_default": rest.override_for(new_cfg, exercise.slug) is None,
        "config_version": row.version,
    }


def _slot_out(pe: Any) -> dict[str, Any]:
    return {
        "exercise": pe.exercise,
        "variant": pe.variant,
        "sets": pe.sets,
        "rep_range": list(pe.rep_range) if pe.rep_range else None,
    }


def _slot_dict(pe: Any) -> dict[str, Any]:
    slot: dict[str, Any] = {"exercise": pe.exercise, "variant": pe.variant, "sets": pe.sets}
    if pe.rep_range:
        slot["rep_range"] = list(pe.rep_range)
    if pe.increment_kg:
        slot["increment_kg"] = pe.increment_kg
    if not pe.active:
        slot["active"] = False
    if pe.note:
        slot["note"] = pe.note
    return slot


def set_day_template(
    db: Session,
    kind: str,
    picks: list[tuple[int, int | None]],
    settings: Settings | None = None,
    *,
    replan_now: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """The exercise list the user actually did, as (variant id, sets or None), becomes the
    template of that day kind: only `days.<kind>` of the live program file is rewritten (backup
    first), then `replan_now(reason)` re-plans synchronously. Idempotent: an identical list
    writes nothing."""
    if kind not in KINDS:
        raise ExerciseError(422, f"kind must be one of {list(KINDS)}")
    s = settings or get_settings()
    try:
        program = engine.get_program(s)
    except (OSError, ValueError) as exc:
        raise ExerciseError(409, f"no program: {exc}") from None
    current = program.days.get(kind, [])
    old = {pe.exercise: pe for pe in current}
    slots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for variant_id, sets in picks:
        variant, exercise = _live_variant(db, variant_id, f"variant {variant_id} not found")
        if exercise.slug in seen:
            raise ExerciseError(422, f"exercise {exercise.slug!r} appears twice")
        seen.add(exercise.slug)
        prev = old.get(exercise.slug)
        slot: dict[str, Any] = {
            "exercise": exercise.slug,
            "variant": variant.name.split(" @ ")[0],
            "sets": sets or (prev.sets if prev else 3),
        }
        if prev is not None:  # rep_range / increment_kg / note carry over
            slot.update({k: v for k, v in _slot_dict(prev).items() if k in CARRY})
        slots.append(slot)
    cs.ensure_seeded(db, s.engine_config_path)
    cfg = cs.load_active(db)[0]
    # A per-exercise `variant` override (a rename writes one) would win over the picked variant:
    # point it at the picked name (not unset: the exercise's other day kind may rely on it).
    stale = {
        sl["exercise"]: sl["variant"]
        for sl in slots
        if (cfg.exercise_overrides.get(sl["exercise"]) or {}).get("variant")
        not in (None, sl["variant"])
    }
    backup = None
    file_changed = slots != [_slot_dict(pe) for pe in current]
    if file_changed:
        backup = set_kind_slots(s.live_program_path, kind, slots)
    if stale:
        cs.create_version(
            db, {f"exercise_overrides.{slug}.variant": name for slug, name in stale.items()},
            author="user",
            reason=f"Template update ({kind}): variant override follows the picked variant",
        )  # fmt: skip
        db.commit()
    if (file_changed or stale) and replan_now is not None:
        replan_now("template update")
    cfg = cs.load_active(db)[0]
    effective = apply_overrides(engine.get_program(s), cfg.exercise_overrides)
    return {
        "kind": kind,
        "exercises": [_slot_out(pe) for pe in effective.days.get(kind, []) if pe.active],
        "backup": backup.name if backup else None,
    }
