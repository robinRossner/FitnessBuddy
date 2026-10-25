"""Double progression, increments, stall + reset rounding, deload for one variant."""

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from app.engine import lifting as L
from app.engine.config import load_engine_config
from app.engine.increments import previous_load, round_down_load
from app.settings import BACKEND_DIR

CFG = load_engine_config(BACKEND_DIR / "config" / "engine.v3.yaml")
D0 = date(2026, 10, 1)


@dataclass
class V:
    equipment_type: str = "barbell"
    load_convention: str = "total"
    weight_ladder: list[float] | None = None
    name: str = "Test"


def expo(i: int, weight: float, reps: list[int], e1rm: float | None = None) -> L.Exposure:
    return L.Exposure(
        D0 + timedelta(days=3 * i),
        tuple(L.SetResult(weight, r) for r in reps),
        e1rm if e1rm is not None else round(weight * (1 + max(reps) / 30), 2),
    )


def spec(v=None, sets=3, rr=(7, 12), **kw) -> L.LiftSpec:
    return L.LiftSpec(v or V(), rr, sets, **kw)


def targets(p: L.LiftPrescription) -> list[tuple[float | None, int | None]]:
    return [(s.weight, s.reps) for s in p.sets]


# --------------------------------------------------------------------------- double progression


def test_all_sets_at_top_same_load_increases_load_and_resets_reps():
    p = L.prescribe(spec(), [expo(0, 60, [12, 12, 12])], CFG)
    assert targets(p) == [(65.0, 7)] * 3 and p.rule == "double_progression:increase_load"
    assert p.previous.sets[0].weight == 60  # previous performance travels with the prescription


def test_top_reps_but_mixed_loads_does_not_increase():
    last = L.Exposure(D0, (L.SetResult(60, 12), L.SetResult(60, 12), L.SetResult(50, 12)), 84.0)
    p = L.prescribe(spec(), [last], CFG)
    assert p.rule == "double_progression:add_reps"
    assert targets(p) == [(60, 12), (60, 12), (50, 12)]  # capped at the top, loads kept per set


def test_otherwise_same_load_plus_one_rep_per_set_capped():
    p = L.prescribe(spec(), [expo(0, 60, [12, 10, 8])], CFG)
    assert targets(p) == [(60, 12), (60, 11), (60, 9)] and p.rule == "double_progression:add_reps"


def test_first_set_below_range_decreases_one_step():
    p = L.prescribe(spec(), [expo(0, 60, [6, 6, 5])], CFG)
    assert targets(p) == [(55.0, 7)] * 3 and p.rule == "double_progression:decrease_load"
    assert "load_down" in p.flags


def test_decrease_can_be_switched_off_by_config():
    cfg = CFG.model_copy(
        update={
            "strength": CFG.strength.model_copy(
                update={"decrease_load_if_first_set_below_range": False}
            )
        }
    )
    p = L.prescribe(spec(), [expo(0, 60, [6, 6, 5])], cfg)
    assert p.rule == "double_progression:add_reps" and targets(p)[0] == (60, 7)


def test_only_later_sets_below_range_just_adds_reps():
    p = L.prescribe(spec(), [expo(0, 60, [9, 6, 5])], CFG)
    assert p.rule == "double_progression:add_reps" and targets(p) == [(60, 10), (60, 7), (60, 6)]


def test_n_sets_from_program_pads_and_truncates():
    p = L.prescribe(spec(sets=4), [expo(0, 60, [10, 9, 8])], CFG)
    assert targets(p) == [(60, 11), (60, 10), (60, 9), (60, 9)]
    assert len(L.prescribe(spec(sets=2), [expo(0, 60, [10, 9, 8])], CFG).sets) == 2


def test_rep_range_override_and_increment_override():
    p = L.prescribe(spec(rr=(8, 15), increment_kg=2.5), [expo(0, 60, [15, 15, 15])], CFG)
    assert targets(p) == [(62.5, 8)] * 3
    p = L.prescribe(spec(rr=(5, 8)), [expo(0, 60, [8, 8, 8])], CFG)
    assert targets(p)[0] == (65.0, 5)


def test_no_history_has_no_weight():
    p = L.prescribe(spec(), [], CFG)
    assert p.rule == "no_history" and targets(p) == [(None, 7)] * 3 and p.previous is None


def test_increments_per_equipment():
    ex = [expo(0, 20, [12, 12, 12])]
    db = V("dumbbell", "per_hand")
    assert targets(L.prescribe(spec(db), ex, CFG))[0] == (22.0, 7)  # +2 kg per dumbbell
    stack = V("machine_stack", weight_ladder=[10, 15, 20, 25, 32.5])
    assert targets(L.prescribe(spec(stack), ex, CFG))[0] == (25.0, 7)  # next rung
    ex = [expo(0, 32.5, [12, 12, 12])]
    assert targets(L.prescribe(spec(stack), ex, CFG))[0] == (37.5, 7)  # above the ladder: +5


def test_bodyweight_reps_first_then_added_weight():
    bw = V("bodyweight", "total")
    p = L.prescribe(spec(bw), [expo(0, 0, [9, 8, 7], e1rm=95)], CFG)
    assert targets(p) == [(0, 10), (0, 9), (0, 8)]
    p = L.prescribe(spec(bw), [expo(0, 0, [12, 12, 12], e1rm=105)], CFG)
    assert targets(p) == [(2.5, 7)] * 3  # first added plate


def test_timed_hold_progression():
    last = L.Exposure(D0, (L.SetResult(None, None, 30.0), L.SetResult(None, None, 25.0)), None)
    p = L.prescribe(spec(sets=2, timed=True), [last], CFG)
    assert [s.duration_s for s in p.sets] == [35.0, 35.0] and p.rule == "timed_progression"


# --------------------------------------------------------------------------- stall


def test_detect_stall():
    assert L.detect_stall([100, 100, 99, 100], 3)  # nothing above 100 in the last 3
    assert not L.detect_stall([100, 101, 99, 100], 3)  # 101 is an improvement
    assert not L.detect_stall([100, 100, 100], 3)  # needs a baseline before the window
    assert not L.detect_stall([100, 101, 102, 103], 3)
    assert L.detect_stall([None, 100, 100, 100, 99], 3)  # None ignored


def test_stall_resets_load_minus_10_percent_rounded_down():
    hist = [
        expo(0, 60, [10, 9, 9], 80.0),
        expo(1, 60, [10, 9, 8], 78.0),
        expo(2, 60, [9, 9, 8], 77.0),
        expo(3, 60, [9, 8, 8], 76.0),
    ]
    p = L.prescribe(spec(), hist, CFG)
    # 60 * 0.9 = 54 -> barbell grid of 5 kg below 60 -> 50 (rounded DOWN to an available load)
    assert p.stall and p.rule == "stall_reset" and targets(p) == [(50.0, 7)] * 3
    # a ladder machine rounds down to a rung
    stack = V("machine_stack", weight_ladder=[30, 35, 40, 45, 50, 55, 60, 65])
    hist = [expo(i, 60, [10, 9, 9], 80.0 - i) for i in range(4)]
    assert targets(L.prescribe(spec(stack), hist, CFG))[0] == (50.0, 7)  # 54 -> rung 50


def test_round_down_load_helpers():
    bb = V("barbell")
    assert round_down_load(bb, 52.5, 52.5 * 0.9, CFG) == 42.5  # 47.25 -> stays on the 52.5 grid
    assert round_down_load(bb, 60, 59.9, CFG) == 55  # always at least one step
    db = V("dumbbell", "per_hand")
    assert round_down_load(db, 30, 27, CFG) == 26  # 2 kg grid: 28 is above 27 -> 26
    assert previous_load(V("machine_stack", weight_ladder=[10, 20, 30]), 30, CFG) == 20
    assert previous_load(V("barbell"), 2, CFG) == 0
    assisted = V("machine_stack", "assistance", weight_ladder=[10, 20, 30])
    assert previous_load(assisted, 20, CFG) == 30  # easier = more assistance
    assert round_down_load(assisted, 20, 22, CFG) == 30  # assistance rounds UP


def test_stall_not_flagged_after_reset_until_enough_new_exposures():
    base = [expo(i, 60, [10, 9, 9], 80.0 - i) for i in range(4)]
    reset = L.StallReset(D0 + timedelta(days=12), 50.0)
    # reset not trained yet: stays in force
    p = L.prescribe(spec(), base, CFG, stall_reset=reset)
    assert p.rule == "stall_reset" and not p.stall and targets(p)[0] == (50.0, 7)
    # trained at 50 after the reset: normal progression, no new stall from the pre-reset history
    after = [*base, L.Exposure(D0 + timedelta(days=13), (L.SetResult(50, 8),) * 3, 63.3)]
    p = L.prescribe(spec(), after, CFG, stall_reset=reset)
    assert p.rule == "double_progression:add_reps" and targets(p)[0] == (50, 9)


# --------------------------------------------------------------------------- deload / yellow


def test_deload_cuts_sets_and_load():
    p = L.prescribe(spec(sets=4), [expo(0, 60, [10, 9, 8, 8])], CFG, deload=True)
    # 4 sets -40 % = 2.4 -> 2; load 60 -10 % = 54 -> rounded down on the 5 kg grid -> 50
    assert len(p.sets) == 2 and p.sets[0].weight == 50.0
    assert "deload" in p.flags and p.rule.endswith("+deload")
    one = L.prescribe(spec(sets=1), [expo(0, 60, [10])], CFG, deload=True)
    assert len(one.sets) == 1  # min 1
    three = L.prescribe(spec(sets=3), [expo(0, 60, [10, 9, 8])], CFG, deload=True)
    assert len(three.sets) == 2  # 3 * 0.6 = 1.8 -> 2


def test_drop_sets_for_yellow_keeps_at_least_one():
    sets = [L.SetTarget(i + 1, 60, 8) for i in range(3)]
    assert len(L.drop_sets(sets)) == 2
    assert len(L.drop_sets(sets[:1])) == 1
    assert [s.index for s in L.drop_sets(sets)] == [1, 2]


def test_no_deload_on_first_ever_exposure_prescription():
    p = L.prescribe(spec(), [], CFG, deload=True)
    assert p.rule == "no_history" and len(p.sets) == 3


@pytest.mark.parametrize("reps", [[12, 12, 12], [8, 8, 8]])
def test_prescription_always_has_requested_set_count(reps):
    assert len(L.prescribe(spec(sets=3), [expo(0, 60, reps)], CFG).sets) == 3


def test_stall_with_identical_sets_is_flagged_and_notes_lowest_load():
    stack = V("machine_stack", weight_ladder=[5, 10])
    hist = [L.Exposure(D0 + timedelta(days=3 * i), (L.SetResult(5, 9),) * 3, 6.5) for i in range(5)]
    p = L.prescribe(spec(stack), hist, CFG)
    assert p.stall and "identical_exposures" in p.flags
    assert targets(p) == [(5.0, 7)] * 3  # no lower rung: load stays, reps restart
    assert "lowest available load" in p.reason and "identical sets" in p.reason
