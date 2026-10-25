"""Rest timers: lifting.rest_default_s, exercise_overrides.<slug>.rest_s, `rest_s` on every plan
item, PUT /exercises/{variant_id}/rest (docs/api/exercises.md) and the advisor's allowlist."""

from __future__ import annotations

import pytest

from app.advisor import validator as V
from app.engine import config_service as cs
from app.engine import rest as R
from app.engine import service as engine
from app.engine.config import EngineConfig, load_engine_config
from app.engine.program import load_program
from app.settings import BACKEND_DIR, get_settings
from tests.advisor_helpers import PROGRAM_YAML, TODAY

# --------------------------------------------------------------------------- config


def test_default_rest_is_two_minutes_even_without_the_section():
    cfg = EngineConfig.model_validate({"version": "v3"})
    assert cfg.lifting_cfg.rest_default_s == 120
    assert R.resolve_rest_s(cfg, "squat") == 120
    assert R.resolve_rest_s(cfg, None) == 120
    v5 = load_engine_config(BACKEND_DIR / "config" / "engine.v5.yaml")
    assert v5.lifting == {"rest_default_s": 120}


def test_override_wins_and_garbage_is_ignored():
    cfg = EngineConfig.model_validate(
        {
            "version": "x",
            "lifting": {"rest_default_s": 90},
            "exercise_overrides": {
                "squat": {"rest_s": 180},
                "bench_press": {"rest_s": 5},  # out of bounds
                "row": {"rest_s": "soon"},
                "curl": {"rest_s": True},
                "dips": {"sets": 3},
            },
        }
    )
    assert R.resolve_rest_s(cfg, "squat") == 180
    for slug in ("bench_press", "row", "curl", "dips", "unknown"):
        assert R.resolve_rest_s(cfg, slug) == 90, slug
    assert R.overrides_map(cfg) == {"squat": 180}


def test_annotate_session_only_touches_lifting_and_copies():
    cfg = EngineConfig.model_validate(
        {"version": "x", "exercise_overrides": {"squat": {"rest_s": 180}}}
    )
    session = {"type": "lifting", "exercises": [{"exercise": "squat"}, {"exercise": "row"}]}
    out = R.annotate_session(session, cfg)
    assert [e["rest_s"] for e in out["exercises"]] == [180, 120]
    assert "rest_s" not in session["exercises"][0]  # input untouched
    run = {"type": "easy_run"}
    assert R.annotate_session(run, cfg) is run and R.annotate_session(None, cfg) is None


# --------------------------------------------------------------------------- plan items


def exercises(items: list[dict]) -> list[dict]:
    return [
        e
        for s in items
        if (s["session"] or {}).get("type") == "lifting"
        for e in s["session"]["exercises"]
    ]


def week_items(client, start="2026-10-26") -> list[dict]:
    week = client.get(f"/api/v1/plan/week?start={start}").json()
    return [s for d in week["days"] for s in d["sessions"]]


def test_every_lifting_exercise_in_the_plan_has_rest_s(world, db, env, client):
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()
    stored = exercises(week_items(client))
    preview = exercises(week_items(client, "2026-11-02"))  # computed on the fly
    assert stored and preview
    assert all(e["rest_s"] == 120 for e in stored + preview)
    today = client.get("/api/v1/plan/today").json()
    assert all(e["rest_s"] == 120 for e in exercises(today["sessions"]))
    pulled = client.get("/api/v1/sync/pull", params={"limit": 5000}).json()
    assert all(e["rest_s"] == 120 for e in exercises(pulled["planned_sessions"]))
    assert pulled["config"]["lifting"] == {"rest_default_s": 120, "rest_s_by_variant": {}}


# --------------------------------------------------------------------------- PUT endpoint


def put(client, variant_id, body):
    return client.put(f"/api/v1/exercises/{variant_id}/rest", json=body)


def versions(db):
    db.expire_all()
    return [v.version for v in cs.history(db)]


def test_put_sets_the_rest_time_and_creates_a_user_version(world, db, client):
    before = versions(db)
    r = put(client, world.squat.id, {"rest_s": 180})
    assert r.status_code == 200
    body = r.json()
    assert body["variant_id"] == world.squat.id and body["rest_s"] == 180
    assert body["is_default"] is False and body["config_version"] not in before
    active = cs.get_active(db)
    db.refresh(active)
    assert active.version == body["config_version"] and active.author == "user"
    assert active.reason == "Rest time for Squat (Smith Machine) set to 3:00"
    assert active.content["exercise_overrides"]["squat"]["rest_s"] == 180
    assert active.diff == {"exercise_overrides.squat.rest_s": {"old": None, "new": 180}}
    assert len(versions(db)) == len(before) + 1


def test_put_is_idempotent(world, db, client):
    first = put(client, world.squat.id, {"rest_s": 180}).json()
    n = len(versions(db))
    again = put(client, world.squat.id, {"rest_s": 180})
    assert again.status_code == 200 and again.json() == first
    assert len(versions(db)) == n
    # resetting something that was never set is a no-op too
    n = len(versions(db))
    other = put(client, world.bench.id, {"rest_s": None})
    assert other.status_code == 200 and other.json()["rest_s"] == 120
    assert other.json()["is_default"] is True and len(versions(db)) == n


def test_put_null_resets_to_the_default_and_drops_the_override(world, db, client):
    put(client, world.squat.id, {"rest_s": 180})
    r = put(client, world.squat.id, {"rest_s": None})
    assert r.status_code == 200 and r.json()["rest_s"] == 120 and r.json()["is_default"] is True
    active = cs.get_active(db)
    db.refresh(active)
    assert "squat" not in (active.content.get("exercise_overrides") or {})
    assert active.reason == "Rest time for Squat (Smith Machine) reset to the default (2:00)"


def test_put_keeps_other_overrides_of_the_exercise(world, db, client):
    cs.create_version(db, {"exercise_overrides.squat.sets": 4}, reason="t", author="advisor")
    db.commit()
    put(client, world.squat.id, {"rest_s": 150})
    put(client, world.squat.id, {"rest_s": None})
    active = cs.get_active(db)
    db.refresh(active)
    assert active.content["exercise_overrides"]["squat"] == {"sets": 4}


@pytest.mark.parametrize(
    "body",
    [
        {"rest_s": 29},
        {"rest_s": 601},
        {"rest_s": 90.5},
        {"rest_s": "120"},
        {"rest_s": True},
        {},
        {"rest": 90},
    ],
)
def test_put_validates_the_body(world, client, body):
    assert put(client, world.squat.id, body).status_code == 422


@pytest.mark.parametrize("value", [30, 600])
def test_put_accepts_the_bounds(world, client, value):
    assert put(client, world.squat.id, {"rest_s": value}).json()["rest_s"] == value


def test_put_unknown_variant_and_auth(world, client):
    assert put(client, 99999, {"rest_s": 90}).status_code == 404
    r = client.put(
        f"/api/v1/exercises/{world.squat.id}/rest",
        json={"rest_s": 90},
        headers={"Authorization": "Bearer nope"},
    )
    assert r.status_code == 401


def test_a_change_reaches_plans_and_the_next_pull(world, db, env, client):
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()
    cursor = client.get("/api/v1/sync/pull", params={"limit": 5000}).json()["cursor"]
    put(client, world.squat.id, {"rest_s": 180})
    squat = [e for e in exercises(week_items(client)) if e["exercise"] == "squat"]
    assert squat and all(e["rest_s"] == 180 for e in squat)
    others = [e for e in exercises(week_items(client)) if e["exercise"] != "squat"]
    assert others and all(e["rest_s"] == 120 for e in others)
    # the plan is re-sent in an incremental pull, with the override listed per variant id
    inc = client.get("/api/v1/sync/pull", params={"since": cursor, "limit": 5000}).json()
    assert inc["planned_sessions"], "open lifting plans must be pulled again"
    assert all(
        e["rest_s"] == 180 for e in exercises(inc["planned_sessions"]) if e["exercise"] == "squat"
    )
    assert inc["config"]["lifting"]["rest_s_by_variant"] == {str(world.squat.id): 180}
    # a stored plan keeps working after the engine re-ran (resolved at read time)
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()
    assert all(
        e["rest_s"] == 180 for e in exercises(week_items(client)) if e["exercise"] == "squat"
    )


# --------------------------------------------------------------------------- advisor allowlist


@pytest.fixture
def ctx(db, world, tmp_path):
    _cfg, row = cs.load_active(db)
    program = tmp_path / "p.yaml"
    program.write_text(PROGRAM_YAML)
    return V.build_context(db, row.content, program=load_program(program), settings=get_settings())


def test_advisor_may_set_rest_times_low_risk(ctx):
    v = V.validate_change("exercise_overrides.squat.rest_s", 180, ctx)
    assert v.valid, v.error
    assert v.risk == V.LOW and v.current == 120 and v.value == 180
    d = V.validate_change("lifting.rest_default_s", 90, ctx)
    assert d.valid and d.risk == V.LOW and d.current == 120


@pytest.mark.parametrize("param", ["exercise_overrides.squat.rest_s", "lifting.rest_default_s"])
def test_advisor_rest_bounds(ctx, param):
    for bad in (29, 601, 0, -60, 90.5, "90", True, None, [90]):
        assert not V.validate_change(param, bad, ctx).valid, bad
    for good in (30, 600):
        assert V.validate_change(param, good, ctx).valid, good
    assert "nothing to change" in V.validate_change(param, 120, ctx).error


def test_advisor_rest_override_needs_a_program_exercise_and_is_the_next_baseline(ctx):
    assert not V.validate_change("exercise_overrides.calf_raise.rest_s", 90, ctx).valid
    ctx.apply("exercise_overrides.squat.rest_s", 180)
    v = V.validate_change("exercise_overrides.squat.rest_s", 180, ctx)
    assert not v.valid and "nothing to change" in v.error
    assert V.validate_change("exercise_overrides.squat.rest_s", 120, ctx).current == 180
