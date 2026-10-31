"""Mounts the fitness API on the shared FastAPI app.

Every fitness route is served under two prefixes with the same handlers and the same bearer auth:

- `/api/v1/...`          the original URLs; the installed iOS app still uses them, keep them.
- `/api/v1/fitness/...`  the app-scoped URLs (one backend serves several apps).

`/health` (public, no prefix) stays as it was; `/api/v1/health` and `/api/v1/fitness/health` are
the public connectivity checks of the sync API.
"""

from __future__ import annotations

from fastapi import FastAPI

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

LEGACY_PREFIX = "/api/v1"
FITNESS_PREFIX = "/api/v1/fitness"

# Order matters for route matching; unchanged from the single-app server.
_API_ROUTERS = (
    router,
    bodyweight_router,
    garmin_router,
    engine_router,
    exercises_router,
    gyms_router,
    stats_router,
    coach_router,
    sync_public,
    sync_router,
)


def include_fitness_routes(app: FastAPI) -> None:
    app.include_router(public_router)
    for prefix in (LEGACY_PREFIX, FITNESS_PREFIX):
        for api_router in _API_ROUTERS:
            app.include_router(api_router, prefix=prefix)
