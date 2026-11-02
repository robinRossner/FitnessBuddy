"""Golden trip-wire for the planner: the full /plan/week output (stored week + next week's preview)
for three fixed worlds. A diff here means planner output changed; if intended, rerun with
UPDATE_GOLDEN=1 and review the JSON diff."""

from __future__ import annotations

from datetime import date

from apps.fitness.engine.store import service as engine
from tests.support.golden import assert_golden
from tests.support.world import TODAY

MON = date(2026, 10, 26)  # the week after the synthetic history (TODAY is Sunday 2026-10-25)
WED = date(2026, 10, 28)


def planned_weeks(client) -> dict:
    return {
        "week": client.get(f"/api/v1/plan/week?start={MON}").json(),
        "next_week_preview": client.get("/api/v1/plan/week?start=2026-11-02").json(),
    }


def test_planner_golden_base_world(world, db, env, client):
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()
    assert_golden("planner_base_world", planned_weeks(client))


def test_planner_golden_after_a_missed_session(world, db, env, client):
    engine.run_engine(db, today=MON, settings=env)
    world.lift_session(MON, "upper", [(world.bench, 60, [8, 8, 8])])  # Monday was done
    db.commit()
    engine.run_engine(db, today=WED, settings=env)  # Tuesday's lower was missed -> make-up
    db.commit()
    assert_golden("planner_missed_session", planned_weeks(client))


def test_planner_golden_with_a_program_override(world, db, env, client):
    engine.get_program(env)  # seed the live program file
    r = client.put(
        "/api/v1/program/upper",
        json={
            "exercises": [
                {"variant_id": world.fly.id},
                {"variant_id": world.incline_db.id, "sets": 4},
                {"variant_id": world.chest_press.id},
            ]
        },
    )
    assert r.status_code == 200, r.text
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()
    assert_golden("planner_program_override", planned_weeks(client))
