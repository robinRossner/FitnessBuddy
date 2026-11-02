"""Coach schedule changes: the validator rules and risk levels, proposals through the API (ids,
shapes), applying on approval, persistence across an engine re-run, and the approval modes."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from apps.fitness.advisor import service as advisor
from apps.fitness.advisor import validator as V
from apps.fitness.advisor.prompt import SYSTEM_PROMPT
from apps.fitness.advisor.schema import ProposedChange, ScheduleChange
from apps.fitness.db.models import AdvisorScheduleChange, DecisionLog, PlannedSession
from apps.fitness.engine.core.ids import planned_id
from apps.fitness.engine.store import config_service as cs
from apps.fitness.engine.store import service as engine
from tests.support.fakes_anthropic import make_output
from tests.support.world import AUTH, TODAY  # noqa: F401

NEXT = date(2026, 10, 26)  # Monday after TODAY (a Sunday)
DAY = {
    n: NEXT + timedelta(days=i)
    for i, n in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))
}


@pytest.fixture
def planned(world, db, env):
    """Engine run on Sunday 2026-10-25: today's long run + the whole next week are stored."""
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()
    return world


def ctx_for(db, today=TODAY) -> V.ScheduleContext:
    cfg, _ = cs.load_active(db)
    return V.build_schedule_context(db, cfg, today)


def check(ctx, date_, action, target, **kw):
    return V.validate_schedule_change(
        ctx,
        day=date_.isoformat() if isinstance(date_, date) else date_,
        action=action,
        target=target,
        to_day=_iso(kw.pop("to", None)),
        new_kind=kw.pop("new_kind", None),
        distance_km=kw.pop("km", None),
        reason=kw.pop("reason", "because"),
    )


def _iso(d):
    return d.isoformat() if isinstance(d, date) else d


# --------------------------------------------------------------------------- validator rules


def test_valid_move_swaps_the_rest_day_and_is_medium_risk(planned, db):
    v = check(ctx_for(db), DAY["fri"], "move", "intervals", to=DAY["sat"])
    assert v.valid and v.risk == "medium" and v.label == "Move intervals Fri → Sat"
    assert [(e.day, e.moved_from) for e in v.edits] == [
        (DAY["sat"], DAY["fri"]), (DAY["fri"], DAY["sat"])
    ]  # fmt: skip


def test_only_current_and_next_week_and_never_the_past(planned, db):
    c = ctx_for(db)
    past = check(c, TODAY - timedelta(days=1), "skip", "upper")
    assert not past.valid and "in the past" in past.error
    into_past = check(c, DAY["wed"], "move", "easy", to=TODAY - timedelta(days=1))
    assert not into_past.valid and "in the past" in into_past.error
    far = check(c, NEXT + timedelta(days=8), "skip", "upper")
    assert not far.valid and "current and the next week" in far.error
    today_ok = check(c, TODAY, "skip", "long")  # today itself is still changeable
    assert today_ok.valid


def test_the_target_must_exist(planned, db):
    v = check(ctx_for(db), DAY["wed"], "skip", "lower")
    assert not v.valid and "no open planned lower" in v.error
    assert not check(ctx_for(db), DAY["wed"], "skip", "yoga").valid
    assert not check(ctx_for(db), "not-a-date", "skip", "easy").valid
    assert not check(ctx_for(db), DAY["wed"], "teleport", "easy").valid


def test_hard_rules_two_hard_days_in_a_row_and_24h(planned, db):
    v = check(ctx_for(db), DAY["fri"], "move", "intervals", to=DAY["wed"])  # right after Tuesday
    assert not v.valid and ("in a row" in v.error or "24 h rule" in v.error)
    v = check(ctx_for(db), DAY["tue"], "move", "lower", to=DAY["thu"])  # next to Friday intervals
    assert not v.valid and "in a row" in v.error


def test_one_hard_session_per_day(planned, db):
    v = check(ctx_for(db), DAY["tue"], "move", "lower", to=DAY["fri"])
    assert not v.valid and "2 hard sessions" in v.error


def test_two_sessions_per_day_at_most(planned, db):
    c = ctx_for(db)
    ok = check(c, DAY["wed"], "move", "easy", to=DAY["mon"])  # upper + easy run
    assert ok.valid
    v = check(ctx_for(db), DAY["wed"], "move", "easy", to=DAY["mon"])
    V.accept_schedule(c, v)
    third = check(c, DAY["sun"], "move", "long", to=DAY["mon"])
    assert not third.valid and "3 sessions" in third.error


def test_moves_and_swaps_stay_inside_one_week_and_swap_needs_one_partner(planned, db):
    v = check(ctx_for(db), TODAY, "move", "long", to=DAY["mon"])
    assert not v.valid and "own week" in v.error
    same_day = check(ctx_for(db), DAY["wed"], "swap", "easy", to=DAY["wed"])
    assert not same_day.valid
    # Mon holds exactly one open session (upper): a swap of Wed easy <-> Mon works
    ok = check(ctx_for(db), DAY["wed"], "swap", "easy", to=DAY["mon"])
    assert ok.valid and ok.label == "Swap easy run Wed ↔ upper Mon"
    c = ctx_for(db)
    V.accept_schedule(c, check(c, DAY["wed"], "move", "easy", to=DAY["mon"]))  # Mon: upper + easy
    two = check(c, DAY["tue"], "swap", "lower", to=DAY["mon"])
    assert not two.valid and "exactly one open session" in two.error


def test_shorten_cuts_at_most_half_and_only_runs(planned, db):
    c = ctx_for(db)
    km = db_km(db, DAY["sun"])
    assert km >= 3  # whatever the weekly cap left the long run
    ok = check(c, DAY["sun"], "shorten", "long", km=round(km * 0.6, 1))
    assert ok.valid and ok.risk == "low" and ok.label.startswith("Shorten long run Sun to ")
    too_much = check(c, DAY["sun"], "shorten", "long", km=round(km * 0.4, 1))
    assert not too_much.valid and "at most 50 %" in too_much.error
    assert not check(c, DAY["sun"], "shorten", "long", km=km + 2).valid  # must reduce
    assert not check(c, DAY["fri"], "shorten", "intervals", km=3).valid
    assert not check(c, DAY["sun"], "shorten", "long").valid  # needs a distance
    exactly_half = check(c, DAY["sun"], "shorten", "long", km=km * 0.5)
    assert exactly_half.valid


def db_km(db, day):
    row = db.scalar(select(PlannedSession).where(PlannedSession.day == day))
    return row.prescription["session"]["distance_km"]


def test_replace_only_to_an_equal_or_easier_kind(planned, db):
    c = ctx_for(db)
    easier = check(c, DAY["fri"], "replace", "intervals", new_kind="easy")
    assert (
        easier.valid
        and easier.risk == "low"
        and easier.label == "Replace intervals Fri with easy run"
    )
    assert check(c, DAY["tue"], "replace", "lower", new_kind="upper").risk == "low"
    assert check(c, DAY["thu"], "replace", "upper", new_kind="rest").risk == "low"
    harder = check(c, DAY["wed"], "replace", "easy", new_kind="intervals")
    assert not harder.valid and "equal or easier" in harder.error
    assert not check(c, DAY["sat"], "replace", "rest", new_kind="easy").valid
    assert not check(c, DAY["wed"], "replace", "easy", new_kind="easy").valid  # nothing changes
    assert not check(c, DAY["wed"], "replace", "easy").valid  # needs a kind
    equal = check(c, DAY["fri"], "replace", "intervals", new_kind="lower")  # same load rank
    assert equal.valid and equal.risk == "medium"


def test_replace_may_go_harder_to_make_up_a_missed_session(planned, db):
    wk = planned_id(NEXT, "fri_intervals")
    db.get(PlannedSession, wk).status = "skipped"
    db.commit()
    sat = NEXT + timedelta(days=5)
    c = ctx_for(db, today=sat)
    v = check(c, sat, "replace", "rest", new_kind="intervals")
    assert v.valid and v.risk == "medium" and v.makeup_for == wk
    assert v.edits[0].moved_from == DAY["fri"] and v.edits[0].makeup_for == wk
    # no missed lower this week: a harder replace stays forbidden
    assert not check(c, sat, "replace", "rest", new_kind="lower").valid


def test_weekly_volume_cap_blocks_a_makeup_that_needs_more_km(planned, db):
    wk = planned_id(NEXT, "fri_intervals")
    db.get(PlannedSession, wk).status = "skipped"
    db.commit()
    sat = NEXT + timedelta(days=5)
    c = ctx_for(db, today=sat)
    c.rooms[NEXT] = 3.0
    v = check(c, sat, "replace", "rest", new_kind="intervals")
    assert not v.valid and "weekly volume cap" in v.error
    c.rooms[NEXT] = None  # no cap yet
    assert check(c, sat, "replace", "rest", new_kind="intervals").valid


def test_skip_is_low_risk_and_a_session_changes_once(planned, db):
    c = ctx_for(db)
    s = check(c, DAY["wed"], "skip", "easy")
    assert s.valid and s.risk == "low" and s.label == "Skip easy run Wed"
    V.accept_schedule(c, s)
    again = check(c, DAY["wed"], "shorten", "easy", km=4)
    assert not again.valid and "already changed" in again.error


def test_changes_never_set_weights_or_paces(planned, db):
    c = ctx_for(db)
    assert "weights or paces" in check(c, DAY["wed"], "skip", "easy", reason="do 80 kg").error
    assert "weights or paces" in check(c, DAY["wed"], "skip", "easy", reason="at 4:30/km").error
    assert "distance_km only applies to shorten" in check(c, DAY["wed"], "skip", "easy", km=3).error
    assert "to_date only applies" in check(c, DAY["wed"], "skip", "easy", to=DAY["thu"]).error
    assert "new_kind only applies" in check(c, DAY["wed"], "skip", "easy", new_kind="rest").error


def test_later_changes_see_the_earlier_accepted_ones(planned, db):
    c = ctx_for(db)
    first = check(c, DAY["fri"], "move", "intervals", to=DAY["sat"])
    V.accept_schedule(c, first)
    clash = check(c, DAY["tue"], "move", "lower", to=DAY["fri"])  # Fri lower, Sat intervals
    assert not clash.valid and "in a row" in clash.error


def test_risk_levels_overview(planned, db):
    c = ctx_for(db)
    risks = {
        "skip": check(c, DAY["wed"], "skip", "easy").risk,
        "shorten": check(c, DAY["sun"], "shorten", "long", km=db_km(db, DAY["sun"]) - 1).risk,
        "replace_easier": check(c, DAY["fri"], "replace", "intervals", new_kind="easy").risk,
        "move": check(c, DAY["fri"], "move", "intervals", to=DAY["sat"]).risk,
        "swap": check(c, DAY["wed"], "swap", "easy", to=DAY["sat"]).risk,
    }
    assert risks == {
        "skip": "low", "shorten": "low", "replace_easier": "low", "move": "medium", "swap": "medium"
    }  # fmt: skip


# --------------------------------------------------------------------------- API


def ask_changes(client, fake, changes, *, params=()):
    fake.output = make_output(
        proposed_changes=list(params), session_overrides=[], schedule_changes=changes
    )
    r = client.post(
        "/api/v1/coach/ask",
        json={"scope": "week", "ref_id": "2026-10-26", "question": "I missed Tuesday, what now?"},
    )
    assert r.status_code == 202, r.text
    return client.get(f"/api/v1/coach/proposals/{r.json()['id']}").json()


def sc(date_, action, target, **kw):
    return ScheduleChange(date=_iso(date_), action=action, target=target, reason="Because.",
                          to_date=_iso(kw.get("to")), new_kind=kw.get("new_kind"),
                          distance_km=kw.get("km"))  # fmt: skip


SCHEDULE_KEYS = {
    "id", "date", "action", "target", "to_date", "new_kind", "distance_km", "reason", "risk",
    "valid", "validation_error", "status", "label",
}  # fmt: skip


def test_proposal_shape_with_a_valid_move_and_an_invalid_one_into_the_past(planned, client, fake):
    p = ask_changes(
        client,
        fake,
        [
            sc(DAY["fri"], "move", "intervals", to=DAY["sat"]),
            sc(DAY["wed"], "move", "easy", to=TODAY - timedelta(days=1)),
        ],
    )
    ok, bad = p["schedule_changes"]
    assert set(ok) == SCHEDULE_KEYS == set(bad)
    assert ok["id"].startswith("s") and ok["status"] == "proposed" and ok["valid"] is True
    assert ok["label"] == "Move intervals Fri → Sat" and ok["risk"] == "medium"
    assert ok["date"] == "2026-10-30" and ok["to_date"] == "2026-10-31"
    assert bad["status"] == "invalid" and bad["valid"] is False
    assert "in the past" in bad["validation_error"]
    assert p["status"] == "pending"


def test_ids_are_unique_across_both_lists(planned, client, fake):
    params = [
        ProposedChange(param="exercise_overrides.bench_press.rep_range", **{"from": [7, 12]},
                       to=[8, 12], reason="r", risk="low"),
        ProposedChange(param="running.weekly_volume_cap_pct", **{"from": 10}, to=12,
                       reason="r", risk="medium"),
    ]  # fmt: skip
    p = ask_changes(
        client,
        fake,
        [sc(DAY["wed"], "skip", "easy"), sc(DAY["fri"], "move", "intervals", to=DAY["sat"])],
        params=params,
    )
    ids = [c["id"] for c in p["changes"]] + [c["id"] for c in p["schedule_changes"]]
    assert len(ids) == 4 == len(set(ids))
    assert {i[0] for i in ids} == {"p", "s"}


def test_approve_applies_the_move_and_it_survives_the_next_engine_run(
    planned, client, fake, db, env
):
    p = ask_changes(client, fake, [sc(DAY["fri"], "move", "intervals", to=DAY["sat"])])
    sid = p["schedule_changes"][0]["id"]
    r = client.post(f"/api/v1/coach/proposals/{p['id']}/approve", json={"change_ids": [sid]})
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "applied" and done["schedule_changes"][0]["status"] == "applied"
    assert done["applied_config_version"] is None  # no parameter changed
    db.expire_all()
    fri = db.get(PlannedSession, planned_id(NEXT, "fri_intervals"))
    sat = db.get(PlannedSession, planned_id(NEXT, "sat_rest"))
    assert (fri.day, fri.kind, fri.moved_from, fri.changed_by) == (
        DAY["sat"],
        "intervals",
        DAY["fri"],
        "advisor",
    )
    assert fri.change_reason == "Because." and (sat.day, sat.kind) == (DAY["fri"], "rest")
    assert fri.prescription["session"]["type"] == "intervals"  # content recomputed by the engine
    d = [
        x
        for x in db.scalars(select(DecisionLog))
        if (x.output or {}).get("rule") == "advisor_schedule_change"
    ]
    assert len(d) == 1 and d[0].inputs["consultation_id"] == int(p["id"])
    assert d[0].planned_session_id == fri.id and "Move intervals Fri → Sat" in d[0].rationale
    for today in (TODAY, DAY["mon"], DAY["wed"]):  # the engine never undoes it
        engine.run_engine(db, today=today, settings=env)
        db.commit()
        db.expire_all()
        fri = db.get(PlannedSession, planned_id(NEXT, "fri_intervals"))
        assert (fri.day, fri.moved_from, fri.changed_by) == (DAY["sat"], DAY["fri"], "advisor"), (
            today
        )
    week = client.get("/api/v1/plan/week?start=2026-10-26").json()
    item = next(s for d in week["days"] for s in d["sessions"] if s["kind"] == "intervals")
    assert item["moved_from"] == "2026-10-30"
    assert item["change_reason"] == "Because."


def test_replace_and_shorten_are_applied_through_the_engine(planned, client, fake, db):
    km = db_km(db, DAY["sun"])
    p = ask_changes(
        client,
        fake,
        [
            sc(DAY["sun"], "shorten", "long", km=round(km * 0.7, 1)),
            sc(DAY["thu"], "replace", "upper", new_kind="rest"),
        ],
    )
    ids = [c["id"] for c in p["schedule_changes"]]
    client.post(f"/api/v1/coach/proposals/{p['id']}/approve", json={"change_ids": ids})
    db.expire_all()
    sun = db.get(PlannedSession, planned_id(NEXT, "sun_long_run"))
    assert sun.prescription["session"]["distance_km"] == round(km * 0.7, 1)
    thu = db.get(PlannedSession, planned_id(NEXT, "thu_upper"))
    assert thu.kind == "rest" and thu.prescription["session"]["type"] == "rest"


def test_skip_closes_the_session_and_nothing_is_made_up(planned, client, fake, db, env):
    p = ask_changes(client, fake, [sc(DAY["wed"], "skip", "easy")])
    client.post(f"/api/v1/coach/proposals/{p['id']}/approve", json={"change_ids": None})
    db.expire_all()
    wed = db.get(PlannedSession, planned_id(NEXT, "wed_easy_run"))
    assert wed.status == "skipped" and wed.changed_by == "advisor"
    res = engine.run_engine(db, today=DAY["thu"], settings=env)
    assert not [m for m in res.makeups.results if m.session_id == wed.id]  # deliberate skip


def test_mixed_approve_legacy_ids_and_errors(planned, client, fake):
    params = [ProposedChange(param="running.weekly_volume_cap_pct", **{"from": 10}, to=12,
                             reason="r", risk="medium")]  # fmt: skip
    p = ask_changes(
        client,
        fake,
        [sc(DAY["wed"], "skip", "easy"), sc(DAY["thu"], "move", "upper", to=TODAY)],
        params=params,
    )
    pid, ok, bad = (
        p["changes"][0]["id"],
        p["schedule_changes"][0]["id"],
        p["schedule_changes"][1]["id"],
    )
    url = f"/api/v1/coach/proposals/{p['id']}/approve"
    assert client.post(url, json={"change_ids": ["s999"]}).status_code == 422
    assert client.post(url, json={"change_ids": [bad]}).status_code == 422  # invalid
    assert client.post(url, json={"change_ids": ["x"]}).status_code == 422
    r = client.post(url, json={"change_ids": [pid.lstrip("p"), ok]})  # a bare number = parameter
    assert r.status_code == 200
    out = r.json()
    assert (
        out["changes"][0]["status"] == "applied"
        and out["schedule_changes"][0]["status"] == "applied"
    )
    assert out["status"] == "applied"  # the invalid one has nothing left to decide


def test_reject_rejects_schedule_changes_too(planned, client, fake):
    p = ask_changes(client, fake, [sc(DAY["wed"], "skip", "easy")])
    assert client.get("/api/v1/plan/today").json()["coach"]["pending_count"] == 1
    r = client.post(f"/api/v1/coach/proposals/{p['id']}/reject", json={"note": "no"})
    assert (
        r.json()["schedule_changes"][0]["status"] == "rejected" and r.json()["status"] == "rejected"
    )
    assert client.get("/api/v1/plan/today").json()["coach"]["pending_count"] == 0


def test_auto_low_risk_applies_skip_and_shorten_but_not_a_move(planned, client, fake, db):
    client.put("/api/v1/coach/settings", json={"approval_mode": "auto-low-risk"})
    km = db_km(db, DAY["sun"])
    p = ask_changes(
        client,
        fake,
        [
            sc(DAY["wed"], "skip", "easy"),
            sc(DAY["sun"], "shorten", "long", km=round(km * 0.8, 1)),
            sc(DAY["fri"], "move", "intervals", to=DAY["sat"]),
        ],
    )
    st = {c["action"]: c["status"] for c in p["schedule_changes"]}
    assert st == {"skip": "applied", "shorten": "applied", "move": "proposed"}
    assert p["status"] == "partially_applied"
    db.expire_all()
    assert db.get(PlannedSession, planned_id(NEXT, "wed_easy_run")).status == "skipped"
    assert db.get(PlannedSession, planned_id(NEXT, "fri_intervals")).day == DAY["fri"]


def test_auto_mode_applies_every_valid_change(planned, client, fake):
    client.put("/api/v1/coach/settings", json={"approval_mode": "auto"})
    p = ask_changes(
        client,
        fake,
        [
            sc(DAY["fri"], "move", "intervals", to=DAY["sat"]),
            sc(DAY["wed"], "move", "easy", to=TODAY - timedelta(days=3)),
        ],
    )
    assert [c["status"] for c in p["schedule_changes"]] == ["applied", "invalid"]
    assert p["status"] == "applied"


def test_manual_mode_applies_nothing_by_itself(planned, client, fake, db):
    p = ask_changes(client, fake, [sc(DAY["wed"], "skip", "easy")])
    assert p["schedule_changes"][0]["status"] == "proposed"
    db.expire_all()
    assert db.get(PlannedSession, planned_id(NEXT, "wed_easy_run")).status == "proposed"


def test_changes_are_revalidated_when_approved(planned, client, fake, db):
    p = ask_changes(client, fake, [sc(DAY["wed"], "skip", "easy")])
    row = db.get(PlannedSession, planned_id(NEXT, "wed_easy_run"))
    row.status = "done"  # the session happened in the meantime
    db.commit()
    r = client.post(f"/api/v1/coach/proposals/{p['id']}/approve", json={"change_ids": None})
    got = r.json()["schedule_changes"][0]
    assert got["status"] == "invalid" and got["validation_error"].startswith("no longer valid")


def test_a_stored_proposal_only_has_schedule_changes_in_db(planned, client, fake, db):
    ask_changes(client, fake, [sc(DAY["wed"], "skip", "easy")])
    rows = db.scalars(select(AdvisorScheduleChange)).all()
    assert len(rows) == 1 and rows[0].planned_session_id == planned_id(NEXT, "wed_easy_run")


# --------------------------------------------------------------------------- prompt / summary


def test_prompt_describes_schedule_changes_and_stays_free_of_dates():
    needles = (
        "Schedule changes",
        "schedule_changes",
        "missed_session",
        "Prefer dropping over cramming",
        "two hard days in a row",
        "24 h rule",
    )
    for needle in needles:
        assert needle in SYSTEM_PROMPT
    assert "2026-" not in SYSTEM_PROMPT


def test_input_carries_the_schedule_section(planned, db, env):
    from apps.fitness.advisor import summary

    doc = summary.build_input(
        db, trigger={"type": "ask", "title": "t", "detail": "d"}, question="q", settings=env
    )
    sch = doc["schedule"]
    assert [w["week_start"] for w in sch["weeks"]] == ["2026-10-19", "2026-10-26"]
    assert sch["columns"] == ["date", "kind", "status", "km", "moved_from"]
    assert sch["rules"]["max_sessions_per_day"] == 2 and sch["rules"]["hard_kinds"] == [
        "lower",
        "intervals",
    ]


def test_missed_session_trigger_is_a_proposal_trigger_type(db):
    from apps.fitness.db.models import AdvisorTrigger

    t = AdvisorTrigger(type="missed_session", dedupe_key="m:1", day=date(2026, 10, 30),
                       payload={"missed": {"kind": "intervals"}, "reason": "no slot",
                                "week_start": "2026-10-26"})  # fmt: skip
    info = advisor.trigger_info_from_rows([t])
    assert info["type"] == "missed_session" and info["title"] == "Missed intervals"
    assert info["detail"] == "no slot" and info["ref"] == {"kind": "week", "id": "2026-10-26"}
