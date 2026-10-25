"""Garmin sync jobs shared by the CLI and the scheduler."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from app.db.session import get_session_factory, session_scope
from app.garmin_sync import GarminSync, SyncReport
from app.settings import Settings, get_settings
from app.sources.garmin.base import ACTIVITIES, ALL_KINDS, DAILY_SUMMARY, SLEEP, GarminSource
from app.sources.garmin.factory import build_source
from app.stats import rebuild_with_config

log = logging.getLogger(__name__)

LIGHT_KINDS = (ACTIVITIES, DAILY_SUMMARY, SLEEP)  # daytime runs; nightly runs everything
_lock = threading.Lock()  # one sync at a time, whether CLI-in-process or scheduled


def on_new_data(report: SyncReport) -> None:
    """Hook: run the progression/readiness engine after new data arrived. Placeholder."""
    log.info(
        "new Garmin data (%d kinds updated); engine hook not implemented yet",
        sum(1 for r in report.results if r.rows),
    )


def after_sync(report: SyncReport) -> None:
    """Post-processing shared by every sync entry point."""
    if report.weights_changed:  # bodyweight feeds e1RM of bodyweight-loaded lifts
        with session_scope() as db:
            n = rebuild_with_config(db)
        log.info("stats rebuilt after new Garmin weights (%d rows)", n)
    if report.new_data:
        on_new_data(report)


def run_sync(
    kinds: tuple[str, ...] = ALL_KINDS,
    *,
    settings: Settings | None = None,
    source: GarminSource | None = None,
    progress: Callable[[str], None] = lambda msg: None,
    rate_limit_waits: tuple[float, ...] = (),
) -> SyncReport | None:
    """Incremental sync. Returns None if another sync is already running."""
    s = settings or get_settings()
    if not _lock.acquire(blocking=False):
        log.warning("Garmin sync already running; skipping")
        return None
    try:
        sync = GarminSync(
            get_session_factory(),
            source or build_source(s),
            s,
            progress=progress,
            rate_limit_waits=rate_limit_waits,
        )
        report = sync.sync(kinds)
        after_sync(report)
        return report
    finally:
        _lock.release()
