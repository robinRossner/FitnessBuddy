"""Engine / session factory. SQLite runs in WAL mode with foreign keys enforced."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from apps.fitness.settings import get_settings
from core.db import make_engine as core_make_engine


def make_engine(url: str | None = None) -> Engine:
    return core_make_engine(url or get_settings().database_url)


_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _factory
    if _factory is None:
        _factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _factory


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """One transaction for an entry point (CLI command, job, background thread): commit on
    success, roll back on error, always close. `factory` defaults to the app's session factory;
    requests get theirs from `api.deps.get_db` instead."""
    session = (factory or get_session_factory())()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
