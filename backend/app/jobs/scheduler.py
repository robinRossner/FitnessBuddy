"""APScheduler wiring: nightly full incremental Garmin sync + lighter daytime runs.

Started from the FastAPI lifespan only when SCHEDULER_ENABLED=true. Jobs never raise into the
scheduler: failures are logged (and recorded in `sync_state.last_error`).
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.jobs.garmin_jobs import LIGHT_KINDS, run_sync
from app.settings import Settings
from app.sources.garmin.base import ALL_KINDS

log = logging.getLogger(__name__)


def _hhmm(value: str) -> tuple[int, int]:
    hour, _, minute = value.strip().partition(":")
    return int(hour), int(minute or 0)


def garmin_job(kinds: tuple[str, ...]) -> None:
    try:
        run_sync(kinds)
    except Exception:
        log.exception("scheduled Garmin sync failed (kinds=%s)", ",".join(kinds))


def build_scheduler(settings: Settings) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=settings.scheduler_tz)
    h, m = _hhmm(settings.sync_nightly_time)
    sched.add_job(
        garmin_job,
        CronTrigger(hour=h, minute=m, timezone=settings.scheduler_tz),
        args=[ALL_KINDS],
        id="garmin_nightly",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
        replace_existing=True,
    )
    for t in (x for x in settings.sync_day_times.split(",") if x.strip()):
        h, m = _hhmm(t)
        sched.add_job(
            garmin_job,
            CronTrigger(hour=h, minute=m, timezone=settings.scheduler_tz),
            args=[LIGHT_KINDS],
            id=f"garmin_day_{h:02d}{m:02d}",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1800,
            replace_existing=True,
        )
    return sched
