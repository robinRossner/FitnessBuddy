"""Editable Strong-name -> variant mapping (data/catalog/strong_mapping.csv).

The CSV is the source of truth that the user edits. `merge_mapping` adds newly seen names as
`unmapped` (with a suggestion pre-filled) and never touches existing rows.
"""

from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from app.db.catalog import CatalogExercise
from app.db.types import EquipmentType, LoadConvention, MappingStatus

FIELDNAMES = [
    "strong_name",
    "canonical_exercise",
    "variant_name",
    "equipment_type",
    "load_convention",
    "status",
    "notes",
]

# Strong's parenthetical equipment suffix -> equipment type
_EQUIPMENT_HINTS = {
    "barbell": EquipmentType.BARBELL,
    "dumbbell": EquipmentType.DUMBBELL,
    "machine": EquipmentType.MACHINE_STACK,
    "cable": EquipmentType.CABLE,
    "smith machine": EquipmentType.SMITH,
    "plate loaded": EquipmentType.PLATE_LOADED,
    "assisted": EquipmentType.MACHINE_STACK,
    "band": EquipmentType.BODYWEIGHT,
    "bodyweight": EquipmentType.BODYWEIGHT,
    "weighted": EquipmentType.BODYWEIGHT,
    "kettlebell": EquipmentType.OTHER,
}


@dataclass
class MappingRow:
    strong_name: str
    canonical_exercise: str = ""
    variant_name: str = ""
    equipment_type: str = ""
    load_convention: str = ""
    status: str = MappingStatus.UNMAPPED.value
    notes: str = ""

    @property
    def importable(self) -> bool:
        return self.status in (MappingStatus.AUTO, MappingStatus.CONFIRMED)


def read_mapping(path: Path) -> dict[str, MappingRow]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        known = {f.name for f in fields(MappingRow)}
        out: dict[str, MappingRow] = {}
        for rec in reader:
            name = (rec.get("strong_name") or "").strip()
            if not name:
                continue
            values = {k: (v or "").strip() for k, v in rec.items() if k in known}
            values["status"] = (values.get("status") or MappingStatus.UNMAPPED.value).lower()
            out[name] = MappingRow(**values)
        return out


def write_mapping(path: Path, rows: dict[str, MappingRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        for name in sorted(rows, key=str.casefold):
            writer.writerow(asdict(rows[name]))


def _split_name(name: str) -> tuple[str, str | None]:
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", name)
    return (m[1].strip(), m[2].strip().lower()) if m else (name.strip(), None)


def suggest(name: str, catalog: dict[str, CatalogExercise]) -> MappingRow:
    """Best-effort suggestion for a new name; always status=unmapped so the user confirms."""
    base, paren = _split_name(name)
    base_l = base.lower()
    match: CatalogExercise | None = None
    for ex in catalog.values():  # exact alias first, then longest alias contained in the name
        if base_l in ex.aliases or base_l == ex.name.lower():
            match = ex
            break
    if match is None:
        best = 0
        for ex in catalog.values():
            for alias in ex.aliases:
                if alias in base_l and len(alias) > best:
                    match, best = ex, len(alias)
    equipment = _EQUIPMENT_HINTS.get(paren or "")
    if equipment is None and match is not None:
        equipment = match.default_equipment
    convention = LoadConvention.TOTAL
    if paren in ("assisted", "band"):
        convention = LoadConvention.ASSISTANCE
    elif equipment is EquipmentType.DUMBBELL:
        convention = LoadConvention.PER_HAND
    return MappingRow(
        strong_name=name,
        canonical_exercise=match.slug if match else "",
        variant_name=name,
        equipment_type=equipment.value if equipment else "",
        load_convention=convention.value,
        status=MappingStatus.UNMAPPED.value,
        notes="new name: suggestion only, review and set status=confirmed"
        if match
        else "new name: no catalog match, fill in and set status=confirmed",
    )


@dataclass
class MergeResult:
    rows: dict[str, MappingRow]
    added: list[str]
    not_seen: list[str]  # in the mapping file but not in any export


def merge_mapping(
    existing: dict[str, MappingRow], seen_names: set[str], catalog: dict[str, CatalogExercise]
) -> MergeResult:
    rows = dict(existing)
    added = sorted((n for n in seen_names if n not in rows), key=str.casefold)
    for name in added:
        rows[name] = suggest(name, catalog)
    not_seen = sorted((n for n in rows if n not in seen_names), key=str.casefold)
    return MergeResult(rows=rows, added=added, not_seen=not_seen)


def validate_mapping(rows: dict[str, MappingRow], catalog: dict[str, CatalogExercise]) -> list[str]:
    """Errors for importable rows (auto/confirmed). Unmapped rows are not validated."""
    errors: list[str] = []
    variant_specs: dict[str, tuple[str, str, str]] = {}
    for r in rows.values():
        if r.status not in {s.value for s in MappingStatus}:
            errors.append(f"{r.strong_name}: invalid status {r.status!r}")
            continue
        if not r.importable:
            continue
        if r.canonical_exercise not in catalog:
            errors.append(f"{r.strong_name}: unknown canonical_exercise {r.canonical_exercise!r}")
        if r.equipment_type not in {e.value for e in EquipmentType}:
            errors.append(f"{r.strong_name}: invalid equipment_type {r.equipment_type!r}")
        if r.load_convention not in {c.value for c in LoadConvention}:
            errors.append(f"{r.strong_name}: invalid load_convention {r.load_convention!r}")
        if not r.variant_name:
            errors.append(f"{r.strong_name}: empty variant_name")
        spec = (r.canonical_exercise, r.equipment_type, r.load_convention)
        prev = variant_specs.setdefault(r.variant_name, spec)
        if prev != spec:
            errors.append(
                f"variant {r.variant_name!r} mapped with conflicting specs {prev} vs {spec}"
            )
    return errors
