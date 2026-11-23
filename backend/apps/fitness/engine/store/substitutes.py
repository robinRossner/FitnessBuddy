"""Slots of the program whose default variant is not available at the current gym, and the
variants the user may add ad hoc. Database side only; the planner gets plain `LiftContext`s.

The program (program.yaml) stays the user's default: a slot whose variant cannot be resolved
at the current gym is filled, for this plan only, by another exercise of the same movement
pattern that is usable here (a generic variant, or an active one at this gym). Preference:

1. the candidate the user trained most recently at this gym (so the one picked in an earlier
   session usually comes back);
2. else the one trained most recently anywhere;
3. else the catalog: the same exercise, then the same primary muscle, then a machine set up at
   this gym before a generic variant, then the lowest id (stable).

Variants already in the same day are never picked twice. Nothing of the same pattern usable
here -> the slot stays unresolved (`needs_setup`).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from apps.fitness.db.models import Exercise, ExerciseVariant, Gym, TrainingSession, WorkoutSet
from apps.fitness.db.types import SetType

WORKING = (SetType.WORKING.value, SetType.FAILURE.value)
ADHOC_MAX = 30  # ad-hoc targets per lifting session (the most recently trained variants)


def _usable(gym: Gym | None):
    """Filter: a live, active variant usable at `gym` (generic, or at that gym). An inactive
    generic variant is one the user stopped training: never offered in its place."""
    here = ExerciseVariant.gym_id == gym.id if gym is not None else ExerciseVariant.gym_id.is_(None)
    return (
        ExerciseVariant.deleted_at.is_(None)
        & ExerciseVariant.active.is_(True)
        & or_(ExerciseVariant.gym_id.is_(None), here)
    )


def last_trained(
    db: Session, variant_ids: list[int] | None = None, gym_id: int | None = None
) -> dict[int, datetime]:
    """Latest session start (local) with working sets per variant, optionally at one gym."""
    q = (
        select(WorkoutSet.variant_id, func.max(TrainingSession.started_at_local))
        .join(TrainingSession, WorkoutSet.session_id == TrainingSession.id)
        .where(
            WorkoutSet.deleted_at.is_(None),
            TrainingSession.deleted_at.is_(None),
            WorkoutSet.set_type.in_(WORKING),
        )
        .group_by(WorkoutSet.variant_id)
    )
    if variant_ids is not None:
        q = q.where(WorkoutSet.variant_id.in_(variant_ids))
    if gym_id is not None:
        q = q.where(TrainingSession.gym_id == gym_id)
    return {vid: started for vid, started in db.execute(q).all() if started is not None}


def find_substitute(
    db: Session,
    exercise_slug: str,
    gym: Gym | None,
    exclude: set[int] | frozenset[int] = frozenset(),
) -> ExerciseVariant | None:
    """A usable variant of the same movement pattern for a slot whose default `exercise_slug`
    has none at `gym` (see the module docstring for the order), or None."""
    default = db.scalar(select(Exercise).where(Exercise.slug == exercise_slug))
    if default is None:
        return None
    cands = [
        v
        for v in db.scalars(
            select(ExerciseVariant)
            .join(Exercise, Exercise.id == ExerciseVariant.exercise_id)
            .where(Exercise.movement_pattern == default.movement_pattern, _usable(gym))
        )
        if v.id not in exclude and v.exercise.is_timed == default.is_timed
    ]
    if not cands:
        return None
    ids = [v.id for v in cands]
    anywhere = last_trained(db, ids)
    here = last_trained(db, ids, gym.id) if gym is not None else {}
    never = datetime.min

    def key(v: ExerciseVariant):
        return (
            -(here.get(v.id, never) - never).total_seconds(),
            -(anywhere.get(v.id, never) - never).total_seconds(),
            v.exercise_id != default.id,
            v.exercise.primary_muscle != default.primary_muscle,
            v.gym_id is None,
            v.id,
        )

    return min(cands, key=key)


def substitute_reason(default_name: str, gym: Gym | None) -> str:
    where = f"at {gym.name}" if gym is not None else "here"
    return f"{default_name} is not set up {where}"


def adhoc_variants(db: Session, gym: Gym | None, today: date, days: int) -> list[ExerciseVariant]:
    """Variants usable at `gym` with working sets in the last `days` days, most recent first
    (at most `ADHOC_MAX`): the ones the user may add or swap in outside the plan."""
    since = datetime.combine(today - timedelta(days=days), datetime.min.time())
    recent = {vid: t for vid, t in last_trained(db).items() if t >= since}
    if not recent:
        return []
    rows = list(
        db.scalars(
            select(ExerciseVariant).where(ExerciseVariant.id.in_(list(recent)), _usable(gym))
        )
    )
    rows.sort(key=lambda v: (recent[v.id], -v.id), reverse=True)
    return rows[:ADHOC_MAX]
