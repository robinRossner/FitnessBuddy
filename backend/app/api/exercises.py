"""Per-exercise settings for the iOS app (bearer token like the other write APIs).

PUT   /api/v1/exercises/{variant_id}/rest      body {"rest_s": 30..600 | null}
POST  /api/v1/exercises                        a new exercise + its first variant
POST  /api/v1/exercises/{exercise_id}/variants a variant by equipment (at a gym for machines)
PATCH /api/v1/exercises/{exercise_id}          the name and / or muscle group


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

from app import exercise_service as xs
from app.api.auth import require_token
from app.api.routes import get_db
from app.db.models import Exercise, ExerciseVariant, PlannedSession
from app.db.types import utcnow
from app.engine import config_service as cs
from app.engine import rest
from app.engine import service as engine
from app.engine.config import REST_MAX_S, REST_MIN_S, EngineConfig
from app.jobs.engine_jobs import rerun_engine
from app.settings import get_settings
from app.sync.pull import exercise_out, program_rep_ranges, variant_out

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])


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
