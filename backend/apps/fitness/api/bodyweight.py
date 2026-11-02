"""Bodyweight log endpoints (bearer-token protected; the iOS app syncs through these).

POST is an idempotent upsert keyed by the client-generated uuid; DELETE writes a tombstone.
Stats that depend on bodyweight are refreshed on the next import / `python -m apps.fitness.cli stats
rebuild`, not on every request.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from apps.fitness.api.deps import get_db, get_settings_dep, service_errors
from apps.fitness.bodyweight import service
from apps.fitness.db.models import BodyMeasurement
from apps.fitness.db.types import MeasurementSource
from apps.fitness.settings import Settings
from core.auth import require_token

router = APIRouter(prefix="/bodyweight", dependencies=[Depends(require_token)])


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
    return service.list_measurements(db, from_, to)


@router.post("", response_model=MeasurementOut)
def upsert(
    body: MeasurementIn,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> BodyMeasurement:
    with service_errors():
        row, created = service.save_measurement(
            db, id=body.id, measured_at=body.measured_at, weight_kg=body.weight_kg,
            source=body.source, note=body.note, settings=settings,
        )  # fmt: skip
    response.status_code = 201 if created else 200
    return row


@router.delete("/{measurement_id}", status_code=204)
def delete(measurement_id: str, db: Session = Depends(get_db)) -> Response:
    with service_errors():
        service.delete_measurement(db, measurement_id)
    return Response(status_code=204)
