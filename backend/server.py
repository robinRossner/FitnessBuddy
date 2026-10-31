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
from core.settings import get_core_settings

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


def create_app(docs_enabled: bool | None = None) -> FastAPI:
    """The shared app. `/docs`, `/redoc` and `/openapi.json` exist only with DOCS_ENABLED (dev)."""
    if docs_enabled is None:
        docs_enabled = get_core_settings().docs_enabled
    app = FastAPI(
        title="FitnessBuddy",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    include_fitness_routes(app)
    return app


app = create_app()
