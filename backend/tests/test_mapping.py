from apps.fitness.db.catalog import load_catalog
from apps.fitness.importers.strong.mapping import (
    MappingRow,
    merge_mapping,
    read_mapping,
    suggest,
    validate_mapping,
    write_mapping,
)
from apps.fitness.settings import FITNESS_DIR

CATALOG = load_catalog(FITNESS_DIR / "data" / "catalog" / "exercises.yaml")


def test_merge_preserves_user_edits_and_adds_new_names(tmp_path):
    path = tmp_path / "m.csv"
    edited = MappingRow(
        strong_name="Chest Fly",
        canonical_exercise="chest_fly",
        variant_name="Pec Deck",
        equipment_type="machine_stack",
        load_convention="total",
        status="confirmed",
        notes="my edit",
    )
    write_mapping(path, {"Chest Fly": edited})
    merged = merge_mapping(read_mapping(path), {"Chest Fly", "Hammer Curl (Dumbbell)"}, CATALOG)
    write_mapping(path, merged.rows)

    rows = read_mapping(path)
    assert rows["Chest Fly"] == edited
    assert merged.added == ["Hammer Curl (Dumbbell)"]
    new = rows["Hammer Curl (Dumbbell)"]
    assert new.status == "unmapped"  # never imported until the user confirms
    assert (new.canonical_exercise, new.equipment_type, new.load_convention) == (
        "hammer_curl",
        "dumbbell",
        "per_hand",
    )


def test_merge_reports_names_not_seen(tmp_path):
    existing = {"Old Exercise": MappingRow(strong_name="Old Exercise", status="confirmed")}
    merged = merge_mapping(existing, set(), CATALOG)
    assert merged.not_seen == ["Old Exercise"] and "Old Exercise" in merged.rows


def test_suggest_assisted_and_unknown():
    s = suggest("Pull Up (Assisted)", CATALOG)
    assert s.canonical_exercise == "pull_up" and s.load_convention == "assistance"
    unknown = suggest("Zercher Carry", CATALOG)
    assert unknown.canonical_exercise == "" and unknown.status == "unmapped"


def test_validate_mapping():
    rows = {
        "A": MappingRow("A", "nope", "A", "barbell", "total", "auto"),
        "B": MappingRow("B", "squat", "B", "laser", "total", "confirmed"),
        "C": MappingRow("C", "", "", "", "", "unmapped"),  # unmapped rows are not validated
        "D": MappingRow("D", "squat", "Same", "barbell", "total", "auto"),
        "E": MappingRow("E", "squat", "Same", "smith", "total", "auto"),
        "F": MappingRow("F", "squat", "F", "barbell", "total", "maybe"),
    }
    errors = validate_mapping(rows, CATALOG)
    assert any("unknown canonical_exercise 'nope'" in e for e in errors)
    assert any("invalid equipment_type 'laser'" in e for e in errors)
    assert any("conflicting specs" in e for e in errors)
    assert any("invalid status 'maybe'" in e for e in errors)
    assert not any(e.startswith("C:") for e in errors)


def test_shipped_mapping_is_valid():
    rows = read_mapping(FITNESS_DIR / "data" / "catalog" / "strong_mapping.csv")
    assert len(rows) >= 68
    assert validate_mapping(rows, CATALOG) == []
