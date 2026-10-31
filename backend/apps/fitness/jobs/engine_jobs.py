"""Engine re-run after the iOS app pushed new training data (sibling of garmin_jobs.on_new_data)."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from apps.fitness.db.session import session_scope
from apps.fitness.settings import get_settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from apps.fitness.engine.service import RunSummary

log = logging.getLogger(__name__)
_lock = threading.Lock()  # engine runs write plans: never two at once


def rerun_engine(reason: str = "sync push") -> None:
    """Reconcile + re-plan current and next week. Never raises: a push must not fail on this."""
    if not get_settings().engine_auto_run:
        return
    try:
        from apps.fitness.engine import service

        with _lock, session_scope() as db:
            summary = service.run_engine(db)
        log.info(
            "engine re-run after %s: config %s, %d week(s) planned",
            reason,
            summary.config_version,
            len(summary.weeks),
        )
        push_runs_soon(f"engine re-run after {reason}")
    except FileNotFoundError as exc:  # no program.yaml yet
        log.warning("engine not run: %s", exc)
    except Exception:
        log.exception("engine re-run after %s failed", reason)


def run_engine_now(db: Session, reason: str = "API") -> RunSummary:
    """Reconcile + re-plan current and next week on `db` right now (the explicit
    `POST /api/v1/engine/run`: runs even when ENGINE_AUTO_RUN is off, raises on errors), then the
    Garmin workout push hook like every other re-run. Returns the `RunSummary`."""
    from apps.fitness.engine import service

    with _lock:
        summary = service.run_engine(db)
        db.commit()
    log.info(
        "engine run (%s): config %s, %d week(s) planned",
        reason,
        summary.config_version,
        len(summary.weeks),
    )
    push_runs_soon(f"engine run ({reason})")
    return summary


def push_runs_soon(reason: str) -> None:
    """Changed planned runs -> Garmin watch, in the background (only in the server process)."""
    try:
        from apps.fitness.garmin_workouts import push_soon

        push_soon(reason)
    except Exception:
        log.exception("could not start the Garmin workout push")
