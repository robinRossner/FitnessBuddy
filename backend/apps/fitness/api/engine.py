"""Plan / readiness / config endpoints for the iOS app (bearer token like the other write APIs).

Plan JSON (one entry per planned session; ids are stable per (week, template slot)):

    {"id", "date", "kind": upper|lower|easy_run|intervals|long_run|rest, "status", "title",
     "readiness": {"color","known","reason"} | null, "adjustments": [..], "notes": [..],
     "session": {"type": "lifting", "exercises": [{"name","variant_id","rep_range",
                   "sets": [{"index","set_type","target_weight","target_reps"}],
                   "last": {"date","sets":[{"weight","reps"}]}, "rule","reason","flags"}]}
               | {"type": "easy_run"|"long_run", "distance_km","hr":{"low","high"},"pace":{..}}
               | {"type": "intervals", "structure": {"warmup","work","recovery","cooldown"}, ..}
               | {"type": "rest", "optional", "original"?}}

GET /plan/today also carries `readiness_today` (same object as /stats/health: level, headline,
metrics, details; contract in docs/api/stats.md) next to the engine's `readiness`, and
`coach: {pending_count, latest_pending_id}` (coach proposals waiting for a decision;
docs/api/coach.md) for the Today banner.

Days without a stored plan get a `status: "preview"` entry with `id: null` (computed on the fly).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.fitness.advisor import service as service_advisor
from apps.fitness.analytics import health
from apps.fitness.api.routes import get_db
from apps.fitness.engine.store import config_service as cs
from apps.fitness.engine.store import service
from apps.fitness.jobs import engine_jobs
from apps.fitness.settings import get_settings
from core.auth import require_token

router = APIRouter(dependencies=[Depends(require_token)])


def _today() -> date:
    return service.user_today(get_settings())


@router.get("/plan/today")
def plan_today(db: Session = Depends(get_db)) -> dict:
    today = _today()
    day = service.plan_for_api(db, today, 1)[0]
    r = service.readiness_for(db, today)
    return {
        "date": today.isoformat(),
        "readiness": r.as_dict(),
        "readiness_today": health.readiness_today(db, today, r),
        "sessions": day["sessions"],
        "coach": service_advisor.pending_summary(db),
    }


@router.get("/plan/week")
def plan_week(
    start: date | None = Query(None, description="any date; the week (Mon-Sun) containing it"),
    db: Session = Depends(get_db),
) -> dict:
    ws = service.monday(start or _today())
    return {"start": ws.isoformat(), "days": service.plan_for_api(db, ws, 7)}


@router.get("/readiness")
def readiness(
    date_: date | None = Query(None, alias="date"), db: Session = Depends(get_db)
) -> dict:
    return service.readiness_for(db, date_ or _today()).as_dict()


def _version(v) -> dict:
    return {
        "id": v.id,
        "version": v.version,
        "is_active": v.is_active,
        "author": v.author,
        "reason": v.reason,
        "parent_id": v.parent_id,
        "diff": v.diff,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


@router.get("/config")
def get_config(db: Session = Depends(get_db)) -> dict:
    cs.ensure_seeded(db, get_settings().engine_config_path)
    active = cs.get_active(db)
    return {
        "active": {**_version(active), "content": active.content},
        "history": [_version(v) for v in cs.history(db)],
    }


@router.post("/config/rollback/{version}")
def rollback_config(version: str, db: Session = Depends(get_db)) -> dict:
    try:
        row = cs.rollback(db, version, author="user")
    except cs.ConfigError as exc:
        raise HTTPException(404, str(exc)) from None
    db.commit()
    return {"active": _version(row)}


class DeloadCancel(BaseModel):
    week_start: date  # any date; the week (Mon-Sun) containing it
    reason: str | None = None


@router.post("/engine/deload/cancel")
def cancel_deload(body: DeloadCancel, db: Session = Depends(get_db)) -> dict:
    """Cancel the lifting deload of a week (docs/api/engine.md). Idempotent."""
    try:
        res = service.cancel_deload(
            db, week_start=body.week_start, reason=(body.reason or "").strip() or None
        )
    except cs.ConfigError as exc:
        raise HTTPException(409, str(exc)) from None
    db.commit()
    return res.as_dict()


@router.post("/engine/run")
def run_engine(db: Session = Depends(get_db)) -> dict:
    """Re-run the engine for the current and the next week now (docs/api/engine.md): reconcile,
    re-plan, persist, then the Garmin workout push hook. Idempotent on unchanged data."""
    try:
        summary = engine_jobs.run_engine_now(db, "API")
    except FileNotFoundError as exc:  # no program.yaml yet
        raise HTTPException(409, str(exc)) from None
    return summary.as_dict()
