"""Scheduling of the advisor jobs (weekly review Sunday 20:00 Europe/Vienna, daily trigger batch,
daily outcome check) and what the jobs do."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from apps.fitness.advisor import claude, service
from apps.fitness.db.models import AdvisorConsultation, AdvisorTrigger
from apps.fitness.engine import config_service as cs
from apps.fitness.jobs import advisor_jobs
from apps.fitness.jobs.scheduler import build_scheduler
from apps.fitness.settings import Settings
from tests.support.fakes_anthropic import FakeClient
from tests.support.world import TODAY

VIENNA = ZoneInfo("Europe/Vienna")


@pytest.fixture
def jobs():
    sched = build_scheduler(Settings(_env_file=None))
    sched.start(paused=True)
    try:
        yield {j.id: j for j in sched.get_jobs()}
    finally:
        sched.shutdown(wait=False)


def fields(job):
    return {f.name: str(f) for f in job.trigger.fields}


def test_weekly_review_is_scheduled_for_sunday_20_00_vienna(jobs):
    job = jobs["advisor_weekly"]
    assert str(job.trigger.timezone) == "Europe/Vienna"
    f = fields(job)
    assert (f["day_of_week"], f["hour"], f["minute"]) == ("sun", "20", "0")
    assert job.max_instances == 1 and job.coalesce is True
    # fires next on the coming Sunday evening, exactly once per week
    mon = datetime(2026, 10, 19, 12, 0, tzinfo=VIENNA)
    first = job.trigger.get_next_fire_time(None, mon)
    assert first == datetime(2026, 10, 25, 20, 0, tzinfo=VIENNA)
    assert job.trigger.get_next_fire_time(first, first) == datetime(
        2026, 11, 1, 20, 0, tzinfo=VIENNA
    )
    just_after = datetime(2026, 10, 25, 20, 0, 1, tzinfo=VIENNA)
    assert job.trigger.get_next_fire_time(None, just_after) == datetime(
        2026, 11, 1, 20, 0, tzinfo=VIENNA
    )


def test_trigger_and_outcome_jobs_are_daily(jobs):
    f = fields(jobs["advisor_triggers"])
    assert (f["hour"], f["minute"], f["day_of_week"]) == ("18", "30", "*")
    f = fields(jobs["advisor_outcomes"])
    assert (f["hour"], f["minute"], f["day_of_week"]) == ("4", "15", "*")
    assert jobs["advisor_weekly"].func is advisor_jobs.weekly_review_job


def test_times_are_configurable():
    s = Settings(
        _env_file=None,
        advisor_weekly_time="19:15",
        advisor_trigger_time="17:00",
        advisor_outcome_time="05:00",
    )
    sched = build_scheduler(s)
    sched.start(paused=True)
    try:
        jobs = {j.id: j for j in sched.get_jobs()}
        assert (
            fields(jobs["advisor_weekly"])["hour"],
            fields(jobs["advisor_weekly"])["minute"],
        ) == ("19", "15")
        assert fields(jobs["advisor_triggers"])["hour"] == "17"
        assert fields(jobs["advisor_outcomes"])["hour"] == "5"
    finally:
        sched.shutdown(wait=False)


def test_weekly_job_makes_one_call_per_week(db, world, fake, no_rerun, env, pin_session_factory):
    advisor_jobs.weekly_review_job(env)
    advisor_jobs.weekly_review_job(env)  # e.g. a misfire / manual run
    db.expire_all()
    rows = list(db.scalars(select(AdvisorConsultation)))
    assert len(rows) == 1 and rows[0].kind == "weekly" and rows[0].status == "pending"
    assert len(fake.calls) == 1


def test_jobs_never_raise(db, world, no_rerun, env, pin_session_factory, monkeypatch):
    monkeypatch.setattr(claude, "make_client", lambda s: FakeClient(exc=RuntimeError("boom")))
    advisor_jobs.weekly_review_job(env)
    db.expire_all()
    assert db.scalar(select(AdvisorConsultation.status)) == "failed"

    def broken(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(service, "run_trigger_batch", broken)
    advisor_jobs.trigger_job(env)  # logged, not raised
    monkeypatch.setattr("apps.fitness.advisor.outcomes.run_outcome_check", broken)
    advisor_jobs.outcome_job(env)


def test_trigger_job_batches_on_weekdays_but_not_on_sunday(
    db, world, fake, no_rerun, env, pin_session_factory, monkeypatch
):
    db.add(
        AdvisorTrigger(type="rule_conflict", dedupe_key="k", day=TODAY, payload={"conflict": "x"})
    )
    db.commit()
    advisor_jobs.trigger_job(env)  # TODAY is a Sunday: the weekly review takes the triggers
    assert fake.calls == []
    monkeypatch.setattr("apps.fitness.analytics.common.today", lambda: TODAY + timedelta(days=1))
    advisor_jobs.trigger_job(env)
    assert len(fake.calls) == 1
    db.expire_all()
    c = db.scalars(select(AdvisorConsultation)).one()
    assert c.kind == "trigger" and c.trigger == "rule_conflict"


def test_trigger_job_retries_consultations_that_waited_for_a_key(
    db, world, fake, no_rerun, env, pin_session_factory, monkeypatch
):
    c = service.create_consultation(
        db,
        kind="ask",
        trigger_info={"type": "ask", "title": "q"},
        question="q",
        status="awaiting_key",
    )
    db.commit()
    monkeypatch.setattr("apps.fitness.analytics.common.today", lambda: TODAY + timedelta(days=1))
    advisor_jobs.trigger_job(env)
    db.expire_all()
    assert db.get(AdvisorConsultation, c.id).status == "pending"


def test_rollback_does_not_inherit_the_consultation_link(db, world):
    v = cs.create_version(
        db, {"running.weekly_volume_cap_pct": 8}, reason="r", author="advisor", consultation_id=7
    )
    assert v.meta["consultation_id"] == 7
    after = cs.create_version(db, {"strength.stall_exposures": 4}, reason="user", author="user")
    assert "consultation_id" not in after.meta
    back = cs.rollback(db, "v3")
    assert "consultation_id" not in (back.meta or {})
