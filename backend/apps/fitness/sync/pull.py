"""`GET /api/v1/sync/pull`: everything that changed since an opaque cursor.

The cursor is built on `server_seq`, a global monotonic counter stamped on every write by SQLite
triggers (migration 0005), so it is independent of client clocks and of which code path wrote.
Records of all tables are merged by seq and cut into pages of `PAGE_SIZE`.

Cursor forms (clients must treat them as opaque):
  ""                       empty: start a snapshot (history limited to HISTORY_DAYS)
  "s<pos>.<upper>.<yyyymmdd>"  snapshot in progress: continue after seq <pos>, stop at <upper>,
                           history window starts <yyyymmdd> (the filter must survive paging)
  "c<seq>"                 incremental: everything with a larger seq
When a snapshot finishes, the cursor becomes "c<upper>" (upper = the counter at snapshot start),
so old history that was filtered out never leaks into later incremental pulls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.fitness.db.catalog import region_of_muscle
from apps.fitness.db.models import (
    BodyMeasurement,
    Exercise,
    ExerciseVariant,
    Gym,
    PlannedSession,
    SyncSeq,
    TrainingSession,
    WorkoutSet,
)
from apps.fitness.db.types import iso_z, utcnow
from apps.fitness.engine import config_service as cs
from apps.fitness.engine import rest, service
from apps.fitness.engine.config import EngineConfig
from apps.fitness.engine.persist import planned_to_dict
from apps.fitness.engine.program import apply_overrides
from apps.fitness.errors import ServiceError
from apps.fitness.settings import Settings, get_settings

PAGE_SIZE = 2000
HISTORY_DAYS = 120

# Session kinds the app knows; the importer also stores finer kinds.
_KIND_OUT = {"push": "upper", "pull": "upper", "legs": "lower"}
_APP_KINDS = {"upper", "lower", "full", "other"}

_CURSOR = re.compile(r"^(?:c(?P<c>\d+)|s(?P<pos>\d+)\.(?P<upper>\d+)\.(?P<win>\d{8}))$")


class BadCursor(ValueError):
    pass


@dataclass(frozen=True)
class Cursor:
    pos: int
    upper: int | None = None  # set while a snapshot is being paged
    window_start: date | None = None

    @property
    def snapshot(self) -> bool:
        return self.upper is not None

    def encode(self) -> str:
        if self.snapshot:
            assert self.upper is not None and self.window_start is not None
            return f"s{self.pos}.{self.upper}.{self.window_start:%Y%m%d}"
        return f"c{self.pos}"


def parse_cursor(since: str | None, db: Session, now: datetime) -> Cursor:
    if not since:
        upper = db.scalar(select(func.max(SyncSeq.seq))) or 0
        return Cursor(0, upper, (now - timedelta(days=HISTORY_DAYS)).date())
    m = _CURSOR.match(since)
    if not m:
        raise BadCursor("malformed cursor")
    if m["c"] is not None:
        return Cursor(int(m["c"]))
    try:
        win = datetime.strptime(m["win"], "%Y%m%d").date()
    except ValueError:
        raise BadCursor("malformed cursor") from None
    return Cursor(int(m["pos"]), int(m["upper"]), win)


# --------------------------------------------------------------------------- serializers


def _val(x: Any) -> Any:
    return getattr(x, "value", x)


def session_out(s: TrainingSession) -> dict[str, Any]:
    kind = _val(s.session_kind)
    kind = _KIND_OUT.get(kind, kind if kind in _APP_KINDS else "other")
    ended = s.started_at_utc + timedelta(seconds=s.duration_s) if s.duration_s is not None else None
    return {
        "id": s.id,
        "planned_session_id": s.planned_session_id,
        "gym_id": s.gym_id,
        "kind": kind,
        "name": s.name or "",
        "started_at": iso_z(s.started_at_utc),
        "ended_at": iso_z(ended),
        "notes": s.notes,
        "updated_at": iso_z(s.updated_at),
        "deleted_at": iso_z(s.deleted_at),
    }


def set_out(s: WorkoutSet) -> dict[str, Any]:
    return {
        "id": s.id,
        "session_id": s.session_id,
        "variant_id": s.variant_id,
        "exercise_order": s.exercise_order,
        "set_order": s.set_index,
        "set_type": _val(s.set_type),
        "weight_kg": s.weight_kg,
        "reps": s.reps,
        "duration_s": s.duration_s,
        "completed_at": iso_z(s.completed_at),
        "notes": s.notes,
        "updated_at": iso_z(s.updated_at),
        "deleted_at": iso_z(s.deleted_at),
    }


def measurement_out(m: BodyMeasurement) -> dict[str, Any]:
    return {
        "id": m.id,
        "measured_at": iso_z(m.measured_at),
        "weight_kg": m.weight_kg,
        "note": m.note,
        "updated_at": iso_z(m.updated_at),
        "deleted_at": iso_z(m.deleted_at),
    }


def planned_out(p: PlannedSession, cfg: EngineConfig | None = None) -> dict[str, Any]:
    d = planned_to_dict(p, cfg)
    d["deleted_at"] = iso_z(p.deleted_at)
    return d


def exercise_out(e: Exercise) -> dict[str, Any]:
    return {
        "id": e.id,
        "name": e.name,
        "muscle_group": e.primary_muscle,
        "movement_pattern": e.movement_pattern,
        # additive (docs/api/exercises.md): for grouping pickers and the "new exercise" form
        "body_region": region_of_muscle(e.primary_muscle),
        "default_equipment": _val(e.default_equipment_type),
        "is_timed": bool(e.is_timed),
    }


def gym_out(g: Gym) -> dict[str, Any]:
    # deleted_at is additive to the base contract: retired gyms stay resolvable but are hidden
    return {"id": g.id, "name": g.name, "deleted_at": iso_z(g.deleted_at)}


def program_rep_ranges(settings: Settings, db: Session | None = None) -> dict[str, list[int]]:
    """Per-exercise rep range overrides from program.yaml, keyed by variant name without gym.
    With `db` the config's `exercise_overrides` apply (rep_range, and a renamed variant)."""
    try:
        prog = service.get_program(settings)
    except (FileNotFoundError, ValueError):
        return {}
    cfg = service.active_config(db, settings) if db is not None else None
    if cfg is not None:
        prog = apply_overrides(prog, cfg.exercise_overrides)
    return {
        e.variant: list(e.rep_range)
        for items in prog.days.values()
        for e in items
        if e.rep_range is not None
    }


def variant_out(v: ExerciseVariant, rep_ranges: dict[str, list[int]]) -> dict[str, Any]:
    return {
        "id": v.id,
        "exercise_id": v.exercise_id,
        "name": v.name,
        "gym_id": v.gym_id,
        "equipment_type": _val(v.equipment_type),
        "load_convention": _val(v.load_convention),
        "active": bool(v.active),
        "deleted_at": iso_z(v.deleted_at),
        "weight_ladder": v.weight_ladder,
        "increment_kg": v.increment_kg,
        "rep_range": rep_ranges.get(v.name.split(" @ ")[0]),
    }


def config_out(db: Session, settings: Settings) -> dict[str, Any]:
    try:
        cs.ensure_seeded(db, settings.engine_config_path)
        cfg, row = cs.load_active(db)
    except Exception:  # never fail a pull because of the config
        return {"version": "unknown", "dumbbell_increment_kg": 2.0, "rep_range": [7, 12]}
    inc = cfg.increments
    return {
        "version": row.version,
        "timezone": settings.user_tz,
        "dumbbell_increment_kg": inc.dumbbell_increment_kg,
        "rep_range": list(cfg.strength.rep_range),
        "rir_target": list(cfg.strength.rir_target),
        "increments_kg": {
            "barbell": inc.barbell_kg,
            "smith": inc.smith_kg,
            "plate_loaded": inc.plate_loaded_kg,
            "dumbbell": inc.dumbbell_increment_kg,
            "default_stack_step": inc.default_stack_step_kg,
            "bodyweight_added": inc.bodyweight_added_kg,
            "other": inc.other_kg,
        },
        # rest timers: the default and the per-exercise overrides, listed per variant id (the iOS
        # app reconciles its local overrides with this on every pull)
        "lifting": {
            "rest_default_s": cfg.lifting_cfg.rest_default_s,
            "rest_s_by_variant": rest.overrides_by_variant(db, cfg),
        },
        **gym_config(db, settings),
    }


def gym_config(db: Session, settings: Settings) -> dict[str, Any]:
    """The current gym and the gym list (docs/api/gyms.md); never fails a pull."""
    try:
        from apps.fitness.gyms import service as gym_service

        listing = gym_service.list_gyms(db, settings)
        return {"current_gym_id": listing["current_gym_id"], "gyms": listing["gyms"]}
    except Exception:
        return {"current_gym_id": None, "gyms": []}


# --------------------------------------------------------------------------- pull


def pull(
    db: Session,
    since: str | None,
    settings: Settings,
    *,
    limit: int = PAGE_SIZE,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utcnow()
    cur = parse_cursor(since, db, now)
    win_dt = (
        datetime.combine(cur.window_start, datetime.min.time(), tzinfo=UTC)
        if cur.snapshot
        else None
    )

    # (model, serializer key, extra snapshot filter)
    specs: list[tuple[type, str, Any]] = [
        (TrainingSession, "sessions", None),
        (WorkoutSet, "sets", None),
        (BodyMeasurement, "body_measurements", None),
        (PlannedSession, "planned_sessions", None),
        (ExerciseVariant, "variants", None),
        (Exercise, "exercises", None),
        (Gym, "gyms", None),
    ]
    found: list[tuple[int, str, Any]] = []
    for model, key, _ in specs:
        q = select(model).where(model.server_seq > cur.pos)
        if cur.upper is not None:
            q = q.where(model.server_seq <= cur.upper)
        if win_dt is not None:  # snapshot: limit history, catalog stays whole
            if model is TrainingSession:
                q = q.where(TrainingSession.started_at_utc >= win_dt)
            elif model is WorkoutSet:
                q = q.where(
                    WorkoutSet.session_id.in_(
                        select(TrainingSession.id).where(TrainingSession.started_at_utc >= win_dt)
                    )
                )
            elif model is PlannedSession:
                q = q.where(PlannedSession.day >= cur.window_start)
        q = q.order_by(model.server_seq).limit(limit + 1).execution_options(populate_existing=True)
        found.extend((row.server_seq, key, row) for row in db.scalars(q))

    found.sort(key=lambda t: t[0])
    has_more = len(found) > limit
    page = found[:limit]
    last = page[-1][0] if page else cur.pos
    if has_more:
        next_cur = Cursor(last, cur.upper, cur.window_start)
    elif cur.snapshot:
        next_cur = Cursor(cur.upper or 0)
    else:
        next_cur = Cursor(last)

    rep_ranges = program_rep_ranges(settings, db)
    plan_cfg = service.active_config(db, settings)
    out: dict[str, Any] = {
        "cursor": next_cur.encode(),
        "has_more": has_more,
        "server_time": iso_z(now),
        "sessions": [],
        "sets": [],
        "body_measurements": [],
        "planned_sessions": [],
        "variants": [],
        "exercises": [],
        "gyms": [],
    }
    ser = {
        "sessions": session_out,
        "sets": set_out,
        "body_measurements": measurement_out,
        "planned_sessions": lambda p: planned_out(p, plan_cfg),
        "variants": lambda v: variant_out(v, rep_ranges),
        "exercises": exercise_out,
        "gyms": gym_out,
    }
    for _seq, key, row in page:
        out[key].append(ser[key](row))
    out["config"] = config_out(db, settings)
    return out


def pull_page(
    db: Session, since: str | None, settings: Settings | None = None, *, limit: int = PAGE_SIZE
) -> dict[str, Any]:
    """`pull` for the API: a bad cursor is a ServiceError 400."""
    try:
        return pull(db, since, settings or get_settings(), limit=limit)
    except BadCursor as exc:
        raise ServiceError(400, str(exc)) from None
