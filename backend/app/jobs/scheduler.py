"""APScheduler wiring: nightly full incremental Garmin sync + lighter daytime runs, the push of
planned runs to the watch (evening + early morning) and the advisor jobs (weekly review Sunday
20:00, daily trigger batch and outcome check).

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


def add_advisor_jobs(sched: BackgroundScheduler, settings: Settings) -> None:
    """Weekly review (Sunday), daily trigger batch and daily outcome check (jobs/advisor_jobs)."""
    from app.jobs import advisor_jobs

    tz = settings.scheduler_tz
    h, m = _hhmm(settings.advisor_weekly_time)
    sched.add_job(
        advisor_jobs.weekly_review_job,
        CronTrigger(day_of_week="sun", hour=h, minute=m, timezone=tz),
        id="advisor_weekly",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=6 * 3600,
        replace_existing=True,
    )
    h, m = _hhmm(settings.advisor_trigger_time)
    sched.add_job(
        advisor_jobs.trigger_job,
        CronTrigger(hour=h, minute=m, timezone=tz),
        id="advisor_triggers",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
        replace_existing=True,
    )
    h, m = _hhmm(settings.advisor_outcome_time)
    sched.add_job(
        advisor_jobs.outcome_job,
        CronTrigger(hour=h, minute=m, timezone=tz),
        id="advisor_outcomes",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=6 * 3600,
        replace_existing=True,
    )


def add_workout_push_jobs(sched: BackgroundScheduler, settings: Settings) -> None:
    """Planned runs -> Garmin watch (app/garmin_workouts): in the evening so tomorrow morning's
    run is on the watch before bed, and early in the morning (after the nightly sync) so a
    readiness change still lands. The watch picks it up on its next phone sync."""
    if not settings.garmin_push_workouts:
        return
    from app.garmin_workouts import push_job

    for t in (x for x in settings.garmin_push_times.split(",") if x.strip()):
        h, m = _hhmm(t)
        sched.add_job(
            push_job,
            CronTrigger(hour=h, minute=m, timezone=settings.scheduler_tz),
            id=f"garmin_workouts_{h:02d}{m:02d}",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1800,
            replace_existing=True,
        )


def build_scheduler(settings: Settings) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=settings.scheduler_tz)
    add_advisor_jobs(sched, settings)
    add_workout_push_jobs(sched, settings)
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
