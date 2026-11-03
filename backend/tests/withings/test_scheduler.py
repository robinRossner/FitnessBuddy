"""The scheduler registers Withings jobs only when Withings is configured."""

from __future__ import annotations

from apps.fitness.jobs import scheduler as sched_mod
from apps.fitness.jobs import withings_jobs
from apps.fitness.settings import Settings


def job_ids(settings: Settings) -> set[str]:
    return {job.id for job in sched_mod.build_scheduler(settings).get_jobs()}


def test_no_withings_job_when_unconfigured(unconfigured):
    ids = job_ids(unconfigured)
    assert "garmin_nightly" in ids
    assert not [i for i in ids if i.startswith("withings")]


def test_partial_configuration_is_unconfigured(unconfigured):
    partial = unconfigured.model_copy(update={"withings_client_id": "x"})
    assert not partial.withings_configured
    assert not [i for i in job_ids(partial) if i.startswith("withings")]


def test_jobs_at_the_configured_times(settings):
    assert {i for i in job_ids(settings) if i.startswith("withings")} == {
        "withings_0615",
        "withings_0915",
        "withings_1315",
        "withings_2015",
    }
    custom = settings.model_copy(update={"withings_sync_times": "7, 22:30"})
    assert {i for i in job_ids(custom) if i.startswith("withings")} == {
        "withings_0700",
        "withings_2230",
    }


def test_job_is_a_quiet_noop_without_a_token(
    settings, monkeypatch, session_factory, pin_session_factory
):
    monkeypatch.setattr(withings_jobs, "get_settings", lambda: settings)
    calls = []
    monkeypatch.setattr(withings_jobs, "after_sync", lambda r: calls.append(r))
    sched_mod.withings_job()  # no token file: nothing contacted, nothing raised
    assert calls == []


def test_job_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(withings_jobs, "run_sync", boom)
    sched_mod.withings_job()  # logged, never raised into the scheduler
