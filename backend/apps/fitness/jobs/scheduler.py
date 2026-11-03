"""APScheduler wiring: nightly full incremental Garmin sync + lighter daytime runs, the push of
planned runs to the watch (evening + early morning), the Withings weigh-in import (only when
configured) and the advisor jobs (weekly review Sunday 20:00, daily trigger batch and outcome
check).

Started from the FastAPI lifespan only when SCHEDULER_ENABLED=true. Jobs never raise into the
scheduler: failures are logged (and recorded in `sync_state.last_error`).
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from apps.fitness.garmin.base import ALL_KINDS
from apps.fitness.jobs.garmin_jobs import DAY_KINDS, run_sync
from apps.fitness.settings import Settings
from core.scheduler import add_cron_job, hhmm

log = logging.getLogger(__name__)


def garmin_job(kinds: tuple[str, ...]) -> None:
    try:
        run_sync(kinds)
    except Exception:
        log.exception("scheduled Garmin sync failed (kinds=%s)", ",".join(kinds))


def withings_job() -> None:
    """Scheduled weigh-in import; a quiet no-op until the user has connected Withings."""
    from apps.fitness.jobs import withings_jobs

    try:
        outcome = withings_jobs.run_sync()
        log.debug("scheduled Withings sync: %s", outcome.status)
    except Exception:
        log.exception("scheduled Withings sync failed")


def add_withings_jobs(sched: BackgroundScheduler, settings: Settings) -> None:
    """Withings weigh-ins, polled a few times a day; only when Withings is configured."""
    if not settings.withings_configured:
        return
    for t in (x for x in settings.withings_sync_times.split(",") if x.strip()):
        h, m = hhmm(t)
        add_cron_job(
            sched,
            withings_job,
            job_id=f"withings_{h:02d}{m:02d}",
            hour=h,
            minute=m,
            tz=settings.scheduler_tz,
            misfire_grace_time=1800,
        )


def add_advisor_jobs(sched: BackgroundScheduler, settings: Settings) -> None:
    """Weekly review (Sunday), daily trigger batch and daily outcome check (jobs/advisor_jobs)."""
    from apps.fitness.jobs import advisor_jobs

    tz = settings.scheduler_tz
    h, m = hhmm(settings.advisor_weekly_time)
    add_cron_job(
        sched,
        advisor_jobs.weekly_review_job,
        job_id="advisor_weekly",
        hour=h,
        minute=m,
        tz=tz,
        day_of_week="sun",
        misfire_grace_time=6 * 3600,
    )
    h, m = hhmm(settings.advisor_trigger_time)
    add_cron_job(
        sched,
        advisor_jobs.trigger_job,
        job_id="advisor_triggers",
        hour=h,
        minute=m,
        tz=tz,
        misfire_grace_time=3600,
    )
    h, m = hhmm(settings.advisor_outcome_time)
    add_cron_job(
        sched,
        advisor_jobs.outcome_job,
        job_id="advisor_outcomes",
        hour=h,
        minute=m,
        tz=tz,
        misfire_grace_time=6 * 3600,
    )


def add_workout_push_jobs(sched: BackgroundScheduler, settings: Settings) -> None:
    """Planned runs -> Garmin watch (apps/fitness/garmin/workouts.py): in the evening so tomorrow
    morning's run is on the watch before bed, and early in the morning (after the nightly sync)
    so a readiness change still lands. The watch picks it up on its next phone sync."""
    if not settings.garmin_push_workouts:
        return
    from apps.fitness.garmin.workouts import push_job

    for t in (x for x in settings.garmin_push_times.split(",") if x.strip()):
        h, m = hhmm(t)
        add_cron_job(
            sched,
            push_job,
            job_id=f"garmin_workouts_{h:02d}{m:02d}",
            hour=h,
            minute=m,
            tz=settings.scheduler_tz,
            misfire_grace_time=1800,
        )


def build_scheduler(settings: Settings) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=settings.scheduler_tz)
    add_advisor_jobs(sched, settings)
    add_workout_push_jobs(sched, settings)
    add_withings_jobs(sched, settings)
    h, m = hhmm(settings.sync_nightly_time)
    add_cron_job(
        sched,
        garmin_job,
        job_id="garmin_nightly",
        hour=h,
        minute=m,
        tz=settings.scheduler_tz,
        args=[ALL_KINDS],
        misfire_grace_time=3600,
    )
    for t in (x for x in settings.sync_day_times.split(",") if x.strip()):
        h, m = hhmm(t)
        add_cron_job(
            sched,
            garmin_job,
            job_id=f"garmin_day_{h:02d}{m:02d}",
            hour=h,
            minute=m,
            tz=settings.scheduler_tz,
            args=[DAY_KINDS],
            misfire_grace_time=1800,
        )
    return sched
