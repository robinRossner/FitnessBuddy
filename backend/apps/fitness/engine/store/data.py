"""Database adapters for the engine: load plain-data `PlanInputs`, read the stored state.

Everything pure lives in `engine.core`; this file is the only place that knows the
tables (apart from `persist.py`, which writes).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.db.models import (
    AthleteMetric,
    DailyHealth,
    DecisionLog,
    Exercise,
    ExerciseSessionStats,
    ExerciseVariant,
    Gym,
    PlannedSession,
    Run,
    TrainingSession,
    WorkoutSet,
)
from apps.fitness.db.types import EquipmentType, SessionKind, SetType
from apps.fitness.engine.core import lifting as L
from apps.fitness.engine.core import quality as Q
from apps.fitness.engine.core import running as R
from apps.fitness.engine.core import vdot as V
from apps.fitness.engine.core.config import EngineConfig
from apps.fitness.engine.core.ids import base_name_of, planned_id, template_slot_ids
from apps.fitness.engine.core.planner import LiftContext, PlanInputs, VariantInfo
from apps.fitness.engine.core.program import Program, apply_overrides
from apps.fitness.engine.core.readiness import HealthDay
from apps.fitness.engine.core.scheduling import SlotLock
from apps.fitness.engine.core.windows import DELOAD_CANCEL, apply_cancellations
from apps.fitness.engine.core.windows import window_from as _window_from
from apps.fitness.garmin.mapping import RUN_TYPES

LOWER_KINDS = {SessionKind.LOWER.value, SessionKind.LEGS.value}
WORKING = (SetType.WORKING.value, SetType.FAILURE.value)


# --------------------------------------------------------------------------- gyms / variants


def current_gym(db: Session, gyms_path: Path | None = None) -> Gym | None:
    """The gym in use now: the one chosen in the app (config `gym.current_id`), else the latest
    entry of gyms.yaml, else the gym of the newest session."""
    from apps.fitness.gyms.service import configured_current_gym

    chosen = configured_current_gym(db)
    if chosen is not None:
        return chosen
    if gyms_path is not None and gyms_path.exists():
        from apps.fitness.gyms.catalog import load_gyms

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


def resolve_variant(
    db: Session, base_name: str, gym: Gym | None, exercise_slug: str | None = None
) -> ExerciseVariant | None:
    """Generic variant `base_name`, or the machine at `gym`: named "<base_name> @ <gym>", else
    (given `exercise_slug`) the active variant of that exercise at the gym, so a renamed machine
    still resolves. When the generic variant exists, only a gym variant with the same equipment
    replaces it (a picked "Incline Bench Press (Dumbbell)" stays dumbbell at a gym with a Smith).
    Gym-specific variants must be active (a deactivated machine is "not set up")."""
    names = [base_name] + ([f"{base_name} @ {gym.name}"] if gym else [])
    rows = list(
        db.scalars(
            select(ExerciseVariant).where(
                ExerciseVariant.name.in_(names), ExerciseVariant.deleted_at.is_(None)
            )
        )
    )
    rows = [v for v in rows if v.gym_id is None or v.active]
    rows.sort(key=lambda v: v.gym_id is None)  # gym-specific first
    if (not rows or rows[0].gym_id is None) and gym is not None and exercise_slug:
        here = list(
            db.scalars(
                select(ExerciseVariant)
                .join(Exercise, Exercise.id == ExerciseVariant.exercise_id)
                .where(
                    Exercise.slug == exercise_slug,
                    ExerciseVariant.gym_id == gym.id,
                    ExerciseVariant.deleted_at.is_(None),
                    ExerciseVariant.active.is_(True),
                )
                .order_by(ExerciseVariant.id)
            )
        )
        if rows:
            here = [v for v in here if v.equipment_type == rows[0].equipment_type]
        if here:
            return here[0]
    return rows[0] if rows else None


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
        progression="time" if ex.is_timed else (ex.progression or "load"),
        increment_kg=float(v.increment_kg) if v.increment_kg else None,
    )


# --------------------------------------------------------------------------- history


def load_exposures(db: Session, variant_id: int, before: date | None = None) -> list[L.Exposure]:
    rows = db.execute(
        select(WorkoutSet, TrainingSession.started_at_local, TrainingSession.gym_id)
        .join(TrainingSession, WorkoutSet.session_id == TrainingSession.id)
        .where(
            WorkoutSet.variant_id == variant_id,
            WorkoutSet.deleted_at.is_(None),
            TrainingSession.deleted_at.is_(None),
            WorkoutSet.set_type.in_(WORKING),
        )
        .order_by(TrainingSession.started_at_local, WorkoutSet.exercise_order, WorkoutSet.set_index)
    ).all()
    by_session: dict[str, tuple[date, int | None, list[L.SetResult]]] = {}
    variant = db.get(ExerciseVariant, variant_id)
    # a weight-less set of a loaded exercise is a placeholder ticked without logging (the app now
    # refuses those): it says nothing about the load, so it never counts as an exposure
    loaded = variant is not None and (
        variant.equipment_type != EquipmentType.BODYWEIGHT and not variant.exercise.is_timed
    )
    for ws, started, gym_id in rows:
        if before is not None and started.date() >= before:
            continue
        if loaded and ws.weight_kg is None:
            continue
        _day, _gym, sets = by_session.setdefault(ws.session_id, (started.date(), gym_id, []))
        sets.append(L.SetResult(ws.weight_kg, ws.reps, ws.duration_s))
    e1rm = {
        s.session_id: s.best_e1rm_kg
        for s in db.scalars(
            select(ExerciseSessionStats).where(ExerciseSessionStats.variant_id == variant_id)
        )
    }
    out = [
        L.Exposure(day, tuple(sets), e1rm.get(sid), gym)
        for sid, (day, gym, sets) in by_session.items()
    ]
    out.sort(key=lambda e: e.day)
    return out


def _progression_rows(db: Session, variant_id: int, limit: int = 400) -> list[DecisionLog]:
    return list(
        db.scalars(
            select(DecisionLog)
            .where(
                DecisionLog.kind == "progression",
                DecisionLog.subject_type == "variant",
                DecisionLog.subject_id == str(variant_id),
            )
            .order_by(DecisionLog.id.desc())
            .limit(limit)
        )
    )


def load_ladder(db: Session, variant_id: int) -> list[L.LadderEvent]:
    """The stall-ladder steps the engine decided for this variant (decisions_log
    `output.ladder`), one per (day, step), oldest first."""
    seen: dict[tuple[str, int], L.LadderEvent] = {}
    for r in _progression_rows(db, variant_id):
        lad = (r.output or {}).get("ladder")
        if not lad or not lad.get("day"):
            continue
        key = (lad["day"], int(lad["step"]))
        if key in seen:
            continue
        seen[key] = L.LadderEvent(
            date.fromisoformat(lad["day"]),
            int(lad["step"]),
            float(lad["load"]) if lad.get("load") is not None else None,
            lad.get("peak_e1rm"),
            lad.get("from_load"),
        )
    return sorted(seen.values(), key=lambda e: (e.day, e.step))


def load_cancelled_weeks(db: Session) -> dict[date, str | None]:
    """Weeks (Mondays) whose deload the user cancelled (`engine deload cancel`) -> the reason."""
    out: dict[date, str | None] = {}
    for r in db.scalars(
        select(DecisionLog).where(DecisionLog.kind == DELOAD_CANCEL).order_by(DecisionLog.id)
    ):
        try:
            out[date.fromisoformat(r.subject_id or "")] = (r.output or {}).get("reason")
        except ValueError:
            continue
    return out


def load_windows(db: Session, cfg: EngineConfig, since: date) -> list[L.DeloadWindow]:
    """Deload windows (multi-lift stall, fatigue index) the engine decided, plus the converted
    legacy -10 % stall resets (engine v1) that were never trained."""
    out: dict[tuple[str, str], L.DeloadWindow] = {}
    for r in db.scalars(
        select(DecisionLog).where(DecisionLog.kind == "deload").order_by(DecisionLog.id.desc())
    ):
        w = (r.output or {}).get("window")
        if not w or not w.get("start"):
            continue
        win = _window_from(w)
        if win.until < since:
            continue
        out.setdefault((w["start"], win.reason), win)
    windows = list(out.values())
    windows += [w for w in legacy_windows(db, cfg) if (w.start.isoformat(), w.reason) not in out]
    windows = apply_cancellations(windows, load_cancelled_weeks(db))
    return sorted(windows, key=lambda w: w.start)


def legacy_windows(db: Session, cfg: EngineConfig) -> list[L.DeloadWindow]:
    """Engine v1 planned `stall_reset` (-10 %, `output.reset_day/reset_load`). Every such reset
    that was not trained yet becomes a retest of the OLD load; when at least
    `strength.multi_stall_lifts` share the reset day, a soft deload week comes first (the v2
    multi-lift-stall rule). Derived on every run, so it needs no stored state."""
    latest: dict[str, DecisionLog] = {}
    for r in db.scalars(
        select(DecisionLog)
        .where(DecisionLog.kind == "progression", DecisionLog.subject_type == "variant")
        .order_by(DecisionLog.id.desc())
    ):
        out = r.output or {}
        if out.get("rule") != "stall_reset" or not out.get("reset_day") or "ladder" in out:
            continue
        latest.setdefault(r.subject_id or "", r)
    by_day: dict[date, dict[int, L.Retest]] = defaultdict(dict)
    for sid, r in latest.items():
        if not sid.isdigit():
            continue
        day = date.fromisoformat(r.output["reset_day"])
        trained = db.scalar(
            select(TrainingSession.id)
            .join(WorkoutSet, WorkoutSet.session_id == TrainingSession.id)
            .where(
                WorkoutSet.variant_id == int(sid),
                WorkoutSet.deleted_at.is_(None),
                TrainingSession.deleted_at.is_(None),
                TrainingSession.started_at_local >= datetime.combine(day, datetime.min.time()),
            )
            .limit(1)
        )
        if trained is not None:
            continue
        last = (r.inputs or {}).get("last") or {}
        sets = [s for s in last.get("sets") or [] if s.get("reps") is not None]
        if not sets:
            continue
        load = max(float(s.get("weight") or 0.0) for s in sets)
        reps = tuple(
            int(s["reps"]) for s in sets if abs(float(s.get("weight") or 0.0) - load) < 1e-6
        )
        by_day[day][int(sid)] = L.Retest(load, reps, last.get("best_e1rm"))
    st = cfg.strength
    out = []
    for day, retest in sorted(by_day.items()):
        if len(retest) >= st.multi_stall_lifts:
            out.append(
                L.DeloadWindow(
                    day, day + timedelta(days=st.deload.days - 1), "legacy_reset", retest
                )
            )
        else:
            out.append(
                L.DeloadWindow(day, day - timedelta(days=1), "legacy_reset", retest, deload=False)
            )
    return out


def load_lift_contexts(db: Session, program: Program, gym: Gym | None) -> dict[str, LiftContext]:
    out: dict[str, LiftContext] = {}
    from apps.fitness.gyms.service import previous_hint

    names = {e.variant: e.exercise for exs in program.days.values() for e in exs if e.active}
    for name in sorted(names):
        v = resolve_variant(db, name, gym, names[name])
        if v is None:
            out[name] = LiftContext(None)
            continue
        history = load_exposures(db, v.id)
        hint = previous_hint(db, v) if not history else None
        out[name] = LiftContext(
            variant_info(v),
            history,
            load_ladder(db, v.id),
            (hint["gym_name"], hint["weight_kg"], hint["reps"]) if hint else None,
        )
    return out


# --------------------------------------------------------------------------- running state


def load_quality_log(db: Session, since: date, until: date) -> list[Q.QualityRecord]:
    """Prescribed quality sessions (planned `intervals`) from the stored plans. Sessions of the
    v1 engine (no `session_type`) count as `vo2max`."""
    out = []
    for p in db.scalars(
        select(PlannedSession)
        .where(
            PlannedSession.kind == "intervals",
            PlannedSession.deleted_at.is_(None),
            PlannedSession.day >= since,
            PlannedSession.day < until,
        )
        .order_by(PlannedSession.day)
    ):
        sess = (p.prescription or {}).get("session") or {}
        st = sess.get("structure") or {}
        work = st.get("work") or {}
        if not work.get("reps") or not work.get("distance_m"):
            continue
        rec = st.get("recovery") or {}
        out.append(
            Q.QualityRecord(
                p.day,
                st.get("session_type") or "vo2max",
                int(work["reps"]),
                float(work["distance_m"]),
                float(rec["duration_s"]) if rec.get("duration_s") else None,
                float(work["pace_s_per_km"]) if work.get("pace_s_per_km") else None,
            )
        )
    return out


def load_vdot_history(db: Session) -> list[tuple[V.VdotRecord, str | None]]:
    """The VDOT records of the decision log with their reason (the decision's rationale), oldest
    first; the last one is the VDOT state the planner uses."""
    out = []
    for r in db.scalars(
        select(DecisionLog)
        .where(DecisionLog.kind == "running", DecisionLog.subject_type == "vdot")
        .order_by(DecisionLog.id)
    ):
        rec = (r.output or {}).get("vdot_record")
        if not rec:
            continue
        out.append(
            (
                V.VdotRecord(
                    date.fromisoformat(rec["day"]),
                    float(rec["vdot"]),
                    rec.get("source") or "initial",
                    float(rec.get("delta") or 0.0),
                ),
                r.rationale,
            )
        )
    # one record per (day, source): the latest wins
    dedup = {(x.day, x.source): (x, why) for x, why in out}
    return sorted(dedup.values(), key=lambda p: p[0].day)


def load_vdot_log(db: Session) -> list[V.VdotRecord]:
    return [rec for rec, _why in load_vdot_history(db)]


def load_cycle_start(db: Session) -> date | None:
    for r in db.scalars(
        select(DecisionLog)
        .where(DecisionLog.kind == "running", DecisionLog.subject_type == "week")
        .order_by(DecisionLog.id)
    ):
        ps = (r.output or {}).get("phase_state")
        if ps and ps.get("cycle_start"):
            return date.fromisoformat(ps["cycle_start"])
    return None


def load_last_step_back(db: Session, before: date) -> date | None:
    """Monday of the latest week whose (latest) long-run decision was a step-back."""
    latest: dict[str, bool] = {}
    for r in db.scalars(
        select(DecisionLog)
        .where(DecisionLog.kind == "running", DecisionLog.subject_type == "slot")
        .order_by(DecisionLog.id.desc())
        .limit(400)
    ):
        out = r.output or {}
        if out.get("rule") != "running:long_run_progression" or not out.get("week_start"):
            continue
        latest.setdefault(out["week_start"], bool(out.get("step_back")))
    weeks = [date.fromisoformat(w) for w, sb in latest.items() if sb]
    weeks = [w for w in weeks if w < before]
    return max(weeks) if weeks else None


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


def latest_zone_floors(db: Session) -> tuple[float, ...] | None:
    """Garmin's current zone floors (what upcoming sessions are planned on)."""
    return _zone_floors(db, date.max)


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
                zone_floors=_zone_floors(db, day),
                title=r.title,
                elevation_gain_m=r.elevation_gain_m,
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


_KIND_ALIASES_FINAL = {"upper", "lower", "easy_run", "intervals", "long_run", "rest"}


def lock_from_row(row: PlannedSession) -> SlotLock | None:
    """The stored placement of a planned session (None if it follows the template)."""
    raw = row.schedule_lock
    if not raw:
        return None
    return SlotLock(
        day=date.fromisoformat(raw["day"]) if raw.get("day") else row.day,
        kind=raw.get("kind") if raw.get("kind") in _KIND_ALIASES_FINAL else None,
        distance_km=raw.get("distance_km"),
        moved_from=row.moved_from,
        reason=row.change_reason,
        source=row.changed_by or raw.get("source") or "engine",
    )


def slot_placements(
    db: Session, week_start: date, template: dict[str, str]
) -> tuple[dict[str, SlotLock], set[str]]:
    """(locks, excluded slots) of a week from the stored planned sessions. A skipped or deleted
    session carries no training load, so it is left out of the recovery-rule maths."""
    locks: dict[str, SlotLock] = {}
    excluded: set[str] = set()
    for slot in template_slot_ids(template):
        row = db.get(PlannedSession, planned_id(week_start, slot))
        if row is None:
            continue
        if row.deleted_at is not None or row.status == "skipped":
            excluded.add(slot)
            continue
        lk = lock_from_row(row)
        if lk is not None:
            locks[slot] = lk
    return locks, excluded


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
    from apps.fitness.bodyweight.service import bodyweight_at

    gym = current_gym(db, gyms_path)
    program = apply_overrides(program, cfg.exercise_overrides)
    cycle_start = load_cycle_start(db)
    weeks = max(cfg.running_cfg.vdot.lookback_weeks, 8) + 4
    since = today - timedelta(weeks=weeks)
    if cycle_start is not None:
        since = min(since, cycle_start - timedelta(days=7))
    runs = load_runs(db, since, today)
    locks, excluded = slot_placements(db, week_start, cfg.weekly_template)
    return PlanInputs(
        today=today,
        week_start=week_start,
        cfg=cfg,
        config_version=config_version,
        program=program,
        lifts=load_lift_contexts(db, program, gym),
        health=load_health(db, today - timedelta(days=120), today),
        runs=runs,
        actual_lower_days=lower_days(db, today - timedelta(weeks=8), today),
        frozen_slots=frozen_slots(db, week_start, cfg.weekly_template),
        slot_locks=locks,
        excluded_slots=excluded,
        gym_name=gym.name if gym else None,
        windows=load_windows(db, cfg, today - timedelta(days=90)),
        cancelled_weeks=load_cancelled_weeks(db),
        quality_log=load_quality_log(db, since, today),
        vdot_log=load_vdot_log(db),
        cycle_start=cycle_start,
        last_step_back=load_last_step_back(db, week_start),
        bodyweight_kg=bodyweight_at(db, today, cfg.user.bodyweight_kg),
        zone_floors=latest_zone_floors(db),
    )


# --------------------------------------------------------------------------- program derivation


def sessions_for_program(db: Session, since: date, until: date, gym: Gym | None) -> list:
    """Upper / Lower sessions in the window at the current gym, with their working blocks."""
    from apps.fitness.engine.core.program import ExerciseUse, SessionUses

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
