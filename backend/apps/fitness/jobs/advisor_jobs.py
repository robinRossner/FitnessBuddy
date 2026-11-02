"""Advisor jobs: the Sunday weekly review, the daily trigger batch, the daily outcome check and
the background run of an "Ask coach" question. None of them raises into the scheduler / a request:
problems are logged and end up in the consultation's `status` / `error`.
"""

from __future__ import annotations

import logging

from apps.fitness.db import session as dbsession
from apps.fitness.settings import Settings, get_settings

log = logging.getLogger(__name__)


def run_consultation(consultation_id: int, settings: Settings | None = None) -> None:
    """Background task of POST /coach/ask (also used to re-run a waiting consultation)."""
    from apps.fitness.advisor import service

    s = settings or get_settings()
    try:
        with dbsession.get_session_factory()() as db:
            service.execute(db, consultation_id, settings=s)
    except Exception:
        log.exception("advisor consultation %s crashed", consultation_id)


def weekly_review_job(settings: Settings | None = None) -> None:
    """Sunday 20:00 (Europe/Vienna): one weekly consultation, triggers included."""
    from apps.fitness.advisor import service

    s = settings or get_settings()
    try:
        with dbsession.get_session_factory()() as db:
            service.recover_stale(db, s)
            c = service.run_weekly_review(db, s)
            log.info("weekly advisor review: %s", c.status if c else "nothing to do")
    except Exception:
        log.exception("weekly advisor review failed")


def trigger_job(settings: Settings | None = None) -> None:
    """Daily (not Sundays: the weekly review takes the open triggers): batch open triggers."""
    from apps.fitness.advisor import service

    s = settings or get_settings()
    try:
        with dbsession.get_session_factory()() as db:
            service.recover_stale(db, s)
            service.retry_awaiting_key(db, s)
            service.retry_usage_limited(db, s)
            if service.user_today(s).weekday() == 6:
                return
            c = service.run_trigger_batch(db, s)
            log.info("advisor trigger batch: %s", c.status if c else "nothing to do")
    except Exception:
        log.exception("advisor trigger batch failed")


def outcome_job(settings: Settings | None = None) -> None:
    """Daily: record how changes applied 21-28 days ago turned out."""
    from apps.fitness.advisor import outcomes

    s = settings or get_settings()
    try:
        with dbsession.get_session_factory()() as db:
            made = outcomes.run_outcome_check(db, s)
            log.info("advisor outcome check: %d outcome(s) recorded", len(made))
    except Exception:
        log.exception("advisor outcome check failed")
