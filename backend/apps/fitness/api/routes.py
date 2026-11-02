"""Read-only API skeleton. Sync/write endpoints for the iOS app come in a later step."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from apps.fitness.api.deps import get_db, service_errors
from apps.fitness.db.models import ExerciseSessionStats, ExerciseVariant, TrainingSession
from apps.fitness.exercises import service as xs
from apps.fitness.sync import history
from core.auth import require_token

public_router = APIRouter()  # /health only
router = APIRouter(dependencies=[Depends(require_token)])


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SessionOut(_Out):
    id: str
    name: str | None
    session_kind: str
    started_at_local: datetime
    started_at_utc: datetime
    duration_s: int | None
    source: str


class VariantOut(_Out):
    id: int
    name: str
    gym_id: int | None
    equipment_type: str
    load_convention: str
    weight_ladder: list[float] | None


class StatOut(_Out):
    session_id: str
    session_date: date
    set_count: int
    total_volume_kg: float
    best_e1rm_kg: float | None
    top_set_weight_kg: float | None
    top_set_reps: int | None
    is_e1rm_pr: bool
    is_weight_pr: bool


@public_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(
    limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)
) -> list[TrainingSession]:
    return history.recent_sessions(db, limit)


@router.get("/variants", response_model=list[VariantOut])
def list_variants(db: Session = Depends(get_db)) -> list[ExerciseVariant]:
    return xs.active_variants(db)


@router.get("/variants/{variant_id}/stats", response_model=list[StatOut])
def variant_stats(variant_id: int, db: Session = Depends(get_db)) -> list[ExerciseSessionStats]:
    with service_errors():
        return history.variant_stats(db, variant_id)
