"""`GET /stats/volume/weekly`: lifting + running load per ISO week (Monday start), gaps filled."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import common
from app.analytics.common import num
from app.db.models import Exercise, ExerciseSessionStats, ExerciseVariant, Run
from app.sources.garmin.mapping import RUN_TYPES


def weekly_volume(db: Session, weeks: int) -> dict:
    starts = common.week_starts(weeks, common.today())
    first, last = starts[0], starts[-1]
    buckets = {
        w: {
            "week_start": w.isoformat(),
            "lifting_volume_kg": 0.0,
            "lifting_sets": 0,
            "_sessions": set(),
            "sets_by_muscle_group": defaultdict(int),
            "running_km": 0.0,
            "running_time_s": 0.0,
            "runs": 0,
        }
        for w in starts
    }
    lifts = db.execute(
        select(
            ExerciseSessionStats.session_date,
            ExerciseSessionStats.session_id,
            ExerciseSessionStats.working_set_count,
            ExerciseSessionStats.total_volume_kg,
            Exercise.primary_muscle,
        )
        .join(ExerciseVariant, ExerciseSessionStats.variant_id == ExerciseVariant.id)
        .join(Exercise, ExerciseVariant.exercise_id == Exercise.id)
        .where(
            ExerciseSessionStats.session_date >= first,
            ExerciseSessionStats.session_date < last + timedelta(days=7),
        )
    )
    for day, sid, sets, vol, muscle in lifts:
        b = buckets.get(common.week_start(day))
        if b is None:
            continue
        b["lifting_volume_kg"] += vol or 0.0
        b["lifting_sets"] += sets
        b["_sessions"].add(sid)
        if sets:
            b["sets_by_muscle_group"][muscle] += sets
    runs = db.execute(
        select(Run.started_at_local, Run.distance_m, Run.duration_s).where(
            Run.deleted_at.is_(None),
            Run.activity_type.in_(RUN_TYPES),
            Run.started_at_local >= datetime.combine(first, time.min),
            Run.started_at_local < datetime.combine(last + timedelta(days=7), time.min),
        )
    )
    for started, dist, dur in runs:
        b = buckets.get(common.week_start(started.date()))
        if b is not None:
            b["running_km"] += (dist or 0.0) / 1000
            b["running_time_s"] += dur or 0.0
            b["runs"] += 1
    out = []
    for b in buckets.values():
        out.append(
            {
                "week_start": b["week_start"],
                "lifting_volume_kg": num(b["lifting_volume_kg"]),
                "lifting_sets": b["lifting_sets"],
                "lifting_sessions": len(b["_sessions"]),
                "sets_by_muscle_group": dict(sorted(b["sets_by_muscle_group"].items())),
                "running_km": num(b["running_km"]),
                "running_time_s": round(b["running_time_s"]),
                "runs": b["runs"],
            }
        )
    return {"generated_at": common.generated_at(), "weeks": out}
