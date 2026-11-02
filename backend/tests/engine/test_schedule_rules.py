"""The hard scheduling rules as pure functions (apps/fitness/engine/schedule_rules.py)."""

from __future__ import annotations

from datetime import date, timedelta

from apps.fitness.engine.core.config import EngineConfig
from apps.fitness.engine.core.schedule_rules import (
    Edit,
    Layout,
    Rules,
    Sess,
    apply_edits,
    new_violations,
    normalize_kind,
    past_problem,
    violations,
    volume_problem,
)

MON = date(2026, 10, 26)
D = {
    n: MON + timedelta(days=i)
    for i, n in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))
}


def rules(**schedule) -> Rules:
    return Rules.from_config(EngineConfig(version="t", schedule=schedule))


def layout(*items: tuple[str, str], lower_days=()) -> Layout:
    return Layout(
        [Sess(f"s{i}", D[day], kind) for i, (day, kind) in enumerate(items)], set(lower_days)
    )


TODAY = MON


def test_default_template_week_is_valid():
    lay = layout(
        ("mon", "upper"), ("tue", "lower"), ("wed", "easy_run"), ("thu", "upper"),
        ("fri", "intervals"), ("sat", "rest"), ("sun", "long_run"),
    )  # fmt: skip
    assert violations(lay, rules(), today=TODAY) == []


def test_one_hard_session_per_day():
    out = violations(layout(("wed", "lower"), ("wed", "intervals")), rules(), today=TODAY)
    assert any("2 hard sessions" in v for v in out)


def test_never_two_hard_days_in_a_row():
    out = violations(layout(("tue", "lower"), ("wed", "intervals")), rules(), today=TODAY)
    assert any("never two hard days in a row" in v for v in out)
    # switchable in the config
    assert not any(
        "in a row" in v
        for v in violations(
            layout(("tue", "lower"), ("wed", "intervals")),
            rules(no_consecutive_hard_days=False),
            today=TODAY,
        )
    )


def test_24h_rule_after_lower_is_two_calendar_days():
    lax = rules(no_consecutive_hard_days=False)
    assert any(
        "24 h rule" in v
        for v in violations(layout(("tue", "lower"), ("wed", "intervals")), lax, today=TODAY)
    )
    assert violations(layout(("tue", "lower"), ("thu", "intervals")), lax, today=TODAY) == []
    # a lower day that really happened (from the training log) counts too
    assert any(
        "24 h rule" in v
        for v in violations(layout(("tue", "intervals"), lower_days=[D["mon"]]), lax, today=TODAY)
    )


def test_two_sessions_per_day_at_most_and_rest_does_not_count():
    three = layout(("thu", "upper"), ("thu", "easy_run"), ("thu", "long_run"))
    assert any("3 sessions" in v for v in violations(three, rules(), today=TODAY))
    two = layout(("thu", "upper"), ("thu", "easy_run"), ("thu", "rest"))
    assert violations(two, rules(), today=TODAY) == []


def test_skipped_sessions_carry_no_load_and_the_past_is_not_judged():
    lay = layout(("tue", "lower"), ("wed", "intervals"))
    lay.sessions[0].status = "skipped"
    assert violations(lay, rules(), today=TODAY) == []
    past = layout(("mon", "lower"), ("tue", "intervals"))
    assert violations(past, rules(), today=D["wed"]) == []


def test_new_violations_ignores_what_was_already_broken():
    before = layout(("tue", "lower"), ("wed", "intervals"), ("fri", "easy_run"))
    after = apply_edits(before, [Edit("s2", day=D["sat"])])
    assert new_violations(before, after, rules(), today=TODAY) == []
    worse = apply_edits(before, [Edit("s2", day=D["thu"], kind="lower")])
    assert new_violations(before, worse, rules(), today=TODAY)


def test_apply_edits_does_not_touch_the_input():
    lay = layout(("tue", "lower"))
    out = apply_edits(lay, [Edit("s0", day=D["wed"], kind="upper")])
    assert (lay.sessions[0].day, lay.sessions[0].kind) == (D["tue"], "lower")
    assert (out.sessions[0].day, out.sessions[0].kind, out.sessions[0].locked) == (
        D["wed"], "upper", True
    )  # fmt: skip
    assert apply_edits(lay, [Edit("s0", skip=True)]).sessions[0].status == "skipped"


def test_past_problem_and_kind_names():
    assert past_problem([Sess("a", D["mon"], "upper")], D["tue"]).startswith("Mon")
    assert past_problem([Sess("a", D["tue"], "upper")], D["tue"]) is None
    assert normalize_kind("Easy") == "easy_run" and normalize_kind("long run") == "long_run"
    assert normalize_kind("intervals") == "intervals" and normalize_kind("yoga") is None


def test_volume_problem_only_when_the_change_adds_minimum_km():
    cfg = EngineConfig(version="t")
    before = layout(("wed", "easy_run"), ("sun", "long_run"))
    after = apply_edits(before, [Edit("s0", kind="intervals")])
    assert volume_problem(before, after, cfg, today=TODAY, week_start=MON, room_km=None) is None
    assert volume_problem(before, after, cfg, today=TODAY, week_start=MON, room_km=30) is None
    # minimums: intervals 6.7 km (4 reps, minimal warm-up / cool-down) + long run 3 km (v2)
    msg = volume_problem(before, after, cfg, today=TODAY, week_start=MON, room_km=8)
    assert msg and "weekly volume cap" in msg
    # a shrinking change is never blamed on the cap
    shrink = apply_edits(before, [Edit("s1", kind="easy_run")])
    assert volume_problem(before, shrink, cfg, today=TODAY, week_start=MON, room_km=1) is None


def test_v4_seed_file_carries_the_schedule_defaults():
    from apps.fitness.engine.core.config import ScheduleConfig, load_engine_config
    from apps.fitness.settings import FITNESS_DIR

    v4 = load_engine_config(FITNESS_DIR / "config" / "engine.v4.yaml")
    v3 = load_engine_config(FITNESS_DIR / "config" / "engine.v3.yaml")
    assert v4.version == "v4" and v3.schedule == {}
    assert v4.schedule_cfg == ScheduleConfig()  # the file documents exactly the code defaults
    assert v3.schedule_cfg == ScheduleConfig()  # a version without the section behaves the same
    assert v4.weekly_template == v3.weekly_template and v4.running == v3.running
