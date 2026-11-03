"""Custom column types and shared enums."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum

from apps.fitness.engine.core.enums import EquipmentType as EquipmentType
from apps.fitness.engine.core.enums import Fidelity as Fidelity
from apps.fitness.engine.core.enums import LoadConvention as LoadConvention
from apps.fitness.engine.core.enums import MappingStatus as MappingStatus
from apps.fitness.engine.core.enums import MeasurementSource as MeasurementSource
from apps.fitness.engine.core.enums import PlannedStatus as PlannedStatus
from apps.fitness.engine.core.enums import Progression as Progression
from apps.fitness.engine.core.enums import SessionKind as SessionKind
from apps.fitness.engine.core.enums import SetType as SetType

# Domain enums live in the pure engine core (engine.core may not import db); re-exported here so
# models, migrations and `from apps.fitness.db.types import SessionKind` keep working.
from apps.fitness.engine.core.enums import Source as Source

# Shared helpers live in core/db.py; re-exported so imports of
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
