"""FastAPI entry point: `uv run uvicorn apps.fitness.main:app --reload`."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.fitness import __version__
from apps.fitness.api.bodyweight import router as bodyweight_router
from apps.fitness.api.coach import router as coach_router
from apps.fitness.api.engine import router as engine_router
from apps.fitness.api.exercises import router as exercises_router
from apps.fitness.api.garmin import router as garmin_router
from apps.fitness.api.gyms import router as gyms_router
from apps.fitness.api.routes import public_router, router
from apps.fitness.api.stats import router as stats_router
from apps.fitness.api.sync import public as sync_public
from apps.fitness.api.sync import router as sync_router
from apps.fitness.settings import get_settings

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    scheduler = None
    if settings.scheduler_enabled:
        from apps.fitness.jobs.scheduler import build_scheduler

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
app.include_router(exercises_router)
app.include_router(gyms_router)
app.include_router(stats_router)
app.include_router(coach_router)
app.include_router(sync_public)
app.include_router(sync_router)
