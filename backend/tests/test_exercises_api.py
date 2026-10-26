"""User-managed exercises (docs/api/exercises.md): a new exercise with its first variant, adding a
variant of an existing exercise (generic or a machine at a gym), editing the muscle (kept through
catalog syncs), a variant's equipment type and load step, and how pull / the engine see them."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.catalog import CatalogExercise, sync_catalog
from app.db.models import Exercise, ExerciseVariant
from app.db.types import EquipmentType
from app.engine import data
from app.exercise_service import generic_variant_key, is_user_generic_variant
from tests.advisor_helpers import AUTH


@pytest.fixture
def reruns(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr("app.api.exercises.rerun_engine", lambda reason="": calls.append(reason))
    monkeypatch.setattr("app.api.gyms.rerun_engine", lambda reason="": calls.append(reason))
    return calls


def test_endpoints_need_the_token(client):
    calls = [
        (
            "post",
            "/api/v1/exercises",
            {"name": "X", "muscle_group": "chest", "equipment_type": "cable"},
        ),
        ("post", "/api/v1/exercises/1/variants", {"equipment_type": "cable"}),
        ("patch", "/api/v1/exercises/1", {"muscle_group": "chest"}),
    ]
    for method, url, body in calls:
        r = client.request(method, url, headers={"Authorization": "Bearer wrong"}, json=body)
        assert r.status_code == 401, (method, url)
    assert AUTH["Authorization"].startswith("Bearer ")


def test_create_machine_exercise_at_a_gym(client, world, db, reruns):
    body = {
        "name": "  Pec  Deck ",
        "muscle_group": "chest",
        "equipment_type": "machine_stack",
        "gym_id": world.gym.id,
        "increment_kg": 2.5,
    }
    r = client.post("/api/v1/exercises", json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["exercise"]["name"] == "Pec Deck"
    assert out["exercise"]["muscle_group"] == "chest"
    assert out["exercise"]["body_region"] == "Chest"
    assert out["exercise"]["default_equipment"] == "machine_stack"
    v = out["variant"]
    assert v["name"] == "Pec Deck (Machine) @ Gym One"
    assert v["gym_id"] == world.gym.id and v["equipment_type"] == "machine_stack"
    assert v["increment_kg"] == 2.5 and v["exercise_id"] == out["exercise"]["id"]
    ex = db.get(Exercise, out["exercise"]["id"])
    assert ex.slug == "user_pec_deck" and ex.edited_by_user

    # the same name again is a conflict (add a variant instead)
    r = client.post("/api/v1/exercises", json={**body, "name": "pec deck"})
    assert r.status_code == 409


def test_create_generic_exercise_and_validation(client, world):
    r = client.post(
        "/api/v1/exercises",
        json={"name": "Zottman Curl", "muscle_group": "biceps", "equipment_type": "dumbbell"},
    )
    assert r.status_code == 201, r.text
    v = r.json()["variant"]
    assert v["name"] == "Zottman Curl (Dumbbell)" and v["gym_id"] is None
    assert v["load_convention"] == "per_hand"
    bad = [
        {"name": "A", "muscle_group": "elbows", "equipment_type": "cable"},
        {"name": "A", "muscle_group": "chest", "equipment_type": "kettlebell"},
        {"name": "", "muscle_group": "chest", "equipment_type": "cable"},
        {"name": "A", "muscle_group": "chest", "equipment_type": "cable", "increment_kg": 0},
        {"name": "A", "muscle_group": "chest", "equipment_type": "cable", "extra": 1},
    ]
    for body in bad:
        assert client.post("/api/v1/exercises", json=body).status_code == 422, body


def test_add_variant_machine_and_generic_is_idempotent(client, world, db, reruns):
    # a machine already at that gym is returned as is
    r = client.post(
        f"/api/v1/exercises/{world.ex['chest_press'].id}/variants",
        json={"equipment_type": "cable", "gym_id": world.other_gym.id},
    )
    assert r.status_code == 200 and r.json()["id"] == world.foreign.id

    lat = world.ex["calf_raise"].id
    r = client.post(
        f"/api/v1/exercises/{lat}/variants",
        json={"equipment_type": "cable", "gym_id": world.other_gym.id},
    )
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["gym_id"] == world.other_gym.id and first["equipment_type"] == "cable"
    assert reruns[-1] == "machine set up"
    again = client.post(
        f"/api/v1/exercises/{lat}/variants",
        json={"equipment_type": "cable", "gym_id": world.other_gym.id},
    ).json()
    assert again["id"] == first["id"]

    r = client.post(f"/api/v1/exercises/{lat}/variants", json={"equipment_type": "bodyweight"})
    assert r.status_code == 200, r.text
    gen = r.json()
    assert gen["gym_id"] is None and gen["name"] == "Calf Raise (Bodyweight)"
    row = db.get(ExerciseVariant, gen["id"])
    assert row.key == generic_variant_key(lat, "bodyweight") and is_user_generic_variant(row)
    # a tombstoned one comes back with the same id
    row.deleted_at = row.created_at
    db.commit()
    back = client.post(
        f"/api/v1/exercises/{lat}/variants", json={"equipment_type": "bodyweight"}
    ).json()
    assert back["id"] == gen["id"] and back["deleted_at"] is None

    assert (
        client.post(
            "/api/v1/exercises/99999/variants", json={"equipment_type": "cable"}
        ).status_code
        == 404
    )
    r = client.post(
        f"/api/v1/exercises/{lat}/variants", json={"equipment_type": "cable", "gym_id": 99999}
    )
    assert r.status_code == 404


def test_patch_muscle_survives_catalog_sync(client, world, db):
    ex = world.ex["chest_fly"]
    r = client.patch(f"/api/v1/exercises/{ex.id}", json={"muscle_group": "shoulders"})
    assert r.status_code == 200, r.text
    assert r.json()["muscle_group"] == "shoulders" and r.json()["body_region"] == "Shoulders"
    assert client.patch(f"/api/v1/exercises/{ex.id}", json={"muscle_group": "x"}).status_code == 422
    assert client.patch("/api/v1/exercises/99999", json={"muscle_group": "abs"}).status_code == 404

    entry = CatalogExercise(
        slug="chest_fly",
        name="Chest Fly",
        primary_muscle="chest",
        movement_pattern="horizontal_adduction",
        default_equipment=EquipmentType.MACHINE_STACK,
    )
    sync_catalog(db, {"chest_fly": entry})
    db.commit()
    db.refresh(ex)
    assert ex.primary_muscle == "shoulders"


def test_patch_variant_equipment_and_increment(client, world, db, reruns):
    v = db.scalars(select(ExerciseVariant).where(ExerciseVariant.gym_id.is_not(None))).first()
    r = client.patch(
        f"/api/v1/variants/{v.id}", json={"equipment_type": "smith", "increment_kg": 1.3}
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["equipment_type"] == "smith" and out["increment_kg"] == 1.25
    assert reruns  # engine inputs changed
    db.refresh(v)
    assert data.variant_info(v).increment_kg == 1.25

    r = client.patch(f"/api/v1/variants/{v.id}", json={"increment_kg": None})
    assert r.json()["increment_kg"] is None
    assert client.patch(f"/api/v1/variants/{v.id}", json={"increment_kg": 60}).status_code == 422
    assert (
        client.patch(f"/api/v1/variants/{v.id}", json={"equipment_type": "rope"}).status_code == 422
    )


def test_pull_carries_the_new_fields(client, world):
    client.post(
        "/api/v1/exercises",
        json={
            "name": "Sled Push",
            "muscle_group": "quads",
            "equipment_type": "other",
            "timed": True,
        },
    )
    body = client.get("/api/v1/sync/pull").json()
    ex = next(e for e in body["exercises"] if e["name"] == "Sled Push")
    assert ex["is_timed"] is True and ex["body_region"] == "Legs"
    v = next(v for v in body["variants"] if v["exercise_id"] == ex["id"])
    assert v["name"] == "Sled Push" and "increment_kg" in v
