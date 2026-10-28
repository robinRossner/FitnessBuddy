"""Engine re-run after the iOS app pushed new training data (sibling of garmin_jobs.on_new_data)."""

from __future__ import annotations

import logging
import threading

from app.db.session import session_scope
from app.settings import get_settings

log = logging.getLogger(__name__)
_lock = threading.Lock()  # engine runs write plans: never two at once


def rerun_engine(reason: str = "sync push") -> None:
    """Reconcile + re-plan current and next week. Never raises: a push must not fail on this."""
    if not get_settings().engine_auto_run:
        return
    try:
        from app.engine import service

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


def push_runs_soon(reason: str) -> None:
    """Changed planned runs -> Garmin watch, in the background (only in the server process)."""
    try:
        from app.garmin_workouts import push_soon

        push_soon(reason)
    except Exception:
        log.exception("could not start the Garmin workout push")
