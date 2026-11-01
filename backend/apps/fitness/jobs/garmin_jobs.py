"""Garmin sync jobs shared by the CLI and the scheduler."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from apps.fitness.analytics import cache as stats_cache
from apps.fitness.db.session import get_session_factory, session_scope
from apps.fitness.garmin_sync import GarminSync, SyncReport
from apps.fitness.settings import Settings, get_settings
from apps.fitness.sources.garmin.base import (
    ACTIVITIES,
    ALL_KINDS,
    DAILY_SUMMARY,
    HRV,
    SLEEP,
    GarminSource,
)
from apps.fitness.sources.garmin.factory import build_source
from apps.fitness.stats import rebuild_with_config

log = logging.getLogger(__name__)

# Daytime runs and the manual sync; nightly runs everything. HRV belongs here: last night's HRV
# lands after the 03:30 run, so only a daytime run can bring it in time for the morning.
LIGHT_KINDS = (ACTIVITIES, DAILY_SUMMARY, SLEEP, HRV)
_lock = threading.Lock()  # one sync at a time, whether CLI-in-process or scheduled


def on_new_data(report: SyncReport) -> None:
    """Hook: after new Garmin data, (1) auto-update max HR / LTHR in the engine config if Garmin
    reports different values (new config version, author=engine), then (2) re-run the engine
    (reconcile + plan current and next week). Never raises: a sync must not fail because of it."""
    stats_cache.invalidate()
    log.info(
        "new Garmin data (%d kinds updated); running the engine",
        sum(1 for r in report.results if r.changed),
    )
    try:
        from apps.fitness.engine import service

        with session_scope() as db:
            service.record_config_check(db)
        if get_settings().engine_auto_run:
            with session_scope() as db:
                summary = service.run_engine(db)
            log.info(
                "engine run: config %s, %d week(s) planned",
                summary.config_version,
                len(summary.weeks),
            )
            from apps.fitness.jobs.engine_jobs import push_runs_soon

            push_runs_soon("Garmin sync")
    except FileNotFoundError as exc:  # no program.yaml yet
        log.warning("engine not run: %s", exc)
    except Exception:
        log.exception("engine run after Garmin sync failed")


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
    cached_tokens_only: bool = False,
) -> SyncReport | None:
    """Incremental sync. Returns None if another sync is already running.
    `cached_tokens_only`: the login-based fallback may never do a fresh SSO login."""
    s = settings or get_settings()
    if not _lock.acquire(blocking=False):
        log.warning("Garmin sync already running; skipping")
        return None
    try:
        sync = GarminSync(
            get_session_factory(),
            source or build_source(s, cached_tokens_only=cached_tokens_only),
            s,
            progress=progress,
            rate_limit_waits=rate_limit_waits,
        )
        report = sync.sync(kinds)
        after_sync(report)
        return report
    finally:
        _lock.release()
