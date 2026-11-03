"""Shared domain enums. Pure (no SQLAlchemy) so the engine core can use them; the database
columns use them through `apps.fitness.db.types`, which re-exports them."""

from __future__ import annotations

from enum import StrEnum


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


class Progression(StrEnum):
    """How an exercise progresses (`Exercise.progression`): added load (double progression),
    reps only (never a weight step), or time (timed holds)."""

    LOAD = "load"
    REPS = "reps"
    TIME = "time"


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
    WITHINGS = "withings"


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
