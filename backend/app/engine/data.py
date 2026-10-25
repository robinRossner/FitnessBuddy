"""Database adapters for the engine: load plain-data `PlanInputs`, read the stored state.

Everything pure lives in the other engine modules; this file is the only place that knows the
tables (apart from `persist.py`, which writes).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AthleteMetric,
    DailyHealth,
    DecisionLog,
    ExerciseSessionStats,
    ExerciseVariant,
    Gym,
    PlannedSession,
    Run,
    TrainingSession,
    WorkoutSet,
)
from app.db.types import SessionKind, SetType
from app.engine import lifting as L
from app.engine import running as R
from app.engine.config import EngineConfig
from app.engine.planner import LiftContext, PlanInputs, VariantInfo
from app.engine.program import Program, apply_overrides
from app.engine.readiness import HealthDay
from app.engine.scheduling import WEEKDAYS
from app.sources.garmin.mapping import RUN_TYPES

PLAN_NS = uuid.UUID("5f0c3b52-71f1-4a6e-9c3e-3d1d0a7b9e11")
LOWER_KINDS = {SessionKind.LOWER.value, SessionKind.LEGS.value}
WORKING = (SetType.WORKING.value, SetType.FAILURE.value)


def planned_id(week_start: date, slot_id: str) -> str:
    """Stable client-visible id of a planned session: one per (week, template slot)."""
    return str(uuid.uuid5(PLAN_NS, f"{week_start.isoformat()}|{slot_id}"))


def template_slot_ids(template: dict[str, str]) -> list[str]:
    return [f"{wd}_{template[wd]}" for wd in WEEKDAYS if template.get(wd)]


# --------------------------------------------------------------------------- gyms / variants


def current_gym(db: Session, gyms_path: Path | None = None) -> Gym | None:
    """The gym in use now: latest entry of gyms.yaml, else the gym of the newest session."""
    if gyms_path is not None and gyms_path.exists():
        from app.gyms import load_gyms

        key = load_gyms(gyms_path).latest.key
        gym = db.scalar(select(Gym).where(Gym.key == key))
        if gym is not None:
            return gym
    gym_id = db.scalar(
        select(TrainingSession.gym_id)
        .where(TrainingSession.deleted_at.is_(None), TrainingSession.gym_id.is_not(None))
        .order_by(TrainingSession.started_at_utc.desc())
        .limit(1)
    )
    return db.get(Gym, gym_id) if gym_id else None


def resolve_variant(db: Session, base_name: str, gym: Gym | None) -> ExerciseVariant | None:
    """Generic variant `base_name`, or the gym-bound one named "<base_name> @ <gym>"."""
    names = [base_name] + ([f"{base_name} @ {gym.name}"] if gym else [])
    rows = list(
        db.scalars(
            select(ExerciseVariant).where(
                ExerciseVariant.name.in_(names), ExerciseVariant.deleted_at.is_(None)
            )
        )
    )
    rows.sort(key=lambda v: v.gym_id is None)  # gym-specific first
    return rows[0] if rows else None


def base_name_of(variant: ExerciseVariant) -> str:
    if variant.gym_id is not None and variant.gym is not None:
        suffix = f" @ {variant.gym.name}"
        if variant.name.endswith(suffix):
            return variant.name[: -len(suffix)]
    return variant.name


def variant_info(v: ExerciseVariant) -> VariantInfo:
    ex = v.exercise
    return VariantInfo(
        id=v.id,
        key=v.key,
        name=v.name,
        base_name=base_name_of(v),
        exercise_slug=ex.slug,
        exercise_name=ex.name,
        equipment_type=v.equipment_type,
        load_convention=v.load_convention,
        weight_ladder=[float(w) for w in v.weight_ladder] if v.weight_ladder else None,
        is_timed=bool(ex.is_timed),
    )


# --------------------------------------------------------------------------- history


def load_exposures(db: Session, variant_id: int, before: date | None = None) -> list[L.Exposure]:
    rows = db.execute(
        select(WorkoutSet, TrainingSession.started_at_local)
        .join(TrainingSession, WorkoutSet.session_id == TrainingSession.id)
        .where(
            WorkoutSet.variant_id == variant_id,
            WorkoutSet.deleted_at.is_(None),
            TrainingSession.deleted_at.is_(None),
            WorkoutSet.set_type.in_(WORKING),
        )
        .order_by(TrainingSession.started_at_local, WorkoutSet.exercise_order, WorkoutSet.set_index)
    ).all()
    by_session: dict[str, tuple[date, list[L.SetResult]]] = {}
    for ws, started in rows:
        if before is not None and started.date() >= before:
            continue
        _day, sets = by_session.setdefault(ws.session_id, (started.date(), []))
        sets.append(L.SetResult(ws.weight_kg, ws.reps, ws.duration_s))
    e1rm = {
        s.session_id: s.best_e1rm_kg
        for s in db.scalars(
            select(ExerciseSessionStats).where(ExerciseSessionStats.variant_id == variant_id)
        )
    }
    out = [L.Exposure(day, tuple(sets), e1rm.get(sid)) for sid, (day, sets) in by_session.items()]
    out.sort(key=lambda e: e.day)
    return out


def last_stall_reset(db: Session, variant_id: int) -> L.StallReset | None:
    """The most recent stall reset the engine decided for this variant (decisions_log)."""
    rows = db.scalars(
        select(DecisionLog)
        .where(
            DecisionLog.kind == "progression",
            DecisionLog.subject_type == "variant",
            DecisionLog.subject_id == str(variant_id),
        )
        .order_by(DecisionLog.id.desc())
        .limit(40)
    )
    for r in rows:
        out = r.output or {}
        if out.get("reset_day") and out.get("reset_load") is not None:
            return L.StallReset(date.fromisoformat(out["reset_day"]), float(out["reset_load"]))
    return None


def load_lift_contexts(db: Session, program: Program, gym: Gym | None) -> dict[str, LiftContext]:
    out: dict[str, LiftContext] = {}
    names = {e.variant for exs in program.days.values() for e in exs if e.active}
    for name in sorted(names):
        v = resolve_variant(db, name, gym)
        if v is None:
            out[name] = LiftContext(None)
            continue
        out[name] = LiftContext(
            variant_info(v), load_exposures(db, v.id), last_stall_reset(db, v.id)
        )
    return out


# --------------------------------------------------------------------------- health / runs


def load_health(db: Session, start: date, end: date) -> list[HealthDay]:
    rows = db.scalars(
        select(DailyHealth)
        .where(DailyHealth.day >= start, DailyHealth.day <= end, DailyHealth.source == "garmin")
        .order_by(DailyHealth.day)
    )
    return [
        HealthDay(
            day=r.day,
            resting_hr=r.resting_hr,
            hrv_ms=r.hrv_overnight_ms,
            hrv_status=r.hrv_status,
            sleep_s=r.sleep_s,
            sleep_score=r.sleep_score,
            hrv_baseline_low=r.hrv_baseline_low_ms,
            hrv_baseline_high=r.hrv_baseline_high_ms,
        )
        for r in rows
    ]


def _zone_floors(db: Session, day: date) -> tuple[float, ...] | None:
    floors = []
    for i in range(1, 6):
        v = db.scalar(
            select(AthleteMetric.value)
            .where(
                AthleteMetric.metric == f"hr_zone{i}_floor",
                AthleteMetric.source == "garmin",
                AthleteMetric.day <= day,
            )
            .order_by(AthleteMetric.day.desc())
            .limit(1)
        )
        if v is None:
            v = db.scalar(
                select(AthleteMetric.value)
                .where(
                    AthleteMetric.metric == f"hr_zone{i}_floor", AthleteMetric.source == "garmin"
                )
                .order_by(AthleteMetric.day.asc())
                .limit(1)
            )
        if v is None:
            return None
        floors.append(float(v))
    return tuple(floors)


def load_runs(db: Session, since: date, until: date | None = None) -> list[R.RunData]:
    q = (
        select(Run)
        .where(
            Run.deleted_at.is_(None),
            Run.activity_type.in_(RUN_TYPES),
            Run.started_at_local >= datetime.combine(since, datetime.min.time()),
            Run.distance_m.is_not(None),
        )
        .order_by(Run.started_at_local)
    )
    out = []
    for r in db.scalars(q):
        day = r.started_at_local.date()
        if until is not None and day > until:
            continue
        laps = tuple(
            R.LapData(
                lp.duration_s or 0.0, lp.distance_m or 0.0, lp.avg_hr, lp.max_hr, lp.intensity
            )
            for lp in sorted(r.laps, key=lambda x: x.lap_index)
        )
        out.append(
            R.RunData(
                id=r.id,
                day=day,
                distance_m=r.distance_m or 0.0,
                duration_s=r.duration_s or 0.0,
                avg_hr=r.avg_hr,
                max_hr=r.max_hr,
                laps=laps,
                time_in_zones_s=r.time_in_zones_s,
                zone_floors=_zone_floors(db, day) if r.time_in_zones_s else None,
                title=r.title,
            )
        )
    return out


def lower_days(db: Session, start: date, end: date) -> set[date]:
    rows = db.scalars(
        select(TrainingSession.started_at_local).where(
            TrainingSession.deleted_at.is_(None),
            TrainingSession.session_kind.in_(LOWER_KINDS),
            TrainingSession.started_at_local >= datetime.combine(start, datetime.min.time()),
            TrainingSession.started_at_local
            < datetime.combine(end + timedelta(days=1), datetime.min.time()),
        )
    )
    return {r.date() for r in rows}


# --------------------------------------------------------------------------- planned sessions


def frozen_slots(db: Session, week_start: date, template: dict[str, str]) -> set[str]:
    """Slots whose planned session is done / skipped / modified (or deleted): never regenerated."""
    out = set()
    for slot in template_slot_ids(template):
        row = db.get(PlannedSession, planned_id(week_start, slot))
        if row is not None and (
            row.deleted_at is not None or row.status in ("done", "skipped", "modified")
        ):
            out.add(slot)
    return out


def load_inputs(
    db: Session,
    *,
    today: date,
    week_start: date,
    cfg: EngineConfig,
    config_version: str,
    program: Program,
    gyms_path: Path | None = None,
) -> PlanInputs:
    gym = current_gym(db, gyms_path)
    program = apply_overrides(program, cfg.exercise_overrides)
    lookback = max(cfg.running_cfg.vdot.lookback_weeks, 8) + 4
    runs = load_runs(db, today - timedelta(weeks=lookback), today)
    return PlanInputs(
        today=today,
        week_start=week_start,
        cfg=cfg,
        config_version=config_version,
        program=program,
        lifts=load_lift_contexts(db, program, gym),
        health=load_health(db, today - timedelta(days=45), today),
        runs=runs,
        actual_lower_days=lower_days(db, week_start - timedelta(days=3), today),
        frozen_slots=frozen_slots(db, week_start, cfg.weekly_template),
        gym_name=gym.name if gym else None,
    )


# --------------------------------------------------------------------------- program derivation


def sessions_for_program(db: Session, since: date, until: date, gym: Gym | None) -> list:
    """Upper / Lower sessions in the window at the current gym, with their working blocks."""
    from app.engine.program import ExerciseUse, SessionUses

    q = (
        select(TrainingSession)
        .where(
            TrainingSession.deleted_at.is_(None),
            TrainingSession.session_kind.in_(["upper", "lower"]),
            TrainingSession.started_at_local >= datetime.combine(since, datetime.min.time()),
            TrainingSession.started_at_local
            < datetime.combine(until + timedelta(days=1), datetime.min.time()),
        )
        .order_by(TrainingSession.started_at_local)
    )
    out = []
    for s in db.scalars(q):
        if gym is not None and s.gym_id != gym.id:
            continue
        blocks: dict[tuple[int, int], list[WorkoutSet]] = defaultdict(list)
        for ws in s.sets:
            if ws.deleted_at is None:
                blocks[(ws.exercise_order, ws.block_occurrence)].append(ws)
        uses = []
        for pos, (_key, sets) in enumerate(sorted(blocks.items())):
            v = sets[0].variant
            work = sum(1 for x in sets if x.set_type in WORKING)
            if work == 0 or not v.active:
                continue
            uses.append(ExerciseUse(v.exercise.slug, base_name_of(v), work, pos))
        out.append(SessionUses(s.started_at_local.date(), s.session_kind, tuple(uses)))
    return out
