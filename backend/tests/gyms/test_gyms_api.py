"""Gyms and machines API (docs/api/gyms.md): list / create / rename / delete gyms, the current gym
(engine config `gym.current_id`), machines per gym, variant PATCH, ladder preview, the
deterministic variant key and re-import safety, and the baseline state of a new machine."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from apps.fitness.db.models import Exercise, ExerciseVariant, Gym
from apps.fitness.db.types import utcnow
from apps.fitness.engine.core import lifting as L
from apps.fitness.engine.core.config import EngineConfig
from apps.fitness.engine.store import config_service as cs
from apps.fitness.engine.store import service as engine
from apps.fitness.gyms import service as gs
from apps.fitness.importers.strong.importer import ImportOptions, run_import, variant_uuid
from apps.fitness.settings import FITNESS_DIR
from tests.support.world import AUTH, TODAY


@pytest.fixture
def reruns(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        "apps.fitness.api.gyms.rerun_engine", lambda reason="": calls.append(reason)
    )
    return calls


def ex_id(world, slug: str) -> int:
    return world.ex[slug].id


def machine(client, gym_id: int, slug_id: int, **body):
    body.setdefault("equipment_type", "machine_stack")
    return client.post(f"/api/v1/gyms/{gym_id}/machines", json={"exercise_id": slug_id, **body})


def new_gym(client, name="Gym Three") -> dict:
    r = client.post("/api/v1/gyms", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def history_at(world, db, variant, gym, day: date, weight: float, reps: list[int]):
    s = world.lift_session(day, "upper", [(variant, weight, reps)])
    s.gym_id = gym.id
    db.commit()
    return s


# --------------------------------------------------------------------------- auth


def test_every_endpoint_needs_the_token(client):
    calls = [
        ("get", "/api/v1/gyms", None),
        ("post", "/api/v1/gyms", {"name": "X"}),
        ("patch", "/api/v1/gyms/1", {"name": "X"}),
        ("delete", "/api/v1/gyms/1", None),
        ("put", "/api/v1/gyms/current", {"gym_id": 1}),
        ("get", "/api/v1/gyms/1/machines", None),
        ("post", "/api/v1/gyms/1/machines", {"exercise_id": 1, "equipment_type": "cable"}),
        ("patch", "/api/v1/variants/1", {"active": False}),
        ("post", "/api/v1/weight-ladder/preview", {"start": 5, "step": 5, "count": 3}),
    ]
    for method, url, body in calls:
        for headers in ({"Authorization": "Bearer wrong"}, {}):
            kw = {"json": body} if body is not None else {}
            # an empty header dict still sends the client's default; override it explicitly
            r = client.request(method, url, headers={"Authorization": "", **headers}, **kw)
            assert r.status_code == 401, (method, url, r.status_code)
    assert AUTH["Authorization"].startswith("Bearer ")


# --------------------------------------------------------------------------- gyms


def test_list_gyms(world, client):
    body = client.get("/api/v1/gyms").json()
    # nothing configured: the old default (gym of the newest session)
    assert body["current_gym_id"] == world.gym.id
    gyms = {g["name"]: g for g in body["gyms"]}
    assert set(gyms) == {"Gym One", "Gym Two"}
    one = gyms["Gym One"]
    assert set(one) == {"id", "name", "created_at", "machine_count", "is_current", "source"}
    assert one["is_current"] is True and gyms["Gym Two"]["is_current"] is False
    assert one["source"] == "import" and one["machine_count"] == 1  # Chest Press (Plate)
    assert one["created_at"].endswith("Z")


def test_create_gym_is_a_user_gym_with_unique_name(world, client):
    g = new_gym(client, "  Home Gym ")
    assert g["name"] == "Home Gym" and g["source"] == "user"
    assert g["machine_count"] == 0 and g["is_current"] is False
    for dup in ("home gym", "HOME GYM", "Gym One"):
        assert client.post("/api/v1/gyms", json={"name": dup}).status_code == 409
    assert client.post("/api/v1/gyms", json={"name": "   "}).status_code == 422
    assert client.post("/api/v1/gyms", json={}).status_code == 422
    listed = client.get("/api/v1/gyms").json()["gyms"]
    assert [x["name"] for x in listed].count("Home Gym") == 1


def test_a_deleted_gym_name_can_be_used_again(world, client, db):
    g = new_gym(client, "Temp")
    assert client.delete(f"/api/v1/gyms/{g['id']}").status_code == 204
    again = new_gym(client, "temp")
    assert again["id"] == g["id"] and again["name"] == "temp"


def test_rename_gym_renames_its_machines(world, client, db):
    g = new_gym(client)
    r = machine(client, g["id"], ex_id(world, "chest_fly"))
    assert r.json()["variant"]["name"] == "Chest Fly (Machine) @ Gym Three"
    out = client.patch(f"/api/v1/gyms/{g['id']}", json={"name": "Garage"})
    assert out.status_code == 200 and out.json()["name"] == "Garage"
    items = client.get(f"/api/v1/gyms/{g['id']}/machines").json()["items"]
    fly = next(i for i in items if i["exercise_name"] == "Chest Fly")
    assert fly["variant"]["name"] == "Chest Fly (Machine) @ Garage"
    assert client.patch(f"/api/v1/gyms/{g['id']}", json={"name": "gym one"}).status_code == 409
    assert client.patch("/api/v1/gyms/9999", json={"name": "X"}).status_code == 404
    assert client.patch(f"/api/v1/gyms/{g['id']}", json={"name": ""}).status_code == 422


def test_delete_gym_rules(world, client, db):
    g = new_gym(client)
    assert client.delete("/api/v1/gyms/9999").status_code == 404
    # sessions: Gym One has history, Gym Two does not
    assert client.delete(f"/api/v1/gyms/{world.gym.id}").status_code == 409
    # the current gym cannot go
    client.put("/api/v1/gyms/current", json={"gym_id": g["id"]})
    assert client.delete(f"/api/v1/gyms/{g['id']}").status_code == 409
    assert client.delete(f"/api/v1/gyms/{world.other_gym.id}").status_code == 204
    names = [x["name"] for x in client.get("/api/v1/gyms").json()["gyms"]]
    assert "Gym Two" not in names
    db.expire_all()
    assert db.get(Gym, world.other_gym.id).deleted_at is not None  # soft delete
    assert client.delete(f"/api/v1/gyms/{world.other_gym.id}").status_code == 404
    assert (
        client.put("/api/v1/gyms/current", json={"gym_id": world.other_gym.id}).status_code == 404
    )


# --------------------------------------------------------------------------- current gym


def test_put_current_gym_stores_a_user_config_version_and_reruns(world, client, db, reruns):
    g = new_gym(client)
    before = [v.version for v in cs.history(db)]
    r = client.put("/api/v1/gyms/current", json={"gym_id": g["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_gym_id"] == g["id"]
    # five of the six program exercises are machines / cables / smith; bench is a barbell
    assert body["unmapped_count"] == 5
    assert reruns == ["current gym"]
    db.expire_all()
    active = cs.get_active(db)
    assert active.version not in before and active.author == "user"
    assert active.reason == "Current gym set to Gym Three"
    assert active.content["gym"] == {"current_id": g["id"]}
    assert active.diff == {"gym.current_id": {"old": None, "new": g["id"]}}
    EngineConfig.model_validate(active.content)  # the engine still loads it
    listing = client.get("/api/v1/gyms").json()
    assert listing["current_gym_id"] == g["id"]
    assert [x["is_current"] for x in listing["gyms"] if x["id"] == g["id"]] == [True]


def test_put_current_gym_is_idempotent(world, client, db, reruns):
    g = new_gym(client)
    first = client.put("/api/v1/gyms/current", json={"gym_id": g["id"]}).json()
    n = len(cs.history(db))
    second = client.put("/api/v1/gyms/current", json={"gym_id": g["id"]}).json()
    assert first == second
    db.expire_all()
    assert len(cs.history(db)) == n  # no new version
    assert client.put("/api/v1/gyms/current", json={"gym_id": "x"}).status_code == 422


def test_unmapped_count_goes_down_as_machines_are_set_up(world, client, reruns):
    g = new_gym(client)
    assert (
        client.put("/api/v1/gyms/current", json={"gym_id": g["id"]}).json()["unmapped_count"] == 5
    )
    machine(client, g["id"], ex_id(world, "chest_fly"))
    out = client.put("/api/v1/gyms/current", json={"gym_id": g["id"]}).json()
    assert out["unmapped_count"] == 4


# --------------------------------------------------------------------------- machines list


def test_machines_list_order_in_program_first_and_previous_hint(world, client, db):
    history_at(world, db, world.foreign, world.other_gym, date(2026, 10, 1), 65.0, [9, 8])
    g = new_gym(client)
    body = client.get(f"/api/v1/gyms/{g['id']}/machines").json()
    assert body["gym"]["id"] == g["id"]
    items = body["items"]
    names = [i["exercise_name"] for i in items]
    # program order (upper then lower), only the gym-bound ones, then the others with history
    assert names == [
        "Incline Bench Press",
        "Chest Fly",
        "Lateral Raise",
        "Squat",
        "Leg Extension",
        "Chest Press",
    ]
    assert [i["in_program"] for i in items] == [True] * 5 + [False]
    assert all(i["variant"] is None for i in items)
    press = items[-1]
    assert press["previous"] == {
        "gym_name": "Gym Two",
        "variant_id": world.foreign.id,
        "last_top_set": {"weight_kg": 65.0, "reps": 9},
        "weight_ladder": None,
    }
    assert press["equipment_type"] == "plate_loaded"
    assert items[0]["previous"] is None  # generic variant only: nothing at another gym
    assert set(items[0]) == {
        "exercise_id", "exercise_name", "body_region", "in_program", "equipment_type",
        "variant", "previous",
    }  # fmt: skip
    # Gym One: Chest Press (Plate) is set up there, with no sessions
    one = client.get(f"/api/v1/gyms/{world.gym.id}/machines").json()["items"]
    mine = next(i for i in one if i["exercise_name"] == "Chest Press")["variant"]
    assert mine == {
        "id": world.local.id,
        "name": "Chest Press (Plate)",
        "equipment_type": "plate_loaded",
        "weight_ladder": None,
        "notes": None,
        "active": True,
        "sessions": 0,
        "last_used": None,
    }
    assert client.get("/api/v1/gyms/9999/machines").status_code == 404


def test_machine_variant_carries_usage(world, client, db):
    history_at(world, db, world.foreign, world.other_gym, date(2026, 10, 1), 65.0, [9])
    history_at(world, db, world.foreign, world.other_gym, date(2026, 10, 8), 70.0, [8, 7])
    items = client.get(f"/api/v1/gyms/{world.other_gym.id}/machines").json()["items"]
    v = next(i for i in items if i["exercise_name"] == "Chest Press")["variant"]
    assert v["sessions"] == 2 and v["last_used"] == "2026-10-08"


# --------------------------------------------------------------------------- create machine


def test_create_machine_uses_the_program_name_and_a_deterministic_key(world, client, db, reruns):
    g = new_gym(client)
    r = machine(
        client, g["id"], ex_id(world, "lateral_raise"),
        equipment_type="cable", weight_ladder=[5, 10, 12.5], notes="left side",
    )  # fmt: skip
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["in_program"] is True and item["equipment_type"] == "cable"
    v = item["variant"]
    assert v["name"] == "Lateral Raise (Cable) @ Gym Three"  # the program's variant name
    assert v["weight_ladder"] == [5.0, 10.0, 12.5] and v["notes"] == "left side"
    assert v["active"] is True and v["sessions"] == 0 and v["last_used"] is None
    row = db.get(ExerciseVariant, v["id"])
    assert row.gym_id == g["id"] and row.exercise_id == ex_id(world, "lateral_raise")
    assert row.key == gs.user_variant_key(g["id"], row.exercise_id) and gs.is_user_variant(row)
    assert row.ladder_inferred is False
    assert reruns == []  # not the current gym: the plan does not change


def test_create_machine_default_name_follows_the_previous_gym_else_the_exercise(world, client, db):
    history_at(world, db, world.foreign, world.other_gym, date(2026, 10, 1), 65.0, [9])
    g = new_gym(client)
    v = machine(client, g["id"], ex_id(world, "chest_press"), equipment_type="plate_loaded")
    assert v.json()["variant"]["name"] == "Chest Press (Plate) @ Gym Three"
    v2 = machine(client, g["id"], ex_id(world, "leg_curl"))
    assert v2.json()["variant"]["name"] == "Leg Curl @ Gym Three"  # no program line, no history
    assert v2.json()["in_program"] is False


def test_create_machine_conflicts_and_validation(world, client, db):
    g = new_gym(client)
    fly = ex_id(world, "chest_fly")
    assert machine(client, g["id"], fly).status_code == 201
    assert machine(client, g["id"], fly).status_code == 409  # already set up: PATCH instead
    assert machine(client, g["id"], 99999).status_code == 404
    assert machine(client, 9999, fly).status_code == 404
    assert machine(client, g["id"], fly, equipment_type="barbell").status_code == 422
    assert machine(client, g["id"], fly, weight_ladder=[10, 5]).status_code == 422
    # a name that another machine at this gym already has
    other = machine(
        client, g["id"], ex_id(world, "leg_curl"), name="Chest Fly (Machine) @ Gym Three"
    )
    assert other.status_code == 409
    ok = machine(client, g["id"], ex_id(world, "leg_curl"), name="My leg curl")
    assert ok.status_code == 201 and ok.json()["variant"]["name"] == "My leg curl"


def test_create_machine_revives_inactive_and_tombstoned_variants(world, client, db):
    g = new_gym(client)
    fly = ex_id(world, "chest_fly")
    first = machine(client, g["id"], fly, weight_ladder=[5, 10]).json()["variant"]
    # inactive -> revived with the same id, key and name
    client.patch(f"/api/v1/variants/{first['id']}", json={"active": False})
    again = machine(client, g["id"], fly, weight_ladder=[5, 10, 15]).json()["variant"]
    assert again["id"] == first["id"] and again["active"] is True
    assert again["weight_ladder"] == [5.0, 10.0, 15.0] and again["name"] == first["name"]
    # tombstoned (what the importer does to orphans) -> revived too
    row = db.get(ExerciseVariant, first["id"])
    row.deleted_at, row.active = utcnow(), False
    db.commit()
    third = machine(client, g["id"], fly).json()["variant"]
    assert third["id"] == first["id"] and third["active"] is True
    db.expire_all()
    assert db.get(ExerciseVariant, first["id"]).deleted_at is None
    count = len(list(db.scalars(select(ExerciseVariant).where(ExerciseVariant.gym_id == g["id"]))))
    assert count == 1


def test_creating_a_machine_at_the_current_gym_reruns_the_engine(world, client, reruns):
    g = new_gym(client)
    client.put("/api/v1/gyms/current", json={"gym_id": g["id"]})
    reruns.clear()
    machine(client, g["id"], ex_id(world, "chest_fly"))
    assert reruns == ["machine set up"]


# --------------------------------------------------------------------------- PATCH variant


def test_patch_variant_fields_and_rerun_on_ladder_change(world, client, db, reruns):
    g = new_gym(client)
    v = machine(client, g["id"], ex_id(world, "chest_fly")).json()["variant"]
    url = f"/api/v1/variants/{v['id']}"
    r = client.patch(url, json={"weight_ladder": [5, 7.5, 10], "notes": "pin is stiff"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["weight_ladder"] == [5.0, 7.5, 10.0] and out["notes"] == "pin is stiff"
    assert out["gym_id"] == g["id"] and out["active"] is True
    assert reruns == ["machine changed"]
    # same ladder again: nothing changed, no re-run
    client.patch(url, json={"weight_ladder": [5, 7.5, 10]})
    assert reruns == ["machine changed"]
    # name / notes only: no re-run
    assert client.patch(url, json={"name": "Fly 2"}).json()["name"] == "Fly 2"
    assert reruns == ["machine changed"]
    # deactivate and clear the ladder
    r = client.patch(url, json={"active": False, "weight_ladder": None})
    assert r.json()["active"] is False and r.json()["weight_ladder"] is None
    assert len(reruns) == 2
    assert client.patch("/api/v1/variants/99999", json={"active": True}).status_code == 404
    assert client.patch(url, json={"bogus": 1}).status_code == 422
    assert client.patch(url, json={"name": ""}).status_code == 422


def test_patch_variant_name_must_be_unique_at_the_gym(world, client):
    g = new_gym(client)
    a = machine(client, g["id"], ex_id(world, "chest_fly")).json()["variant"]
    b = machine(client, g["id"], ex_id(world, "leg_curl")).json()["variant"]
    r = client.patch(f"/api/v1/variants/{b['id']}", json={"name": a["name"]})
    assert r.status_code == 409


@pytest.mark.parametrize(
    "ladder",
    [
        [10, 5],  # not sorted
        [5, 5, 10],  # duplicate
        [0, 5],  # zero
        [-5, 5],
        [5, 500.5],  # above 500
        [],  # empty
        list(range(1, 62)),  # 61 entries
        [5, 5.1],  # 5.1 rounds to 5.0: a duplicate
    ],
)
def test_ladder_validation(world, client, ladder):
    g = new_gym(client)
    v = machine(client, g["id"], ex_id(world, "chest_fly")).json()["variant"]
    r = client.patch(f"/api/v1/variants/{v['id']}", json={"weight_ladder": ladder})
    assert r.status_code == 422, r.text
    assert (
        machine(client, g["id"], ex_id(world, "leg_curl"), weight_ladder=ladder).status_code == 422
    )


def test_ladder_is_rounded_to_a_quarter_kilo_and_may_have_60_entries(world, client):
    g = new_gym(client)
    v = machine(client, g["id"], ex_id(world, "chest_fly")).json()["variant"]
    r = client.patch(f"/api/v1/variants/{v['id']}", json={"weight_ladder": [2.6, 5.1, 500]})
    assert r.json()["weight_ladder"] == [2.5, 5.0, 500.0]
    r = client.patch(f"/api/v1/variants/{v['id']}", json={"weight_ladder": list(range(1, 61))})
    assert r.status_code == 200 and len(r.json()["weight_ladder"]) == 60


# --------------------------------------------------------------------------- ladder preview


def test_ladder_preview(client):
    r = client.post("/api/v1/weight-ladder/preview", json={"start": 5, "step": 5, "count": 20})
    assert r.status_code == 200
    assert r.json()["weight_ladder"] == [float(w) for w in range(5, 105, 5)]
    r = client.post(
        "/api/v1/weight-ladder/preview",
        json={"start": 5, "step": 5, "count": 4, "extra": [2.5, 7.5, 10, 22.6]},
    )
    assert r.json()["weight_ladder"] == [2.5, 5.0, 7.5, 10.0, 15.0, 20.0, 22.5]
    r = client.post("/api/v1/weight-ladder/preview", json={"start": 2.5, "step": 2.5, "count": 3})
    assert r.json()["weight_ladder"] == [2.5, 5.0, 7.5]
    bad = [
        {"start": 5, "step": 5, "count": 61},  # too many
        {"start": 5, "step": 5, "count": 0},
        {"start": 0, "step": 5, "count": 3},
        {"start": 5, "step": 0, "count": 3},
        {"start": 400, "step": 50, "count": 5},  # past 500
        {"start": 5, "step": 5, "count": 59, "extra": [1, 2]},  # 61 with the extras
    ]
    for body in bad:
        assert client.post("/api/v1/weight-ladder/preview", json=body).status_code == 422, body


# --------------------------------------------------------------------------- sync pull


def test_sync_pull_config_has_the_current_gym_and_gyms(world, client):
    cfg = client.get("/api/v1/sync/pull", params={"limit": 50}).json()["config"]
    assert cfg["current_gym_id"] == world.gym.id
    assert {g["name"] for g in cfg["gyms"]} == {"Gym One", "Gym Two"}
    g = new_gym(client)
    client.put("/api/v1/gyms/current", json={"gym_id": g["id"]})
    cfg = client.get("/api/v1/sync/pull", params={"limit": 50}).json()["config"]
    assert cfg["current_gym_id"] == g["id"]
    assert [x["is_current"] for x in cfg["gyms"] if x["id"] == g["id"]] == [True]


# --------------------------------------------------------------------------- engine


def lifting_exercises(client, start="2026-10-26") -> list[dict]:
    week = client.get(f"/api/v1/plan/week?start={start}").json()
    return [
        e
        for d in week["days"]
        for s in d["sessions"]
        if (s["session"] or {}).get("type") == "lifting"
        for e in s["session"]["exercises"]
    ]


def plan_fly(client, db, env, name):
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()
    return next(e for e in lifting_exercises(client) if e["name"] == name)


def test_new_machine_without_history_is_a_baseline(world, client, db, env, reruns):
    g = new_gym(client)
    machine(client, g["id"], ex_id(world, "chest_fly"), weight_ladder=[5, 10, 15, 20])
    client.put("/api/v1/gyms/current", json={"gym_id": g["id"]})
    fly = plan_fly(client, db, env, "Chest Fly (Machine) @ Gym Three")
    assert fly["progression_state"] == "baseline" and fly["needs_setup"] is False
    assert fly["progression_note"] == "First session at this gym: pick a weight for about 10 reps"
    assert all(s["target_weight"] is None for s in fly["sets"])
    assert fly["last"] is None and "hint_weight_kg" not in fly  # no history at another gym


def test_baseline_carries_the_previous_gym_as_a_hint(world, client, db, env, reruns):
    other = world.other_gym
    mach = ExerciseVariant(
        exercise_id=ex_id(world, "chest_fly"), gym_id=other.id, key="cf-two",
        name="Chest Fly (Machine) @ Gym Two", equipment_type="machine_stack",
        load_convention="total",
    )  # fmt: skip
    db.add(mach)
    db.flush()
    history_at(world, db, mach, other, date(2026, 10, 1), 65.0, [9, 8])
    g = new_gym(client)
    machine(client, g["id"], ex_id(world, "chest_fly"))
    client.put("/api/v1/gyms/current", json={"gym_id": g["id"]})
    fly = plan_fly(client, db, env, "Chest Fly (Machine) @ Gym Three")
    assert fly["progression_state"] == "baseline"
    assert fly["progression_note"] == (
        "First session at this gym: pick a weight for about 10 reps"
        " (previous gym: 65 kg × 9; machines differ)"
    )
    assert fly["hint_weight_kg"] == 65.0
    assert all(s["target_weight"] is None for s in fly["sets"])


def test_unresolved_machine_is_flagged_needs_setup(world, client, db, env, reruns):
    # chest_fly's generic variant exists in this world; make the program line gym-bound only
    world.fly.gym_id, world.fly.name = world.other_gym.id, "Chest Fly (Machine) @ Gym Two"
    db.commit()
    g = new_gym(client)
    client.put("/api/v1/gyms/current", json={"gym_id": g["id"]})
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()
    fly = next(e for e in lifting_exercises(client) if e["name"] == "Chest Fly (Machine)")
    assert fly["unresolved"] is True and fly["needs_setup"] is True
    assert fly["progression_note"] == "Set up this machine for Gym Three"
    assert all(s["target_weight"] is None for s in fly["sets"])
    assert unmapped(client, g["id"]) >= 1
    machine(client, g["id"], ex_id(world, "chest_fly"))  # set up: now a baseline item
    fly = plan_fly(client, db, env, "Chest Fly (Machine) @ Gym Three")
    assert fly["needs_setup"] is False and fly["progression_state"] == "baseline"


def unmapped(client, gym_id) -> int:
    return client.put("/api/v1/gyms/current", json={"gym_id": gym_id}).json()["unmapped_count"]


def test_a_renamed_or_inactive_machine_resolves_by_exercise(world, client, db, env, reruns):
    g = new_gym(client)
    v = machine(client, g["id"], ex_id(world, "chest_fly")).json()["variant"]
    client.put("/api/v1/gyms/current", json={"gym_id": g["id"]})
    client.patch(f"/api/v1/variants/{v['id']}", json={"name": "Pec deck"})
    assert plan_fly(client, db, env, "Pec deck")["progression_state"] == "baseline"
    client.patch(f"/api/v1/variants/{v['id']}", json={"active": False})
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()
    names = [e["name"] for e in lifting_exercises(client)]
    assert "Pec deck" not in names and "Chest Fly (Machine)" in names  # generic variant again


def test_baseline_misses_are_censored_for_the_first_two_sessions():
    cfg = EngineConfig.model_validate({"version": "x"})
    assert cfg.strength.censor.calibration_sessions == 2

    class V:
        id, name, equipment_type, load_convention = 1, "Chest Fly @ Gym", "machine_stack", "total"

    spec = L.LiftSpec(V(), (8, 12), 3)
    day = date(2026, 10, 5)
    hist = [
        L.Exposure(day, (L.SetResult(30, 6), L.SetResult(30, 5))),  # misses (below the range)
        L.Exposure(date(2026, 10, 8), (L.SetResult(30, 6), L.SetResult(30, 5))),
    ]
    an = L.analyze(spec, hist, cfg)
    assert [e.censored for e in an.evals] == ["first sessions of this variant (calibration)"] * 2


def test_baseline_note_with_previous_gym_hint():
    class V:
        id, name, equipment_type, load_convention = (
            1,
            "Lat Pulldown (Cable) @ Gym 4",
            "cable",
            "total",
        )

    cfg = EngineConfig.model_validate({"version": "x"})
    spec = L.LiftSpec(V(), (8, 12), 3)
    env = L.LiftEnv(previous_gym="Gym 3", previous_weight_kg=65.0, previous_reps=9)
    p = L.prescribe(spec, [], cfg, ctx=env)
    assert p.state == L.BASELINE and p.rule == "no_history"
    assert p.note == (
        "First session at this gym: pick a weight for about 10 reps"
        " (previous gym: 65 kg × 9; machines differ)"
    )
    assert p.hint_weight_kg == 65.0 and all(s.weight is None for s in p.sets)
    plain = L.prescribe(spec, [], cfg)
    assert plain.hint_weight_kg is None and "previous gym" not in plain.note


# --------------------------------------------------------------------------- importer safety

HEADER = "Date,Workout Name,Duration,Exercise Name,Set Order,Weight,Reps,Distance,Seconds,RPE"
MAPPING = """\
strong_name,canonical_exercise,variant_name,equipment_type,load_convention,status,active,notes
Lat Pulldown (Cable),lat_pulldown,Lat Pulldown (Cable),cable,total,confirmed,true,
Leg Curl (Machine),leg_curl,Leg Curl (Machine),machine_stack,total,confirmed,true,
"""
GYMS = """\
gyms:
  - {key: gym1, name: Gym 1}
  - {key: gym2, name: Gym 2, start: 2026-01-01}
"""


@pytest.fixture
def imp(tmp_path: Path):
    d = tmp_path / "imports"
    d.mkdir()
    rows = [
        ("2025-06-01 10:00:00", "Upper", "Lat Pulldown (Cable)", 50.0, 10),
        ("2026-02-01 10:00:00", "Upper", "Lat Pulldown (Cable)", 40.0, 10),
    ]
    lines = [HEADER] + [f'{w},"{n}",1h,"{e}",1,{kg},{r},0,0.0,' for w, n, e, kg, r in rows]
    (d / "strong.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (tmp_path / "strong_mapping.csv").write_text(MAPPING, encoding="utf-8")
    (tmp_path / "gyms.yaml").write_text(GYMS, encoding="utf-8")
    return ImportOptions(
        paths=[d],
        mapping_path=tmp_path / "strong_mapping.csv",
        catalog_path=FITNESS_DIR / "data" / "catalog" / "exercises.yaml",
        engine_config_path=FITNESS_DIR / "config" / "engine.v2.yaml",
        gyms_path=tmp_path / "gyms.yaml",
        apply=True,
    )


def test_reimport_keeps_manual_gyms_and_machines(db, imp, env):
    run_import(db, imp)
    db.commit()
    gym = gs.create_gym(db, "Home Gym")
    curl = db.scalar(select(Exercise).where(Exercise.slug == "leg_curl"))
    v = gs.create_machine(
        db, gym, curl, name=None, equipment_type="machine_stack", weight_ladder=[5, 10],
        notes=None, settings=env,
    )  # fmt: skip
    db.commit()
    vid, key = v.id, v.key
    for _ in range(2):
        res = run_import(db, imp)
        db.commit()
        assert res.error is None
    db.expire_all()
    assert db.get(Gym, gym.id).deleted_at is None  # not retired: it has no key
    kept = db.get(ExerciseVariant, vid)
    assert kept.deleted_at is None and kept.key == key and kept.weight_ladder == [5.0, 10.0]
    assert kept.ladder_inferred is False


def test_importer_adopts_a_machine_set_up_in_the_app_instead_of_duplicating(db, imp, env):
    csv = imp.paths[0] / "strong.csv"
    full = csv.read_text(encoding="utf-8")
    csv.write_text("\n".join(full.splitlines()[:2]) + "\n", encoding="utf-8")  # gym 1 only
    run_import(db, imp)
    db.commit()
    gym2 = db.scalar(select(Gym).where(Gym.key == "gym2"))
    ex = db.scalar(select(Exercise).where(Exercise.slug == "lat_pulldown"))
    assert db.scalar(select(ExerciseVariant).where(ExerciseVariant.gym_id == gym2.id)) is None
    made = gs.create_machine(
        db, gym2, ex, name="Lat Pulldown (Cable) @ Gym 2", equipment_type="cable",
        weight_ladder=[10, 20], notes=None, settings=env,
    )  # fmt: skip
    db.commit()
    assert gs.is_user_variant(made)
    # an import without sets at gym 2 must not tombstone it either
    run_import(db, imp)
    db.commit()
    db.expire_all()
    assert db.get(ExerciseVariant, made.id).deleted_at is None
    # now the Strong history of that gym arrives: the same machine, not a duplicate
    csv.write_text(full, encoding="utf-8")
    res = run_import(db, imp)
    db.commit()
    assert res.error is None
    rows = list(
        db.scalars(
            select(ExerciseVariant).where(
                ExerciseVariant.gym_id == gym2.id, ExerciseVariant.exercise_id == ex.id
            )
        )
    )
    assert [r.name for r in rows if r.deleted_at is None] == ["Lat Pulldown (Cable) @ Gym 2"]
    assert len(rows) == 1 and rows[0].id == made.id
    assert rows[0].key == variant_uuid("Lat Pulldown (Cable)", "gym2")
    assert rows[0].weight_ladder == [10.0, 20.0]  # the user's ladder wins over the inferred one


def test_previous_hint_for_a_new_machine(world, client, db):
    history_at(world, db, world.foreign, world.other_gym, date(2026, 10, 1), 65.0, [9, 8])
    g = new_gym(client)
    v = machine(client, g["id"], ex_id(world, "chest_press"), equipment_type="plate_loaded")
    row = db.get(ExerciseVariant, v.json()["variant"]["id"])
    assert gs.previous_hint(db, row) == {"gym_name": "Gym Two", "weight_kg": 65.0, "reps": 9}
    assert gs.previous_hint(db, world.bench) is None  # generic variant: no hint
    fly = machine(client, g["id"], ex_id(world, "chest_fly")).json()["variant"]
    assert gs.previous_hint(db, db.get(ExerciseVariant, fly["id"])) is None
