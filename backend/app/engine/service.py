"""Engine entry points used by the CLI, the Garmin hook and the API."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PlannedSession
from app.engine import config_service as cs
from app.engine import data as D
from app.engine import persist as P
from app.engine import running as R
from app.engine.config import EngineConfig
from app.engine.hr import zones_from_config
from app.engine.makeup import MakeupSummary, handle_missed
from app.engine.planner import WeekPlan, generate_week
from app.engine.program import Program, derive_program, load_program, render_program
from app.engine.readiness import Readiness, score_day
from app.settings import Settings, get_settings

log = logging.getLogger(__name__)


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def user_today(settings: Settings | None = None) -> date:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo((settings or get_settings()).user_tz)).date()


def get_program(settings: Settings | None = None) -> Program:
    path = (settings or get_settings()).program_path
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist: run `python -m app.cli engine program generate`"
        )
    return load_program(path)


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
        s.program_path.parent.mkdir(parents=True, exist_ok=True)
        s.program_path.write_text(text, encoding="utf-8")
    return program, text


# --------------------------------------------------------------------------- API helpers


def stored_day(db: Session, day: date) -> list[dict]:
    rows = db.scalars(
        select(PlannedSession)
        .where(PlannedSession.day == day, PlannedSession.deleted_at.is_(None))
        .order_by(PlannedSession.kind)
    )
    return [P.planned_to_dict(r) for r in rows]


def plan_for_api(
    db: Session, start: date, days: int, *, settings: Settings | None = None
) -> list[dict]:
    """Stored planned sessions for each day; days without any are filled by a dry-run plan."""
    s = settings or get_settings()
    today = user_today(s)
    per_day: dict[date, list[dict]] = {start + timedelta(days=i): [] for i in range(days)}
    for d in per_day:
        per_day[d] = stored_day(db, d)
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
                                "session": dp.prescription,
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
