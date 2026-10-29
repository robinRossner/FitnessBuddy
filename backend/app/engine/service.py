"""Engine entry points used by the CLI, the Garmin hook and the API."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ExerciseVariant, PlannedSession
from app.engine import config_service as cs
from app.engine import data as D
from app.engine import persist as P
from app.engine import running as R
from app.engine.config import EngineConfig
from app.engine.decisions import log_decision
from app.engine.hr import zones_from_config
from app.engine.makeup import MakeupSummary, handle_missed
from app.engine.planner import WeekPlan, generate_week
from app.engine.program import (
    Program,
    derive_program,
    load_program,
    render_program,
    seed_live_program,
    write_program_text,
)
from app.engine.readiness import Readiness, score_day
from app.engine.rest import annotate_session
from app.settings import Settings, get_settings

log = logging.getLogger(__name__)


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def user_today(settings: Settings | None = None) -> date:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo((settings or get_settings()).user_tz)).date()


def get_program(settings: Settings | None = None) -> Program:
    """The live program (next to the database), seeded from the repo's program.yaml on first use."""
    s = settings or get_settings()
    seed_live_program(s.live_program_path, s.program_path)
    return load_program(s.live_program_path)


@dataclass
class WeekResult:
    plan: WeekPlan
    persisted: P.PersistResult | None = None


@dataclass
class RunSummary:
    config_version: str
    reconciled: dict[str, int] = field(default_factory=dict)
    makeups: MakeupSummary = field(default_factory=MakeupSummary)
    weeks: list[WeekResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        """Short JSON of an engine run (`POST /api/v1/engine/run`, docs/api/engine.md)."""
        return {
            "config_version": self.config_version,
            "reconciled": dict(self.reconciled),
            "makeups": [
                {"kind": m.kind, "missed": m.missed_day.isoformat(), "action": m.action}
                for m in self.makeups.results
            ],
            "weeks": [_week_json(w) for w in self.weeks],
        }


def _run_json(d) -> dict | None:
    p = d.prescription or {}
    if d.kind == "intervals" and p.get("structure"):
        w = p["structure"].get("work") or {}
        return {
            "date": d.day.isoformat(),
            "kind": d.kind,
            "session_type": p["structure"].get("session_type"),
            "reps": w.get("reps"),
            "distance_m": w.get("distance_m"),
            "pace_label": w.get("pace_label"),
        }
    if d.kind in ("easy_run", "long_run"):
        pace = p.get("pace") or {}
        return {
            "date": d.day.isoformat(),
            "kind": d.kind,
            "distance_km": p.get("distance_km"),
            "pace_label": pace.get("label"),
            "pace_basis": pace.get("basis"),
        }
    return None


def _week_json(w: WeekResult) -> dict:
    run = w.plan.running or {}
    easy = run.get("easy_pace") or {}
    reps = run.get("vdot_from_intervals") or {}
    r = w.persisted
    return {
        "week_start": w.plan.week_start.isoformat(),
        "persisted": (
            {"created": r.created, "updated": r.updated, "unchanged": r.unchanged}
            if r is not None
            else None
        ),
        "quality_vdot": run.get("vdot"),
        "vdot_reason": run.get("vdot_reason"),
        "vdot_from_intervals": {"vdot": reps.get("vdot"), "reason": reps.get("reason")},
        "easy_pace": {
            "basis": easy.get("basis"),
            "vdot": easy.get("vdot"),
            "reason": easy.get("reason"),
        },
        "runs": [x for x in (_run_json(d) for d in w.plan.days) if x is not None],
    }


def plan_week(
    db: Session,
    *,
    today: date,
    week_start: date,
    settings: Settings | None = None,
    program: Program | None = None,
) -> tuple[WeekPlan, EngineConfig, int]:
    """Compute (never write) the plan for the week starting `week_start`."""
    s = settings or get_settings()
    cs.ensure_seeded(db, s.engine_config_path)
    cfg, row = cs.load_active(db)
    inputs = D.load_inputs(
        db,
        today=today,
        week_start=week_start,
        cfg=cfg,
        config_version=row.version,
        program=program or get_program(s),
        gyms_path=s.gyms_path,
    )
    return generate_week(inputs), cfg, row.id


def run_engine(
    db: Session,
    *,
    today: date | None = None,
    weeks: int = 2,
    settings: Settings | None = None,
    program: Program | None = None,
) -> RunSummary:
    """Apply: reconcile earlier plans, then (re)generate the current and next week and persist."""
    s = settings or get_settings()
    today = today or user_today(s)
    cs.ensure_seeded(db, s.engine_config_path)
    summary = RunSummary(config_version=cs.get_active(db).version)
    summary.reconciled, missed = P.reconcile_detail(db, today)
    if missed:
        cfg, row = cs.load_active(db)
        summary.makeups = handle_missed(
            db, today=today, missed=missed, cfg=cfg, config_version_id=row.id
        )
    prog = program or get_program(s)
    for i in range(weeks):
        ws = monday(today) + timedelta(weeks=i)
        summary.weeks.append(apply_week(db, today=today, week_start=ws, settings=s, program=prog))
    return summary


def apply_week(
    db: Session,
    *,
    today: date,
    week_start: date,
    settings: Settings | None = None,
    program: Program | None = None,
    only_days: set[date] | None = None,
) -> WeekResult:
    s = settings or get_settings()
    plan, _cfg, cv_id = plan_week(
        db, today=today, week_start=week_start, settings=s, program=program
    )
    gym = D.current_gym(db, s.gyms_path)
    res = P.persist_week(
        db, plan, config_version_id=cv_id, gym_id=gym.id if gym else None, only_days=only_days
    )
    return WeekResult(plan, res)


@dataclass
class CancelResult:
    week_start: date
    status: str  # cancelled | already_cancelled | no_deload
    message: str
    reason: str | None = None
    retest: list[str] = field(default_factory=list)  # lifts that retest their old load now
    replanned: bool = False

    def as_dict(self) -> dict:
        return {
            "week_start": self.week_start.isoformat(),
            "status": self.status,
            "changed": self.status == "cancelled",
            "message": self.message,
            "reason": self.reason,
            "retest": self.retest,
            "replanned": self.replanned,
        }


def cancel_deload(
    db: Session,
    *,
    week_start: date,
    reason: str | None = None,
    today: date | None = None,
    replan: bool = True,
    settings: Settings | None = None,
    program: Program | None = None,
) -> CancelResult:
    """The user cancels the soft deload of a week (multi-lift stall, fatigue index): that week's
    lifts get their normal sets and the lifts the deload was going to retest do so at once, at
    their old loads. Stored as a `deload_cancel` decision (author user) that the window loader
    honours, so later engine runs keep the week cancelled. Idempotent: a week that is already
    cancelled, or that has no deload, changes nothing. With `replan`, the week's plan is
    regenerated and persisted (what the next `engine run` would do)."""
    s = settings or get_settings()
    today = today or user_today(s)
    ws = monday(week_start)
    cs.ensure_seeded(db, s.engine_config_path)
    cfg, _row = cs.load_active(db)
    if ws in D.load_cancelled_weeks(db):
        return CancelResult(
            ws, "already_cancelled", f"The deload of the week of {ws} is already cancelled."
        )
    found = [
        w
        for w in D.load_windows(db, cfg, ws - timedelta(days=30))
        if D.cancel_window(w, ws) is not None
    ]
    if not found:  # not stored yet: would the engine create one for this week?
        try:
            plan, _c, _id = plan_week(db, today=today, week_start=ws, settings=s, program=program)
        except FileNotFoundError:
            plan = None
        for d in plan.decisions if plan else []:
            win = (d.output or {}).get("window")
            if d.kind == "deload" and win and win.get("start"):
                w = D._window_from(win)
                if D.cancel_window(w, ws) is not None:
                    found.append(w)
    if not found:
        return CancelResult(
            ws,
            "no_deload",
            f"No lifting deload to cancel in the week of {ws} (nothing was changed).",
        )
    names = sorted(
        {v.name for w in found for k in w.retest if (v := db.get(ExerciseVariant, k)) is not None}
    )
    why = f" ({reason})" if reason else ""
    log_decision(
        db,
        kind=D.DELOAD_CANCEL,
        rule="deload_cancel",
        subject_type="week",
        subject_id=ws.isoformat(),
        inputs={"windows": [_window_json(w) for w in found]},
        output={"author": "user", "week_start": ws.isoformat(), "reason": reason},
        rationale=f"The user cancelled the deload of the week of {ws}{why}: normal sets, "
        "stalled lifts retest their old loads this week.",
    )
    res = CancelResult(
        ws,
        "cancelled",
        f"Deload of the week of {ws} cancelled: normal sets"
        + (f"; {len(names)} lifts retest their old loads now" if names else ""),
        reason,
        names,
    )
    if replan:
        try:
            apply_week(db, today=today, week_start=ws, settings=s, program=program)
            res.replanned = True
        except FileNotFoundError:
            pass
    return res


def _window_json(w) -> dict:
    from app.engine.planner import _window_dict

    return _window_dict(w)


def readiness_for(db: Session, day: date, settings: Settings | None = None) -> Readiness:
    s = settings or get_settings()
    cs.ensure_seeded(db, s.engine_config_path)
    cfg, _ = cs.load_active(db)
    health = D.load_health(db, day - timedelta(days=45), day)
    return score_day(day, health, cfg.readiness_cfg)


def evaluate_runs(
    db: Session, *, today: date, weeks: int = 6, settings: Settings | None = None
) -> list[dict]:
    """Evaluate the recent runs: easy-run pass/fail + efficiency + decoupling, interval sessions."""
    s = settings or get_settings()
    cs.ensure_seeded(db, s.engine_config_path)
    cfg, _ = cs.load_active(db)
    zones = zones_from_config(cfg)
    rc = cfg.running_cfg
    out = []
    for r in D.load_runs(db, today - timedelta(weeks=weeks), today):
        sp = R.split_intervals(r.laps)
        item = {
            "date": r.day.isoformat(),
            "km": round(r.km, 2),
            "pace_s_per_km": round(r.pace) if r.pace else None,
            "avg_hr": r.avg_hr,
            "title": r.title,
        }
        if sp is not None:
            item["type"] = "intervals"
            item["evaluation"] = R.evaluate_intervals(sp, zones, rc.intervals).as_dict()
        else:
            item["type"] = "easy/long"
            item["evaluation"] = R.evaluate_easy_run(r, zones, rc.easy).as_dict()
        out.append(item)
    return out


def generate_program(
    db: Session,
    *,
    today: date | None = None,
    weeks: int = 8,
    settings: Settings | None = None,
    write: bool = True,
) -> tuple[Program, str]:
    s = settings or get_settings()
    today = today or user_today(s)
    gym = D.current_gym(db, s.gyms_path)
    sessions = D.sessions_for_program(db, today - timedelta(weeks=weeks), today, gym)
    program = derive_program(sessions)
    text = render_program(program, gym=gym.name if gym else None)
    if write:
        write_program_text(s.live_program_path, text)
    return program, text


# --------------------------------------------------------------------------- API helpers


def active_config(db: Session, settings: Settings) -> EngineConfig | None:
    """The active engine config (seeded if the database has none); None if there is none."""
    try:
        cs.ensure_seeded(db, settings.engine_config_path)
        return cs.load_active(db)[0]
    except Exception:  # a plan is still shown without the config-dependent fields
        log.warning("no active engine config for the plan", exc_info=True)
        return None


def stored_day(db: Session, day: date, cfg: EngineConfig | None = None) -> list[dict]:
    rows = db.scalars(
        select(PlannedSession)
        .where(PlannedSession.day == day, PlannedSession.deleted_at.is_(None))
        .order_by(PlannedSession.kind)
    )
    return [P.planned_to_dict(r, cfg) for r in rows]


def plan_for_api(
    db: Session, start: date, days: int, *, settings: Settings | None = None
) -> list[dict]:
    """Stored planned sessions for each day; days without any are filled by a dry-run plan."""
    s = settings or get_settings()
    today = user_today(s)
    per_day: dict[date, list[dict]] = {start + timedelta(days=i): [] for i in range(days)}
    cfg = active_config(db, s)
    for d in per_day:
        per_day[d] = stored_day(db, d, cfg)
    missing = [d for d, v in per_day.items() if not v and d >= today]
    if missing:
        try:
            for ws in sorted({monday(d) for d in missing}):
                plan, _, _ = plan_week(db, today=today, week_start=ws, settings=s)
                for dp in plan.days:
                    if dp.day in missing:
                        item = dp.as_dict()
                        item.update(
                            {
                                "id": None,
                                "status": "preview",
                                "session": annotate_session(dp.prescription, cfg)
                                if cfg
                                else dp.prescription,
                                **P.adaptive_fields(dp.kind, "preview", dp.prescription),
                            }
                        )
                        per_day[dp.day].append(item)
        except FileNotFoundError:
            pass
    return [{"date": d.isoformat(), "sessions": v} for d, v in sorted(per_day.items())]


def record_config_check(db: Session) -> None:
    """Hook helper: auto-update HR numbers from Garmin."""
    new = cs.apply_garmin_hr(db)
    if new is not None:
        log.info("config %s created: %s", new.version, new.reason)
