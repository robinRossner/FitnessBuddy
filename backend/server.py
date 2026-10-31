"""FastAPI entry point of the shared backend: `uv run uvicorn server:app --reload`.

One process, one SQLite-backed deployment, several personal apps (fitness, receipts). Each app
mounts its routes (apps/<name>/router.py) and registers its scheduler jobs; auth, settings and
scheduler plumbing come from core/. Receipts additionally runs its extraction queue worker as a
daemon thread (apps/receipts/worker.py).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.fitness import __version__
from apps.fitness.router import include_fitness_routes
from apps.fitness.settings import get_settings
from apps.receipts.router import include_receipts_routes
from core.settings import get_core_settings

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    scheduler = None
    if settings.scheduler_enabled:
        from apps.fitness.jobs.scheduler import build_scheduler
        from apps.receipts.jobs import add_receipts_jobs
        from apps.receipts.settings import get_settings as get_receipts_settings

        scheduler = build_scheduler(settings)
        add_receipts_jobs(scheduler, get_receipts_settings())
        scheduler.start()
        log.info("scheduler started (%d jobs)", len(scheduler.get_jobs()))
    receipts_worker = _start_receipts_worker()
    try:
        yield
    finally:
        if receipts_worker is not None:
            receipts_worker.stop()
        if scheduler is not None:
            scheduler.shutdown(wait=False)


def _start_receipts_worker():
    """The receipts extraction worker thread; a failure here must not stop the other apps."""
    from apps.receipts.settings import get_settings as get_receipts_settings

    if not get_receipts_settings().receipts_worker_enabled:
        return None
    try:
        from apps.receipts import worker
        from apps.receipts.db.session import get_session_factory

        receipts_worker = worker.ExtractionWorker(get_session_factory())
        receipts_worker.start()
        worker.set_worker(receipts_worker)
        return receipts_worker
    except Exception:
        log.exception("receipts worker did not start")
        return None


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
    include_receipts_routes(app)
    return app


app = create_app()
