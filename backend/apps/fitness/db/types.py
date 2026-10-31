"""Custom column types and shared enums."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum

# Shared helpers live in core/db.py (receipts uses them too); re-exported so imports of
# `apps.fitness.db.types.UTCDateTime` (Alembic revisions, models) keep working.
from core.db import UTCDateTime as UTCDateTime
from core.db import iso_z as iso_z
from core.db import utcnow as utcnow


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Portable enum: VARCHAR + CHECK constraint, stores `.value`."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=24,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


class Source(StrEnum):
    STRONG_IMPORT = "strong_import"
    APP = "app"
    GARMIN = "garmin"


class EquipmentType(StrEnum):
    BARBELL = "barbell"
    DUMBBELL = "dumbbell"
    MACHINE_STACK = "machine_stack"
    PLATE_LOADED = "plate_loaded"
    CABLE = "cable"
    BODYWEIGHT = "bodyweight"
    SMITH = "smith"
    OTHER = "other"


class LoadConvention(StrEnum):
    TOTAL = "total"  # logged weight is the whole load (bar+plates, stack pin, added weight)
    PER_HAND = "per_hand"  # logged weight is one implement (one dumbbell)
    ASSISTANCE = "assistance"  # logged weight is assistance (assisted / band pull-up): lower=harder


class SetType(StrEnum):
    WORKING = "working"
    WARMUP = "warmup"
    DROP = "drop"
    FAILURE = "failure"


class SessionKind(StrEnum):
    UPPER = "upper"
    LOWER = "lower"
    FULL = "full"  # upper and lower body in one session (inferred from the exercises)
    PUSH = "push"
    PULL = "pull"
    LEGS = "legs"
    CALISTHENICS = "calisthenics"
    EASY_RUN = "easy_run"
    INTERVALS = "intervals"
    LONG_RUN = "long_run"
    REST = "rest"
    OTHER = "other"


class MeasurementSource(StrEnum):
    MANUAL = "manual"
    GARMIN = "garmin"
    HEALTHKIT = "healthkit"


class Fidelity(StrEnum):
    HIGH = "high"
    LOW = "low"  # e.g. cardio rows from Strong (no HR, no laps)


class MappingStatus(StrEnum):
    AUTO = "auto"
    CONFIRMED = "confirmed"
    UNMAPPED = "unmapped"


class PlannedStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    DONE = "done"
    SKIPPED = "skipped"
    MODIFIED = "modified"
