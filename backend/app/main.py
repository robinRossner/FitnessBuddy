"""FastAPI entry point: `uv run uvicorn app.main:app --reload`."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.bodyweight import router as bodyweight_router
from app.api.engine import router as engine_router
from app.api.garmin import router as garmin_router
from app.api.routes import public_router, router
from app.api.sync import public as sync_public
from app.api.sync import router as sync_router
from app.settings import get_settings

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    scheduler = None
    if settings.scheduler_enabled:
        from app.jobs.scheduler import build_scheduler

        scheduler = build_scheduler(settings)
        scheduler.start()
        log.info("scheduler started (%d jobs)", len(scheduler.get_jobs()))
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


app = FastAPI(title="FitnessBuddy", version=__version__, lifespan=lifespan)
app.include_router(public_router)
app.include_router(router)
app.include_router(bodyweight_router)
app.include_router(garmin_router)
app.include_router(engine_router)
app.include_router(sync_public)
app.include_router(sync_router)
