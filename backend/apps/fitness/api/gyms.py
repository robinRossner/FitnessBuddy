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

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from apps.fitness import gym_service as gs
from apps.fitness.api.auth import require_token
from apps.fitness.api.routes import get_db
from apps.fitness.db.models import Exercise, ExerciseVariant
from apps.fitness.jobs.engine_jobs import rerun_engine
from apps.fitness.settings import get_settings

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])

Name = Annotated[str, Field(min_length=1, max_length=120)]
Equipment = Literal["machine_stack", "cable", "smith", "plate_loaded", "other"]
AnyEquipment = Literal[
    "barbell", "dumbbell", "machine_stack", "plate_loaded", "cable", "bodyweight", "smith", "other"
]


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except gs.GymError as exc:
        raise HTTPException(exc.status, exc.message) from None
    except ValueError as exc:  # ladder validation
        raise HTTPException(422, str(exc)) from None


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


def _gym_json(db: Session, gym) -> dict[str, Any]:
    current = gs.effective_current_gym(db, get_settings())
    return gs.gym_dict(db, gym, current.id if current else None)


# --------------------------------------------------------------------------- gyms


@router.get("/gyms")
def list_gyms(db: Session = Depends(get_db)) -> dict[str, Any]:
    return gs.list_gyms(db, get_settings())


@router.post("/gyms", status_code=201)
def create_gym(body: NameBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    gym = _call(gs.create_gym, db, body.name)
    db.commit()
    return _gym_json(db, gym)


@router.put("/gyms/current")
def put_current_gym(body: CurrentBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Make a gym the current one and re-plan the upcoming sessions. Idempotent."""
    settings = get_settings()
    gym = _call(gs.find_gym, db, body.gym_id)
    gs.set_current_gym(db, gym, settings)
    db.commit()
    rerun_engine("current gym")
    return {
        "current_gym_id": gym.id,
        "unmapped_count": gs.unmapped_count(db, gym, settings),
    }


@router.patch("/gyms/{gym_id}")
def rename_gym(gym_id: int, body: NameBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    gym = _call(gs.find_gym, db, gym_id)
    _call(gs.rename_gym, db, gym, body.name)
    db.commit()
    return _gym_json(db, gym)


@router.delete("/gyms/{gym_id}", status_code=204)
def delete_gym(gym_id: int, db: Session = Depends(get_db)) -> Response:
    gym = _call(gs.find_gym, db, gym_id)
    _call(gs.delete_gym, db, gym, get_settings())
    db.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------- machines


@router.get("/gyms/{gym_id}/machines")
def gym_machines(gym_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    gym = _call(gs.find_gym, db, gym_id)
    return {"gym": _gym_json(db, gym), "items": gs.machine_items(db, gym, get_settings())}


@router.post("/gyms/{gym_id}/machines", status_code=201)
def create_machine(gym_id: int, body: MachineBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    gym = _call(gs.find_gym, db, gym_id)
    ex = db.get(Exercise, body.exercise_id)
    if ex is None:
        raise HTTPException(404, "exercise not found")
    _call(
        gs.create_machine,
        db,
        gym,
        ex,
        name=body.name,
        equipment_type=body.equipment_type,
        weight_ladder=body.weight_ladder,
        notes=body.notes,
        settings=settings,
    )
    db.commit()
    current = gs.effective_current_gym(db, settings)
    if current is not None and current.id == gym.id:
        rerun_engine("machine set up")  # the plan item stops being "set up this machine"
    return gs.machine_item_for(db, gym, ex, settings)


@router.patch("/variants/{variant_id}")
def patch_variant(
    variant_id: int, body: VariantPatch, db: Session = Depends(get_db)
) -> dict[str, Any]:
    v = db.get(ExerciseVariant, variant_id)
    if v is None or v.deleted_at is not None:
        raise HTTPException(404, "variant not found")
    v, engine_changed = _call(gs.update_variant, db, v, body.model_dump(exclude_unset=True))
    db.commit()
    if engine_changed:
        rerun_engine("machine changed")
    usage = gs.usage_by_variant(db).get(v.id, gs.Usage())
    return {
        "id": v.id,
        "exercise_id": v.exercise_id,
        "gym_id": v.gym_id,
        "name": v.name,
        "equipment_type": gs._val(v.equipment_type),
        "weight_ladder": v.weight_ladder,
        "increment_kg": v.increment_kg,
        "notes": v.notes,
        "active": bool(v.active),
        "sessions": usage.sessions,
        "last_used": usage.last_used.isoformat() if usage.last_used else None,
    }


@router.post("/weight-ladder/preview")
def ladder_preview(body: LadderPreview) -> dict[str, Any]:
    ladder = _call(gs.preview_ladder, body.start, body.step, body.count, body.extra)
    return {"weight_ladder": ladder}
