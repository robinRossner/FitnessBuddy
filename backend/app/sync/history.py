"""`GET /api/v1/variants/{id}/history`: last N sessions of a variant."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ExerciseSessionStats, TrainingSession, WorkoutSet


def variant_history(db: Session, variant_id: int, limit: int) -> list[dict[str, Any]]:
    """Newest first; tombstoned sessions/sets excluded; all set types included (warmups too,
    flagged by `set_type`). `best_e1rm` comes from exercise_session_stats (kg, null if none)."""
    sessions = db.execute(
        select(TrainingSession.id, TrainingSession.started_at_local)
        .where(
            TrainingSession.deleted_at.is_(None),
            TrainingSession.id.in_(
                select(WorkoutSet.session_id).where(
                    WorkoutSet.variant_id == variant_id, WorkoutSet.deleted_at.is_(None)
                )
            ),
        )
        .order_by(TrainingSession.started_at_utc.desc(), TrainingSession.id)
        .limit(limit)
    ).all()
    if not sessions:
        return []
    ids = [sid for sid, _ in sessions]
    sets: dict[str, list[dict[str, Any]]] = {sid: [] for sid in ids}
    for ws in db.scalars(
        select(WorkoutSet)
        .where(
            WorkoutSet.variant_id == variant_id,
            WorkoutSet.deleted_at.is_(None),
            WorkoutSet.session_id.in_(ids),
        )
        .order_by(WorkoutSet.exercise_order, WorkoutSet.block_occurrence, WorkoutSet.set_index)
    ):
        sets[ws.session_id].append(
            {
                "weight_kg": ws.weight_kg,
                "reps": ws.reps,
                "duration_s": ws.duration_s,
                "set_type": getattr(ws.set_type, "value", ws.set_type),
            }
        )
    best = dict(
        db.execute(
            select(ExerciseSessionStats.session_id, ExerciseSessionStats.best_e1rm_kg).where(
                ExerciseSessionStats.variant_id == variant_id,
                ExerciseSessionStats.session_id.in_(ids),
            )
        ).all()
    )
    return [
        {
            "session_id": sid,
            "date": local.date().isoformat(),
            "sets": sets[sid],
            "best_e1rm": best.get(sid),
        }
        for sid, local in sessions
    ]
