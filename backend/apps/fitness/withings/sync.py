"""Withings weigh-ins -> `body_measurements` (source `withings`).

Incremental: asks Withings for groups modified since the last successful run minus one day (a
re-read of unchanged rows is a no-op), or since `withings_backfill_days` ago on the first run.
Row ids are `uuid5(NAMESPACE, "withings:<grpid>")`, so a re-run, an edited weigh-in or an
overlapping window updates the same row; a row the user deleted in the app stays deleted.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.fitness.bodyweight.service import upsert_measurement
from apps.fitness.db.models import BodyMeasurement, SyncState
from apps.fitness.db.session import session_scope
from apps.fitness.db.types import MeasurementSource, utcnow
from apps.fitness.settings import Settings
from apps.fitness.withings.client import WeightGroup, WithingsClient
from core.redact import redact

log = logging.getLogger(__name__)

STATE_SOURCE = "withings"
STATE_KIND = "weight"
NAMESPACE = uuid.UUID("5b2f0c7e-3d1a-4c86-b7f4-9e0d62a8c1d3")
OVERLAP = timedelta(days=1)
MIN_KG, MAX_KG = 20.0, 400.0  # same bounds as the bodyweight API (exclusive)
NOTE = "withings"


def measurement_id(grpid: int) -> str:
    return str(uuid.uuid5(NAMESPACE, f"withings:{grpid}"))


@dataclass
class SyncReport:
    fetched: int = 0
    new: int = 0
    updated: int = 0
    skipped: int = 0  # implausible weights
    latest_measured_at: datetime | None = None  # newest stored Withings weigh-in (not deleted)
    latest_weight_kg: float | None = None

    @property
    def weights_changed(self) -> bool:
        return self.new + self.updated > 0


def latest_weight(db: Session) -> tuple[datetime, float] | None:
    row = db.execute(
        select(BodyMeasurement.measured_at, BodyMeasurement.weight_kg)
        .where(
            BodyMeasurement.source == MeasurementSource.WITHINGS,
            BodyMeasurement.deleted_at.is_(None),
        )
        .order_by(BodyMeasurement.measured_at.desc())
        .limit(1)
    ).first()
    return None if row is None else (row[0], row[1])


def record_state(
    factory: sessionmaker[Session],
    *,
    error: str | None = None,
    rows: int | None = None,
    today: date | None = None,
) -> None:
    """Write the run into `sync_state` (source withings, kind weight)."""
    with session_scope(factory) as db:
        st = db.scalar(
            select(SyncState).where(SyncState.source == STATE_SOURCE, SyncState.kind == STATE_KIND)
        )
        if st is None:
            st = SyncState(source=STATE_SOURCE, kind=STATE_KIND, rows_last=0)
            db.add(st)
        now = utcnow()
        st.last_run_at = now
        if error is not None:
            st.last_error, st.last_error_at = redact(error)[:500], now
        else:
            st.last_error = None
            st.last_success_at = now
            st.cursor_date = today
            if rows is not None:
                st.rows_last = rows


def _since(factory: sessionmaker[Session], settings: Settings, now: datetime) -> int:
    with session_scope(factory) as db:
        last = db.scalar(
            select(SyncState.last_success_at).where(
                SyncState.source == STATE_SOURCE, SyncState.kind == STATE_KIND
            )
        )
    start = (
        now - timedelta(days=settings.withings_backfill_days)
        if last is None
        else last.astimezone(UTC) - OVERLAP
    )
    return int(start.timestamp())


def _store(db: Session, groups: list[WeightGroup], tz: str, report: SyncReport) -> None:
    for g in groups:
        if not MIN_KG < g.weight_kg < MAX_KG:
            report.skipped += 1
            continue
        mid = measurement_id(g.grpid)
        old = db.get(BodyMeasurement, mid)
        if old is not None:
            if old.deleted_at is not None:
                continue  # deleted in the app: stays deleted
            if (
                abs(old.weight_kg - g.weight_kg) < 1e-9
                and old.measured_at.astimezone(UTC) == g.measured_at
            ):
                continue
        _, created = upsert_measurement(
            db,
            id=mid,
            measured_at=g.measured_at,
            weight_kg=g.weight_kg,
            tz=tz,
            source=MeasurementSource.WITHINGS,
            note=NOTE,
        )
        if created:
            report.new += 1
        else:
            report.updated += 1


def sync_weights(
    factory: sessionmaker[Session],
    client: WithingsClient,
    settings: Settings,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SyncReport:
    """One incremental run. The outcome (success or error) lands in `sync_state`; errors are
    re-raised for the caller to map."""
    report = SyncReport()
    started = now()
    try:
        groups = client.weights(_since(factory, settings, started))
        report.fetched = len(groups)
        with session_scope(factory) as db:
            _store(db, groups, settings.user_tz, report)
        with session_scope(factory) as db:
            if (latest := latest_weight(db)) is not None:
                report.latest_measured_at, report.latest_weight_kg = latest
    except Exception as exc:
        record_state(factory, error=f"{type(exc).__name__}: {exc}")
        raise
    record_state(
        factory,
        rows=report.new + report.updated,
        today=started.astimezone(ZoneInfo(settings.user_tz)).date(),
    )
    log.info(
        "Withings sync: %d fetched, %d new, %d updated, %d skipped",
        report.fetched,
        report.new,
        report.updated,
        report.skipped,
    )
    return report
