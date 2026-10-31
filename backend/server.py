"""FastAPI entry point of the shared backend: `uv run uvicorn server:app --reload`.

One process, one SQLite-backed deployment, several personal apps. Each app mounts its routes
(apps/<name>/router.py) and registers its scheduler jobs; auth, settings and scheduler plumbing
come from core/. Only the fitness app exists so far.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.fitness import __version__
from apps.fitness.router import include_fitness_routes
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
include_fitness_routes(app)
