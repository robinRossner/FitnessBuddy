"""Bodyweight log endpoints (bearer-token protected; the iOS app syncs through these).

POST is an idempotent upsert keyed by the client-generated uuid; DELETE writes a tombstone.
Stats that depend on bodyweight are refreshed on the next import / `python -m apps.fitness.cli stats
rebuild`, not on every request.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.api.routes import get_db
from apps.fitness.bodyweight import upsert_measurement
from apps.fitness.db.models import BodyMeasurement
from apps.fitness.db.types import MeasurementSource, utcnow
from apps.fitness.settings import get_settings
from core.auth import require_token

router = APIRouter(prefix="/api/v1/bodyweight", dependencies=[Depends(require_token)])


class MeasurementIn(BaseModel):
    id: str = Field(description="client-generated uuid; re-posting the same id updates the row")
    measured_at: datetime = Field(
        description="ISO datetime (naive = server user timezone) or a plain date (midnight)"
    )
    weight_kg: float = Field(gt=20, lt=400)
    source: MeasurementSource = MeasurementSource.MANUAL
    note: str | None = Field(default=None, max_length=500)

    @field_validator("id")
    @classmethod
    def _uuid(cls, v: str) -> str:
        return str(uuid.UUID(v))  # raises ValueError -> 422


class MeasurementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    measured_at: datetime
    day: date
    weight_kg: float
    source: str
    note: str | None


@router.get("", response_model=list[MeasurementOut])
def list_measurements(
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    db: Session = Depends(get_db),
) -> list[BodyMeasurement]:
    q = select(BodyMeasurement).where(BodyMeasurement.deleted_at.is_(None))
    if from_:
        q = q.where(BodyMeasurement.day >= from_)
    if to:
        q = q.where(BodyMeasurement.day <= to)
    return list(db.scalars(q.order_by(BodyMeasurement.measured_at)))


@router.post("", response_model=MeasurementOut)
def upsert(
    body: MeasurementIn, response: Response, db: Session = Depends(get_db)
) -> BodyMeasurement:
    existing = db.get(BodyMeasurement, body.id)
    if existing is not None and existing.deleted_at is not None:
        raise HTTPException(409, "measurement was deleted")
    row, created = upsert_measurement(
        db,
        id=body.id,
        measured_at=body.measured_at,
        weight_kg=body.weight_kg,
        tz=get_settings().user_tz,
        source=body.source,
        note=body.note,
    )
    db.commit()
    response.status_code = 201 if created else 200
    return row


@router.delete("/{measurement_id}", status_code=204)
def delete(measurement_id: str, db: Session = Depends(get_db)) -> Response:
    row = db.get(BodyMeasurement, measurement_id)
    if row is None:
        raise HTTPException(404, "measurement not found")
    if row.deleted_at is None:
        row.deleted_at = utcnow()
        db.commit()
    return Response(status_code=204)
