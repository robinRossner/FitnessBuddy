"""Shared FastAPI dependencies of the fitness routers: the request's database session, the
settings, and the mapping of service errors to HTTP errors."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException
from sqlalchemy.orm import Session

from apps.fitness.db.session import get_session_factory
from apps.fitness.errors import ServiceError
from apps.fitness.settings import Settings, get_settings


def get_db() -> Iterator[Session]:
    """One session per request; the service called by the handler commits."""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


def get_settings_dep() -> Settings:
    """The settings as a dependency (tests override it with `app.dependency_overrides`)."""
    return get_settings()


@contextmanager
def service_errors(*, value_error_status: int | None = None) -> Iterator[None]:
    """`ServiceError` -> HTTPException with the same status, message and headers; with
    `value_error_status`, a plain ValueError (e.g. weight-ladder validation) too."""
    try:
        yield
    except ServiceError as exc:
        raise HTTPException(exc.status, exc.message, exc.headers) from None
    except ValueError as exc:
        if value_error_status is None:
            raise
        raise HTTPException(value_error_status, str(exc)) from None
