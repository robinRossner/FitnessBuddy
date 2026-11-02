"""Outcome tracking: 21-28 days after an advisor change, record improved / neutral / worse."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from apps.fitness.advisor import outcomes, service, summary
from apps.fitness.advisor.schema import ProposedChange
from apps.fitness.db.models import Outcome
from apps.fitness.engine.store import config_service as cs
from apps.fitness.jobs import advisor_jobs
from apps.fitness.stats import rebuild_stats
from tests.support.fakes_anthropic import make_output
from tests.support.world import TODAY, days_ago


def applied_change(
    db,
    world,
    age_days: int,
    *,
    param="exercise_overrides.bench_press.rep_range",
    frm=(7, 12),
    to=(8, 12),
    fake_client=None,
):
    """An advisor-authored config version created `age_days` ago, with its consultation."""
    fake_client.output = make_output(
        proposed_changes=[
            ProposedChange(
                param=param,
                **{"from": list(frm) if isinstance(frm, tuple) else frm},
                to=list(to) if isinstance(to, tuple) else to,
                reason="try it",
                risk="low",
            )
        ],
        session_overrides=[],
    )
    c = service.create_consultation(
        db, kind="weekly", trigger_info={"type": "weekly_review", "title": "t"}
    )
    db.commit()
    service.execute(db, c.id)
    version = service.approve(db, c, None)
    version.created_at = datetime.combine(
        days_ago(age_days), time(12, 0), tzinfo=ZoneInfo("Europe/Vienna")
    ).astimezone(UTC)
    db.commit()
    return c, version


def bench_session(world, day, kg):
    world.lift_session(day, "upper", [(world.bench, kg, [8, 7, 6])])
    rebuild_stats(world.db, bodyweight_kg=82)
    world.db.commit()


def only_outcome(db):
    db.expire_all()
    return db.scalars(select(Outcome)).one()


def test_improved_lift_is_recorded_with_metrics(db, world, fake, no_rerun, env):
    c, v = applied_change(db, world, 22, fake_client=fake)
    bench_session(world, days_ago(2), 80.0)  # e1RM up ~10 % after the change
    made = outcomes.run_outcome_check(db, env, today=TODAY)
    assert len(made) == 1
    o = only_outcome(db)
    assert o.kind == "advisor_change" and o.verdict == "improved"
    assert o.config_version_id == v.id and o.consultation_id == c.id
    m = o.metrics["metrics"][0]
    assert m["name"] == "bench_press_e1rm_kg" and m["params"] == [
        "exercise_overrides.bench_press.rep_range"
    ]
    assert m["after"] > m["before"] > 0 and m["change"] > 5 and m["verdict"] == "improved"
    assert o.metrics["before"] == {"bench_press_e1rm_kg": m["before"]}
    assert "22 days after the change" in o.notes and "improved" in o.notes


def test_worse_and_neutral(db, world, fake, no_rerun, env):
    applied_change(db, world, 22, fake_client=fake)
    bench_session(
        world, days_ago(2), 60.0
    )  # lighter than before, but the best of the window counts
    # the best e1RM of the after window includes the older +20 day sessions (72.5 kg): neutral
    outcomes.run_outcome_check(db, env, today=TODAY)
    assert only_outcome(db).verdict == "neutral"


def test_worse_when_the_best_of_the_window_drops(db, world, fake, no_rerun, env):
    # change applied 22 days ago; remove the sessions of the after window and add a weak one
    from apps.fitness.db.models import TrainingSession

    applied_change(db, world, 22, fake_client=fake)
    for s in db.scalars(select(TrainingSession)):
        if s.started_at_local.date() >= days_ago(22):
            for ws in s.sets:
                ws.deleted_at = datetime.now(UTC)
            s.deleted_at = datetime.now(UTC)
    db.commit()
    bench_session(world, days_ago(2), 50.0)
    outcomes.run_outcome_check(db, env, today=TODAY)
    o = only_outcome(db)
    assert o.verdict == "worse" and o.metrics["metrics"][0]["change"] < -1


def test_nothing_before_21_days_and_only_once(db, world, fake, no_rerun, env):
    applied_change(db, world, 20, fake_client=fake)
    assert outcomes.run_outcome_check(db, env, today=TODAY) == []
    assert outcomes.run_outcome_check(db, env, today=TODAY + timedelta(days=1)) != []
    assert outcomes.run_outcome_check(db, env, today=TODAY + timedelta(days=2)) == []
    assert len(db.scalars(select(Outcome)).all()) == 1


def test_no_data_waits_until_28_days_then_is_neutral(db, world, fake, no_rerun, env):
    # a lift with no sessions at all after the change
    from apps.fitness.db.models import TrainingSession

    applied_change(db, world, 21, fake_client=fake)
    for s in db.scalars(select(TrainingSession)):
        if s.started_at_local.date() >= days_ago(21):
            for ws in s.sets:
                ws.deleted_at = datetime.now(UTC)
            s.deleted_at = datetime.now(UTC)
    db.commit()
    rebuild_stats(db, bodyweight_kg=82)
    db.commit()
    assert outcomes.run_outcome_check(db, env, today=TODAY) == []  # age 21: wait
    assert outcomes.run_outcome_check(db, env, today=TODAY + timedelta(days=6)) == []  # age 27
    made = outcomes.run_outcome_check(db, env, today=TODAY + timedelta(days=7))  # age 28
    assert len(made) == 1 and made[0].verdict == "neutral"
    assert "not enough data" in made[0].notes


def test_running_parameter_is_judged_on_aerobic_efficiency(db, world, fake, no_rerun, env):
    applied_change(
        db, world, 22, param="running.weekly_volume_cap_pct", frm=10, to=8, fake_client=fake
    )
    outcomes.run_outcome_check(db, env, today=TODAY)
    o = only_outcome(db)
    m = o.metrics["metrics"][0]
    assert m["name"] == "aerobic_efficiency" and m["before"] and m["after"]
    assert o.verdict in ("improved", "neutral", "worse")


def test_interval_parameter_is_judged_on_the_pass_rate(db, world, fake, no_rerun, env):
    applied_change(db, world, 22, param="running.intervals.reps_max", frm=6, to=7, fake_client=fake)
    outcomes.run_outcome_check(db, env, today=TODAY)
    m = only_outcome(db).metrics["metrics"][0]
    assert m["name"] == "interval_pass_rate"


def test_a_swap_is_not_scored(db, world, fake, no_rerun, env):
    applied_change(
        db,
        world,
        22,
        param="exercise_overrides.incline_bench_press.variant",
        frm="Incline Bench Press (Smith Machine)",
        to="Incline Bench Press (Dumbbell)",
        fake_client=fake,
    )
    outcomes.run_outcome_check(db, env, today=TODAY + timedelta(days=6))
    o = only_outcome(db)
    assert o.verdict == "neutral" and "not scored" in o.notes


def test_outcomes_reach_history_and_the_next_advisor_input(db, world, fake, no_rerun, env):
    _c, v = applied_change(db, world, 22, fake_client=fake)
    bench_session(world, days_ago(2), 80.0)
    outcomes.run_outcome_check(db, env, today=TODAY)
    from apps.fitness.advisor.serialize import history_json

    top = history_json(db)["versions"][0]
    assert top["version"] == v.version
    assert top["outcome"]["verdict"] == "improved"
    assert (
        top["outcome"]["metric_after"]["bench_press_e1rm_kg"]
        > top["outcome"]["metric_before"]["bench_press_e1rm_kg"]
    )
    assert top["outcome"]["notes"]
    doc = summary.build_input(
        db, trigger={"type": "weekly_review", "title": "w"}, question=None, settings=env
    )
    (past,) = doc["past_advisor_changes"]
    assert (
        past["param"] == "exercise_overrides.bench_press.rep_range" and past["status"] == "applied"
    )
    assert past["outcome"]["verdict"] == "improved"


def test_only_advisor_versions_are_tracked(db, world, env):
    cs.create_version(db, {"running.weekly_volume_cap_pct": 8}, reason="me", author="user")
    db.commit()
    assert outcomes.run_outcome_check(db, env, today=TODAY + timedelta(days=60)) == []


def test_the_daily_job_records_outcomes(db, world, fake, no_rerun, env, pin_session_factory):
    applied_change(db, world, 22, fake_client=fake)
    bench_session(world, days_ago(2), 80.0)
    advisor_jobs.outcome_job(env)
    assert only_outcome(db).verdict == "improved"
    advisor_jobs.outcome_job(env)  # second run: nothing new, nothing raised
    assert len(db.scalars(select(Outcome)).all()) == 1


def test_window_is_configurable(db, world, fake, no_rerun, env):
    cs.create_version(db, {"advisor.outcome_after_days": [10, 14]}, reason="t", author="user")
    db.commit()
    applied_change(db, world, 11, fake_client=fake)
    assert len(outcomes.run_outcome_check(db, env, today=TODAY)) == 1
