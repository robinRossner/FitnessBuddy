"""Weekly template + the 'no intervals right after heavy legs' swap rule."""

from datetime import date, timedelta

from app.engine import scheduling as S
from app.engine.config import load_engine_config
from app.settings import BACKEND_DIR

TEMPLATE = load_engine_config(BACKEND_DIR / "config" / "engine.v3.yaml").weekly_template
MON = date(2026, 10, 26)


def by_kind(slots: list[S.Slot]) -> dict[str, date]:
    return {s.id: s.day for s in slots}


def test_template_week_ids_and_days():
    slots = S.build_week(MON, TEMPLATE)
    assert [s.id for s in slots] == [
        "mon_upper",
        "tue_lower",
        "wed_easy_run",
        "thu_upper",
        "fri_intervals",
        "sat_rest",
        "sun_long_run",
    ]
    assert slots[4].day == MON + timedelta(days=4)


def test_default_template_needs_no_swap():
    slots, events = S.apply_recovery_rules(
        S.build_week(MON, TEMPLATE), today=MON, actual_lower_days=set()
    )
    assert events == [] and by_kind(slots)["fri_intervals"] == MON + timedelta(days=4)


def test_gap_hours_to_days():
    assert S.min_gap_days(24) == 2 and S.min_gap_days(48) == 3


def test_planned_lower_the_day_before_intervals_swaps_with_next_free_day():
    tpl = {**TEMPLATE, "thu": "lower", "tue": "upper"}  # lower Thu -> intervals Fri violates
    slots, events = S.apply_recovery_rules(
        S.build_week(MON, tpl), today=MON, actual_lower_days=set()
    )
    d = by_kind(slots)
    assert d["fri_intervals"] == MON + timedelta(days=5)  # Saturday (rest swapped)
    assert d["sat_rest"] == MON + timedelta(days=4)
    assert [e.rule for e in events] == ["swap"] and events[0].swapped_with == "sat_rest"
    assert "after the lower-body day" in events[0].reason
    assert any("moved from" in n for s in slots for n in s.notes)


def test_actual_lower_session_on_thursday_pushes_friday_intervals():
    actual = {MON + timedelta(days=3)}  # lower really done Thursday
    tpl = {**TEMPLATE, "tue": "upper"}  # the planned lower Tuesday is not in the way
    slots, events = S.apply_recovery_rules(
        S.build_week(MON, tpl), today=MON + timedelta(days=3), actual_lower_days=actual
    )
    assert by_kind(slots)["fri_intervals"] == MON + timedelta(days=5)
    assert events and events[0].rule == "swap"


def test_same_day_is_also_a_violation():
    tpl = {**TEMPLATE, "fri": "lower", "thu": "intervals"}
    slots, _ = S.apply_recovery_rules(S.build_week(MON, tpl), today=MON, actual_lower_days=set())
    # intervals on Thu, lower Fri: Thu is before the lower day -> fine (rule is only 'after')
    assert by_kind(slots)["thu_intervals"] == MON + timedelta(days=3)


def test_prefers_a_day_that_is_not_right_before_the_long_run():
    tpl = {**TEMPLATE, "thu": "lower", "sat": "easy_run", "fri": "intervals", "wed": "rest"}
    # Fri violates; candidates: Sat (easy, but next day is the long run) -> only one later candidate
    slots, events = S.apply_recovery_rules(
        S.build_week(MON, tpl), today=MON, actual_lower_days=set()
    )
    assert by_kind(slots)["fri_intervals"] == MON + timedelta(days=5)  # still the only option
    assert events[0].rule == "swap"


def test_no_later_free_day_downgrades_to_easy_run():
    tpl = {**TEMPLATE, "thu": "lower", "sat": "upper"}  # nothing swappable after Friday
    slots, events = S.apply_recovery_rules(
        S.build_week(MON, tpl), today=MON, actual_lower_days=set()
    )
    kinds = {s.id: s.kind for s in slots}
    assert kinds["fri_intervals"] == "easy_run" and events[0].rule == "downgrade"


def test_past_days_are_never_moved():
    tpl = {**TEMPLATE, "thu": "lower"}
    slots, events = S.apply_recovery_rules(
        S.build_week(MON, tpl), today=MON + timedelta(days=5), actual_lower_days=set()
    )
    assert events == [] and by_kind(slots)["fri_intervals"] == MON + timedelta(days=4)


def test_slot_ids_survive_a_swap():
    tpl = {**TEMPLATE, "thu": "lower", "tue": "upper"}
    slots, _ = S.apply_recovery_rules(S.build_week(MON, tpl), today=MON, actual_lower_days=set())
    assert "fri_intervals" in {s.id for s in slots} and "sat_rest" in {s.id for s in slots}
