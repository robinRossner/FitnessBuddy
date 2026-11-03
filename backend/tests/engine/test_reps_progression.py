"""Progression mode `reps` (decisions.md "Per-exercise progression mode"): bodyweight work like
the captain's chair knee raise moves by reps only. The load is never stepped (a `load` exercise on
the same history still gets its +2.5 kg), each set's target is its last reps + 1 up to the top of
the range, and at the top it holds with a note about a harder variation."""

from dataclasses import dataclass
from datetime import date, timedelta

from apps.fitness.engine.core import lifting as L
from apps.fitness.engine.core.config import load_engine_config
from apps.fitness.settings import FITNESS_DIR

CFG = load_engine_config(FITNESS_DIR / "config" / "engine.v6.yaml")
D0 = date(2026, 9, 1)
RR = (10, 20)


@dataclass
class BW:
    equipment_type: str = "bodyweight"
    load_convention: str = "total"
    weight_ladder: list[float] | None = None
    name: str = "Knee Raise (Captain's Chair)"


def ex(day: int, reps: list[int], weight: float | None = 0.0) -> L.Exposure:
    return L.Exposure(D0 + timedelta(days=day), tuple(L.SetResult(weight, r) for r in reps))


def spec(progression="reps", rr=RR, sets=2) -> L.LiftSpec:
    return L.LiftSpec(BW(), rr, sets, per_week=2, progression=progression, bodyweight_kg=80.0)


def nxt(hist, s=None, env=None, gap=3):
    return L.prescribe(s or spec(), hist, CFG, day=hist[-1].day + timedelta(days=gap), ctx=env)


def history(n=5, start=10):
    """Clean sessions each one rep better, every set at the previous target."""
    return [ex(i * 3, [start + i, start + i]) for i in range(n)]


def targets(p):
    return [(s.weight, s.reps) for s in p.sets]


def test_progression_defaults_to_load_and_time_is_the_timed_track():
    assert L.LiftSpec(BW(), RR, 2).progression == "load" and not L.LiftSpec(BW(), RR, 2).timed
    assert spec("time").timed and not spec("time").reps_only and spec("reps").reps_only


def test_no_history_asks_for_the_bottom_of_the_range_without_weight_step():
    p = L.prescribe(spec(), [], CFG, day=D0)
    assert p.rule == "no_history" and targets(p) == [(0.0, 10)] * 2


def test_clean_session_raises_the_target_by_one_rep():
    p = nxt([ex(0, [12, 12]), ex(3, [13, 13])])
    assert targets(p) == [(0.0, 14)] * 2 and p.rule == "reps_progression:add_reps"
    assert "no added weight" in p.note


def test_every_set_at_the_old_top_never_steps_the_weight():
    # 12 reps is the top of the global 7-12 range: load mode would now prescribe +2.5 kg
    hist = [ex(0, [12, 12]), ex(3, [12, 12])]
    assert targets(nxt(hist, spec("load", rr=(7, 12))))[0][0] == 2.5
    p = nxt(hist, spec("reps", rr=(7, 12)))
    assert {w for w, _ in targets(p)} == {0.0} and "increase_load" not in p.rule


def test_each_set_gets_its_own_target_and_a_miss_does_not_raise_it():
    warm = [ex(-9, [14, 14]), ex(-6, [14, 14]), ex(-3, [14, 14])]  # past the calibration sessions
    assert targets(nxt([*warm, ex(0, [15, 11]), ex(3, [16, 12])])) == [(0.0, 17), (0.0, 13)]
    assert targets(nxt([*warm, ex(0, [14, 14]), ex(3, [14, 13])])) == [(0.0, 15), (0.0, 14)]


def test_target_never_below_the_range_bottom():
    assert targets(nxt([ex(0, [6, 6]), ex(3, [7, 6])]))[0][1] == 10


def test_a_miss_on_a_censored_day_keeps_the_target():
    hist = history(4, start=12)  # 12, 13, 14, 15
    hist.append(ex(12, [13, 13]))  # worse than 15 on a red day
    env = L.LiftEnv(readiness={hist[-1].day: "red"})
    assert targets(nxt(hist, env=env))[0][1] == 16
    assert targets(nxt(hist))[0][1] == 14  # the same miss on a normal day lowers it


def test_at_the_top_of_the_range_it_holds_and_suggests_a_harder_variation():
    p = nxt([ex(0, [19, 19]), ex(3, [20, 20])])
    assert targets(p) == [(0.0, 20)] * 2
    assert p.rule == "reps_progression:at_cap" and "rep_cap" in p.flags
    assert "harder variation" in p.note and "no weight step" in p.note
    again = nxt([ex(0, [20, 20]), ex(3, [20, 20]), ex(6, [20, 21])])
    assert targets(again) == [(0.0, 20)] * 2  # still capped, still holding


def test_logged_load_is_kept_not_stepped():
    p = nxt([ex(0, [12, 12], 5.0), ex(3, [13, 13], 5.0)])
    assert targets(p) == [(5.0, 14)] * 2


def test_unlogged_weight_counts_as_bodyweight():
    assert targets(nxt([ex(0, [12, 12], None), ex(3, [13, 12], None)]))[0] == (0.0, 14)


def test_deload_window_halves_the_sets_and_adds_no_reps():
    hist = history(4, start=12)
    day = hist[-1].day + timedelta(days=3)
    w = L.DeloadWindow(day, day + timedelta(days=6), "red_streak")
    p = L.prescribe(spec(sets=4), hist, CFG, day=day, ctx=L.LiftEnv(windows=[w]))
    assert p.state == L.DELOAD and len(p.sets) == 2 and p.sets[0].reps <= 15


def test_reps_mode_is_never_flagged_as_a_stall():
    hist = [ex(i * 3, [20, 20]) for i in range(8)]  # nothing improves any more
    p = nxt(hist)
    assert not p.stall and p.state != L.STALLED and p.ladder is None


def test_load_mode_is_unchanged():
    hist = [ex(0, [11, 11], 0.0), ex(3, [12, 12], 0.0)]
    p = nxt(hist, spec("load", rr=(7, 12)))
    assert p.rule == "double_progression:increase_load" and p.sets[0].weight > 0
