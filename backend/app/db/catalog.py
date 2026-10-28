"""Canonical exercise catalog (data/catalog/exercises.yaml) and its sync into the DB."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Exercise
from app.db.types import EquipmentType

BODY_REGIONS = ("Chest", "Back", "Shoulders", "Arms", "Legs", "Core", "Other")
MUSCLE_REGION = {
    "chest": "Chest",
    "back": "Back",
    "lats": "Back",
    "traps": "Back",
    "shoulders": "Shoulders",
    "rear_delts": "Shoulders",
    "biceps": "Arms",
    "triceps": "Arms",
    "forearms": "Arms",
    "quads": "Legs",
    "hamstrings": "Legs",
    "glutes": "Legs",
    "calves": "Legs",
    "adductors": "Legs",
    "abs": "Core",
}


def region_of_muscle(muscle: str | None) -> str:
    """Coarse body region of a primary muscle; 'Other' for anything unknown (e.g. full_body)."""
    return MUSCLE_REGION.get(muscle or "", "Other")


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
    body_region: str = "Other"
    secondary_muscles: tuple[str, ...] = ()
    aliases: tuple[str, ...] = field(default=())


def load_catalog(path: Path) -> dict[str, CatalogExercise]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, CatalogExercise] = {}
    for item in data["exercises"]:
        region = item.get("body_region") or region_of_muscle(item["primary_muscle"])
        if region not in BODY_REGIONS:
            raise ValueError(f"catalog {item['slug']}: unknown body_region {region!r}")
        ex = CatalogExercise(
            slug=item["slug"],
            name=item["name"],
            primary_muscle=item["primary_muscle"],
            movement_pattern=item["movement_pattern"],
            default_equipment=EquipmentType(item["default_equipment"]),
            e1rm=bool(item.get("e1rm", True)),
            timed=bool(item.get("timed", False)),
            bodyweight_loaded=bool(item.get("bodyweight_loaded", False)),
            body_region=region,
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
        if not ex.edited_by_user:  # the user's name and muscle choice in the app win
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
