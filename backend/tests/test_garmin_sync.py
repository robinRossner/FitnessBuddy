"""Persistence: idempotent upserts, chunked/resumable backfill, incremental overlap, details."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from apps.fitness.db.models import (
    AthleteMetric,
    BodyMeasurement,
    DailyHealth,
    GarminRaw,
    Lap,
    Run,
    SyncState,
)
from apps.fitness.garmin_sync import GarminSync, chunk_ranges, run_id, weight_id
from apps.fitness.settings import Settings
from apps.fitness.sources.garmin import mapping
from apps.fitness.sources.garmin.base import (
    ACTIVITIES,
    DAILY_SUMMARY,
    HRV,
    SLEEP,
    WEIGHT,
    ActivityDetail,
    LapData,
)
from apps.fitness.sources.garmin.errors import (
    GarminError,
    NotFoundError,
    RateLimitedError,
    SourceUnavailable,
)
from tests.garmin_fakes import (
    FakeSource,
    activity_raw,
    build_fit,
    hr_zones_raw,
    hrv_raw,
    sleep_raw,
    summary_v2,
    weight_range,
)

TODAY = date(2026, 10, 25)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, garmin_fit_dir=tmp_path / "fit", garmin_sync_overlap_days=3)


def make_sync(session_factory, source, settings, **kw) -> GarminSync:
    sleeps: list[float] = []
    kw.setdefault("sleep", sleeps.append)
    sync = GarminSync(session_factory, source, settings, today=lambda: TODAY, **kw)
    sync.sleeps = sleeps  # type: ignore[attr-defined]
    return sync


def count(session_factory, model) -> int:
    with session_factory() as db:
        return db.scalar(select(func.count()).select_from(model))


def cursor(session_factory, kind) -> date | None:
    with session_factory() as db:
        return db.scalar(select(SyncState.cursor_date).where(SyncState.kind == kind))


def test_chunk_ranges_cover_exactly_without_overlap():
    chunks = list(chunk_ranges(date(2026, 1, 1), date(2026, 3, 15), 31))
    assert chunks[0] == (date(2026, 1, 1), date(2026, 1, 31))
    assert chunks[-1][1] == date(2026, 3, 15)
    assert all((b - a).days + 1 <= 31 for a, b in chunks)
    assert all(chunks[i][1] + timedelta(days=1) == chunks[i + 1][0] for i in range(len(chunks) - 1))
    assert list(chunk_ranges(date(2026, 1, 2), date(2026, 1, 1), 5)) == []


def test_activities_upsert_is_idempotent_and_stores_raw(session_factory, settings):
    src = FakeSource()
    src.activity_items = [
        activity_raw(1, "2026-10-01"),
        activity_raw(2, "2026-10-02", typ="strength_training", distance=0.0),
    ]
    sync = make_sync(session_factory, src, settings)
    sync.backfill(date(2026, 9, 1), (ACTIVITIES,))
    sync.backfill(date(2026, 9, 1), (ACTIVITIES,), restart=True)
    assert count(session_factory, Run) == 2
    assert count(session_factory, GarminRaw) == 2
    with session_factory() as db:
        run = db.scalar(select(Run).where(Run.external_id == "1"))
        assert run.id == run_id("1") and run.source == "garmin"
        assert run.activity_type == "running" and run.avg_pace_s_per_km == 300.0
        assert run.started_at_utc == datetime(2026, 10, 1, 5, 30, tzinfo=UTC)
        assert run.training_effect_aerobic == 3.2 and run.time_in_zones_s["z3"] == 1000
        other = db.scalar(select(Run).where(Run.external_id == "2"))
        assert other.activity_type == "strength_training" and other.avg_pace_s_per_km is None


def test_changed_activity_is_updated_not_duplicated(session_factory, settings):
    src = FakeSource()
    src.activity_items = [activity_raw(1, "2026-10-01", activityName="old")]
    sync = make_sync(session_factory, src, settings)
    sync.backfill(date(2026, 9, 1), (ACTIVITIES,))
    src.activity_items = [activity_raw(1, "2026-10-01", activityName="renamed", averageHR=155.0)]
    sync.backfill(date(2026, 9, 1), (ACTIVITIES,), restart=True)
    with session_factory() as db:
        run = db.scalar(select(Run))
        assert (run.title, run.avg_hr) == ("renamed", 155.0)
        assert db.scalar(select(func.count()).select_from(Run)) == 1


def test_deleted_activity_is_not_resurrected(session_factory, settings):
    src = FakeSource()
    src.activity_items = [activity_raw(1, "2026-10-01")]
    sync = make_sync(session_factory, src, settings)
    sync.backfill(date(2026, 9, 1), (ACTIVITIES,))
    with session_factory() as db:
        db.scalar(select(Run)).deleted_at = datetime.now(UTC)
        db.commit()
    sync.backfill(date(2026, 9, 1), (ACTIVITIES,), restart=True)
    with session_factory() as db:
        assert db.scalar(select(Run)).deleted_at is not None


def test_details_store_laps_fit_and_are_not_refetched(session_factory, settings):
    src = FakeSource()
    src.activity_items = [activity_raw(1, "2026-10-01"), activity_raw(2, "2026-10-02")]
    fit = build_fit(datetime(2026, 10, 1, 5, 30, tzinfo=UTC), [])
    src.details["1"] = ActivityDetail(
        laps=[
            LapData(1, 0.0, 300.0, 1000.0, 150, 160, 300.0, 170.0, 5.0, "active"),
            LapData(2, 300.0, 120.0, 200.0, 120, 130, 600.0, 150.0, 0.0, "rest"),
        ],
        fit_bytes=fit,
    )
    # activity 2 has no file (manual activity): marked done without laps
    sync = make_sync(session_factory, src, settings)
    report = sync.backfill(date(2026, 9, 1), (ACTIVITIES,))
    assert report.details_fetched == 2 and report.details_failed == 0
    with session_factory() as db:
        run = db.scalar(select(Run).where(Run.external_id == "1"))
        assert run.detail_synced_at is not None and run.fit_path == "1.fit"
        assert (settings.garmin_fit_dir / "1.fit").read_bytes() == fit
        assert oct((settings.garmin_fit_dir / "1.fit").stat().st_mode & 0o777) == "0o600"
        assert [(lap.lap_index, lap.intensity) for lap in run.laps] == [(1, "active"), (2, "rest")]
        no_file = db.scalar(select(Run).where(Run.external_id == "2"))
        assert no_file.detail_synced_at is not None and no_file.fit_path is None
    before = len(src.calls)
    make_sync(session_factory, src, settings).sync((ACTIVITIES,))
    assert not [c for c in src.calls[before:] if c[0] == "detail"]
    assert count(session_factory, Lap) == 2


def test_laps_replaced_not_duplicated_on_refetch(session_factory, settings):
    src = FakeSource()
    src.activity_items = [activity_raw(1, "2026-10-01")]
    src.details["1"] = ActivityDetail(laps=[LapData(1, 0, 60, 100)], fit_bytes=None)
    sync = make_sync(session_factory, src, settings)
    sync.backfill(date(2026, 9, 1), (ACTIVITIES,))
    with session_factory() as db:
        db.scalar(select(Run)).detail_synced_at = None
        db.commit()
    src.details["1"] = ActivityDetail(laps=[LapData(1, 0, 61, 100), LapData(2, 61, 5, 10)])
    sync.sync_details()
    with session_factory() as db:
        assert [lap.duration_s for lap in db.scalars(select(Lap).order_by(Lap.lap_index))] == [
            61,
            5,
        ]


def test_detail_failure_is_retried_next_time(session_factory, settings):
    src = FakeSource()
    src.activity_items = [activity_raw(1, "2026-10-01")]
    src.details["1"] = GarminError("HTTP 500")
    sync = make_sync(session_factory, src, settings)
    report = sync.backfill(date(2026, 9, 1), (ACTIVITIES,))
    assert report.details_failed == 1
    with session_factory() as db:
        assert db.scalar(select(Run)).detail_synced_at is None
    src.details["1"] = ActivityDetail(laps=[LapData(1, 0, 60, 100)])
    make_sync(session_factory, src, settings).sync_details()
    with session_factory() as db:
        assert db.scalar(select(Run)).detail_synced_at is not None


def test_daily_kinds_merge_into_one_row_per_day(session_factory, settings):
    src = FakeSource()
    src.daily_items[DAILY_SUMMARY] = [summary_v2("2026-10-01"), summary_v2("2026-10-02")]
    src.daily_items[SLEEP] = [sleep_raw("2026-10-01")]
    src.daily_items[HRV] = [hrv_raw("2026-10-01", 77)]
    sync = make_sync(session_factory, src, settings)
    sync.backfill(date(2026, 10, 1), (DAILY_SUMMARY, SLEEP, HRV))
    sync.backfill(date(2026, 10, 1), (DAILY_SUMMARY, SLEEP, HRV), restart=True)  # idempotent
    assert count(session_factory, DailyHealth) == 2
    with session_factory() as db:
        d1 = db.scalar(select(DailyHealth).where(DailyHealth.day == date(2026, 10, 1)))
        assert (d1.resting_hr, d1.steps, d1.sleep_s, d1.sleep_score) == (53, 9876, 28800, 81)
        assert (d1.hrv_overnight_ms, d1.hrv_status, d1.body_battery_charged) == (77, "BALANCED", 80)
        assert d1.training_readiness is None
        d2 = db.scalar(select(DailyHealth).where(DailyHealth.day == date(2026, 10, 2)))
        assert d2.sleep_s is None and d2.resting_hr == 53
    assert count(session_factory, GarminRaw) == 4  # 2 summaries + 1 sleep + 1 hrv


def test_late_sleep_score_updates_existing_row_without_clearing_others(session_factory, settings):
    src = FakeSource()
    src.daily_items[DAILY_SUMMARY] = [summary_v2("2026-10-25")]
    src.daily_items[SLEEP] = [sleep_raw("2026-10-25", score=None)]
    sync = make_sync(session_factory, src, settings)
    sync.backfill(date(2026, 10, 25), (DAILY_SUMMARY, SLEEP))
    src.daily_items[SLEEP] = [sleep_raw("2026-10-25", score=88)]
    src.daily_items[DAILY_SUMMARY] = [summary_v2("2026-10-25", heartRate={})]  # field vanishes
    sync.sync((DAILY_SUMMARY, SLEEP))
    with session_factory() as db:
        row = db.scalar(select(DailyHealth))
        assert row.sleep_score == 88 and row.resting_hr == 53


def test_weights_land_in_body_measurements_deduped_by_day_and_source(session_factory, settings):
    src = FakeSource()
    src.weight_payloads = [weight_range("2026-10-04", 76500.0)]
    sync = make_sync(session_factory, src, settings)
    report = sync.backfill(date(2026, 10, 1), (WEIGHT,))
    assert report.weights_changed
    again = sync.backfill(date(2026, 10, 1), (WEIGHT,), restart=True)
    assert not again.weights_changed
    with session_factory() as db:
        [m] = db.scalars(select(BodyMeasurement)).all()
        assert m.id == weight_id(date(2026, 10, 4)) and m.source == "garmin"
        assert m.weight_kg == 76.5 and m.day == date(2026, 10, 4)
    src.weight_payloads = [weight_range("2026-10-04", 76000.0)]  # corrected weigh-in
    assert sync.backfill(date(2026, 10, 1), (WEIGHT,), restart=True).weights_changed
    assert count(session_factory, BodyMeasurement) == 1


def test_deleted_weight_stays_deleted(session_factory, settings):
    src = FakeSource()
    src.weight_payloads = [weight_range("2026-10-04")]
    sync = make_sync(session_factory, src, settings)
    sync.backfill(date(2026, 10, 1), (WEIGHT,))
    with session_factory() as db:
        db.scalar(select(BodyMeasurement)).deleted_at = datetime.now(UTC)
        db.commit()
    src.weight_payloads = [weight_range("2026-10-04", 70000.0)]
    sync.backfill(date(2026, 10, 1), (WEIGHT,), restart=True)
    with session_factory() as db:
        m = db.scalar(select(BodyMeasurement))
        assert m.deleted_at is not None and m.weight_kg == 76.5


def test_athlete_metrics_only_written_when_changed(session_factory, settings):
    src = FakeSource()
    sync = make_sync(session_factory, src, settings)
    first = sync.sync(("hr_zones",))
    assert first.results[0].rows == 8
    assert sync.sync(("hr_zones",)).results[0].rows == 0
    src.zones = hr_zones_raw(max_hr=193, lthr=171)
    assert sync.sync(("hr_zones",)).results[0].rows == 1
    with session_factory() as db:
        rows = db.scalars(select(AthleteMetric).where(AthleteMetric.metric == "max_hr")).all()
        assert [r.value for r in rows] == [193.0]  # same day: updated in place
        assert db.scalar(select(AthleteMetric.value).where(AthleteMetric.metric == "lthr")) == 171


def test_vo2max_from_activities_becomes_athlete_metric(session_factory, settings):
    src = FakeSource()
    src.activity_items = [
        activity_raw(1, "2026-10-01", vO2MaxValue=52.0),
        activity_raw(2, "2026-10-05", vO2MaxValue=52.0),
        activity_raw(3, "2026-10-09", vO2MaxValue=53.0),
    ]
    make_sync(session_factory, src, settings).backfill(date(2026, 9, 1), (ACTIVITIES,))
    with session_factory() as db:
        rows = db.execute(
            select(AthleteMetric.day, AthleteMetric.value)
            .where(AthleteMetric.metric == "vo2max")
            .order_by(AthleteMetric.day)
        ).all()
        assert rows == [(date(2026, 10, 1), 52.0), (date(2026, 10, 9), 53.0)]


# --------------------------------------------------------------------------- chunking / resume


def test_backfill_chunks_respect_each_kinds_window(session_factory, settings):
    src = FakeSource()
    src.windows.update({SLEEP: 10, HRV: 20})
    sync = make_sync(session_factory, src, settings)
    start = TODAY - timedelta(days=44)  # 45 days inclusive
    sync.backfill(start, (SLEEP, HRV))
    sleep_calls = [(a, b) for k, a, b in src.calls if k == SLEEP]
    hrv_calls = [(a, b) for k, a, b in src.calls if k == HRV]
    assert len(sleep_calls) == 5 and len(hrv_calls) == 3
    assert all((b - a).days + 1 <= 10 for a, b in sleep_calls)
    assert all((b - a).days + 1 <= 20 for a, b in hrv_calls)
    assert sleep_calls[0][0] == start and sleep_calls[-1][1] == TODAY
    assert cursor(session_factory, SLEEP) == TODAY


def test_backfill_is_resumable_after_failure(session_factory, settings):
    src = FakeSource()
    src.windows[SLEEP] = 10
    start = TODAY - timedelta(days=29)  # three chunks
    third_start = start + timedelta(days=20)
    src.fail_on[(SLEEP, third_start)] = SourceUnavailable("blocked")
    sync = make_sync(session_factory, src, settings)
    with pytest.raises(SourceUnavailable):
        sync.backfill(start, (SLEEP,))
    assert cursor(session_factory, SLEEP) == start + timedelta(days=19)
    with session_factory() as db:
        st = db.scalar(select(SyncState).where(SyncState.kind == SLEEP))
        assert "blocked" in st.last_error and st.last_error_at is not None
    src.fail_on.clear()
    src.calls.clear()
    make_sync(session_factory, src, settings).backfill(start, (SLEEP,))
    assert [(a, b) for _k, a, b in src.calls] == [(third_start, TODAY)]  # only the rest
    assert cursor(session_factory, SLEEP) == TODAY
    with session_factory() as db:
        assert db.scalar(select(SyncState.last_error).where(SyncState.kind == SLEEP)) is None


def test_backfill_up_to_date_makes_no_calls(session_factory, settings):
    src = FakeSource()
    sync = make_sync(session_factory, src, settings)
    sync.backfill(TODAY - timedelta(days=5), (DAILY_SUMMARY,))
    src.calls.clear()
    report = sync.backfill(TODAY - timedelta(days=5), (DAILY_SUMMARY,))
    assert src.calls == [] and report.results[0].skipped == "up to date"


def test_incremental_sync_reads_from_cursor_minus_overlap(session_factory, settings):
    src = FakeSource()
    sync = make_sync(session_factory, src, settings)
    sync.backfill(TODAY - timedelta(days=10), (DAILY_SUMMARY,))
    src.calls.clear()
    later = GarminSync(session_factory, src, settings, today=lambda: TODAY + timedelta(days=2))
    later.sync((DAILY_SUMMARY,))
    assert src.calls == [(DAILY_SUMMARY, TODAY - timedelta(days=3), TODAY + timedelta(days=2))]
    assert cursor(session_factory, DAILY_SUMMARY) == TODAY + timedelta(days=2)


def test_incremental_without_cursor_syncs_one_week(session_factory, settings):
    src = FakeSource()
    make_sync(session_factory, src, settings).sync((SLEEP,))
    assert src.calls[0][1] == TODAY - timedelta(days=7)


def test_rate_limit_waits_then_resumes(session_factory, settings):
    src = FakeSource()
    calls = {"n": 0}
    real = src.daily_health

    def flaky(kind, start, end):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RateLimitedError("429", retry_after=5)
        return real(kind, start, end)

    src.daily_health = flaky  # type: ignore[method-assign]
    sync = make_sync(session_factory, src, settings, rate_limit_waits=(30.0, 60.0, 90.0))
    sync.backfill(TODAY - timedelta(days=3), (SLEEP,))
    assert sync.sleeps == [30.0, 60.0]  # type: ignore[attr-defined]
    assert cursor(session_factory, SLEEP) == TODAY


def test_rate_limit_gives_up_keeping_cursor(session_factory, settings):
    src = FakeSource()
    src.windows[SLEEP] = 5
    start = TODAY - timedelta(days=9)
    src.fail_on[(SLEEP, start + timedelta(days=5))] = RateLimitedError("429")
    sync = make_sync(session_factory, src, settings, rate_limit_waits=(1.0,))
    with pytest.raises(RateLimitedError):
        sync.backfill(start, (SLEEP,))
    assert cursor(session_factory, SLEEP) == start + timedelta(days=4)


def test_reparse_rebuilds_rows_from_raw_without_source(session_factory, settings):
    src = FakeSource()
    src.activity_items = [activity_raw(1, "2026-10-01")]
    src.daily_items[SLEEP] = [sleep_raw("2026-10-01")]
    src.weight_payloads = [weight_range("2026-10-01")]
    make_sync(session_factory, src, settings).backfill(
        date(2026, 10, 1), (ACTIVITIES, SLEEP, WEIGHT)
    )
    with session_factory() as db:
        db.scalar(select(Run)).avg_hr = 1.0
        db.scalar(select(DailyHealth)).sleep_score = 1.0
        db.commit()
    counts = GarminSync(session_factory, None, settings).reparse()
    assert counts["activity"] == 1 and counts["daily"] == 1
    with session_factory() as db:
        assert db.scalar(select(Run.avg_hr)) == 150.0
        assert db.scalar(select(DailyHealth.sleep_score)) == 81.0
    assert mapping.RUN_TYPES  # smoke


def test_fit_dir_is_created_lazily(session_factory, tmp_path):
    s = Settings(_env_file=None, garmin_fit_dir=tmp_path / "nested" / "fit")
    src = FakeSource()
    src.activity_items = [activity_raw(1, "2026-10-01")]
    src.details["1"] = ActivityDetail(laps=[], fit_bytes=b"FITDATA")
    make_sync(session_factory, src, s).backfill(date(2026, 9, 1), (ACTIVITIES,))
    assert Path(tmp_path / "nested" / "fit" / "1.fit").read_bytes() == b"FITDATA"


def test_not_found_detail_marks_done(session_factory, settings):
    src = FakeSource()
    src.activity_items = [activity_raw(1, "2026-10-01")]
    src.details["1"] = NotFoundError("gone")
    report = make_sync(session_factory, src, settings).backfill(date(2026, 9, 1), (ACTIVITIES,))
    assert report.details_failed == 0
