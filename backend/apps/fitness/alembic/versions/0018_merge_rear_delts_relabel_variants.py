"""merge rear_delts into shoulders; re-label variants whose type label is stale

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-23 12:00:00.000000

Data only. (1) `exercises.primary_muscle = 'rear_delts'` becomes 'shoulders' and 'rear_delts' in
`secondary_muscles` becomes 'shoulders' (deduplicated). (2) A variant name is built from its
equipment type at creation ("Lateral Raise (Machine)"); changing the type later left the old
label behind. A name whose base ends in the automatic label of ANOTHER type gets the label of its
current type (none for `other`; "(Machine)" also fits plate-loaded and smith machines), keeping the " @ <gym>" suffix; names without a label, labels of
the right type and renames that would clash within the gym are left alone. A program line that
names the old label is not rewritten: the engine falls back to any active variant of the exercise
at the gym (`resolve_variant`). The `exercises` / `exercise_variants` server_seq triggers stamp
every UPDATE, so the clients receive the changes. Not reversible (the old values are not kept).
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# equipment_type -> label in the variant name (None: no label); same as gyms.service
_LABEL = {
    "barbell": "Barbell",
    "dumbbell": "Dumbbell",
    "machine_stack": "Machine",
    "plate_loaded": "Plate Loaded",
    "cable": "Cable",
    "bodyweight": "Bodyweight",
    "smith": "Smith Machine",
    "other": None,
}
_LABELS = {label for label in _LABEL.values() if label}


def _relabel(name: str, equipment_type: str) -> str:
    base, sep, gym = name.partition(" @ ")
    if equipment_type not in _LABEL:
        return name
    for label in _LABELS:
        suffix = f" ({label})"
        if label == "Machine" and equipment_type in ("plate_loaded", "smith"):
            continue  # "(Machine)" is a fair name for any machine
        if base.endswith(suffix) and len(base) > len(suffix) and label != _LABEL[equipment_type]:
            stem = base[: -len(suffix)]
            new = f"{stem} ({_LABEL[equipment_type]})" if _LABEL[equipment_type] else stem
            return new + sep + gym
    return name


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE exercises SET primary_muscle = 'shoulders' WHERE primary_muscle = 'rear_delts'")
    )
    for ex_id, raw in bind.execute(
        sa.text("SELECT id, secondary_muscles FROM exercises WHERE secondary_muscles LIKE '%rear_delts%'")
    ).fetchall():
        muscles = json.loads(raw) if isinstance(raw, str) else list(raw or [])
        merged: list[str] = []
        for m in muscles:
            m = "shoulders" if m == "rear_delts" else m
            if m not in merged:
                merged.append(m)
        bind.execute(
            sa.text("UPDATE exercises SET secondary_muscles = :m WHERE id = :i"),
            {"m": json.dumps(merged), "i": ex_id},
        )

    rows = bind.execute(
        sa.text("SELECT id, gym_id, name, equipment_type FROM exercise_variants")
    ).fetchall()
    taken = {(r.gym_id, r.name) for r in rows}
    for r in rows:
        new = _relabel(r.name, str(r.equipment_type).lower().rsplit(".", 1)[-1])
        if new == r.name or (r.gym_id, new) in taken:
            continue
        bind.execute(
            sa.text("UPDATE exercise_variants SET name = :n WHERE id = :i"), {"n": new, "i": r.id}
        )
        taken.discard((r.gym_id, r.name))
        taken.add((r.gym_id, new))


def downgrade() -> None:
    """Data migration: the merged muscle and the old labels are not kept."""
