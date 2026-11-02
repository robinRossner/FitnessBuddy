"""Bodyweight log helpers: lookup by date and idempotent upsert.

`bodyweight_at(day)` = latest measurement on/before `day`, else the earliest one after it, else
the configured fallback (engine config `user.bodyweight_kg`). Bodyweight is a context signal
(it scales bodyweight-loaded e1RM), not a training goal.
"""

from __future__ import annotations

import uuid
from bisect import bisect_right
from collections.abc import Iterable
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.db.models import BodyMeasurement
from apps.fitness.db.types import MeasurementSource

# Namespace for deterministic ids of CLI-made manual entries (one per day).
_NAMESPACE = uuid.UUID("0b1d6c5e-8f43-4a8a-9d52-6a2f1c3e7b90")


class BodyweightTimeline:
    """In-memory timeline for fast repeated lookups (stats rebuild)."""

    def __init__(self, points: Iterable[tuple[date, float]], fallback_kg: float | None = None):
        # later entries of the same day win (callers pass them ordered by measured_at)
        by_day: dict[date, float] = {}
        for d, kg in points:
            by_day[d] = kg
        self._days = sorted(by_day)
        self._kgs = [by_day[d] for d in self._days]
        self.fallback_kg = fallback_kg

    def at(self, day: date) -> float | None:
        if not self._days:
            return self.fallback_kg
        i = bisect_right(self._days, day)
        return self._kgs[i - 1] if i > 0 else self._kgs[0]

    def __len__(self) -> int:
        return len(self._days)


def load_timeline(db: Session, fallback_kg: float | None = None) -> BodyweightTimeline:
    rows = db.execute(
        select(BodyMeasurement.day, BodyMeasurement.weight_kg)
        .where(BodyMeasurement.deleted_at.is_(None))
        .order_by(BodyMeasurement.day, BodyMeasurement.measured_at)
    ).all()
    return BodyweightTimeline(((d, kg) for d, kg in rows), fallback_kg)


def bodyweight_at(db: Session, day: date, fallback_kg: float | None = None) -> float | None:
    """Latest measurement on/before `day`, else earliest after, else `fallback_kg`."""
    base = select(BodyMeasurement.weight_kg).where(BodyMeasurement.deleted_at.is_(None))
    before = db.scalar(
        base.where(BodyMeasurement.day <= day)
        .order_by(BodyMeasurement.day.desc(), BodyMeasurement.measured_at.desc())
        .limit(1)
    )
    if before is not None:
        return before
    after = db.scalar(
        base.where(BodyMeasurement.day > day)
        .order_by(BodyMeasurement.day, BodyMeasurement.measured_at)
        .limit(1)
    )
    return after if after is not None else fallback_kg


def local_day(measured_at: datetime, tz: str) -> date:
    return measured_at.astimezone(ZoneInfo(tz)).date()


def as_aware(value: datetime, tz: str) -> datetime:
    """Naive datetimes are wall-clock times in `tz`."""
    return value.replace(tzinfo=ZoneInfo(tz)) if value.tzinfo is None else value.astimezone(UTC)


def manual_id(day: date) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"manual:{day.isoformat()}"))


def upsert_measurement(
    db: Session,
    *,
    id: str,
    measured_at: datetime,
    weight_kg: float,
    tz: str,
    source: MeasurementSource = MeasurementSource.MANUAL,
    note: str | None = None,
) -> tuple[BodyMeasurement, bool]:
    """Create or update by id. Returns (row, created). A tombstoned row stays deleted."""
    when = as_aware(measured_at, tz)
    row = db.get(BodyMeasurement, id)
    created = row is None
    if row is None:
        row = BodyMeasurement(id=id)
        db.add(row)
    if row.deleted_at is None:
        row.measured_at = when
        row.day = local_day(when, tz)
        row.weight_kg = weight_kg
        row.source = source
        row.note = note
    db.flush()
    return row, created
