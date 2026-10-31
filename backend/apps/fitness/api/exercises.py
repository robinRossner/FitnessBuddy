"""Per-exercise settings for the iOS app (bearer token like the other write APIs).

PUT   /api/v1/exercises/{variant_id}/rest      body {"rest_s": 30..600 | null}
POST  /api/v1/exercises                        a new exercise + its first variant
POST  /api/v1/exercises/{exercise_id}/variants a variant by equipment (at a gym for machines)
PATCH /api/v1/exercises/{exercise_id}          the name and / or muscle group
PUT   /api/v1/program/{kind}                   the lifting template of a day kind


Sets the rest timer of the variant's exercise (all variants of one exercise share it; the setting
is `exercise_overrides.<slug>.rest_s` in the engine config). `null` resets it to the default
(`lifting.rest_default_s`, 120). It creates a config version authored by "user" and is idempotent:
the same value again creates no new version. Contract: docs/api/exercises.md.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from apps.fitness import exercise_service as xs
from apps.fitness.api.routes import get_db
from apps.fitness.db.models import Exercise, ExerciseVariant, PlannedSession
from apps.fitness.db.types import utcnow
from apps.fitness.engine import config_service as cs
from apps.fitness.engine import rest
from apps.fitness.engine import service as engine
from apps.fitness.engine.config import REST_MAX_S, REST_MIN_S, EngineConfig
from apps.fitness.engine.program import KINDS, apply_overrides, set_kind_slots
from apps.fitness.jobs.engine_jobs import rerun_engine, run_engine_now
from apps.fitness.settings import get_settings
from apps.fitness.sync.pull import exercise_out, program_rep_ranges, variant_out
from core.auth import require_token

router = APIRouter(dependencies=[Depends(require_token)])


class RestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # null = back to the default; a missing key is an error (422), not a reset
    rest_s: Annotated[int, Field(strict=True, ge=REST_MIN_S, le=REST_MAX_S)] | None


def clock(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


@router.put("/exercises/{variant_id}/rest")
def put_rest(variant_id: int, body: RestBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    variant = db.get(ExerciseVariant, variant_id)
    if variant is None or variant.deleted_at is not None:
        raise HTTPException(404, "variant not found")
    exercise = db.get(Exercise, variant.exercise_id)
    if exercise is None:  # pragma: no cover - FK guarantees it
        raise HTTPException(404, "exercise not found")
    settings = get_settings()
    cs.ensure_seeded(db, settings.engine_config_path)
    cfg, active = cs.load_active(db)
    path = f"exercise_overrides.{exercise.slug}.rest_s"
    name = variant.name.split(" @ ")[0]
    default = cfg.lifting_cfg.rest_default_s
    if body.rest_s is None:
        row = cs.create_version(
            db, {}, unset=(path,), author="user",
            reason=f"Rest time for {name} reset to the default ({clock(default)})",
        )  # fmt: skip
    else:
        row = cs.create_version(
            db, {path: body.rest_s}, author="user",
            reason=f"Rest time for {name} set to {clock(body.rest_s)}",
        )  # fmt: skip
    if row.id != active.id:
        # open lifting plans are re-sent with the new rest_s on the next pull
        db.execute(
            update(PlannedSession)
            .where(
                PlannedSession.kind.in_(("upper", "lower")),
                PlannedSession.deleted_at.is_(None),
                PlannedSession.day >= engine.user_today(settings),
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


# --------------------------------------------------------------------------- program template


CARRY = ("rep_range", "increment_kg", "note")


class TemplateSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variant_id: int
    sets: Annotated[int, Field(strict=True, ge=1, le=10)] | None = None


class TemplateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exercises: Annotated[list[TemplateSlot], Field(min_length=1, max_length=30)]


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


@router.put("/program/{kind}")
def put_program(kind: str, body: TemplateBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The exercise list the user actually did becomes the template of that day kind: only
    `days.<kind>` of the live program file is rewritten (backup first), then the engine re-plans.
    Idempotent: an identical list writes nothing."""
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {list(KINDS)}")
    settings = get_settings()
    try:
        program = engine.get_program(settings)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, f"no program: {exc}") from None
    current = program.days.get(kind, [])
    old = {pe.exercise: pe for pe in current}
    slots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in body.exercises:
        variant = db.get(ExerciseVariant, item.variant_id)
        if variant is None or variant.deleted_at is not None:
            raise HTTPException(404, f"variant {item.variant_id} not found")
        exercise = db.get(Exercise, variant.exercise_id)
        if exercise is None:  # pragma: no cover - FK guarantees it
            raise HTTPException(404, "exercise not found")
        if exercise.slug in seen:
            raise HTTPException(422, f"exercise {exercise.slug!r} appears twice")
        seen.add(exercise.slug)
        prev = old.get(exercise.slug)
        slot: dict[str, Any] = {
            "exercise": exercise.slug,
            "variant": variant.name.split(" @ ")[0],
            "sets": item.sets or (prev.sets if prev else 3),
        }
        if prev is not None:  # rep_range / increment_kg / note carry over
            slot.update({k: v for k, v in _slot_dict(prev).items() if k in CARRY})
        slots.append(slot)
    cs.ensure_seeded(db, settings.engine_config_path)
    cfg = cs.load_active(db)[0]
    # A per-exercise `variant` override (a rename writes one) would win over the picked variant:
    # point it at the picked name (not unset: the exercise's other day kind may rely on it).
    stale = {
        s["exercise"]: s["variant"]
        for s in slots
        if (cfg.exercise_overrides.get(s["exercise"]) or {}).get("variant")
        not in (None, s["variant"])
    }
    backup = None
    file_changed = slots != [_slot_dict(pe) for pe in current]
    if file_changed:
        backup = set_kind_slots(settings.live_program_path, kind, slots)
    if stale:
        cs.create_version(
            db, {f"exercise_overrides.{slug}.variant": name for slug, name in stale.items()},
            author="user",
            reason=f"Template update ({kind}): variant override follows the picked variant",
        )  # fmt: skip
        db.commit()
    if file_changed or stale:
        run_engine_now(db, "template update")
    cfg = cs.load_active(db)[0]
    effective = apply_overrides(engine.get_program(settings), cfg.exercise_overrides)
    return {
        "kind": kind,
        "exercises": [_slot_out(pe) for pe in effective.days.get(kind, []) if pe.active],
        "backup": backup.name if backup else None,
    }


# --------------------------------------------------------------------------- user exercises

Equipment = Literal[
    "barbell", "dumbbell", "machine_stack", "plate_loaded", "cable", "bodyweight", "smith", "other"
]
Increment = Annotated[float, Field(gt=0, le=xs.INCREMENT_MAX_KG)]


class ExerciseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=100)]
    muscle_group: str
    equipment_type: Equipment
    timed: bool = False
    gym_id: int | None = None  # machines: default is the current gym
    increment_kg: Increment | None = None


class VariantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    equipment_type: Equipment
    gym_id: int | None = None


class ExercisePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    muscle_group: str | None = None


def _xcall(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except xs.ExerciseError as exc:
        raise HTTPException(exc.status, exc.message) from None
    except xs.gs.GymError as exc:
        raise HTTPException(exc.status, exc.message) from None


def _exercise(db: Session, exercise_id: int) -> Exercise:
    ex = db.get(Exercise, exercise_id)
    if ex is None:
        raise HTTPException(404, "exercise not found")
    return ex


@router.post("/exercises", status_code=201)
def create_exercise(body: ExerciseBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    ex, v = _xcall(
        xs.create_exercise,
        db,
        name=body.name,
        muscle_group=body.muscle_group,
        equipment_type=body.equipment_type,
        timed=body.timed,
        gym_id=body.gym_id,
        increment_kg=xs.check_increment(body.increment_kg),
        settings=settings,
    )
    db.commit()
    return {
        "exercise": exercise_out(ex),
        "variant": variant_out(v, program_rep_ranges(settings, db)),
    }


@router.post("/exercises/{exercise_id}/variants")
def add_variant(
    exercise_id: int, body: VariantBody, db: Session = Depends(get_db)
) -> dict[str, Any]:
    settings = get_settings()
    ex = _exercise(db, exercise_id)
    v = _xcall(
        xs.add_variant, db, ex, equipment_type=body.equipment_type, gym_id=body.gym_id,
        settings=settings,
    )  # fmt: skip
    db.commit()
    if v.gym_id is not None:
        rerun_engine("machine set up")  # a program exercise may stop being "set up this machine"
    return variant_out(v, program_rep_ranges(settings, db))


@router.patch("/exercises/{exercise_id}")
def patch_exercise(
    exercise_id: int, body: ExercisePatch, db: Session = Depends(get_db)
) -> dict[str, Any]:
    settings = get_settings()
    ex = _exercise(db, exercise_id)
    _, renamed, plan_changed = _xcall(
        xs.update_exercise, db, ex, body.model_dump(exclude_unset=True), settings
    )
    db.commit()
    if plan_changed:
        rerun_engine("exercise renamed")  # the plan line points at the new variant name
    out = exercise_out(ex)
    if renamed:  # additive: the variants that got the new name (pull shape)
        rep_ranges = program_rep_ranges(settings, db)
        out["variants"] = [variant_out(v, rep_ranges) for v in renamed]
    return out
