"""Per-exercise settings for the iOS app (bearer token like the other write APIs).

PUT   /api/v1/exercises/{variant_id}/rest      body {"rest_s": 30..600 | null}
POST  /api/v1/exercises                        a new exercise + its first variant
POST  /api/v1/exercises/{exercise_id}/variants a variant by equipment (at a gym for machines)
PATCH /api/v1/exercises/{exercise_id}          the name, muscle group and / or progression mode
PUT   /api/v1/program/{kind}                   the lifting template of a day kind


Sets the rest timer of the variant's exercise (all variants of one exercise share it; the setting
is `exercise_overrides.<slug>.rest_s` in the engine config). `null` resets it to the default
(`lifting.rest_default_s`, 120). It creates a config version authored by "user" and is idempotent:
the same value again creates no new version. Contract: docs/api/exercises.md.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from apps.fitness.api.deps import get_db, get_settings_dep, service_errors
from apps.fitness.engine.core.config import REST_MAX_S, REST_MIN_S
from apps.fitness.exercises import service as xs
from apps.fitness.exercises import templates
from apps.fitness.jobs.engine_jobs import rerun_engine, run_engine_now
from apps.fitness.settings import Settings
from core.auth import require_token

router = APIRouter(dependencies=[Depends(require_token)])

Db = Annotated[Session, Depends(get_db)]
Cfg = Annotated[Settings, Depends(get_settings_dep)]


class RestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # null = back to the default; a missing key is an error (422), not a reset
    rest_s: Annotated[int, Field(strict=True, ge=REST_MIN_S, le=REST_MAX_S)] | None


@router.put("/exercises/{variant_id}/rest")
def put_rest(variant_id: int, body: RestBody, db: Db, settings: Cfg) -> dict[str, Any]:
    with service_errors():
        return templates.set_rest(db, variant_id, body.rest_s, settings)


# --------------------------------------------------------------------------- program template


class TemplateSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variant_id: int
    sets: Annotated[int, Field(strict=True, ge=1, le=10)] | None = None


class TemplateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exercises: Annotated[list[TemplateSlot], Field(min_length=1, max_length=30)]


@router.put("/program/{kind}")
def put_program(kind: str, body: TemplateBody, db: Db, settings: Cfg) -> dict[str, Any]:
    """The exercise list the user actually did becomes the template of that day kind: only
    `days.<kind>` of the live program file is rewritten (backup first), then the engine re-plans.
    Idempotent: an identical list writes nothing."""
    with service_errors():
        return templates.set_day_template(
            db, kind, [(s.variant_id, s.sets) for s in body.exercises], settings,
            replan_now=lambda reason: run_engine_now(db, reason),
        )  # fmt: skip


# --------------------------------------------------------------------------- user exercises

Equipment = Literal[
    "barbell", "dumbbell", "machine_stack", "plate_loaded", "cable", "bodyweight", "smith", "other"
]
ProgressionMode = Literal["load", "reps", "time"]
Increment = Annotated[float, Field(gt=0, le=xs.INCREMENT_MAX_KG)]


class ExerciseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=100)]
    muscle_group: str
    equipment_type: Equipment
    timed: bool | None = None  # true alone = progression "time"; must not contradict progression
    progression: ProgressionMode | None = None  # default load
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
    # load | reps; "time" is accepted by the schema but rejected (422) by the service
    progression: ProgressionMode | None = None


@router.post("/exercises", status_code=201)
def create_exercise(body: ExerciseBody, db: Db, settings: Cfg) -> dict[str, Any]:
    with service_errors():
        return xs.new_exercise(db, **body.model_dump(), settings=settings)


@router.post("/exercises/{exercise_id}/variants")
def add_variant(exercise_id: int, body: VariantBody, db: Db, settings: Cfg) -> dict[str, Any]:
    with service_errors():
        return xs.new_variant(
            db, exercise_id, equipment_type=body.equipment_type, gym_id=body.gym_id,
            settings=settings, replan=rerun_engine,
        )  # fmt: skip


@router.patch("/exercises/{exercise_id}")
def patch_exercise(exercise_id: int, body: ExercisePatch, db: Db, settings: Cfg) -> dict[str, Any]:
    with service_errors():
        return xs.edit_exercise(
            db, exercise_id, body.model_dump(exclude_unset=True), settings, replan=rerun_engine
        )
