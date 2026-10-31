"""Read-only API skeleton. Sync/write endpoints for the iOS app come in a later step."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.db.models import ExerciseSessionStats, ExerciseVariant, TrainingSession
from apps.fitness.db.session import get_session_factory
from core.auth import require_token

public_router = APIRouter()  # /health only
router = APIRouter(dependencies=[Depends(require_token)])


def get_db() -> Iterator[Session]:
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


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
    return list(
        db.scalars(
            select(TrainingSession)
            .where(TrainingSession.deleted_at.is_(None))
            .order_by(TrainingSession.started_at_utc.desc())
            .limit(limit)
        )
    )


@router.get("/variants", response_model=list[VariantOut])
def list_variants(db: Session = Depends(get_db)) -> list[ExerciseVariant]:
    return list(
        db.scalars(
            select(ExerciseVariant)
            .where(ExerciseVariant.deleted_at.is_(None))
            .order_by(ExerciseVariant.name)
        )
    )


@router.get("/variants/{variant_id}/stats", response_model=list[StatOut])
def variant_stats(variant_id: int, db: Session = Depends(get_db)) -> list[ExerciseSessionStats]:
    if db.get(ExerciseVariant, variant_id) is None:
        raise HTTPException(404, "variant not found")
    return list(
        db.scalars(
            select(ExerciseSessionStats)
            .where(ExerciseSessionStats.variant_id == variant_id)
            .order_by(ExerciseSessionStats.session_date)
        )
    )
