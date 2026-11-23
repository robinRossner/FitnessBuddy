"""The saved program is the default of every session (docs/spec/decisions.md "Program slots"):
substitutes for slots not set up at the current gym, one-session swaps that leave the default
alone, template updates, base set counts, ad-hoc targets and stale history."""

from __future__ import annotations

from datetime import date, timedelta

import yaml

from apps.fitness.db.models import Exercise, ExerciseVariant
from apps.fitness.engine.core import lifting as L
from apps.fitness.engine.core.config import load_engine_config
from apps.fitness.engine.store import service as engine
from apps.fitness.settings import FITNESS_DIR
from tests.support.world import PROGRAM_YAML, TODAY

MON = date(2026, 10, 26)  # the week after the synthetic history (TODAY is Sunday 2026-10-25)


def write_program(env, upper: list[str], lower: list[str] | None = None) -> None:
    lines = ["version: 1", "days:", "  upper:"] + [f"    - {x}" for x in upper]
    lines += ["  lower:"] + [
        f"    - {x}"
        for x in lower
        or [
            "{exercise: squat, variant: Squat (Smith Machine), sets: 3}",
            "{exercise: leg_extension, variant: Leg Extension (Machine), sets: 2}",
        ]
    ]
    env.live_program_path.parent.mkdir(parents=True, exist_ok=True)
    env.live_program_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def upper_plan(db, env, week_start: date = MON, today: date = TODAY) -> dict:
    week, _cfg, _ = engine.plan_week(db, today=today, week_start=week_start, settings=env)
    day = next(d for d in week.days if d.kind == "upper" and d.day > today)
    return day.prescription


def names(plan: dict) -> list[str]:
    return [e["name"] for e in plan["exercises"]]


def put(client, kind, items):
    return client.put(f"/api/v1/program/{kind}", json={"exercises": items})


def live_upper(env) -> list[dict]:
    return yaml.safe_load(env.live_program_path.read_text())["days"]["upper"]


def gym_only_exercise(world, db, slug: str, name: str, pattern: str, muscle: str = "chest"):
    """An exercise whose only variant is a machine at the OTHER gym."""
    ex = Exercise(
        slug=slug,
        name=name,
        primary_muscle=muscle,
        movement_pattern=pattern,
        default_equipment_type="machine_stack",
    )
    db.add(ex)
    db.flush()
    db.add(
        ExerciseVariant(
            exercise_id=ex.id,
            gym_id=world.other_gym.id,
            key=f"{slug}-g2",
            name=f"{name} (Machine) @ {world.other_gym.name}",
            equipment_type="machine_stack",
            load_convention="total",
        )
    )
    db.flush()
    return ex


# --------------------------------------------------------------------------- resolution


def test_default_resolves_when_available(world, db, env):
    engine.get_program(env)
    plan = upper_plan(db, env)
    assert names(plan) == [
        "Bench Press (Barbell)",
        "Incline Bench Press (Smith Machine)",
        "Chest Fly (Machine)",
        "Lateral Raise (Cable)",
    ]
    for e in plan["exercises"]:
        assert e["slot"] == e["name"] and e["template_sets"] == 3
        assert "substitute_for" not in e and not e.get("unresolved")
        assert all(s["target_weight"] for s in e["sets"])


def test_default_unavailable_gets_a_same_pattern_substitute_with_targets(world, db, env):
    gym_only_exercise(world, db, "machine_press", "Machine Press", "horizontal_push")
    write_program(
        env,
        [
            "{exercise: bench_press, variant: Bench Press (Barbell), sets: 3}",
            "{exercise: machine_press, variant: Machine Press (Machine), sets: 4}",
            "{exercise: chest_fly, variant: Chest Fly (Machine), sets: 3}",
        ],
    )
    plan = upper_plan(db, env)
    sub = plan["exercises"][1]
    # bench is in the same day already: the most recently trained other horizontal push here
    assert sub["name"] == "Incline Bench Press (Smith Machine)"
    assert sub["variant_id"] == world.incline.id
    assert sub["slot"] == "Machine Press (Machine)"
    assert sub["substitute_for"] == "Machine Press (Machine)"
    assert "not set up at Gym One" in sub["substitute_reason"]
    # MON's week is the world's multi-lift deload: half the sets, the template count stays
    assert sub["template_sets"] == 4 and len(sub["sets"]) == 2
    assert all(s["target_weight"] == 49 for s in sub["sets"])  # its own history, real targets
    # the program keeps the default
    assert live_upper(env)[1]["variant"] == "Machine Press (Machine)"


def test_nothing_of_the_pattern_here_stays_needs_setup(world, db, env):
    gym_only_exercise(world, db, "pullover", "Pullover", "shoulder_extension", "lats")
    write_program(env, ["{exercise: pullover, variant: Pullover (Machine), sets: 3}"])
    e = upper_plan(db, env)["exercises"][0]
    assert e["unresolved"] and e["needs_setup"] and e["slot"] == "Pullover (Machine)"
    assert e["template_sets"] == 3


def test_same_exercise_on_other_equipment_is_a_substitute(world, db, env):
    def variant(name, gym, eq):
        db.add(
            ExerciseVariant(
                exercise_id=world.ex["lateral_raise"].id,
                gym_id=gym.id,
                key=name,
                name=name,
                equipment_type=eq,
                load_convention="total",
            )
        )
        db.flush()

    variant("Lateral Raise (Machine) @ Gym Two", world.other_gym, "machine_stack")
    variant("Lateral Raise (Pulley) @ Gym One", world.gym, "cable")
    write_program(env, ["{exercise: lateral_raise, variant: Lateral Raise (Machine), sets: 3}"])
    e = upper_plan(db, env)["exercises"][0]
    assert e["name"] == "Lateral Raise (Pulley) @ Gym One"
    assert e["substitute_for"] == "Lateral Raise (Machine)"
    # the same label at this gym is the same exercise (per-gym machines), no substitute
    variant("Lateral Raise (Machine) @ Gym One", world.gym, "machine_stack")
    e = upper_plan(db, env)["exercises"][0]
    assert e["name"] == "Lateral Raise (Machine) @ Gym One"
    assert "substitute_for" not in e


# --------------------------------------------------------------------------- swaps / templates


def test_one_session_swap_without_template_update_keeps_the_default(world, db, env):
    engine.get_program(env)
    before = live_upper(env)
    world.lift_session(
        MON,
        "upper",
        [
            (world.bench, 72.5, [8, 7, 6]),
            (world.incline_db, 22, [10, 9, 9]),  # swapped in for the Smith incline
            (world.fly, 30, [12, 11, 10]),
            (world.lateral, 10, [12, 12, 10]),
        ],
    )
    db.flush()
    plan = upper_plan(db, env, today=MON)
    assert plan["exercises"][1]["name"] == "Incline Bench Press (Smith Machine)"
    assert live_upper(env) == before
    # the swapped variant progresses from its own history (ad-hoc target)
    adhoc = {a["variant_id"]: a for a in plan["adhoc_targets"]}
    assert adhoc[world.incline_db.id]["sets"][0]["target_weight"] == 22


def test_template_update_removing_an_exercise(world, db, env, client):
    engine.get_program(env)
    r = put(client, "upper", [
        {"variant_id": world.bench.id},
        {"variant_id": world.incline.id},
        {"variant_id": world.fly.id},
    ])  # fmt: skip
    assert r.status_code == 200, r.text
    db.expire_all()
    assert "Lateral Raise (Cable)" not in names(upper_plan(db, env))


def test_template_update_replacing_and_reordering(world, db, env, client):
    engine.get_program(env)
    r = put(client, "upper", [
        {"variant_id": world.fly.id},
        {"variant_id": world.bench.id},
        {"variant_id": world.incline_db.id, "sets": 2},
        {"variant_id": world.lateral.id},
    ])  # fmt: skip
    assert r.status_code == 200, r.text
    plan = upper_plan(db, env)
    assert names(plan) == [
        "Chest Fly (Machine)",
        "Bench Press (Barbell)",
        "Incline Bench Press (Dumbbell)",
        "Lateral Raise (Cable)",
    ]
    assert plan["exercises"][2]["template_sets"] == 2


def test_template_update_keeps_a_substituted_slot(world, db, env, client):
    gym_only_exercise(world, db, "machine_press", "Machine Press", "horizontal_push")
    write_program(
        env,
        [
            "{exercise: bench_press, variant: Bench Press (Barbell), sets: 3}",
            "{exercise: machine_press, variant: Machine Press (Machine), sets: 4, "
            "rep_range: [8, 12]}",
            "{exercise: chest_fly, variant: Chest Fly (Machine), sets: 3}",
        ],
    )
    db.commit()
    # the substitute (incline) done in the slot, the fly dropped: the slot keeps its default
    r = put(client, "upper", [
        {"variant_id": world.bench.id, "sets": 3},
        {"variant_id": world.incline.id, "slot": "Machine Press (Machine)", "sets": 4},
    ])  # fmt: skip
    assert r.status_code == 200, r.text
    assert live_upper(env) == [
        {"exercise": "bench_press", "variant": "Bench Press (Barbell)", "sets": 3},
        {
            "exercise": "machine_press",
            "variant": "Machine Press (Machine)",
            "sets": 4,
            "rep_range": [8, 12],
        },
    ]
    # an unknown slot without a variant is rejected; neither field at all is a 422 too
    assert put(client, "upper", [{"slot": "Nope"}]).status_code == 422
    assert put(client, "upper", [{"sets": 3}]).status_code == 422


def persist_the_deload(db, env) -> None:
    """The world's lifts stall together: store MON's soft deload so later weeks are normal."""
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()


def test_template_sets_stay_the_base_count_in_a_comeback_week(world, db, env):
    engine.get_program(env)
    persist_the_deload(db, env)
    # the last upper session was 6 days before TODAY: three weeks later is a comeback
    later = TODAY + timedelta(days=21)  # a Sunday: plan the week from the next day
    plan = upper_plan(db, env, week_start=later + timedelta(days=1), today=later)
    back = [e for e in plan["exercises"] if e["rule"] == "return_after_break"]
    assert back
    for e in back:
        assert len(e["sets"]) == 2 and e["template_sets"] == 3


# --------------------------------------------------------------------------- ad-hoc targets


def test_adhoc_exercise_gets_a_progressed_target(world, db, env):
    engine.get_program(env)
    persist_the_deload(db, env)
    world.lift_session(TODAY - timedelta(days=4), "lower", [(world.leg_curl, 50, [10, 10, 10])])
    world.lift_session(TODAY, "lower", [(world.leg_curl, 50, [12, 12, 12])])
    db.flush()
    plan = upper_plan(db, env, week_start=MON + timedelta(days=7))  # MON's week is a deload
    adhoc = {a["variant_id"]: a for a in plan["adhoc_targets"]}
    curl = adhoc[world.leg_curl.id]
    assert curl["rule"] == "double_progression:increase_load"
    assert [s["target_weight"] for s in curl["sets"]] == [55.0] * 3
    assert all(s["target_reps"] >= 8 for s in curl["sets"])
    # planned lifts are not repeated as ad-hoc targets
    planned = {e["variant_id"] for e in plan["exercises"]}
    assert not planned & set(adhoc)


# --------------------------------------------------------------------------- stale history


class _V:
    name = "Bicep Curl (Dumbbell)"
    load_convention = "total"
    equipment_type = "dumbbell"
    weight_ladder = None
    increment_kg = 2.5


def test_600_day_old_history_does_not_trigger_a_stall():
    cfg = load_engine_config(FITNESS_DIR / "config" / "engine.v6.yaml")
    spec = L.LiftSpec(variant=_V(), rep_range=(7, 12), n_sets=3, per_week=2)
    old_start = date(2025, 1, 1)
    hist = [  # flat for eight sessions: a stall back then
        L.Exposure(old_start + timedelta(days=7 * i), (L.SetResult(14, 9),) * 3) for i in range(8)
    ]
    back = old_start + timedelta(days=600)
    hist.append(L.Exposure(back, (L.SetResult(12, 10), L.SetResult(12, 9))))
    an = L.analyze(spec, hist, cfg)
    assert an.restart == back and not an.stalled and an.counter == 0
    # a stall-ladder step decided from the stale history (before this fix) is ignored too
    stale_step = L.LadderEvent(back + timedelta(days=7), 1, 10.5, 30.0, 12.0)
    day = back + timedelta(days=10)
    p = L.prescribe(spec, hist, cfg, day=day, ctx=L.LiftEnv(ladder=[stale_step]))
    assert not p.stall and p.rule.startswith("double_progression"), p.rule
    assert p.sets[0].weight == 12
    # the comeback itself is handled once by the break tiers
    first = L.prescribe(spec, hist[:-1], cfg, day=back)
    assert first.rule == "return_after_break"


def test_the_seed_program_is_untouched():
    assert "Bench Press (Barbell)" in PROGRAM_YAML
