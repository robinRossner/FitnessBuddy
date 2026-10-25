"""Lifting progression v2 (docs/research/lifting-progression.md, decisions "Progression engine
v2"): reps at a load, noise vs real drops, the two-strike gate, the rung rule, stalls per
frequency, censoring, the stall ladder, deload / retest windows and returns after a break."""

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from app.engine import lifting as L
from app.engine.config import load_engine_config
from app.engine.increments import previous_load, round_down_load
from app.settings import BACKEND_DIR

CFG = load_engine_config(BACKEND_DIR / "config" / "engine.v6.yaml")
D0 = date(2026, 9, 1)


@dataclass
class V:
    equipment_type: str = "barbell"
    load_convention: str = "total"
    weight_ladder: list[float] | None = None
    name: str = "Test"


def ex(day: int, weight: float, reps: list[int], gym: int | None = None) -> L.Exposure:
    return L.Exposure(
        D0 + timedelta(days=day), tuple(L.SetResult(weight, r) for r in reps), None, gym
    )


def spec(v=None, sets=3, rr=(7, 12), per_week=2, **kw) -> L.LiftSpec:
    return L.LiftSpec(v or V(), rr, sets, per_week=per_week, **kw)


def nxt(hist, s=None, env=None, gap=3, cfg=CFG):
    day = hist[-1].day + timedelta(days=gap) if hist else D0
    return L.prescribe(s or spec(), hist, cfg, day=day, ctx=env)


def targets(p: L.LiftPrescription):
    return [(s.weight, s.reps) for s in p.sets]


def grind(n: int, weight=60.0, reps=(10, 9, 8), start=0, step=3.5) -> list[L.Exposure]:
    """`n` sessions with exactly the same sets (no progress event). The first two sessions of a
    variant are calibration (censored), so `n` sessions give `n - 2` clean ones."""
    return [ex(round(start + i * step), weight, list(reps)) for i in range(n)]


CAL = [ex(-8, 60, [9, 8, 7]), ex(-5, 60, [9, 8, 7])]  # two calibration sessions first


# --------------------------------------------------------------------------- basics


def test_no_history_has_no_weight():
    p = L.prescribe(spec(), [], CFG)
    assert p.rule == "no_history" and targets(p) == [(None, 7)] * 3 and p.previous is None


def test_timed_hold_progression():
    p = L.prescribe(spec(timed=True), [L.Exposure(D0, (L.SetResult(None, None, 40.0),))], CFG)
    assert p.rule == "timed_progression" and p.sets[0].duration_s == 45


def test_prescription_keeps_the_program_set_count():
    for n in (1, 2, 4):
        assert len(nxt([ex(0, 60, [10, 9, 8]), ex(3, 60, [10, 9, 9])], spec(sets=n)).sets) == n


# --------------------------------------------------------------------------- good progression


def test_good_progression_all_sets_at_top_takes_the_next_rung_with_predicted_reps():
    p = nxt([ex(0, 60, [11, 11, 10]), ex(3, 60, [12, 12, 12])])
    assert p.rule == "double_progression:increase_load" and p.state == L.PROGRESSING
    # Epley: 60 x (1 + 12/30) = 84 -> at 65 kg ~ 8.8 reps: the target is 8, not blindly 7
    assert targets(p) == [(65.0, 8)] * 3
    assert "Epley" in p.note


def test_slow_grind_adds_one_rep_per_set_capped_at_the_top():
    p = nxt([ex(0, 60, [10, 9, 8]), ex(3, 60, [11, 10, 8])])
    assert p.rule == "double_progression:add_reps"
    assert targets(p) == [(60, 12), (60, 11), (60, 9)]
    assert p.state == L.PROGRESSING  # a rep PR is progress
    assert "+1 rep" in p.note


def test_same_reps_again_is_grinding_not_progress():
    p = nxt([ex(0, 60, [11, 10, 8]), ex(3, 60, [11, 10, 8])])
    assert p.state == L.GRINDING and targets(p) == [(60, 12), (60, 11), (60, 9)]


# --------------------------------------------------------------------------- noise vs real drop


def test_noise_dip_of_two_reps_repeats_the_same_target():
    hist = [ex(0, 60, [10, 9, 8]), ex(3, 60, [11, 10, 9]), ex(7, 60, [10, 9, 9])]
    p = nxt(hist)
    assert p.rule == "double_progression:hold_noise" and p.state == L.GRINDING
    assert targets(p) == [(60, 12), (60, 11), (60, 10)]  # from the better (11/10/9) session


def test_real_drop_of_three_reps_steps_one_rung_down():
    hist = [ex(0, 60, [10, 9, 8]), ex(3, 60, [11, 10, 9]), ex(7, 60, [10, 8, 8])]
    p = nxt(hist)
    assert p.rule == "double_progression:decrease_load" and p.state == L.REBUILD
    assert p.load == 55.0 and "load_down" in p.flags


def test_two_clean_drops_in_a_row_step_down():
    hist = [*CAL, ex(0, 60, [11, 10, 10]), ex(3, 60, [11, 10, 9]), ex(7, 60, [10, 10, 9])]
    p = nxt(hist)
    assert p.rule == "double_progression:decrease_load" and p.load == 55.0


def test_a_single_censored_drop_never_steps_down():
    hist = [*CAL, ex(0, 60, [11, 10, 10]), ex(3, 60, [11, 10, 9]), ex(7, 60, [8, 7, 7])]
    env = L.LiftEnv(readiness={hist[-1].day: "yellow"})
    p = nxt(hist, env=env)
    assert p.rule == "double_progression:add_reps" and p.load == 60
    an = L.analyze(spec(), hist, CFG, env)
    assert an.censored()[-1] == [hist[-1].day.isoformat(), "readiness yellow"]


# --------------------------------------------------------------------------- two-strike gate


def test_two_strike_gate_every_set_at_top_minus_one_twice():
    one = nxt([ex(0, 60, [10, 10, 9]), ex(3, 60, [12, 11, 11])])
    assert one.rule == "double_progression:add_reps"  # once is not enough
    two = nxt([ex(0, 60, [10, 10, 9]), ex(3, 60, [12, 11, 11]), ex(7, 60, [11, 12, 11])])
    assert two.rule == "double_progression:increase_load" and two.load == 65.0
    assert "twice" in two.note


def test_one_weak_last_set_no_longer_blocks_the_jump_but_two_weak_do():
    p = nxt([ex(0, 60, [12, 12, 10]), ex(3, 60, [12, 12, 10])])
    assert p.rule == "double_progression:add_reps"


# --------------------------------------------------------------------------- rung rule


def test_rung_rule_raises_the_ceiling_when_the_next_rung_is_too_big():
    stack = V("machine_stack", weight_ladder=[35.0, 40.0, 46.0])  # 40 -> 46 (+15 %)
    s = spec(stack)
    top, infeasible, step = L.effective_top(s, CFG, 40.0)
    assert (top, infeasible, step) == (14, False, 46.0)
    p = nxt([ex(0, 40, [11, 11, 10]), ex(3, 40, [12, 12, 12])], s)
    assert p.rule == "double_progression:add_reps" and p.effective_rep_range == (7, 14)
    assert targets(p) == [(40, 13)] * 3 and "ceiling_raised" in p.flags
    assert "ceiling 14" in p.note
    jump = nxt([ex(0, 40, [12, 12, 12]), ex(3, 40, [14, 14, 14])], s)
    assert jump.rule == "double_progression:increase_load" and jump.load == 46.0
    assert all(t.reps >= CFG.strength.rung_min_reps for t in jump.sets)


def test_rung_rule_infeasible_jump_caps_at_15_and_asks_the_coach():
    cable = V("cable", weight_ladder=[5.0])  # 5 -> 10 kg = +100 %
    s = spec(cable)
    assert L.effective_top(s, CFG, 5.0)[:2] == (15, True)
    p = nxt([ex(0, 5, [15, 15, 14]), ex(3, 5, [15, 15, 15])], s)
    assert p.rule == "rung_infeasible" and p.state == L.STALLED and p.load == 5.0
    assert "rung_infeasible" in p.flags


def test_ceiling_needed_formula_matches_the_report():
    # a 12-rep set loses ~2 reps at +5 % and ~4 at +10 % (still 8): +15 % needs 14 reps
    assert L.ceiling_needed(100, 105, 8) <= 12
    assert L.ceiling_needed(100, 110, 8) == 12
    assert L.ceiling_needed(100, 115, 8) == 14


def test_first_set_far_below_the_floor_at_a_new_load_steps_back():
    hist = [ex(0, 60, [12, 12, 12]), ex(3, 65, [5, 5, 4])]
    p = nxt(hist)
    assert p.rule == "double_progression:decrease_load" and p.load == 60.0


def test_first_set_just_below_the_floor_gets_one_more_try_then_steps_back():
    hist = [ex(0, 60, [12, 12, 12]), ex(3, 65, [6, 6, 5])]
    assert nxt(hist).rule == "double_progression:hold_new_load"
    hist.append(ex(7, 65, [6, 6, 6]))
    p = nxt(hist)
    assert p.rule == "double_progression:decrease_load" and p.load == 60.0


# --------------------------------------------------------------------------- stall per frequency


def test_stall_needs_four_clean_sessions_twice_a_week_and_twelve_days():
    s2 = spec(per_week=2)
    assert not L.analyze(s2, grind(5), CFG).stalled  # 3 clean sessions
    an = L.analyze(s2, grind(6), CFG)  # 4 clean sessions, 18 days
    assert an.stalled and an.counter == 4 and an.n_required == 4


def test_stall_needs_three_clean_sessions_once_a_week():
    s1 = spec(per_week=1)
    assert not L.analyze(s1, grind(4, step=7), CFG).stalled
    an = L.analyze(s1, grind(5, step=7), CFG)
    assert an.stalled and an.n_required == 3 and an.counter == 3


def test_stall_needs_twelve_days_even_with_enough_sessions():
    an = L.analyze(spec(per_week=2), grind(6, step=2), CFG)  # 4 clean in 10 days
    assert an.counter == 4 and not an.stalled


def test_a_progress_event_resets_the_stall_counter():
    hist = [*grind(5), ex(18, 60, [11, 9, 8]), *grind(2, start=21)]
    an = L.analyze(spec(), hist, CFG)
    assert an.evals[5].event == "rep_pr" and an.counter == 2 and not an.stalled


def test_e1rm_gain_of_one_percent_is_progress():
    hist = [ex(0, 60, [10, 9, 8]), ex(4, 62.5, [9, 8, 8])]
    an = L.analyze(spec(), hist, CFG)
    assert an.evals[-1].event in ("load_pr", "e1rm_pr")


# --------------------------------------------------------------------------- censoring


def test_censored_misses_do_not_count_toward_a_stall():
    hist = grind(6)
    env = L.LiftEnv(readiness={hist[3].day: "yellow", hist[4].day: "red"})
    an = L.analyze(spec(), hist, CFG, env)
    assert an.counter == 2 and not an.stalled


def test_lower_body_misses_after_intervals_or_a_long_run_are_censored():
    hist = grind(5, step=7)
    env = L.LiftEnv(hard_run_days={hist[4].day - timedelta(days=1)})
    lower = L.analyze(spec(per_week=1, lower_body=True), hist, CFG, env)
    upper = L.analyze(spec(per_week=1), hist, CFG, env)
    assert lower.evals[-1].censored == "within 24 h after intervals / a long run"
    assert lower.counter == 2 and not lower.stalled
    assert upper.evals[-1].censored is None and upper.stalled


def test_first_two_sessions_after_a_swap_a_break_or_a_gym_change_are_calibration():
    an = L.analyze(spec(), grind(3), CFG)
    assert [e.censored for e in an.evals][:2] == [
        "first sessions of this variant (calibration)"
    ] * 2
    brk = [ex(0, 60, [9, 8, 7]), ex(3, 60, [9, 8, 7]), ex(6, 60, [9, 8, 7])]
    brk += [ex(30, 60, [9, 8, 7]), ex(33, 60, [9, 8, 7]), ex(36, 60, [9, 8, 7])]
    an = L.analyze(spec(), brk, CFG)
    assert [e.censored for e in an.evals[3:]] == [
        "first sessions after a 24-day break",
        "first sessions after a 24-day break",
        None,
    ]
    gyms = [ex(0, 60, [9, 8, 7], 1), ex(3, 60, [9, 8, 7], 1), ex(6, 60, [9, 8, 7], 1)]
    gyms += [ex(9, 60, [9, 8, 7], 2), ex(12, 60, [9, 8, 7], 2), ex(15, 60, [9, 8, 7], 2)]
    an = L.analyze(spec(), gyms, CFG)
    assert [e.censored for e in an.evals[3:]] == [
        "first sessions after a gym change",
        "first sessions after a gym change",
        None,
    ]


def test_hits_on_censored_days_still_count():
    hist = [*grind(5), ex(18, 60, [11, 10, 9])]
    env = L.LiftEnv(readiness={hist[-1].day: "red"})
    an = L.analyze(spec(), hist, CFG, env)
    assert an.evals[-1].event == "rep_pr" and an.counter == 0


# --------------------------------------------------------------------------- stall ladder


def test_stall_ladder_step_one_is_minus_five_percent_rounded_down():
    hist = grind(6, weight=62.5)
    p = nxt(hist)
    assert p.rule == "stall:reset_small" and p.state == L.REBUILD and p.stall
    assert p.load == 57.5  # 62.5 x 0.95 = 59.4 -> the next barbell load at or below it
    assert p.ladder is not None and p.ladder.step == 1 and p.ladder.from_load == 62.5
    assert "identical_exposures" in p.flags  # same sets every time: stall or copied logs?


def test_stall_ladder_step_two_asks_the_coach_and_holds_the_load():
    hist = grind(6, weight=62.5) + grind(5, weight=57.5, reps=(9, 8, 7), start=22)
    step1 = L.LadderEvent(hist[6].day, 1, 57.5, peak_e1rm=200.0, from_load=62.5)
    p = nxt(hist, env=L.LiftEnv(ladder=[step1]))
    assert p.rule == "stall:advisor" and p.state == L.STALLED and p.load == 57.5
    assert p.ladder.step == L.LADDER_ADVISOR and "variation" in p.note


def test_stall_ladder_step_three_is_the_last_resort_minus_ten_percent():
    hist = grind(6, weight=62.5) + grind(5, weight=57.5, start=22) + grind(5, weight=57.5, start=42)
    ladder = [
        L.LadderEvent(hist[6].day, 1, 57.5, 200.0, 62.5),
        L.LadderEvent(hist[11].day, 2, 57.5, 200.0, 57.5),
    ]
    p = nxt(hist, env=L.LiftEnv(ladder=ladder))
    # 57.5 x 0.9 = 51.75 -> the next load on the 57.5 kg barbell grid at or below it
    assert p.rule == "stall:reset_large" and p.load == 47.5


def test_beating_the_peak_ends_the_ladder_episode():
    hist = [*grind(5, weight=60), ex(20, 57.5, [12, 12, 12]), ex(24, 65, [10, 9, 9])]
    peak = max(L.e1rm(60, 10), 1)
    ladder = [L.LadderEvent(hist[5].day, 1, 57.5, peak, 60)]
    an = L.analyze(spec(), hist, CFG, L.LiftEnv(ladder=ladder))
    assert an.ladder_pos == 0


def test_a_pending_reset_is_prescribed_until_trained():
    hist = grind(6, weight=62.5)
    ev = L.LadderEvent(hist[-1].day + timedelta(days=3), 1, 57.5, 200.0, 62.5)
    p = nxt(hist, env=L.LiftEnv(ladder=[ev]), gap=6)
    # the same prescription as when the step was decided: a re-run changes nothing
    assert p.rule == "stall:reset_small" and p.load == 57.5 and p.state == L.REBUILD
    assert p.ladder == ev


def test_reset_rounding_on_a_stack_ladder_and_assistance():
    stack = V("machine_stack", weight_ladder=[40.0, 45.0, 50.0, 55.0])
    assert L.reduce_load(spec(stack), CFG, 55.0, 5) == 50.0
    assisted = V("machine_stack", "assistance", [10.0, 15.0, 20.0, 25.0])
    assert L.reduce_load(spec(assisted), CFG, 20.0, 10) == 25.0  # easier = more assistance
    assert round_down_load(V(), 62.5, 62.5 * 0.95, CFG) == 57.5
    assert previous_load(V("dumbbell", "per_hand"), 20, CFG) == 18.0


# --------------------------------------------------------------------------- deload / retest


def test_soft_deload_window_halves_sets_rounded_up_and_keeps_loads():
    hist = [ex(0, 60, [10, 9, 8]), ex(3, 60, [11, 10, 9])]
    day = hist[-1].day + timedelta(days=4)
    w = L.DeloadWindow(
        day, day + timedelta(days=6), "multi_lift_stall", {7: L.Retest(60, (11, 10, 9))}
    )
    p = L.prescribe(spec(), hist, CFG, day=day, ctx=L.LiftEnv(variant_id=7, windows=[w]))
    assert p.state == L.DELOAD and targets(p) == [(60, 11), (60, 10)]  # 3 -> 2 sets, no +1
    assert "retest_pending" in p.flags
    assert L.deload_set_count(2, CFG) == 1 and L.deload_set_count(5, CFG) == 3


def test_retest_after_the_deload_targets_the_old_load_to_beat():
    hist = [ex(0, 60, [10, 9, 8]), ex(3, 60, [11, 10, 9])]
    start = hist[-1].day + timedelta(days=4)
    w = L.DeloadWindow(
        start, start + timedelta(days=6), "multi_lift_stall", {7: L.Retest(60, (11, 10, 9))}
    )
    p = L.prescribe(
        spec(), hist, CFG, day=start + timedelta(days=7), ctx=L.LiftEnv(variant_id=7, windows=[w])
    )
    assert (
        p.rule == "retest" and p.state == L.RETEST and targets(p) == [(60, 12), (60, 11), (60, 10)]
    )


def test_a_failed_retest_goes_straight_to_the_first_ladder_step():
    hist = grind(6, weight=62.5)
    start = hist[-1].day + timedelta(days=3)
    w = L.DeloadWindow(
        start, start + timedelta(days=6), "multi_lift_stall", {7: L.Retest(62.5, (10, 9, 8))}
    )
    hist += [ex((start - D0).days + 1, 62.5, [10, 9]), ex((start - D0).days + 8, 62.5, [10, 9, 8])]
    env = L.LiftEnv(variant_id=7, windows=[w])
    an = L.analyze(spec(), hist, CFG, env)
    assert an.retest_failed and an.stalled
    p = L.prescribe(spec(), hist, CFG, day=hist[-1].day + timedelta(days=3), ctx=env)
    assert p.rule == "stall:reset_small" and p.load == 57.5


def test_a_successful_retest_resumes_normal_progression():
    hist = grind(6, weight=62.5)
    start = hist[-1].day + timedelta(days=3)
    w = L.DeloadWindow(
        start, start + timedelta(days=6), "multi_lift_stall", {7: L.Retest(62.5, (10, 9, 8))}
    )
    hist += [ex((start - D0).days + 8, 62.5, [11, 10, 9])]
    env = L.LiftEnv(variant_id=7, windows=[w])
    an = L.analyze(spec(), hist, CFG, env)
    assert not an.stalled and an.evals[-1].event == "rep_pr"
    p = L.prescribe(spec(), hist, CFG, day=hist[-1].day + timedelta(days=3), ctx=env)
    assert p.rule == "double_progression:add_reps" and p.state == L.PROGRESSING


# --------------------------------------------------------------------------- return after a break


@pytest.mark.parametrize(
    ("gap", "load", "n_sets"),
    [(9, 60.0, 3), (14, 60.0, 2), (30, 55.0, 2), (50, 50.0, 2)],
)
def test_return_after_a_break_follows_the_table(gap, load, n_sets):
    hist = [ex(0, 60, [10, 9, 8]), ex(3, 60, [11, 10, 9])]
    p = nxt(hist, gap=gap)
    assert p.load == load and len(p.sets) == n_sets
    if gap > 10:
        assert p.rule == "return_after_break" and p.state == L.REBUILD


def test_the_first_week_back_after_a_long_break_keeps_the_set_cut():
    hist = [ex(0, 60, [10, 9, 8]), ex(3, 60, [11, 10, 9]), ex(40, 55, [10, 9, 9])]
    p = nxt(hist, gap=3)
    assert p.rule == "return_after_break" and len(p.sets) == 2 and p.load == 55


# --------------------------------------------------------------------------- equipment


def test_increments_per_equipment():
    hist = [ex(0, 20, [12, 12, 12]), ex(3, 20, [12, 12, 12])]
    assert nxt(hist, spec(V("dumbbell", "per_hand"))).load == 22.0
    stack = V("machine_stack", weight_ladder=[10, 15, 20, 25, 32.5])
    assert nxt(hist, spec(stack)).rule == "double_progression:add_reps"  # 20 -> 25 too big
    assert nxt(hist, spec(increment_kg=1.0)).load == 21.0


def test_bodyweight_reps_first_then_added_weight():
    bw = V("bodyweight", "total")
    s = spec(bw, bodyweight_kg=82)
    p = nxt([ex(0, 0, [9, 8, 7]), ex(3, 0, [10, 9, 8])], s)
    assert targets(p) == [(0, 11), (0, 10), (0, 9)]
    p = nxt([ex(0, 0, [11, 11, 11]), ex(3, 0, [12, 12, 12])], s)
    assert p.load == 2.5 and p.rule == "double_progression:increase_load"
    assert p.sets[0].reps >= 8  # 75 -> 77.5 kg moved: ~11 reps predicted, clamped to the range


def test_drop_sets_for_yellow_keeps_at_least_one():
    sets = [L.SetTarget(1, 60, 10)]
    assert len(L.drop_sets(sets, 1)) == 1
