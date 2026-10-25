"""Derived strength stats: e1RM per set, per-(session, variant) aggregates and PRs.

Results are materialised into `exercise_session_stats` (and `sets.e1rm_kg`) so charts are cheap.
All weights are in the variant's logged convention (per dumbbell for per_hand variants).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.bodyweight import load_timeline
from app.db.models import (
    Exercise,
    ExerciseSessionStats,
    ExerciseVariant,
    TrainingSession,
    WorkoutSet,
)
from app.db.types import EquipmentType, LoadConvention, SetType

E1RM_SET_TYPES = {SetType.WORKING, SetType.FAILURE}
VOLUME_SET_TYPES = {SetType.WORKING, SetType.FAILURE, SetType.DROP}


def epley(weight: float, reps: int, *, max_reps: int = 15, min_reps: int = 1) -> float | None:
    """Epley e1RM = w * (1 + reps/30). None outside [min_reps, max_reps] or for w <= 0.

    By convention a single (reps == 1) is the weight itself.
    """
    if weight is None or reps is None or weight <= 0 or reps < min_reps or reps > max_reps:
        return None
    if reps == 1:
        return round(float(weight), 2)
    return round(weight * (1 + reps / 30), 2)


@dataclass(frozen=True)
class E1RMContext:
    equipment_type: str
    load_convention: str
    e1rm_meaningful: bool
    bodyweight_kg: float | None = None  # bodyweight on the session's date
    max_reps: int = 15
    min_reps: int = 1
    # True: the body is (part of) the load, so e1RM uses bodyweight + added weight. False (e.g.
    # weighted crunch): logged weight is the whole load.
    bodyweight_loaded: bool = True


def set_e1rm(
    weight: float | None, reps: int | None, set_type: str, ctx: E1RMContext
) -> tuple[float, str] | None:
    """(e1rm, basis) for one set, basis = 'total' | 'added_weight'; None if not meaningful.

    Bodyweight-loaded variants (pull-up, dip, chest-to-bar): load = bodyweight + added weight.
    Assisted machine variants: load = bodyweight - assistance. Band variants (assistance
    convention on bodyweight equipment) never get an e1RM: their value is a band level (0-5),
    not a weight. With unknown bodyweight, bodyweight variants only get an e1RM on a positive
    added weight (basis='added_weight', comparable only within the variant) and assistance
    variants none.
    """
    if not ctx.e1rm_meaningful or reps is None or weight is None:
        return None
    if SetType(set_type) not in E1RM_SET_TYPES:
        return None
    load, basis = weight, "total"
    bw = ctx.bodyweight_kg
    if ctx.load_convention == LoadConvention.ASSISTANCE:
        if bw is None or ctx.equipment_type == EquipmentType.BODYWEIGHT:
            # Band pull-ups: the logged 0-5 is a band *level*, not kilograms -> no e1RM at all.
            return None
        load = bw - weight
    elif ctx.equipment_type == EquipmentType.BODYWEIGHT:
        if ctx.bodyweight_loaded and bw is not None:
            load = bw + weight
        elif weight > 0:
            basis = "added_weight"
        else:
            return None
    value = epley(load, reps, max_reps=ctx.max_reps, min_reps=ctx.min_reps)
    return (value, basis) if value is not None else None


@dataclass
class SetLike:
    weight_kg: float | None
    reps: int | None
    set_type: str
    duration_s: float | None = None
    e1rm: float | None = None
    e1rm_basis: str | None = None


@dataclass
class SessionAggregate:
    session_id: str
    variant_id: int
    session_date: date
    set_count: int = 0
    working_set_count: int = 0
    total_reps: int = 0
    total_volume_kg: float = 0.0
    best_e1rm_kg: float | None = None
    e1rm_basis: str | None = None
    top_set_weight_kg: float | None = None
    top_set_reps: int | None = None
    best_hold_s: float | None = None
    max_reps_at_weight: dict[float, int] = field(default_factory=dict)
    is_e1rm_pr: bool = False
    is_weight_pr: bool = False
    rep_prs: list[dict] = field(default_factory=list)


def aggregate_session(
    session_id: str, variant_id: int, day: date, sets: list[SetLike]
) -> SessionAggregate:
    agg = SessionAggregate(session_id=session_id, variant_id=variant_id, session_date=day)
    for s in sets:
        agg.set_count += 1
        if s.set_type == SetType.WARMUP:
            continue
        agg.working_set_count += 1
        if s.duration_s and s.reps is None:
            agg.best_hold_s = max(agg.best_hold_s or 0.0, s.duration_s)
        if s.reps:
            agg.total_reps += s.reps
            if s.set_type in VOLUME_SET_TYPES and s.weight_kg:
                agg.total_volume_kg += s.weight_kg * s.reps
        if s.weight_kg is not None and s.reps:
            key = (s.weight_kg, s.reps)
            top = (agg.top_set_weight_kg, agg.top_set_reps)
            if top[0] is None or key > (top[0], top[1] or 0):
                agg.top_set_weight_kg, agg.top_set_reps = key
            prev = agg.max_reps_at_weight.get(s.weight_kg, 0)
            agg.max_reps_at_weight[s.weight_kg] = max(prev, s.reps)
        if s.e1rm is not None and (agg.best_e1rm_kg is None or s.e1rm > agg.best_e1rm_kg):
            agg.best_e1rm_kg, agg.e1rm_basis = s.e1rm, s.e1rm_basis
    agg.total_volume_kg = round(agg.total_volume_kg, 2)
    return agg


def mark_prs(aggs: list[SessionAggregate], *, assistance: bool = False) -> None:
    """Mark PRs over a chronologically sorted list of one variant's sessions.

    The first session is the baseline (no PRs). Rep PRs: more reps at a weight that has been
    used before. Weight PRs are not meaningful for assistance variants (less = harder).
    """
    best_e1rm: float | None = None
    best_weight: float | None = None
    reps_at: dict[float, int] = {}
    for i, a in enumerate(aggs):
        first = i == 0
        if a.best_e1rm_kg is not None:
            if best_e1rm is not None and a.best_e1rm_kg > best_e1rm + 1e-9:
                a.is_e1rm_pr = True
            best_e1rm = max(best_e1rm or 0.0, a.best_e1rm_kg)
        if not assistance and a.top_set_weight_kg:
            if best_weight is not None and a.top_set_weight_kg > best_weight + 1e-9 and not first:
                a.is_weight_pr = True
            best_weight = max(best_weight or 0.0, a.top_set_weight_kg)
        for w, r in sorted(a.max_reps_at_weight.items()):
            prev = reps_at.get(w)
            if prev is not None and r > prev:
                a.rep_prs.append({"weight_kg": w, "reps": r, "previous": prev})
            reps_at[w] = max(prev or 0, r)


def rebuild_stats(
    db: Session,
    *,
    bodyweight_kg: float | None = None,
    max_reps: int = 15,
    min_reps: int = 1,
    variant_ids: Collection[int] | None = None,
) -> int:
    """Recompute sets.e1rm_kg and exercise_session_stats (all variants, or just `variant_ids`:
    PRs are per variant, so a partial rebuild is exact). Returns the row count written.

    `bodyweight_kg` is only the fallback (engine config); the bodyweight log is used per session
    date via `bodyweight_at` semantics (latest on/before, else earliest after).
    """
    timeline = load_timeline(db, bodyweight_kg)
    variants = {
        v.id: (v, ex)
        for v, ex in db.execute(
            select(ExerciseVariant, Exercise).join(
                Exercise, ExerciseVariant.exercise_id == Exercise.id
            )
        )
    }
    q = (
        select(WorkoutSet, TrainingSession.started_at_local, TrainingSession.started_at_utc)
        .join(TrainingSession, WorkoutSet.session_id == TrainingSession.id)
        .where(WorkoutSet.deleted_at.is_(None), TrainingSession.deleted_at.is_(None))
    )
    if variant_ids is not None:
        q = q.where(WorkoutSet.variant_id.in_(list(variant_ids)))
    rows = db.execute(
        q.order_by(
            TrainingSession.started_at_utc,
            WorkoutSet.exercise_order,
            WorkoutSet.set_index,
        )
    ).all()

    grouped: dict[int, dict[str, tuple[date, list[SetLike]]]] = defaultdict(dict)
    for ws, local, _utc in rows:
        variant, ex = variants[ws.variant_id]
        ctx = E1RMContext(
            equipment_type=variant.equipment_type,
            load_convention=variant.load_convention,
            e1rm_meaningful=ex.e1rm_meaningful and not ex.is_timed,
            bodyweight_kg=timeline.at(local.date()),
            bodyweight_loaded=ex.bodyweight_loaded,
            max_reps=max_reps,
            min_reps=min_reps,
        )
        res = set_e1rm(ws.weight_kg, ws.reps, ws.set_type, ctx)
        value = res[0] if res else None
        if ws.e1rm_kg != value:
            ws.e1rm_kg = value
            # e1rm_kg is derived: keep the client-visible updated_at (no onupdate bump).
            flag_modified(ws, "updated_at")
        bucket = grouped[ws.variant_id].setdefault(ws.session_id, (local.date(), []))
        bucket[1].append(
            SetLike(
                weight_kg=ws.weight_kg,
                reps=ws.reps,
                set_type=ws.set_type,
                duration_s=ws.duration_s,
                e1rm=value,
                e1rm_basis=res[1] if res else None,
            )
        )

    stale = delete(ExerciseSessionStats)
    if variant_ids is not None:
        stale = stale.where(ExerciseSessionStats.variant_id.in_(list(variant_ids)))
    db.execute(stale)
    count = 0
    for variant_id, sessions in grouped.items():
        variant, _ex = variants[variant_id]
        aggs = [aggregate_session(sid, variant_id, d, sets) for sid, (d, sets) in sessions.items()]
        mark_prs(aggs, assistance=variant.load_convention == LoadConvention.ASSISTANCE)
        for a in aggs:
            db.add(
                ExerciseSessionStats(
                    session_id=a.session_id,
                    variant_id=a.variant_id,
                    session_date=a.session_date,
                    set_count=a.set_count,
                    working_set_count=a.working_set_count,
                    total_reps=a.total_reps,
                    total_volume_kg=a.total_volume_kg,
                    best_e1rm_kg=a.best_e1rm_kg,
                    e1rm_basis=a.e1rm_basis,
                    top_set_weight_kg=a.top_set_weight_kg,
                    top_set_reps=a.top_set_reps,
                    best_hold_s=a.best_hold_s,
                    is_e1rm_pr=a.is_e1rm_pr,
                    is_weight_pr=a.is_weight_pr,
                    rep_prs=a.rep_prs,
                )
            )
            count += 1
    db.flush()
    return count


def rebuild_with_config(db: Session, variant_ids: Collection[int] | None = None) -> int:
    """`rebuild_stats` with the active engine config (bodyweight fallback, e1RM rep limits)."""
    from app.engine.config import load_engine_config
    from app.settings import get_settings

    cfg = load_engine_config(get_settings().engine_config_path)
    return rebuild_stats(
        db,
        bodyweight_kg=cfg.user.bodyweight_kg,
        max_reps=cfg.strength.e1rm.max_reps,
        min_reps=cfg.strength.e1rm.min_reps,
        variant_ids=variant_ids,
    )
