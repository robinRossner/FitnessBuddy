"""User-managed exercises (docs/api/exercises.md): a new exercise with its first variant, adding a
variant of an existing exercise (generic or a machine at a gym), the same name with other
equipment, renaming, editing the muscle (name and muscle are kept through catalog syncs), a
variant's equipment type and load step, and how pull / the engine see them."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app import gym_service as gs
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
        ("patch", "/api/v1/exercises/1", {"name": "Y"}),
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

    # the same name, equipment and gym again is a duplicate
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


def _create(client, name, equipment, **extra):
    body = {"name": name, "muscle_group": "chest", "equipment_type": equipment, **extra}
    return client.post("/api/v1/exercises", json=body)


def test_same_name_with_other_equipment_adds_a_variant(client, world, db, reruns):
    first = _create(client, "Zottman Curl", "dumbbell").json()
    ex_id = first["exercise"]["id"]
    r = _create(client, "zottman curl", "barbell")
    assert r.status_code == 201, r.text
    assert r.json()["exercise"]["id"] == ex_id  # same movement, history stays together
    assert r.json()["variant"]["name"] == "Zottman Curl (Barbell)"
    assert r.json()["variant"]["exercise_id"] == ex_id
    assert _create(client, "Zottman Curl", "barbell").status_code == 409  # same name + equipment

    # a machine: another gym is a new variant of the exercise, the same gym is a duplicate
    g1, g2 = world.gym.id, world.other_gym.id
    deck = _create(client, "Pec Deck", "machine_stack", gym_id=g1).json()["exercise"]["id"]
    r = _create(client, "Pec Deck", "machine_stack", gym_id=g2)
    assert r.status_code == 201 and r.json()["exercise"]["id"] == deck
    assert r.json()["variant"]["name"] == "Pec Deck (Machine) @ Gym Two"
    assert _create(client, "Pec Deck", "machine_stack", gym_id=g1).status_code == 409
    # a generic variant of the same name does not clash with the machines
    assert _create(client, "Pec Deck", "bodyweight").json()["exercise"]["id"] == deck
    # one machine per exercise and gym: another machine type there becomes its own exercise
    r = _create(client, "Pec Deck", "cable", gym_id=g1)
    assert r.status_code == 201, r.text
    other = r.json()["exercise"]
    assert other["id"] != deck and other["name"] == "Pec Deck"
    assert db.get(Exercise, other["id"]).slug == "user_pec_deck_2"
    assert r.json()["variant"]["name"] == "Pec Deck (Cable) @ Gym One"
    assert _create(client, "Pec Deck", "cable", gym_id=g1).status_code == 409


def test_same_name_as_a_catalog_exercise_adds_to_it(client, world):
    r = _create(client, "Squat", "barbell")
    assert r.status_code == 201, r.text
    assert r.json()["exercise"]["id"] == world.ex["squat"].id
    assert r.json()["variant"]["name"] == "Squat (Barbell)"


def test_rename_user_exercise_renames_its_variants(client, world, db, reruns):
    g1, g2 = world.gym.id, world.other_gym.id
    ex = _create(client, "Pec Deck", "machine_stack", gym_id=g1, increment_kg=2.5).json()
    ex_id = ex["exercise"]["id"]
    _create(client, "Pec Deck", "machine_stack", gym_id=g2)
    _create(client, "Pec Deck", "dumbbell")
    cursor = client.get("/api/v1/sync/pull").json()["cursor"]

    r = client.patch(f"/api/v1/exercises/{ex_id}", json={"name": "  Butterfly  "})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["name"] == "Butterfly"
    assert sorted(v["name"] for v in out["variants"]) == [
        "Butterfly (Dumbbell)",
        "Butterfly (Machine) @ Gym One",
        "Butterfly (Machine) @ Gym Two",
    ]
    assert not reruns  # not a program exercise: the plan is untouched
    row = db.get(Exercise, ex_id)
    db.refresh(row)
    assert row.name == "Butterfly" and row.edited_by_user
    kept = db.scalars(select(ExerciseVariant).where(ExerciseVariant.exercise_id == ex_id)).all()
    assert all(v.key and v.deleted_at is None for v in kept)  # identity (id, key) unchanged

    # the next incremental pull carries the renamed exercise and variants
    pull = client.get("/api/v1/sync/pull", params={"since": cursor}).json()
    assert [e["name"] for e in pull["exercises"]] == ["Butterfly"]
    assert {v["name"] for v in pull["variants"]} >= {"Butterfly (Dumbbell)"}

    # the muscle alone does not rename; both together work
    r = client.patch(f"/api/v1/exercises/{ex_id}", json={"muscle_group": "shoulders"})
    assert r.json()["name"] == "Butterfly" and "variants" not in r.json()
    r = client.patch(
        f"/api/v1/exercises/{ex_id}", json={"name": "Reverse Fly", "muscle_group": "rear_delts"}
    )
    assert r.json()["name"] == "Reverse Fly" and r.json()["muscle_group"] == "rear_delts"


def test_rename_validation_and_duplicates(client, world, db):
    deck = _create(client, "Pec Deck", "machine_stack", gym_id=world.gym.id).json()["exercise"]
    _create(client, "Zottman Curl", "dumbbell")
    for bad in ({"name": ""}, {"name": "   "}, {"name": "x" * 101}, {"name": 5}):
        r = client.patch(f"/api/v1/exercises/{deck['id']}", json=bad)
        assert r.status_code == 422, bad
    assert client.patch("/api/v1/exercises/99999", json={"name": "A"}).status_code == 404

    # a name that exists is fine while the equipment differs ...
    r = client.patch(f"/api/v1/exercises/{deck['id']}", json={"name": "zottman curl"})
    assert r.status_code == 200 and r.json()["name"] == "zottman curl"
    names = {n for (n,) in db.execute(select(Exercise.name)).all()}
    assert {"zottman curl", "Zottman Curl"} <= names
    # ... and a duplicate (same name, same equipment) is refused
    client.post(f"/api/v1/exercises/{deck['id']}/variants", json={"equipment_type": "dumbbell"})
    r = client.patch(f"/api/v1/exercises/{deck['id']}", json={"name": "Zottman Curl"})
    assert r.status_code == 409
    # renaming to the same name (only the case) is not a duplicate of itself
    hammer = _create(client, "Hammer Curl", "dumbbell").json()["exercise"]
    r = client.patch(f"/api/v1/exercises/{hammer['id']}", json={"name": "HAMMER CURL"})
    assert r.status_code == 200


def test_rename_keeps_the_plan_pointing_at_the_variant(client, world, db, env):
    squat = world.ex["squat"]
    r = client.patch(f"/api/v1/exercises/{squat.id}", json={"name": "Back Squat"})
    assert r.status_code == 200, r.text
    assert [v["name"] for v in r.json()["variants"]] == ["Back Squat (Smith Machine)"]
    db.expire_all()
    assert db.get(ExerciseVariant, world.squat.id).name == "Back Squat (Smith Machine)"
    assert gs.program_entries(db, env)["squat"].variant == "Back Squat (Smith Machine)"
    # the other program lines are untouched
    assert gs.program_entries(db, env)["leg_extension"].variant == "Leg Extension (Machine)"
    # history is attached to the same variant id
    assert data.resolve_variant(db, "Back Squat (Smith Machine)", None).id == world.squat.id


def test_renamed_program_variant_keeps_its_rep_range_in_the_pull(client, world, db, env):
    from app.engine import config_service as cs

    cs.ensure_seeded(db, env.engine_config_path)
    cs.create_version(
        db, {"exercise_overrides.squat.rep_range": [6, 10]}, author="user", reason="t"
    )
    db.commit()

    def squat_range():
        body = client.get("/api/v1/sync/pull").json()
        return next(v for v in body["variants"] if v["id"] == world.squat.id)["rep_range"]

    assert squat_range() == [6, 10]  # an override on top of program.yaml
    r = client.patch(f"/api/v1/exercises/{world.ex['squat'].id}", json={"name": "Back Squat"})
    assert r.status_code == 200, r.text
    assert r.json()["variants"][0]["rep_range"] == [6, 10]
    assert squat_range() == [6, 10]  # the plan line now has the new name; the range follows it


def test_rename_survives_catalog_sync_and_clashing_variant_names(client, world, db):
    ex = world.ex["chest_fly"]
    assert client.patch(f"/api/v1/exercises/{ex.id}", json={"name": "Pec Fly"}).status_code == 200
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
    assert ex.name == "Pec Fly"

    # a variant of another exercise already has the name the rename would give
    r = client.patch(f"/api/v1/exercises/{world.ex['leg_curl'].id}", json={"name": "Pec Fly"})
    assert r.status_code == 409
    other = world.ex["leg_extension"]
    db.add(
        ExerciseVariant(
            exercise_id=other.id, gym_id=None, key="x", name="Cable Row (Machine)",
            equipment_type="machine_stack",
        )
    )  # fmt: skip
    db.commit()
    r = client.patch(f"/api/v1/exercises/{world.ex['leg_curl'].id}", json={"name": "Cable Row"})
    assert r.status_code == 409 and "Cable Row (Machine)" in r.text
