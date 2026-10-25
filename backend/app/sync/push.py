"""`POST /api/v1/sync/push`: per-record last-write-wins upserts of what the iOS app logged.

Rules (docs/api/sync.md): upsert by client uuid; the incoming record wins only if its `updated_at`
is strictly newer than the stored one (equal or older -> `stale`, nothing changes, which also makes
re-pushing a payload a no-op); a record that fails validation is `rejected` alone; everything runs
in one transaction. Sessions are applied before sets, sets before nothing else depends on them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import cache as stats_cache
from app.bodyweight import local_day
from app.db.models import (
    BodyMeasurement,
    ExerciseVariant,
    Gym,
    PlannedSession,
    TrainingSession,
    WorkoutSet,
)
from app.db.types import MeasurementSource, SessionKind, SetType, Source
from app.engine.persist import LIFT_MATCH
from app.settings import Settings
from app.stats import rebuild_with_config
from app.sync.schemas import BodyMeasurementIn, PushRequest, PushResult, SessionIn, SetIn

log = logging.getLogger(__name__)


@dataclass
class PushOutcome:
    results: list[PushResult] = field(default_factory=list)
    session_ids: set[str] = field(default_factory=set)  # sessions applied
    variant_ids: set[int] = field(default_factory=set)  # variants of applied sets (old + new)
    planned_ids: set[str] = field(default_factory=set)  # planned sessions whose link changed
    sets_applied: int = 0
    measurements_applied: int = 0

    @property
    def training_changed(self) -> bool:
        return bool(self.session_ids) or self.sets_applied > 0


def _result(entity: str, id_: Any, status: str, reason: str | None = None) -> PushResult:
    return PushResult(
        entity=entity, id=id_ if isinstance(id_, str) else None, status=status, reason=reason
    )  # type: ignore[arg-type]


def _parse(model: type[BaseModel], raw: dict[str, Any]) -> tuple[Any, str | None]:
    try:
        return model.model_validate(raw), None
    except ValidationError as exc:
        errs = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:3]]
        return None, "invalid: " + "; ".join(errs)


def _newer(incoming: datetime, stored: datetime | None) -> bool:
    return stored is None or incoming > stored


def apply_push(db: Session, req: PushRequest, settings: Settings) -> PushOutcome:
    """Apply the payload to `db` (flushes, never commits)."""
    out = PushOutcome()
    tz = settings.user_tz
    variant_ids = set(db.scalars(select(ExerciseVariant.id)))
    gym_ids = set(db.scalars(select(Gym.id)))

    # ---- sessions
    for raw in req.sessions:
        rec, err = _parse(SessionIn, raw)
        if err:
            out.results.append(_result("session", raw.get("id"), "rejected", err))
            continue
        out.results.append(_session(db, rec, req.device_id, tz, gym_ids, out))
    db.flush()

    # ---- sets (their sessions are now in the database if they were in this payload)
    for raw in req.sets:
        rec, err = _parse(SetIn, raw)
        if err:
            out.results.append(_result("set", raw.get("id"), "rejected", err))
            continue
        out.results.append(_set(db, rec, variant_ids, out))
    db.flush()

    # ---- body measurements
    for raw in req.body_measurements:
        rec, err = _parse(BodyMeasurementIn, raw)
        if err:
            out.results.append(_result("body_measurement", raw.get("id"), "rejected", err))
            continue
        out.results.append(_measurement(db, rec, tz, out))
    db.flush()
    return out


def _session(
    db: Session, rec: SessionIn, device_id: str, tz: str, gym_ids: set[int], out: PushOutcome
) -> PushResult:
    if rec.gym_id is not None and rec.gym_id not in gym_ids:
        return _result("session", rec.id, "rejected", f"unknown gym_id {rec.gym_id}")
    if rec.ended_at is not None and rec.ended_at < rec.started_at:
        return _result("session", rec.id, "rejected", "ended_at is before started_at")
    row = db.get(TrainingSession, rec.id)
    if row is not None and not _newer(rec.updated_at, row.updated_at):
        return _result("session", rec.id, "stale")
    # A link to a plan the server no longer has is dropped rather than failing the session.
    planned_id = rec.planned_session_id
    if planned_id is not None and db.get(PlannedSession, planned_id) is None:
        planned_id = None
    if row is None:
        row = TrainingSession(id=rec.id, source=Source.APP, tz=tz)
        db.add(row)
    elif row.planned_session_id:
        out.planned_ids.add(row.planned_session_id)
    local_tz = ZoneInfo(row.tz or tz)
    row.source_ref = device_id if row.source == Source.APP else row.source_ref
    row.planned_session_id = planned_id
    row.gym_id = rec.gym_id
    row.name = rec.name
    row.session_kind = SessionKind(rec.kind)
    row.started_at_utc = rec.started_at
    row.started_at_local = rec.started_at.astimezone(local_tz).replace(tzinfo=None)
    row.duration_s = (
        round((rec.ended_at - rec.started_at).total_seconds()) if rec.ended_at else None
    )
    row.notes = rec.notes
    row.deleted_at = rec.deleted_at
    row.updated_at = rec.updated_at
    if planned_id:
        out.planned_ids.add(planned_id)
    out.session_ids.add(rec.id)
    return _result("session", rec.id, "applied")


def _set(db: Session, rec: SetIn, variant_ids: set[int], out: PushOutcome) -> PushResult:
    if rec.variant_id not in variant_ids:
        return _result("set", rec.id, "rejected", f"unknown variant_id {rec.variant_id}")
    if db.get(TrainingSession, rec.session_id) is None:
        return _result("set", rec.id, "rejected", f"unknown session_id {rec.session_id}")
    row = db.get(WorkoutSet, rec.id)
    if row is not None and not _newer(rec.updated_at, row.updated_at):
        return _result("set", rec.id, "stale")
    if row is None:
        row = WorkoutSet(id=rec.id, source=Source.APP, block_occurrence=1)
        db.add(row)
    else:
        out.variant_ids.add(row.variant_id)
    row.session_id = rec.session_id
    row.variant_id = rec.variant_id
    row.exercise_order = rec.exercise_order
    row.set_index = rec.set_order
    row.set_type = SetType(rec.set_type)
    row.weight_kg = rec.weight_kg
    row.reps = rec.reps
    row.duration_s = rec.duration_s
    row.completed_at = rec.completed_at
    row.notes = rec.notes
    row.deleted_at = rec.deleted_at
    row.updated_at = rec.updated_at
    out.variant_ids.add(rec.variant_id)
    out.sets_applied += 1
    return _result("set", rec.id, "applied")


def _measurement(db: Session, rec: BodyMeasurementIn, tz: str, out: PushOutcome) -> PushResult:
    row = db.get(BodyMeasurement, rec.id)
    if row is not None and not _newer(rec.updated_at, row.updated_at):
        return _result("body_measurement", rec.id, "stale")
    if row is None:
        # measurement_source has no "app" value; app entries are manual weigh-ins.
        row = BodyMeasurement(id=rec.id, source=MeasurementSource.MANUAL)
        db.add(row)
    row.measured_at = rec.measured_at
    row.day = local_day(rec.measured_at, tz)
    row.weight_kg = rec.weight_kg
    row.note = rec.note
    row.deleted_at = rec.deleted_at
    row.updated_at = rec.updated_at
    out.measurements_applied += 1
    return _result("body_measurement", rec.id, "applied")


# --------------------------------------------------------------------------- follow-ups


def link_planned_sessions(db: Session, planned_ids: set[str]) -> None:
    """Close the loop with the plan: a planned session with a live (non-deleted) linked session is
    `done` (matching kind) or `modified`; if its last linked session was deleted it goes back to
    `proposed` (lifting kinds only) so the engine can plan it again."""
    for pid in sorted(planned_ids):
        plan = db.get(PlannedSession, pid)
        if plan is None or plan.deleted_at is not None:
            continue
        live = list(
            db.scalars(
                select(TrainingSession).where(
                    TrainingSession.planned_session_id == pid,
                    TrainingSession.deleted_at.is_(None),
                )
            )
        )
        if live:
            match = LIFT_MATCH.get(plan.kind, set())
            if plan.status in ("proposed", "approved", "skipped"):
                plan.status = "done" if any(s.session_kind in match for s in live) else "modified"
        elif plan.status in ("done", "modified") and plan.kind in LIFT_MATCH:
            plan.status = "proposed"
    db.flush()


def refresh_stats(db: Session, out: PushOutcome) -> None:
    """Recompute exercise_session_stats (and sets.e1rm_kg) for the variants the push touched; a
    new bodyweight changes e1RM of bodyweight lifts, so that rebuilds everything."""
    if out.measurements_applied:
        rebuild_with_config(db)
        return
    variants = set(out.variant_ids)
    if out.session_ids:  # a moved / deleted session changes its variants' stats too
        variants |= set(
            db.scalars(
                select(WorkoutSet.variant_id)
                .where(WorkoutSet.session_id.in_(sorted(out.session_ids)))
                .distinct()
            )
        )
    if variants:
        rebuild_with_config(db, sorted(variants))


def finish_push(db: Session, out: PushOutcome) -> None:
    """Planned-session linking + stats, in a savepoint so a failure here never loses the data."""
    try:
        with db.begin_nested():
            link_planned_sessions(db, out.planned_ids)
            refresh_stats(db, out)
    except Exception:
        log.exception("post-push planned-session linking / stats refresh failed")
    stats_cache.invalidate()
