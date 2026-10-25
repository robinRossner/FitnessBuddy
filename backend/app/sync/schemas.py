"""Wire models of `POST /api/v1/sync/push`. Records are validated one by one (see push.py) so a
single bad record is `rejected` instead of failing the whole request."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _uuid(v: str) -> str:
    return str(uuid.UUID(v))  # canonical lowercase; ValueError -> record rejected


def _utc(v: datetime) -> datetime:
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


UUIDStr = Annotated[str, AfterValidator(_uuid)]
UTCTime = Annotated[datetime, AfterValidator(_utc)]
KindIn = Literal["upper", "lower", "full", "other"]
SetTypeIn = Literal["working", "warmup", "drop", "failure"]


class _Record(BaseModel):
    model_config = ConfigDict(extra="ignore")
    updated_at: UTCTime
    deleted_at: UTCTime | None = None


class SessionIn(_Record):
    id: UUIDStr
    planned_session_id: UUIDStr | None = None
    gym_id: int | None = None
    kind: KindIn
    name: str = Field(max_length=160)
    started_at: UTCTime
    ended_at: UTCTime | None = None
    notes: str | None = None


class SetIn(_Record):
    id: UUIDStr
    session_id: UUIDStr
    variant_id: int
    exercise_order: int = Field(ge=0)
    set_order: int = Field(ge=0)
    set_type: SetTypeIn = "working"
    weight_kg: float | None = Field(default=None, ge=0, le=1000)
    reps: int | None = Field(default=None, ge=0, le=1000)
    duration_s: float | None = Field(default=None, ge=0, le=86400)
    completed_at: UTCTime | None = None
    notes: str | None = None


class BodyMeasurementIn(_Record):
    id: UUIDStr
    measured_at: UTCTime
    weight_kg: float = Field(gt=20, lt=400)
    note: str | None = Field(default=None, max_length=500)


class PushRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=100)
    sessions: list[dict[str, Any]] = []
    sets: list[dict[str, Any]] = []
    body_measurements: list[dict[str, Any]] = []


class PushResult(BaseModel):
    entity: Literal["session", "set", "body_measurement"]
    id: str | None
    status: Literal["applied", "stale", "rejected"]
    reason: str | None = None


class PushResponse(BaseModel):
    server_time: str
    results: list[PushResult]
