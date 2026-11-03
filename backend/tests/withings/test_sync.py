"""Withings sync: weigh-ins into body_measurements, idempotency, tombstones, window, sync_state."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from apps.fitness.db.models import BodyMeasurement, SyncState
from apps.fitness.db.types import MeasurementSource, utcnow
from apps.fitness.withings.errors import RateLimitedError, WithingsError
from apps.fitness.withings.sync import NAMESPACE, measurement_id, sync_weights
from tests.withings.support import SECRET, fail, grp

NOW = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
TS = int(datetime(2026, 11, 2, 5, 30, tzinfo=UTC).timestamp())  # 06:30 Vienna


def run(session_factory, wclient, settings, now: datetime = NOW):
    return sync_weights(session_factory, wclient, settings, now=lambda: now)


def rows(db) -> list[BodyMeasurement]:
    db.rollback()
    return list(db.scalars(select(BodyMeasurement).order_by(BodyMeasurement.measured_at)))


def state(db) -> SyncState:
    db.rollback()
    st = db.scalar(select(SyncState).where(SyncState.source == "withings"))
    assert st is not None
    return st


def test_first_sync_stores_rows_and_asks_for_the_backfill_window(
    session_factory, db, wclient, fake, settings, connected
):
    fake.groups = [grp(11, TS, 81.2), grp(12, TS + 86400, 80.9)]
    report = run(session_factory, wclient, settings)
    assert (report.fetched, report.new, report.updated, report.skipped) == (2, 2, 0, 0)
    assert report.weights_changed
    assert int(fake.measure_requests[0]["lastupdate"]) == int(
        (NOW - timedelta(days=365)).timestamp()
    )

    first, second = rows(db)
    assert first.id == str(uuid.uuid5(NAMESPACE, "withings:11")) == measurement_id(11)
    assert first.source == MeasurementSource.WITHINGS and first.note == "withings"
    assert first.weight_kg == 81.2 and first.measured_at == datetime.fromtimestamp(TS, UTC)
    assert first.day.isoformat() == "2026-11-02"
    assert first.server_seq is not None  # the sync-pull trigger stamped it
    assert (report.latest_measured_at, report.latest_weight_kg) == (second.measured_at, 80.9)


def test_local_day_follows_the_user_timezone(
    session_factory, db, wclient, fake, settings, connected
):
    late = int(datetime(2026, 11, 2, 23, 30, tzinfo=UTC).timestamp())  # 00:30 next day in Vienna
    fake.groups = [grp(1, late, 80)]
    run(session_factory, wclient, settings)
    assert rows(db)[0].day.isoformat() == "2026-11-03"


def test_rerun_is_idempotent(session_factory, db, wclient, fake, settings, connected):
    fake.groups = [grp(11, TS, 81.2)]
    run(session_factory, wclient, settings)
    again = run(session_factory, wclient, settings)
    assert (again.new, again.updated, again.weights_changed) == (0, 0, False)
    assert len(rows(db)) == 1
    seq = rows(db)[0].server_seq
    run(session_factory, wclient, settings)
    assert rows(db)[0].server_seq == seq  # unchanged rows are not touched (no pull churn)


def test_an_edited_weigh_in_updates_the_same_row(
    session_factory, db, wclient, fake, settings, connected
):
    fake.groups = [grp(11, TS, 81.2)]
    run(session_factory, wclient, settings)
    seq = rows(db)[0].server_seq
    edited = grp(11, TS, 80.4)
    edited["modified"] = int(utcnow().timestamp())  # Withings bumps `modified` on an edit
    fake.groups = [edited]
    report = run(session_factory, wclient, settings)
    assert (report.new, report.updated) == (0, 1)
    (row,) = rows(db)
    assert row.weight_kg == 80.4 and row.server_seq != seq


def test_a_row_deleted_in_the_app_is_not_resurrected(
    session_factory, db, wclient, fake, settings, connected
):
    fake.groups = [grp(11, TS, 81.2)]
    run(session_factory, wclient, settings)
    (row,) = rows(db)
    row.deleted_at = utcnow()
    db.commit()
    again = grp(11, TS, 79.0)
    again["modified"] = int(utcnow().timestamp())
    fake.groups = [again]
    report = run(session_factory, wclient, settings)
    assert (report.new, report.updated) == (0, 0)
    (row,) = rows(db)
    assert row.deleted_at is not None and row.weight_kg == 81.2
    assert report.latest_measured_at is None  # the only Withings row is deleted


def test_implausible_weights_are_skipped(session_factory, db, wclient, fake, settings, connected):
    fake.groups = [
        grp(1, TS, 19.9),
        grp(2, TS + 1, 20.0),
        grp(3, TS + 2, 400.0),
        grp(4, TS + 3, 82),
    ]
    report = run(session_factory, wclient, settings)
    assert (report.new, report.skipped) == (1, 3)
    assert [r.weight_kg for r in rows(db)] == [82]


def test_later_runs_use_the_last_success_minus_one_day(
    session_factory, db, wclient, fake, settings, connected
):
    run(session_factory, wclient, settings)
    last = state(db).last_success_at
    assert last is not None
    run(session_factory, wclient, settings)
    expected = int((last.astimezone(UTC) - timedelta(days=1)).timestamp())
    assert int(fake.measure_requests[1]["lastupdate"]) == expected


def test_overlap_rereads_recent_groups_without_duplicating(
    session_factory, db, wclient, fake, settings, connected
):
    recent = int(utcnow().timestamp()) - 3600
    fake.groups = [grp(1, recent, 70.0)]  # modified an hour ago: inside the 1-day overlap
    run(session_factory, wclient, settings, now=utcnow())
    report = run(session_factory, wclient, settings, now=utcnow())
    assert report.fetched == 1 and report.new == 0
    assert len(rows(db)) == 1


def test_sync_state_records_success(session_factory, db, wclient, fake, settings, connected):
    fake.groups = [grp(1, TS, 70), grp(2, TS + 1, 71)]
    run(session_factory, wclient, settings)
    st = state(db)
    assert (st.source, st.kind, st.rows_last) == ("withings", "weight", 2)
    assert st.last_run_at and st.last_success_at and st.last_error is None
    assert st.cursor_date and st.cursor_date.isoformat() == "2026-11-03"
    run(session_factory, wclient, settings)
    assert state(db).rows_last == 0


def test_errors_land_in_sync_state_and_are_reraised(
    session_factory, db, wclient, fake, settings, connected
):
    fake.groups = [grp(1, TS, 70)]
    run(session_factory, wclient, settings)
    ok_at = state(db).last_success_at
    fake.measure_replies.append(fail(2555, f"oops {SECRET}"))
    with pytest.raises(WithingsError):
        run(session_factory, wclient, settings)
    st = state(db)
    assert st.last_error and "2555" in st.last_error and SECRET not in st.last_error
    assert st.last_error_at and st.last_success_at == ok_at
    fake.measure_replies.append(fail(601))
    with pytest.raises(RateLimitedError):
        run(session_factory, wclient, settings)
    run(session_factory, wclient, settings)  # recovers
    assert state(db).last_error is None
