"""Canonical exercise catalog (data/catalog/exercises.yaml) and its sync into the DB."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Exercise
from app.db.types import EquipmentType


@dataclass(frozen=True)
class CatalogExercise:
    slug: str
    name: str
    primary_muscle: str
    movement_pattern: str
    default_equipment: EquipmentType
    e1rm: bool = True
    timed: bool = False
    bodyweight_loaded: bool = False
    secondary_muscles: tuple[str, ...] = ()
    aliases: tuple[str, ...] = field(default=())


def load_catalog(path: Path) -> dict[str, CatalogExercise]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, CatalogExercise] = {}
    for item in data["exercises"]:
        ex = CatalogExercise(
            slug=item["slug"],
            name=item["name"],
            primary_muscle=item["primary_muscle"],
            movement_pattern=item["movement_pattern"],
            default_equipment=EquipmentType(item["default_equipment"]),
            e1rm=bool(item.get("e1rm", True)),
            timed=bool(item.get("timed", False)),
            bodyweight_loaded=bool(item.get("bodyweight_loaded", False)),
            secondary_muscles=tuple(item.get("secondary_muscles", ())),
            aliases=tuple(a.lower() for a in item.get("aliases", ())),
        )
        if ex.slug in out:
            raise ValueError(f"duplicate catalog slug {ex.slug}")
        out[ex.slug] = ex
    return out


def sync_catalog(db: Session, catalog: dict[str, CatalogExercise]) -> dict[str, Exercise]:
    """Upsert catalog entries by slug. Returns slug -> Exercise."""
    existing = {e.slug: e for e in db.scalars(select(Exercise))}
    for c in catalog.values():
        ex = existing.get(c.slug)
        if ex is None:
            ex = Exercise(slug=c.slug)
            db.add(ex)
            existing[c.slug] = ex
        ex.name = c.name
        ex.primary_muscle = c.primary_muscle
        ex.secondary_muscles = list(c.secondary_muscles)
        ex.movement_pattern = c.movement_pattern
        ex.default_equipment_type = c.default_equipment
        ex.e1rm_meaningful = c.e1rm
        ex.is_timed = c.timed
        ex.bodyweight_loaded = c.bodyweight_loaded
    db.flush()
    return existing
