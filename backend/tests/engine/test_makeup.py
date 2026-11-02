"""Missed-session policy (engine/makeup.py) on a synthetic world: what happens to the rest of
the week when a planned session was skipped, and that progression never advances after a miss."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from apps.fitness.db.models import AdvisorTrigger, DecisionLog, PlannedSession
from apps.fitness.engine import config_service as cs
from apps.fitness.engine import data as D
from apps.fitness.engine import service
from tests.support.fakes_anthropic import KEY  # noqa: F401
from tests.support.world import easy_laps, interval_laps  # noqa: F401

MON = date(2026, 10, 26)  # the week after the synthetic history (which ends Sunday 2026-10-25)
TUE, WED, THU, FRI, SAT, SUN = (MON + timedelta(days=i) for i in range(1, 7))
NEXT_MON = date(2026, 11, 2)


def row(db, week: date, slot: str) -> PlannedSession:
    r = db.get(PlannedSession, D.planned_id(week, slot))
    assert r is not None, slot
    return r


def decisions(db, rule: str) -> list[DecisionLog]:
    return [
        d
        for d in db.scalars(select(DecisionLog).order_by(DecisionLog.id))
        if (d.output or {}).get("rule") == rule
    ]


def triggers(db) -> list[AdvisorTrigger]:
    return list(db.scalars(select(AdvisorTrigger).where(AdvisorTrigger.type == "missed_session")))


def plan(db, env, today: date):
    return service.run_engine(db, today=today, settings=env)


def week_rows(db, week: date) -> dict[str, PlannedSession]:
    return {r.id: r for r in db.scalars(select(PlannedSession).where(PlannedSession.day >= week))}


def test_missed_lower_tuesday_moves_to_wednesday_replacing_the_easy_run(world, db, env):
    plan(db, env, MON)
    world.lift_session(MON, "upper", [(world.bench, 60, [8, 8, 8])])  # Monday was done
    db.commit()
    res = plan(db, env, WED)  # Tuesday's lower was not done
    assert res.reconciled["skipped"] == 1
    assert [m.action for m in res.makeups.results] == ["moved"]
    tue = row(db, MON, "tue_lower")
    assert tue.status == "skipped"
    wed = row(db, MON, "wed_easy_run")  # the easy run's slot now holds the lower session
    assert (wed.kind, wed.day, wed.moved_from) == ("lower", WED, TUE)
    assert wed.change_reason == "Lower missed Tue → moved to Wed, replacing the easy run"
    assert wed.changed_by == "engine"
    assert wed.prescription["session"]["type"] == "lifting"
    assert row(db, MON, "fri_intervals").day == FRI  # still ok: two days after the lower day
    d = decisions(db, "makeup:moved")
    assert len(d) == 1 and d[0].rationale == wed.change_reason
    assert d[0].planned_session_id == wed.id and d[0].kind == "scheduling"
    assert not triggers(db)


def test_makeup_survives_every_later_engine_run(world, db, env):
    plan(db, env, MON)
    world.lift_session(MON, "upper", [(world.bench, 60, [8, 8, 8])])
    db.commit()
    plan(db, env, WED)
    for today in (WED, THU, THU):  # re-runs, also on later days
        plan(db, env, today)
        wed = row(db, MON, "wed_easy_run")
        assert (wed.kind, wed.day, wed.moved_from) == ("lower", WED, TUE), today
        assert wed.change_reason.startswith("Lower missed Tue")
    assert len(decisions(db, "makeup:moved")) == 1  # decided once, not once per run


def long_base(world):
    """A long run last Saturday: a higher weekly base, so the volume cap is not the limit."""
    world.run(date(2026, 10, 24), easy_laps(15), "Long run")


def test_missed_intervals_use_the_rest_day(world, db, env):
    long_base(world)
    plan(db, env, MON)
    world.lift_session(MON, "upper", [(world.bench, 60, [8, 8, 8])])
    world.lift_session(TUE, "lower", [(world.squat, 80, [8, 8, 8])])
    world.lift_session(THU, "upper", [(world.bench, 60, [8, 8, 8])])
    world.run(WED, easy_laps(6))
    db.commit()
    res = plan(db, env, SAT)  # Friday's intervals were skipped
    assert [(m.action, m.kind) for m in res.makeups.results] == [("moved", "intervals")]
    sat = row(db, MON, "sat_rest")
    assert (sat.kind, sat.day, sat.moved_from) == ("intervals", SAT, FRI)
    assert sat.change_reason == "Intervals missed Fri → moved to Sat, using the rest day"
    assert sat.prescription["session"]["type"] == "intervals"


def test_hard_rules_block_a_run_makeup_and_the_session_is_dropped_and_repeated(world, db, env):
    long_base(world)
    plan(db, env, MON)
    world.lift_session(MON, "upper", [(world.bench, 60, [8, 8, 8])])
    world.lift_session(TUE, "lower", [(world.squat, 80, [8, 8, 8])])
    world.lift_session(THU, "upper", [(world.bench, 60, [8, 8, 8])])
    world.lift_session(FRI, "lower", [(world.squat, 80, [8, 8, 8])])  # lower instead of intervals
    world.run(WED, easy_laps(6))
    db.commit()
    res = plan(db, env, SAT)
    # Saturday would be hard right after Friday's lower day (24 h rule / two hard days in a row)
    assert [m.action for m in res.makeups.results] == ["dropped"]
    fri = row(db, MON, "fri_intervals")
    assert fri.status == "skipped"
    assert row(db, MON, "sat_rest").kind == "rest"
    d = decisions(db, "makeup:dropped")
    assert len(d) == 1 and "Intervals missed Fri → dropped: no valid slot" in d[0].rationale
    assert "never two hard days in a row" in d[0].rationale or "24 h rule" in d[0].rationale
    assert "the same session is planned again next week" in d[0].rationale
    assert not triggers(db)  # a dropped run simply repeats: no coach needed
    plan(db, env, SUN)  # idempotent: no second decision
    assert len(decisions(db, "makeup:dropped")) == 1


def test_missed_easy_run_is_dropped_without_a_makeup(world, db, env):
    plan(db, env, MON)
    before = {r.id: (r.day, r.kind) for r in week_rows(db, MON).values()}
    res = plan(db, env, THU)  # Wednesday's easy run (and Monday / Tuesday) were not done
    easy = [m for m in res.makeups.results if m.kind == "easy_run"]
    assert [m.action for m in easy] == ["dropped"] and easy[0].trigger is False
    assert row(db, MON, "wed_easy_run").status == "skipped"
    d = [x for x in decisions(db, "makeup:dropped") if "Easy run" in x.rationale]
    assert d[0].rationale == "Easy run missed Wed → dropped, no make-up for this kind"
    assert not [t for t in triggers(db) if "easy" in t.payload["missed"]["kind"]]
    # nothing else moved because of the easy run
    assert row(db, MON, "fri_intervals").day == before[row(db, MON, "fri_intervals").id][0]


def _long_on_tuesday(db):
    tpl = {
        "mon": "upper", "tue": "long_run", "wed": "easy_run", "thu": "upper",
        "fri": "intervals", "sat": "rest", "sun": "lower",
    }  # fmt: skip
    cs.create_version(db, {"weekly_template": tpl}, reason="test template", author="user")
    db.commit()


def test_missed_long_run_moves_at_most_one_day(world, db, env):
    long_base(world)
    _long_on_tuesday(db)
    plan(db, env, MON)
    world.lift_session(MON, "upper", [(world.bench, 60, [8, 8, 8])])
    db.commit()
    res = plan(db, env, WED)
    long = [m for m in res.makeups.results if m.kind == "long_run"]
    assert [m.action for m in long] == ["moved"]
    wed = row(db, MON, "wed_easy_run")
    assert (wed.kind, wed.day, wed.moved_from) == ("long_run", WED, TUE)
    assert wed.change_reason == "Long run missed Tue → moved to Wed, replacing the easy run"
    assert row(db, MON, "sat_rest").kind == "rest"  # never further than one day


def test_a_missed_run_is_dropped_when_the_next_day_is_gone(world, db, env):
    long_base(world)
    _long_on_tuesday(db)
    plan(db, env, MON)
    res = plan(db, env, THU)  # Tuesday's long run missed, Wednesday already over
    long = [m for m in res.makeups.results if m.kind == "long_run"]
    assert [m.action for m in long] == ["dropped"] and long[0].trigger is False
    assert "nothing within 1 day" in long[0].reason and "planned again next week" in long[0].reason


def test_missed_long_run_with_saturday_gone_is_dropped_and_next_long_run_does_not_progress(
    world, db, env
):
    plan(db, env, MON)
    long_km = row(db, MON, "sun_long_run").prescription["session"]["distance_km"]
    res = plan(db, env, NEXT_MON)  # the Sunday long run did not happen
    long = [m for m in res.makeups.results if m.kind == "long_run"]
    assert [m.action for m in long] == ["dropped"] and long[0].trigger is False
    assert "that week is over; the same session is planned again next week" in long[0].reason
    nxt = row(db, NEXT_MON, "sun_long_run").prescription["session"]["distance_km"]
    assert nxt <= long_km  # planned from the last long run that happened: no step on top of it
    assert not any(t.payload["missed"]["kind"] == "long_run" for t in triggers(db))


def test_week_over_misses_older_than_a_week_get_no_trigger(world, db, env):
    plan(db, env, MON)
    res = plan(db, env, date(2026, 11, 17))  # the whole week was missed, long ago
    assert res.makeups.dropped and not any(r.trigger for r in res.makeups.results)
    assert not triggers(db)


def test_interval_progression_does_not_advance_after_a_missed_interval_session(world, db, env):
    plan(db, env, MON)
    plan(db, env, MON)  # the VDOT update of the first run (from the world's interval reps) settles
    miss = row(db, MON, "fri_intervals").prescription["session"]["structure"]["work"]
    plan(db, env, NEXT_MON)  # Friday's intervals were skipped
    assert row(db, MON, "fri_intervals").status == "skipped"
    nxt = row(db, NEXT_MON, "fri_intervals").prescription["session"]
    work = nxt["structure"]["work"]
    assert (work["reps"], work["pace_s_per_km"]) == (miss["reps"], miss["pace_s_per_km"])
    # the same session again: the levers and the phase week do not advance
    assert nxt["progression"]["rule"] == "quality:repeat_missed"
    assert nxt["phase_week"] == row(db, MON, "fri_intervals").prescription["session"]["phase_week"]


def test_lift_targets_and_running_volume_use_what_was_actually_done(world, db, env):
    plan(db, env, MON)
    mon = row(db, MON, "mon_upper").prescription["session"]["exercises"]
    plan(db, env, WED)  # Monday's upper was skipped
    assert row(db, MON, "mon_upper").status == "skipped"
    thu = row(db, MON, "thu_upper").prescription["session"]["exercises"]

    def key(ex):
        return [
            (e["name"], [(s["target_weight"], s["target_reps"]) for s in e["sets"]]) for e in ex
        ]

    assert key(thu) == key(mon)  # the next exposure repeats the last performance
    # the volume cap of next week is computed from the km actually run
    plan_, *_ = service.plan_week(db, today=NEXT_MON, week_start=NEXT_MON, settings=env)
    assert plan_.volume["done_km_this_week"] == 0


def test_volume_cap_blocks_a_makeup_that_would_exceed_it(world, db, env):
    # no extra base: the week's remaining runs could not shrink under the cap with the make-up
    plan(db, env, MON)
    world.lift_session(MON, "upper", [(world.bench, 60, [8, 8, 8])])
    world.lift_session(TUE, "lower", [(world.squat, 80, [8, 8, 8])])
    world.lift_session(THU, "upper", [(world.bench, 60, [8, 8, 8])])
    world.run(WED, easy_laps(6))
    db.commit()
    res = plan(db, env, SAT)
    assert [m.action for m in res.makeups.results] == ["dropped"]
    assert "weekly volume cap" in res.makeups.results[0].reason
    assert "planned again next week" in res.makeups.results[0].reason
    assert not triggers(db)  # never made up in volume, and a dropped run needs no coach
