"""Sync + catalog API for the iOS app. Contract and examples: docs/api/sync.md.

POST /api/v1/sync/push                 last-write-wins upserts of sessions / sets / weigh-ins
GET  /api/v1/sync/pull?since=<cursor>  changes since the cursor (+ catalog, plan, config)
GET  /api/v1/variants/{id}/history     previous performance of one variant
GET  /api/v1/health                    connectivity check (no auth)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import __version__
from app.api.auth import require_token
from app.api.routes import get_db
from app.db.models import ExerciseVariant
from app.db.types import iso_z, utcnow
from app.jobs.engine_jobs import rerun_engine
from app.settings import get_settings
from app.sync import history as history_mod
from app.sync import pull as pull_mod
from app.sync import push as push_mod
from app.sync.schemas import PushRequest, PushResponse

log = logging.getLogger(__name__)

public = APIRouter(prefix="/api/v1")
router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])


@public.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.post("/sync/push", response_model=PushResponse)
def sync_push(
    body: PushRequest, background: BackgroundTasks, db: Session = Depends(get_db)
) -> PushResponse:
    out = push_mod.apply_push(db, body, get_settings())
    push_mod.finish_push(db, out)
    db.commit()
    if out.training_changed:  # after the response; failures are logged inside
        background.add_task(rerun_engine, "sync push")
    return PushResponse(server_time=iso_z(utcnow()) or "", results=out.results)


@router.get("/sync/pull")
def sync_pull(
    since: str = Query("", description="cursor from the previous pull; empty = snapshot"),
    limit: int = Query(pull_mod.PAGE_SIZE, ge=1, le=5000, description="page size (records)"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        return pull_mod.pull(db, since, get_settings(), limit=limit)
    except pull_mod.BadCursor as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/variants/{variant_id}/history")
def variant_history(
    variant_id: int,
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    if db.get(ExerciseVariant, variant_id) is None:
        raise HTTPException(404, "variant not found")
    return history_mod.variant_history(db, variant_id, limit)
