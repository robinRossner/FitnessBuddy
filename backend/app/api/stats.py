"""Pre-aggregated stats for the iOS Progress and Health dashboards. Contract: docs/api/stats.md.

GET /api/v1/stats/lifts                 one summary per lift variant with history
GET /api/v1/stats/lifts/{variant_id}    summary + per-session points + PRs (?from=&to=)
GET /api/v1/stats/volume/weekly         lifting + running load per week
GET /api/v1/stats/running               weekly km / zones, efficiency, VDOT, interval sessions
GET /api/v1/runs/{run_id}/analysis      one run: laps vs plan + the engine's verdict
GET /api/v1/stats/health                daily health + readiness, bodyweight, summaries
GET /api/v1/stats/correlations          three scatter plots with Pearson r
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.analytics import cache, correlations, health, lifts, run_analysis, running, volume
from app.api.auth import require_token
from app.api.routes import get_db

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])

Weeks = Annotated[int, Query(ge=1, le=520, description="number of weeks, ending this week")]
Days = Annotated[int, Query(ge=1, le=3650, description="number of days, ending today")]


@router.get("/stats/lifts")
def stats_lifts(db: Session = Depends(get_db)) -> dict[str, Any]:
    return cache.cached(db, "lifts", (), lambda: lifts.lifts_payload(db))


@router.get("/stats/lifts/{variant_id}")
def stats_lift(
    variant_id: int,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    out = cache.cached(
        db, "lift", (variant_id, from_, to), lambda: lifts.lift_detail(db, variant_id, from_, to)
    )
    if out is None:
        raise HTTPException(404, "no lift history for this variant")
    return out


@router.get("/stats/volume/weekly")
def stats_volume(weeks: Weeks = 26, db: Session = Depends(get_db)) -> dict[str, Any]:
    return cache.cached(db, "volume", (weeks,), lambda: volume.weekly_volume(db, weeks))


@router.get("/stats/running")
def stats_running(weeks: Weeks = 26, db: Session = Depends(get_db)) -> dict[str, Any]:
    return cache.cached(db, "running", (weeks,), lambda: running.running_stats(db, weeks))


@router.get("/runs/{run_id}/analysis")
def run_analysis_(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    out = cache.cached(db, "run", (run_id,), lambda: run_analysis.run_analysis(db, run_id))
    if out is None:
        raise HTTPException(404, "run not found")
    return out


@router.get("/stats/health")
def stats_health(days: Days = 90, db: Session = Depends(get_db)) -> dict[str, Any]:
    return cache.cached(db, "health", (days,), lambda: health.health_stats(db, days))


@router.get("/stats/correlations")
def stats_correlations(days: Days = 120, db: Session = Depends(get_db)) -> dict[str, Any]:
    return cache.cached(db, "corr", (days,), lambda: correlations.correlations(db, days))
