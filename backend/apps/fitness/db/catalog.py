"""Canonical exercise catalog (data/catalog/exercises.yaml) and its sync into the DB."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.db.models import Exercise
from apps.fitness.db.types import EquipmentType, Progression
from apps.fitness.engine.core.config_files import cached_yaml

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
    progression: str = "load"  # load | reps | time (timed: true means time)
    bodyweight_loaded: bool = False
    body_region: str = "Other"
    secondary_muscles: tuple[str, ...] = ()
    aliases: tuple[str, ...] = field(default=())


def load_catalog(path: Path) -> dict[str, CatalogExercise]:
    data = cached_yaml(path)  # shared parse (cached until the file changes): read only
    out: dict[str, CatalogExercise] = {}
    for item in data["exercises"]:
        region = item.get("body_region") or region_of_muscle(item["primary_muscle"])
        if region not in BODY_REGIONS:
            raise ValueError(f"catalog {item['slug']}: unknown body_region {region!r}")
        progression = str(item.get("progression") or ("time" if item.get("timed") else "load"))
        if progression not in {p.value for p in Progression}:
            raise ValueError(f"catalog {item['slug']}: unknown progression {progression!r}")
        if "timed" in item and bool(item["timed"]) != (progression == "time"):
            raise ValueError(f"catalog {item['slug']}: timed and progression contradict")
        ex = CatalogExercise(
            slug=item["slug"],
            name=item["name"],
            primary_muscle=item["primary_muscle"],
            movement_pattern=item["movement_pattern"],
            default_equipment=EquipmentType(item["default_equipment"]),
            e1rm=bool(item.get("e1rm", True)),
            timed=progression == "time",
            progression=progression,
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
        # a user's choice (PATCH progression) wins for load / reps; time always follows the catalog
        if not (
            ex.edited_by_user and ex.progression in ("load", "reps") and c.progression != "time"
        ):
            ex.progression = c.progression
        ex.bodyweight_loaded = c.bodyweight_loaded
    db.flush()
    return existing
