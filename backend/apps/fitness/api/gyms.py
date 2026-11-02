"""Gyms, machines and weight ladders for the iOS app (contract and examples: docs/api/gyms.md).

    GET    /api/v1/gyms                    GET   /api/v1/gyms/{id}/machines
    POST   /api/v1/gyms                    POST  /api/v1/gyms/{id}/machines
    PATCH  /api/v1/gyms/{id}               PATCH /api/v1/variants/{id}
    DELETE /api/v1/gyms/{id}               POST  /api/v1/weight-ladder/preview
    PUT    /api/v1/gyms/current

Bearer auth like the other write APIs. The current gym lives in the versioned engine config
(`gym.current_id`); changing it, or a machine's weight ladder, re-plans the upcoming sessions.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from apps.fitness.api.deps import get_db, get_settings_dep, service_errors
from apps.fitness.gyms import service as gs
from apps.fitness.jobs.engine_jobs import rerun_engine
from apps.fitness.settings import Settings
from core.auth import require_token

router = APIRouter(dependencies=[Depends(require_token)])

Name = Annotated[str, Field(min_length=1, max_length=120)]
Equipment = Literal["machine_stack", "cable", "smith", "plate_loaded", "other"]
AnyEquipment = Literal[
    "barbell", "dumbbell", "machine_stack", "plate_loaded", "cable", "bodyweight", "smith", "other"
]


def _errors():
    return service_errors(value_error_status=422)  # ValueError: weight-ladder validation


def _ladder(v: list[float] | None) -> list[float] | None:
    if v is None:
        return None
    try:
        return gs.normalize_ladder(v)
    except ValueError as exc:
        raise ValueError(str(exc)) from None


class NameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name


class CurrentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gym_id: int


class MachineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exercise_id: int
    name: Annotated[str, Field(min_length=1, max_length=150)] | None = None
    equipment_type: Equipment
    weight_ladder: list[float] | None = None
    notes: Annotated[str, Field(max_length=2000)] | None = None

    @field_validator("weight_ladder")
    @classmethod
    def _check(cls, v):
        return _ladder(v)


class VariantPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=150)] | None = None
    weight_ladder: list[float] | None = None
    notes: Annotated[str, Field(max_length=2000)] | None = None
    active: bool | None = None
    equipment_type: AnyEquipment | None = None
    # the load step of this variant; null = back to the ladder / equipment default
    increment_kg: Annotated[float, Field(gt=0, le=50)] | None = None

    @field_validator("weight_ladder")
    @classmethod
    def _check(cls, v):
        return _ladder(v)


class LadderPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: Annotated[float, Field(gt=0, le=gs.LADDER_MAX_KG)]
    step: Annotated[float, Field(gt=0, le=gs.LADDER_MAX_KG)]
    count: Annotated[int, Field(ge=1, le=gs.LADDER_MAX_LEN)]
    extra: list[float] | None = None


# --------------------------------------------------------------------------- gyms

Db = Annotated[Session, Depends(get_db)]
Cfg = Annotated[Settings, Depends(get_settings_dep)]


@router.get("/gyms")
def list_gyms(db: Db, settings: Cfg) -> dict[str, Any]:
    return gs.list_gyms(db, settings)


@router.post("/gyms", status_code=201)
def create_gym(body: NameBody, db: Db, settings: Cfg) -> dict[str, Any]:
    with _errors():
        return gs.add_gym(db, body.name, settings)


@router.put("/gyms/current")
def put_current_gym(body: CurrentBody, db: Db, settings: Cfg) -> dict[str, Any]:
    """Make a gym the current one and re-plan the upcoming sessions. Idempotent."""
    with _errors():
        return gs.make_current(db, body.gym_id, settings, replan=rerun_engine)


@router.patch("/gyms/{gym_id}")
def rename_gym(gym_id: int, body: NameBody, db: Db, settings: Cfg) -> dict[str, Any]:
    with _errors():
        return gs.rename(db, gym_id, body.name, settings)


@router.delete("/gyms/{gym_id}", status_code=204)
def delete_gym(gym_id: int, db: Db, settings: Cfg) -> Response:
    with _errors():
        gs.remove_gym(db, gym_id, settings)
    return Response(status_code=204)


# --------------------------------------------------------------------------- machines


@router.get("/gyms/{gym_id}/machines")
def gym_machines(gym_id: int, db: Db, settings: Cfg) -> dict[str, Any]:
    with _errors():
        return gs.gym_machines(db, gym_id, settings)


@router.post("/gyms/{gym_id}/machines", status_code=201)
def create_machine(gym_id: int, body: MachineBody, db: Db, settings: Cfg) -> dict[str, Any]:
    with _errors():
        return gs.add_machine(
            db, gym_id, body.exercise_id, name=body.name, equipment_type=body.equipment_type,
            weight_ladder=body.weight_ladder, notes=body.notes, settings=settings,
            replan=rerun_engine,
        )  # fmt: skip


@router.patch("/variants/{variant_id}")
def patch_variant(variant_id: int, body: VariantPatch, db: Db) -> dict[str, Any]:
    with _errors():
        return gs.edit_variant(
            db, variant_id, body.model_dump(exclude_unset=True), replan=rerun_engine
        )


@router.post("/weight-ladder/preview")
def ladder_preview(body: LadderPreview) -> dict[str, Any]:
    with _errors():
        ladder = gs.preview_ladder(body.start, body.step, body.count, body.extra)
    return {"weight_ladder": ladder}
