"""Sync + catalog API for the iOS app. Contract and examples: docs/api/sync.md.

POST /api/v1/sync/push                 last-write-wins upserts of sessions / sets / weigh-ins
GET  /api/v1/sync/pull?since=<cursor>  changes since the cursor (+ catalog, plan, config)
GET  /api/v1/variants/{id}/history     previous performance of one variant
GET  /api/v1/health                    connectivity check (no auth)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from apps.fitness import __version__
from apps.fitness.api.deps import get_db, get_settings_dep, service_errors
from apps.fitness.jobs.engine_jobs import rerun_engine
from apps.fitness.settings import Settings
from apps.fitness.sync import history as history_mod
from apps.fitness.sync import pull as pull_mod
from apps.fitness.sync import push as push_mod
from apps.fitness.sync.schemas import PushRequest, PushResponse
from core.auth import require_token

log = logging.getLogger(__name__)

public = APIRouter()
router = APIRouter(dependencies=[Depends(require_token)])


@public.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.post("/sync/push", response_model=PushResponse)
def sync_push(
    body: PushRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> PushResponse:
    # the engine re-run happens after the response; failures are logged inside
    return push_mod.push(
        db, body, settings, replan=lambda reason: background.add_task(rerun_engine, reason)
    )


@router.get("/sync/pull")
def sync_pull(
    since: str = Query("", description="cursor from the previous pull; empty = snapshot"),
    limit: int = Query(pull_mod.PAGE_SIZE, ge=1, le=5000, description="page size (records)"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    with service_errors():
        return pull_mod.pull_page(db, since, settings, limit=limit)


@router.get("/variants/{variant_id}/history")
def variant_history(
    variant_id: int,
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    with service_errors():
        return history_mod.checked_variant_history(db, variant_id, limit)
