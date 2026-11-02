"""The live program file (seeded from the repo's program.yaml) and PUT /program/{kind}
(docs/api/exercises.md): "Update template" rewrites only that day kind."""

from __future__ import annotations

import pytest
import yaml

from apps.fitness.engine.core.program import BACKUPS_KEEP, backup_program
from apps.fitness.engine.store import service as engine
from apps.fitness.settings import get_settings
from tests.support.world import PROGRAM_YAML, TODAY


def put(client, kind, items):
    return client.put(f"/api/v1/program/{kind}", json={"exercises": items})


def live_days(env) -> dict:
    return yaml.safe_load(env.live_program_path.read_text())["days"]


def backups(env) -> list:
    return sorted(env.live_program_path.with_name("program.backups").glob("program_*.yaml"))


def test_live_path_defaults_next_to_the_database(tmp_path):
    from apps.fitness.settings import Settings

    s = Settings(database_url=f"sqlite:///{tmp_path}/data/fitness.db", program_live_path=None)
    assert s.live_program_path == tmp_path / "data" / "program.yaml"


def test_seeded_on_first_read_and_seed_edits_do_not_matter(env):
    assert not env.live_program_path.exists()
    first = engine.get_program(env)
    assert env.live_program_path.exists()
    assert [e.exercise for e in first.exercises("lower")] == ["squat", "leg_extension"]
    env.program_path.write_text(PROGRAM_YAML.replace("squat", "leg_curl"), encoding="utf-8")
    assert next(e.exercise for e in engine.get_program(env).exercises("lower")) == "squat"
    env.live_program_path.unlink()  # re-seed
    assert next(e.exercise for e in engine.get_program(env).exercises("lower")) == "leg_curl"


def test_endpoint_rewrites_only_that_kind_keeps_comments_and_backs_up(world, db, env, client):
    env.program_path.write_text("# my header\n" + PROGRAM_YAML, encoding="utf-8")
    engine.get_program(env)
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()
    lower_before = live_days(env)["lower"]
    r = put(client, "upper", [
        {"variant_id": world.fly.id},
        {"variant_id": world.incline_db.id, "sets": 4},
        {"variant_id": world.chest_press.id},
    ])  # fmt: skip
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "upper"
    got = [(e["exercise"], e["variant"], e["sets"], e["rep_range"]) for e in body["exercises"]]
    assert got == [
        ("chest_fly", "Chest Fly (Machine)", 3, None),
        ("incline_bench_press", "Incline Bench Press (Dumbbell)", 4, None),
        ("chest_press", "Chest Press (Machine)", 3, None),
    ]
    text = env.live_program_path.read_text()
    assert text.startswith("# my header\n")
    assert live_days(env)["lower"] == lower_before
    assert [s["exercise"] for s in live_days(env)["upper"]] == [g[0] for g in got]
    assert len(backups(env)) == 1 and body["backup"] == backups(env)[0].name
    # the planner uses the new list
    prog = engine.get_program(get_settings())
    assert [e.exercise for e in prog.exercises("upper")] == [g[0] for g in got]


def test_rep_range_carries_over(world, env, client):
    text = PROGRAM_YAML.replace(
        "{exercise: chest_fly, variant: Chest Fly (Machine), sets: 3}",
        "{exercise: chest_fly, variant: Chest Fly (Machine), sets: 2, "
        "rep_range: [10, 15], note: x}",
    )
    env.program_path.write_text(text, encoding="utf-8")
    r = put(client, "upper", [{"variant_id": world.bench.id}, {"variant_id": world.fly.id}])
    fly = r.json()["exercises"][1]
    assert fly["rep_range"] == [10, 15] and fly["sets"] == 2
    assert live_days(env)["upper"][1]["note"] == "x"


def test_same_list_writes_nothing(world, env, client):
    items = [{"variant_id": world.bench.id, "sets": 3}, {"variant_id": world.fly.id, "sets": 2}]
    first = put(client, "upper", items).json()
    stamp = env.live_program_path.read_text()
    n = len(backups(env))
    again = put(client, "upper", items).json()
    assert env.live_program_path.read_text() == stamp
    assert len(backups(env)) == n and again["backup"] is None
    assert again["exercises"] == first["exercises"]


def test_backups_are_pruned(env):
    engine.get_program(env)
    for _ in range(BACKUPS_KEEP + 5):
        backup_program(env.live_program_path)
    assert len(backups(env)) == BACKUPS_KEEP


def test_generate_writes_the_live_file(db, env):
    engine.generate_program(db, today=TODAY, settings=env, write=True)
    assert env.live_program_path.exists()
    assert "days:" in env.live_program_path.read_text()


@pytest.mark.parametrize("kind,items,status", [
    ("push", [{"variant_id": 1}], 422),
    ("upper", [], 422),
    ("upper", [{"variant_id": 999999}], 404),
    ("upper", [{"variant_id": "abc"}], 422),
])  # fmt: skip
def test_errors(world, env, client, kind, items, status):
    assert put(client, kind, items).status_code == status
    assert not env.live_program_path.with_name("program.backups").exists()


def test_duplicate_exercise_is_422(world, client):
    dup = [{"variant_id": world.incline.id}, {"variant_id": world.incline_db.id}]
    assert put(client, "upper", dup).status_code == 422


def test_variant_override_follows_the_picked_variant(world, db, env, client):
    from apps.fitness.engine.store import config_service as cs

    cs.ensure_seeded(db, env.engine_config_path)
    cs.create_version(
        db,
        {
            "exercise_overrides.incline_bench_press.variant": "Incline Bench Press (Smith Machine)",
            "exercise_overrides.incline_bench_press.rep_range": [6, 8],
        },
        author="user",
        reason="rename",
    )
    db.commit()
    items = [{"variant_id": world.bench.id}, {"variant_id": world.incline_db.id}]
    r = put(client, "upper", items)
    assert r.status_code == 200, r.text
    assert r.json()["exercises"][1]["variant"] == "Incline Bench Press (Dumbbell)"
    assert r.json()["exercises"][1]["rep_range"] == [6, 8]  # other override keys stay
    ov = cs.load_active(db)[0].exercise_overrides["incline_bench_press"]
    assert ov["variant"] == "Incline Bench Press (Dumbbell)" and ov["rep_range"] == [6, 8]
    version = cs.get_active(db).version
    again = put(client, "upper", items).json()  # nothing changes: no new version, no backup
    db.expire_all()
    assert cs.get_active(db).version == version and again["backup"] is None
