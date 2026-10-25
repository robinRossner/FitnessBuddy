"""SQLAlchemy 2.x typed models.

Conventions
- Syncable tables (written by the iOS app later) use client-generatable UUID string ids and carry
  created_at / updated_at / deleted_at (tombstone). Rows are never hard-deleted by sync.
- Reference tables (exercises, variants, gyms, ...) use integer ids.
- All "*_utc" columns are aware UTC datetimes (stored naive-UTC); "*_local" columns are naive
  wall-clock times in the zone given by the row's `tz` column.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    true as sa_true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.db.types import (
    EquipmentType,
    Fidelity,
    LoadConvention,
    MappingStatus,
    MeasurementSource,
    PlannedStatus,
    SessionKind,
    SetType,
    Source,
    UTCDateTime,
    str_enum,
    utcnow,
)

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy reads this class attribute
        dict[str, Any]: JSON,
        list[Any]: JSON,
        datetime: UTCDateTime,
    }


def new_uuid() -> str:
    return str(uuid.uuid4())


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Syncable(Timestamps):
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)


# --------------------------------------------------------------------------- reference data


class Gym(Syncable, Base):
    __tablename__ = "gyms"
    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable identity from data/catalog/gyms.yaml (survives renames); NULL for hand-made gyms.
    key: Mapped[str | None] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    timezone: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)


class Exercise(Timestamps, Base):
    """Canonical movement (e.g. 'Lat Pulldown'), independent of equipment and gym."""

    __tablename__ = "exercises"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    primary_muscle: Mapped[str] = mapped_column(String(40))
    secondary_muscles: Mapped[list[Any]] = mapped_column(JSON, default=list)
    movement_pattern: Mapped[str] = mapped_column(String(40))
    default_equipment_type: Mapped[EquipmentType] = mapped_column(
        str_enum(EquipmentType, "equipment_type")
    )
    e1rm_meaningful: Mapped[bool] = mapped_column(Boolean, default=True)
    is_timed: Mapped[bool] = mapped_column(Boolean, default=False)
    bodyweight_loaded: Mapped[bool] = mapped_column(Boolean, default=False)

    variants: Mapped[list[ExerciseVariant]] = relationship(back_populates="exercise")


class ExerciseVariant(Timestamps, Base):
    """The unit progression is tracked on: equipment + (for machines/cables) the specific gym."""

    __tablename__ = "exercise_variants"
    __table_args__ = (UniqueConstraint("name", "gym_id", name="name_gym"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"), index=True)
    gym_id: Mapped[int | None] = mapped_column(ForeignKey("gyms.id"), index=True)  # None=generic
    # Deterministic uuid5 identity (importer): survives gym renames, so re-imports never duplicate.
    key: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    equipment_type: Mapped[EquipmentType] = mapped_column(str_enum(EquipmentType, "equipment_type"))
    load_convention: Mapped[LoadConvention] = mapped_column(
        str_enum(LoadConvention, "load_convention"), default=LoadConvention.TOTAL
    )
    weight_ladder: Mapped[list[Any] | None] = mapped_column(JSON)  # available weights, sorted
    ladder_inferred: Mapped[bool] = mapped_column(Boolean, default=False)  # False = user-edited
    # False = the user no longer does this exercise: history is kept, never planned again.
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
    notes: Mapped[str | None] = mapped_column(Text)

    exercise: Mapped[Exercise] = relationship(back_populates="variants")
    gym: Mapped[Gym | None] = relationship()


class ExerciseNameMapping(Timestamps, Base):
    """Maps an external exercise name (Strong, later others) to a variant.

    The editable source of truth is data/catalog/strong_mapping.csv; this table mirrors it so
    the app/API can resolve names without reading files.
    """

    __tablename__ = "exercise_name_mappings"
    __table_args__ = (UniqueConstraint("source", "source_name", name="source_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # "strong"
    source_name: Mapped[str] = mapped_column(String(160))
    exercise_id: Mapped[int | None] = mapped_column(ForeignKey("exercises.id"))
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("exercise_variants.id"))
    status: Mapped[MappingStatus] = mapped_column(str_enum(MappingStatus, "mapping_status"))
    notes: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------- versioned config


class ConfigVersion(Base):
    """Immutable snapshot of engine config. Every engine decision references the version used."""

    __tablename__ = "config_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    source_path: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# --------------------------------------------------------------------------- training log


class ImportRun(Base):
    __tablename__ = "import_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # "strong"
    mode: Mapped[str] = mapped_column(String(16))  # dry_run | apply
    status: Mapped[str] = mapped_column(String(16), default="running")  # ok | failed
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    files: Mapped[list[Any]] = mapped_column(JSON, default=list)
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    rows_imported: Mapped[int] = mapped_column(Integer, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0)
    sessions_created: Mapped[int] = mapped_column(Integer, default=0)
    sessions_updated: Mapped[int] = mapped_column(Integer, default=0)
    sessions_unchanged: Mapped[int] = mapped_column(Integer, default=0)
    report_path: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TrainingSession(Syncable, Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_started_at_utc", "started_at_utc"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source: Mapped[Source] = mapped_column(str_enum(Source, "source"))
    source_ref: Mapped[str | None] = mapped_column(String(255))  # e.g. export file name
    gym_id: Mapped[int | None] = mapped_column(ForeignKey("gyms.id"))
    name: Mapped[str | None] = mapped_column(String(160))  # Strong "Workout Name"
    session_kind: Mapped[SessionKind] = mapped_column(str_enum(SessionKind, "session_kind"))
    started_at_local: Mapped[datetime] = mapped_column(DateTime)  # naive wall clock in `tz`
    tz: Mapped[str] = mapped_column(String(64))
    started_at_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    fidelity: Mapped[Fidelity] = mapped_column(str_enum(Fidelity, "fidelity"), default="high")
    planned_session_id: Mapped[str | None] = mapped_column(ForeignKey("planned_sessions.id"))
    import_run_id: Mapped[int | None] = mapped_column(ForeignKey("import_runs.id"))

    sets: Mapped[list[WorkoutSet]] = relationship(back_populates="session")
    gym: Mapped[Gym | None] = relationship()


class WorkoutSet(Syncable, Base):
    __tablename__ = "sets"
    __table_args__ = (Index("ix_sets_variant_session", "variant_id", "session_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("exercise_variants.id"))
    source: Mapped[Source] = mapped_column(str_enum(Source, "source"))
    source_exercise_name: Mapped[str | None] = mapped_column(String(160))
    exercise_order: Mapped[int] = mapped_column(Integer)  # block position within the session
    block_occurrence: Mapped[int] = mapped_column(Integer, default=1)  # nth block of same exercise
    set_index: Mapped[int] = mapped_column(Integer)  # 1-based position within the block
    set_type: Mapped[SetType] = mapped_column(str_enum(SetType, "set_type"), default="working")
    weight_kg: Mapped[float | None] = mapped_column(Float)  # 0 = bodyweight only
    reps: Mapped[int | None] = mapped_column(Integer)  # None for timed holds
    duration_s: Mapped[float | None] = mapped_column(Float)
    distance_m: Mapped[float | None] = mapped_column(Float)
    rir: Mapped[float | None] = mapped_column(Float)
    rpe: Mapped[float | None] = mapped_column(Float)
    e1rm_kg: Mapped[float | None] = mapped_column(Float)  # derived, see app/stats.py
    fidelity: Mapped[Fidelity] = mapped_column(str_enum(Fidelity, "fidelity"), default="high")
    notes: Mapped[str | None] = mapped_column(Text)

    session: Mapped[TrainingSession] = relationship(back_populates="sets")
    variant: Mapped[ExerciseVariant] = relationship()


class ExerciseSessionStats(Base):
    """Materialised per (session, variant) aggregates; rebuilt by app.stats.rebuild_stats()."""

    __tablename__ = "exercise_session_stats"
    __table_args__ = (UniqueConstraint("session_id", "variant_id", name="session_variant"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("exercise_variants.id"), index=True)
    session_date: Mapped[date] = mapped_column(Date, index=True)  # local date
    set_count: Mapped[int] = mapped_column(Integer)
    working_set_count: Mapped[int] = mapped_column(Integer)
    total_reps: Mapped[int] = mapped_column(Integer)
    total_volume_kg: Mapped[float] = mapped_column(Float)
    best_e1rm_kg: Mapped[float | None] = mapped_column(Float)
    e1rm_basis: Mapped[str | None] = mapped_column(String(16))  # total | added_weight
    top_set_weight_kg: Mapped[float | None] = mapped_column(Float)
    top_set_reps: Mapped[int | None] = mapped_column(Integer)
    best_hold_s: Mapped[float | None] = mapped_column(Float)
    is_e1rm_pr: Mapped[bool] = mapped_column(Boolean, default=False)
    is_weight_pr: Mapped[bool] = mapped_column(Boolean, default=False)
    rep_prs: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{weight_kg, reps}]
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# --------------------------------------------------------------------------- endurance + health


class BodyMeasurement(Syncable, Base):
    """Bodyweight log (manual now; Garmin / HealthKit later). Syncable: client-supplied uuid."""

    __tablename__ = "body_measurements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    measured_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    day: Mapped[date] = mapped_column(Date, index=True)  # local calendar day of measured_at
    weight_kg: Mapped[float] = mapped_column(Float)
    source: Mapped[MeasurementSource] = mapped_column(
        str_enum(MeasurementSource, "measurement_source"), default=MeasurementSource.MANUAL
    )
    note: Mapped[str | None] = mapped_column(Text)


class Run(Syncable, Base):
    """A recorded endurance/other activity (Garmin). Despite the name it holds *every* Garmin
    activity (running, hiking, strength, cycling, ...); `activity_type` is Garmin's typeKey and
    run-only consumers filter on `app.sources.garmin.mapping.RUN_TYPES`."""

    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source: Mapped[Source] = mapped_column(str_enum(Source, "source"))
    external_id: Mapped[str | None] = mapped_column(String(64), unique=True)  # Garmin activity id
    started_at_utc: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    started_at_local: Mapped[datetime | None] = mapped_column(DateTime)
    tz: Mapped[str | None] = mapped_column(String(64))
    run_type: Mapped[str | None] = mapped_column(String(24))  # easy | long | intervals | ...
    activity_type: Mapped[str | None] = mapped_column(String(40), index=True)  # Garmin typeKey
    title: Mapped[str | None] = mapped_column(String(200))
    duration_s: Mapped[float | None] = mapped_column(Float)
    moving_time_s: Mapped[float | None] = mapped_column(Float)
    avg_pace_s_per_km: Mapped[float | None] = mapped_column(Float)  # duration / distance
    distance_m: Mapped[float | None] = mapped_column(Float)
    avg_hr: Mapped[float | None] = mapped_column(Float)
    max_hr: Mapped[float | None] = mapped_column(Float)
    elevation_gain_m: Mapped[float | None] = mapped_column(Float)
    avg_cadence: Mapped[float | None] = mapped_column(Float)
    training_load: Mapped[float | None] = mapped_column(Float)
    # Garmin's own HR zones (z1..z5 -> seconds); the zone limits are in athlete_metrics.
    time_in_zones_s: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Nullable everywhere: not every watch / activity reports these.
    training_effect_aerobic: Mapped[float | None] = mapped_column(Float)
    training_effect_anaerobic: Mapped[float | None] = mapped_column(Float)
    vo2max: Mapped[float | None] = mapped_column(Float)
    avg_power_w: Mapped[float | None] = mapped_column(Float)
    calories: Mapped[float | None] = mapped_column(Float)
    # Laps / original .FIT fetched (NULL = still to do); fit_path relative to GARMIN_FIT_DIR.
    detail_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    fit_path: Mapped[str | None] = mapped_column(String(255))
    fidelity: Mapped[Fidelity] = mapped_column(str_enum(Fidelity, "fidelity"), default="high")
    planned_session_id: Mapped[str | None] = mapped_column(ForeignKey("planned_sessions.id"))
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)

    laps: Mapped[list[Lap]] = relationship(back_populates="run")


class Lap(Timestamps, Base):
    __tablename__ = "laps"
    __table_args__ = (UniqueConstraint("run_id", "lap_index", name="run_lap"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    lap_index: Mapped[int] = mapped_column(Integer)
    start_offset_s: Mapped[float | None] = mapped_column(Float)
    duration_s: Mapped[float | None] = mapped_column(Float)
    distance_m: Mapped[float | None] = mapped_column(Float)
    avg_hr: Mapped[float | None] = mapped_column(Float)
    max_hr: Mapped[float | None] = mapped_column(Float)
    avg_pace_s_per_km: Mapped[float | None] = mapped_column(Float)
    avg_cadence: Mapped[float | None] = mapped_column(Float)
    elevation_gain_m: Mapped[float | None] = mapped_column(Float)
    intensity: Mapped[str | None] = mapped_column(String(16))  # active | rest | warmup | cooldown

    run: Mapped[Run] = relationship(back_populates="laps")


class DailyHealth(Timestamps, Base):
    __tablename__ = "daily_health"
    __table_args__ = (UniqueConstraint("day", "source", name="day_source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date)
    source: Mapped[Source] = mapped_column(str_enum(Source, "source"))
    resting_hr: Mapped[float | None] = mapped_column(Float)
    hrv_overnight_ms: Mapped[float | None] = mapped_column(Float)
    hrv_status: Mapped[str | None] = mapped_column(String(24))
    sleep_s: Mapped[int | None] = mapped_column(Integer)
    sleep_score: Mapped[float | None] = mapped_column(Float)
    sleep_deep_s: Mapped[int | None] = mapped_column(Integer)
    sleep_light_s: Mapped[int | None] = mapped_column(Integer)
    sleep_rem_s: Mapped[int | None] = mapped_column(Integer)
    sleep_awake_s: Mapped[int | None] = mapped_column(Integer)
    hrv_weekly_avg_ms: Mapped[float | None] = mapped_column(Float)
    hrv_baseline_low_ms: Mapped[float | None] = mapped_column(Float)  # "balanced" range
    hrv_baseline_high_ms: Mapped[float | None] = mapped_column(Float)
    body_battery_charged: Mapped[float | None] = mapped_column(Float)
    body_battery_drained: Mapped[float | None] = mapped_column(Float)
    steps: Mapped[int | None] = mapped_column(Integer)
    body_battery_max: Mapped[float | None] = mapped_column(Float)
    body_battery_min: Mapped[float | None] = mapped_column(Float)
    stress_avg: Mapped[float | None] = mapped_column(Float)
    training_readiness: Mapped[float | None] = mapped_column(Float)
    body_weight_kg: Mapped[float | None] = mapped_column(Float)
    readiness_color: Mapped[str | None] = mapped_column(String(8))  # green|yellow|red (engine)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AthleteMetric(Timestamps, Base):
    """Garmin-reported athlete parameters over time (max_hr, lthr, vo2max, hr zone floors, ...).

    The engine may later *propose* updating the engine config from these; nothing is applied
    automatically. A new row is written only when the value changed.
    """

    __tablename__ = "athlete_metrics"
    __table_args__ = (UniqueConstraint("day", "metric", "source", name="day_metric_source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    metric: Mapped[str] = mapped_column(String(40), index=True)
    value: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="garmin")


class GarminRaw(Base):
    """Raw Garmin JSON per (kind, key) so parsers can be re-run without refetching."""

    __tablename__ = "garmin_raw"
    __table_args__ = (UniqueConstraint("kind", "key", name="kind_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))  # activity | daily_summary | sleep | hrv | ...
    key: Mapped[str] = mapped_column(String(64))  # activity id or ISO date
    payload: Mapped[Any] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class SyncState(Base):
    """Per (source, kind) sync cursor so backfill is resumable and sync incremental."""

    __tablename__ = "sync_state"
    __table_args__ = (UniqueConstraint("source", "kind", name="source_kind"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # "garmin"
    kind: Mapped[str] = mapped_column(String(24))
    cursor_date: Mapped[date | None] = mapped_column(Date)  # data complete up to and incl. this day
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    rows_last: Mapped[int] = mapped_column(Integer, default=0)


# --------------------------------------------------------------------------- planning + audit


class PlannedSession(Syncable, Base):
    __tablename__ = "planned_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    day: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[SessionKind] = mapped_column(str_enum(SessionKind, "session_kind"))
    status: Mapped[PlannedStatus] = mapped_column(
        str_enum(PlannedStatus, "planned_status"), default="proposed"
    )
    gym_id: Mapped[int | None] = mapped_column(ForeignKey("gyms.id"))
    config_version_id: Mapped[int | None] = mapped_column(ForeignKey("config_versions.id"))
    prescription: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notes: Mapped[str | None] = mapped_column(Text)


class DecisionLog(Base):
    """Every deterministic engine decision with its inputs, so it can be audited / replayed."""

    __tablename__ = "decisions_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(32))  # progression | deload | readiness | ...
    subject_type: Mapped[str | None] = mapped_column(String(32))  # variant | planned_session
    subject_id: Mapped[str | None] = mapped_column(String(64))
    config_version_id: Mapped[int | None] = mapped_column(ForeignKey("config_versions.id"))
    engine_version: Mapped[str | None] = mapped_column(String(32))
    planned_session_id: Mapped[str | None] = mapped_column(ForeignKey("planned_sessions.id"))
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text)


class AdvisorConsultation(Base):
    __tablename__ = "advisor_consultations"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    model: Mapped[str | None] = mapped_column(String(64))
    trigger: Mapped[str | None] = mapped_column(String(64))
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("decisions_log.id"))
    accepted: Mapped[bool | None] = mapped_column(Boolean)


class Outcome(Base):
    """Links a plan/decision to what actually happened (adherence, performance)."""

    __tablename__ = "outcomes"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    planned_session_id: Mapped[str | None] = mapped_column(ForeignKey("planned_sessions.id"))
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("decisions_log.id"))
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    adherence: Mapped[str | None] = mapped_column(String(16))  # completed | partial | skipped
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
