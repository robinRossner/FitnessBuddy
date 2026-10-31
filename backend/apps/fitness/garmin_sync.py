"""Garmin sync: pull via a `GarminSource`, normalise, upsert idempotently.

- `backfill()` walks history in chunks no larger than the source's window for each kind and
  commits after every chunk, advancing `sync_state.cursor_date` -> resumable after any failure.
- `sync()` (incremental) re-reads from `cursor - overlap` to today (late edits, sleep scores
  that land after waking, HRV computed later).
- Raw JSON of everything fetched goes to `garmin_raw`; originals (.FIT) to GARMIN_FIT_DIR.
- Rate limits: a persistent 429 is waited out (escalating waits) a bounded number of times,
  then the run stops with the cursor intact so the next run resumes where it left off.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.fitness.bodyweight import upsert_measurement
from apps.fitness.db.models import (
    AthleteMetric,
    BodyMeasurement,
    DailyHealth,
    GarminRaw,
    Lap,
    Run,
    SyncState,
)
from apps.fitness.db.types import MeasurementSource, Source, utcnow
from apps.fitness.settings import Settings
from apps.fitness.sources.garmin.base import (
    ACTIVITIES,
    ALL_KINDS,
    DAILY_KINDS,
    HR_ZONES,
    WEIGHT,
    ActivitySummary,
    AthleteMetricValue,
    DailyHealthData,
    GarminSource,
    WeightEntry,
)
from apps.fitness.sources.garmin.errors import (
    GarminError,
    NotFoundError,
    RateLimitedError,
    SourceUnavailable,
)

log = logging.getLogger(__name__)

STATE_SOURCE = "garmin"
_NS = uuid.UUID("5d0f8a52-7c1e-4c0b-9a57-3f2c8e1b6a44")
DEFAULT_RATE_LIMIT_WAITS = (60.0, 180.0, 600.0)

DAILY_FIELDS = (
    "resting_hr",
    "hrv_overnight_ms",
    "hrv_status",
    "hrv_weekly_avg_ms",
    "hrv_baseline_low_ms",
    "hrv_baseline_high_ms",
    "sleep_s",
    "sleep_score",
    "sleep_deep_s",
    "sleep_light_s",
    "sleep_rem_s",
    "sleep_awake_s",
    "body_battery_max",
    "body_battery_min",
    "body_battery_charged",
    "body_battery_drained",
    "stress_avg",
    "steps",
    "training_readiness",
)
RUN_FIELDS = (
    "activity_type",
    "title",
    "duration_s",
    "moving_time_s",
    "distance_m",
    "avg_hr",
    "max_hr",
    "elevation_gain_m",
    "avg_cadence",
    "avg_pace_s_per_km",
    "training_load",
    "time_in_zones_s",
    "training_effect_aerobic",
    "training_effect_anaerobic",
    "vo2max",
    "avg_power_w",
    "calories",
)


def run_id(external_id: str) -> str:
    return str(uuid.uuid5(_NS, f"activity:{external_id}"))


def weight_id(day: date) -> str:
    return str(uuid.uuid5(_NS, f"weight:{day.isoformat()}"))


def chunk_ranges(start: date, end: date, window_days: int) -> Iterator[tuple[date, date]]:
    """Consecutive inclusive ranges of at most `window_days` days covering start..end."""
    cur = start
    while cur <= end:
        stop = min(end, cur + timedelta(days=window_days - 1))
        yield cur, stop
        cur = stop + timedelta(days=1)


@dataclass
class KindResult:
    kind: str
    rows: int = 0
    chunks: int = 0
    start: date | None = None
    end: date | None = None
    skipped: str | None = None


@dataclass
class SyncReport:
    results: list[KindResult] = field(default_factory=list)
    details_fetched: int = 0
    details_failed: int = 0
    weights_changed: bool = False

    @property
    def new_data(self) -> bool:
        return any(r.rows for r in self.results) or self.details_fetched > 0


class GarminSync:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        source: GarminSource | None,
        settings: Settings,
        *,
        today: Callable[[], date] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        progress: Callable[[str], None] = lambda msg: None,
        rate_limit_waits: tuple[float, ...] = DEFAULT_RATE_LIMIT_WAITS,
    ) -> None:
        self.factory = session_factory
        self.source = source  # None only for network-free operations (reparse)
        self.s = settings
        self._today = today or (lambda: datetime.now(ZoneInfo(settings.user_tz)).date())
        self._sleep = sleep
        self.progress = progress
        self.rate_limit_waits = rate_limit_waits

    # ------------------------------------------------------------------ infrastructure

    @contextmanager
    def _scope(self) -> Iterator[Session]:
        db = self.factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _guarded(self, label: str, fn: Callable[[], Any]) -> Any:
        """Run `fn`, waiting out persistent 429s a bounded number of times."""
        waits = list(self.rate_limit_waits)
        while True:
            try:
                return fn()
            except RateLimitedError as exc:
                if not waits:
                    raise
                wait = max(waits.pop(0), exc.retry_after or 0.0)
                self.progress(f"rate limited during {label}; waiting {wait:.0f}s before resuming")
                self._sleep(wait)

    def get_state(self, kind: str) -> SyncState | None:
        with self._scope() as db:
            st = db.scalar(
                select(SyncState).where(SyncState.source == STATE_SOURCE, SyncState.kind == kind)
            )
            if st is not None:
                db.expunge(st)
            return st

    def _update_state(
        self,
        kind: str,
        *,
        cursor: date | None = None,
        rows: int | None = None,
        error: str | None = None,
    ) -> None:
        with self._scope() as db:
            st = db.scalar(
                select(SyncState).where(SyncState.source == STATE_SOURCE, SyncState.kind == kind)
            )
            if st is None:
                st = SyncState(source=STATE_SOURCE, kind=kind, rows_last=0)
                db.add(st)
            now = utcnow()
            st.last_run_at = now
            if error is not None:
                st.last_error, st.last_error_at = error[:500], now
            else:
                st.last_error = None
                st.last_success_at = now
                if cursor is not None and (st.cursor_date is None or cursor > st.cursor_date):
                    st.cursor_date = cursor
                if rows is not None:
                    st.rows_last = rows

    # ------------------------------------------------------------------ public entry points

    def backfill(
        self,
        since: date,
        kinds: tuple[str, ...] = ALL_KINDS,
        *,
        until: date | None = None,
        restart: bool = False,
    ) -> SyncReport:
        end = until or self._today()
        report = SyncReport()
        for kind in kinds:
            st = self.get_state(kind)
            cursor = st.cursor_date if st else None
            start = (
                since if restart or cursor is None or cursor < since else cursor + timedelta(days=1)
            )
            report.results.append(self._run_kind(kind, start, end, report))
        self._finish(report)
        return report

    def sync(self, kinds: tuple[str, ...] = ALL_KINDS) -> SyncReport:
        end = self._today()
        report = SyncReport()
        for kind in kinds:
            st = self.get_state(kind)
            if st is not None and st.cursor_date is not None:
                start = st.cursor_date - timedelta(days=self.s.garmin_sync_overlap_days)
            else:
                start = end - timedelta(days=7)
                log.info("no cursor for %s: syncing the last 7 days only (run backfill)", kind)
            report.results.append(self._run_kind(kind, start, end, report))
        self._finish(report)
        return report

    def _finish(self, report: SyncReport) -> None:
        if ACTIVITIES in {r.kind for r in report.results} and self.s.garmin_download_fit:
            self.sync_details(report)

    # ------------------------------------------------------------------ per kind

    def _run_kind(self, kind: str, start: date, end: date, report: SyncReport) -> KindResult:
        res = KindResult(kind=kind, start=start, end=end)
        try:
            if kind == HR_ZONES:
                res.rows = self._guarded(kind, self._sync_hr_zones)
                res.chunks = 1
                self._update_state(kind, cursor=end, rows=res.rows)
                self.progress(f"{kind}: {res.rows} metric(s) changed")
                return res
            if start > end:
                res.skipped = "up to date"
                self.progress(f"{kind}: up to date (cursor {end})")
                return res
            window = self.source.max_window_days(kind)
            chunks = list(chunk_ranges(start, end, window))
            for i, (cs, ce) in enumerate(chunks, start=1):
                n = self._guarded(kind, lambda cs=cs, ce=ce: self._sync_chunk(kind, cs, ce, report))
                res.rows += n
                res.chunks += 1
                self._update_state(kind, cursor=ce, rows=n)
                self.progress(f"{kind} {cs}..{ce}: {n} rows (chunk {i}/{len(chunks)})")
        except (RateLimitedError, SourceUnavailable, GarminError) as exc:
            self._update_state(kind, error=f"{type(exc).__name__}: {exc}")
            raise
        return res

    def _sync_chunk(self, kind: str, start: date, end: date, report: SyncReport) -> int:
        if kind == ACTIVITIES:
            acts = list(self.source.activities(start, end))
            with self._scope() as db:
                return sum(1 for a in acts if self._upsert_activity(db, a))
        if kind in DAILY_KINDS:
            rows = list(self.source.daily_health(kind, start, end))
            with self._scope() as db:
                return self._upsert_daily(db, rows)
        if kind == WEIGHT:
            entries = list(self.source.weights(start, end))
            with self._scope() as db:
                changed = self._upsert_weights(db, entries)
            report.weights_changed |= changed > 0
            return len(entries)
        raise ValueError(f"unknown kind {kind!r}")

    # ------------------------------------------------------------------ raw store

    @staticmethod
    def _store_raw(db: Session, kind: str, key: str, payload: Any) -> None:
        row = db.scalar(select(GarminRaw).where(GarminRaw.kind == kind, GarminRaw.key == key))
        if row is None:
            db.add(GarminRaw(kind=kind, key=key, payload=payload))
        else:
            row.payload = payload
            row.fetched_at = utcnow()

    # ------------------------------------------------------------------ activities

    def _upsert_activity(self, db: Session, a: ActivitySummary) -> bool:
        run = db.scalar(select(Run).where(Run.external_id == a.external_id))
        if run is not None and run.deleted_at is not None:
            return False  # the user deleted it; never resurrect
        if run is None:
            run = Run(id=run_id(a.external_id), external_id=a.external_id, source=Source.GARMIN)
            db.add(run)
        run.started_at_utc = a.started_at_utc
        run.started_at_local = a.started_at_local
        run.tz = self.s.user_tz
        for name in RUN_FIELDS:
            setattr(run, name, getattr(a, name))
        self._store_raw(db, "activity", a.external_id, a.raw)
        if a.vo2max is not None:
            self._upsert_metric(db, "vo2max", a.vo2max, a.started_at_local.date())
        db.flush()
        return True

    def sync_details(self, report: SyncReport | None = None, limit: int | None = None) -> int:
        """Laps + original .FIT for activities that do not have them yet."""
        report = report or SyncReport()
        with self._scope() as db:
            q = (
                select(Run.id, Run.external_id)
                .where(
                    Run.source == Source.GARMIN,
                    Run.deleted_at.is_(None),
                    Run.detail_synced_at.is_(None),
                )
                .order_by(Run.started_at_utc.desc())
            )
            if limit:
                q = q.limit(limit)
            pending = [(rid, ext) for rid, ext in db.execute(q) if ext]
        done = 0
        for i, (rid, ext) in enumerate(pending, start=1):
            try:
                detail = self._guarded(
                    f"activity {ext}", lambda ext=ext: self.source.activity_detail(ext)
                )
            except NotFoundError:
                detail = None  # manual activity / no file: do not ask again
            except SourceUnavailable:
                raise
            except GarminError as exc:
                report.details_failed += 1
                log.warning("activity %s: detail failed (%s)", ext, type(exc).__name__)
                continue
            with self._scope() as db:
                run = db.get(Run, rid)
                if run is None:
                    continue
                if detail is not None:
                    self._replace_laps(db, run, detail.laps)
                    if detail.fit_bytes:
                        run.fit_path = self._save_fit(ext, detail.fit_bytes)
                run.detail_synced_at = utcnow()
            report.details_fetched += 1
            done += 1
            if i % 10 == 0 or i == len(pending):
                self.progress(f"activity details: {i}/{len(pending)}")
        return done

    @staticmethod
    def _replace_laps(db: Session, run: Run, laps) -> None:
        for old in list(run.laps):
            db.delete(old)
        db.flush()
        for lap in laps:
            db.add(
                Lap(
                    run_id=run.id,
                    lap_index=lap.lap_index,
                    start_offset_s=lap.start_offset_s,
                    duration_s=lap.duration_s,
                    distance_m=lap.distance_m,
                    avg_hr=lap.avg_hr,
                    max_hr=lap.max_hr,
                    avg_pace_s_per_km=lap.avg_pace_s_per_km,
                    avg_cadence=lap.avg_cadence,
                    elevation_gain_m=lap.elevation_gain_m,
                    intensity=lap.intensity,
                )
            )
        db.flush()

    def _save_fit(self, external_id: str, content: bytes) -> str:
        directory: Path = self.s.garmin_fit_dir
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{external_id}.fit"
        tmp = directory / f".{name}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        tmp.replace(directory / name)
        return name

    # ------------------------------------------------------------------ daily health

    def _upsert_daily(self, db: Session, rows: list[DailyHealthData]) -> int:
        n = 0
        for r in rows:
            dh = db.scalar(
                select(DailyHealth).where(
                    DailyHealth.day == r.day, DailyHealth.source == Source.GARMIN
                )
            )
            if dh is None:
                dh = DailyHealth(day=r.day, source=Source.GARMIN)
                db.add(dh)
            for name in DAILY_FIELDS:
                value = getattr(r, name)
                if value is not None:
                    setattr(dh, name, value)
            self._store_raw(db, r.kind, r.day.isoformat(), r.raw)
            n += 1
        db.flush()
        return n

    # ------------------------------------------------------------------ weight

    def _upsert_weights(self, db: Session, entries: list[WeightEntry]) -> int:
        changed = 0
        for w in entries:
            mid = weight_id(w.day)
            old = db.get(BodyMeasurement, mid)
            if (
                old is not None
                and old.deleted_at is None
                and abs(old.weight_kg - w.weight_kg) < 1e-9
            ):
                continue
            upsert_measurement(
                db,
                id=mid,
                measured_at=w.measured_at_utc,
                weight_kg=w.weight_kg,
                tz=self.s.user_tz,
                source=MeasurementSource.GARMIN,
                note=f"garmin:{w.source_type}" if w.source_type else "garmin",
            )
            self._store_raw(db, "weight", w.day.isoformat(), w.raw)
            changed += 1
        return changed

    # ------------------------------------------------------------------ athlete metrics

    @staticmethod
    def _upsert_metric(db: Session, metric: str, value: float, day: date) -> bool:
        """Insert a row only when the value differs from the newest stored one."""
        latest = db.scalar(
            select(AthleteMetric)
            .where(AthleteMetric.metric == metric, AthleteMetric.source == "garmin")
            .order_by(AthleteMetric.day.desc())
            .limit(1)
        )
        if latest is not None and abs(latest.value - value) < 1e-9:
            return False
        same_day = db.scalar(
            select(AthleteMetric).where(
                AthleteMetric.metric == metric,
                AthleteMetric.source == "garmin",
                AthleteMetric.day == day,
            )
        )
        if same_day is not None:
            same_day.value = value
        else:
            db.add(AthleteMetric(day=day, metric=metric, value=value, source="garmin"))
        db.flush()
        return True

    def _sync_hr_zones(self) -> int:
        metrics: list[AthleteMetricValue] = self.source.athlete_metrics()
        today = self._today()
        with self._scope() as db:
            n = sum(self._upsert_metric(db, m.metric, m.value, today) for m in metrics)
            if metrics:
                self._store_raw(db, "hr_zones", today.isoformat(), metrics[0].raw)
        return n

    # ------------------------------------------------------------------ re-parse

    def reparse(self) -> dict[str, int]:
        """Re-run the parsers over `garmin_raw` (no network): activities, daily kinds, weight."""
        from apps.fitness.sources.garmin import mapping

        counts = {"activity": 0, "daily": 0, "weight": 0}
        with self._scope() as db:
            raws = db.scalars(select(GarminRaw)).all()
            acts, daily, weights = [], [], []
            for r in raws:
                if r.kind == "activity":
                    acts.append(mapping.activity_from_raw(r.payload))
                elif r.kind in mapping.DAILY_PARSERS:
                    row = mapping.DAILY_PARSERS[r.kind](r.payload)
                    if row:
                        daily.append(row)
                elif r.kind == "weight":
                    weights += mapping.weights_from_range(
                        {"dailyWeightSummaries": [{"latestWeight": r.payload}]}
                    )
            counts["activity"] = sum(1 for a in acts if self._upsert_activity(db, a))
            counts["daily"] = self._upsert_daily(db, daily)
            counts["weight"] = self._upsert_weights(db, weights)
        return counts
