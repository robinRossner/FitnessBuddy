"""The Withings job: lock, not-connected handling, revocation, stats rebuild after new weights."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from apps.fitness.db.models import BodyMeasurement, SyncState
from apps.fitness.jobs import withings_jobs
from apps.fitness.withings.errors import NotConfiguredError, WithingsError
from tests.withings.support import fail, grp

TS = int(datetime(2026, 11, 2, 5, 30, tzinfo=UTC).timestamp())


def test_not_connected_without_a_token_and_no_http(
    session_factory, pin_session_factory, wclient, fake, settings
):
    out = withings_jobs.run_sync(settings, client=wclient)
    assert out.status == "not_connected" and out.report is None
    assert fake.measure_requests == [] and fake.token_requests == []


def test_synced_rebuilds_stats_only_when_weights_changed(
    session_factory, pin_session_factory, wclient, fake, settings, connected, rebuilds
):
    fake.groups = [grp(1, TS, 80.0)]
    out = withings_jobs.run_sync(settings, client=wclient)
    assert out.status == "synced" and out.report and out.report.new == 1
    assert rebuilds == [1]
    withings_jobs.run_sync(settings, client=wclient)
    assert rebuilds == [1]


def test_busy_when_another_sync_holds_the_lock(settings, wclient, connected):
    assert withings_jobs._lock.acquire(blocking=False)
    try:
        assert withings_jobs.run_sync(settings, client=wclient).status == "busy"
    finally:
        withings_jobs._lock.release()
    assert withings_jobs._lock.acquire(blocking=False)  # a busy skip did not leak the lock
    withings_jobs._lock.release()


def test_revoked_token_is_deleted_and_recorded(
    session_factory,
    db,
    pin_session_factory,
    wclient,
    fake,
    settings,
    connected,
    store,
    clock,
    rebuilds,
):
    from apps.fitness.withings.tokens import Tokens

    store.save(Tokens("4711", "old-access-12345", "refresh-0-abcdefgh", clock.now + 5, "s"))
    fake.token_replies.append(fail(293, "invalid_grant"))
    out = withings_jobs.run_sync(settings, client=wclient)
    assert out.status == "not_connected"
    assert not store.path.exists()
    db.rollback()
    st = db.scalar(select(SyncState).where(SyncState.source == "withings"))
    assert st and st.last_error and "connect again" in st.last_error
    assert withings_jobs.run_sync(settings, client=wclient).status == "not_connected"
    assert db.scalar(select(BodyMeasurement.id)) is None and rebuilds == []


def test_other_errors_propagate_and_keep_the_token(
    session_factory, pin_session_factory, wclient, fake, settings, connected, store
):
    fake.measure_replies.append(fail(2555))
    with pytest.raises(WithingsError):
        withings_jobs.run_sync(settings, client=wclient)
    assert store.load() is not None
    assert withings_jobs._lock.acquire(blocking=False)
    withings_jobs._lock.release()


def test_build_client_needs_the_full_configuration(unconfigured):
    with pytest.raises(NotConfiguredError):
        withings_jobs.build_client(unconfigured)
