"""Easy-run evaluation, decoupling, interval evaluation + progression, volume cap, long run."""

from datetime import date, timedelta

import pytest

from app.engine import running as R
from app.engine.config import load_engine_config
from app.engine.hr import zones_from_config
from app.engine.pace import VdotPaceModel
from app.settings import BACKEND_DIR

CFG = load_engine_config(BACKEND_DIR / "config" / "engine.v3.yaml")
RC = CFG.running_cfg
ZONES = zones_from_config(CFG)  # easy <= 156, intervals 166-179
D = date(2026, 10, 20)
FLOORS = (98.0, 117.0, 137.0, 156.0, 176.0)  # Garmin zone floors


def lap(pace: float, hr: float, dist: float = 1000, intensity: str | None = None, max_hr=None):
    return R.LapData(pace * dist / 1000, dist, hr, max_hr or hr + 6, intensity)


def run(laps, *, zones_s=None, avg_hr=None, day=D, title="Run") -> R.RunData:
    dist = sum(x.distance_m for x in laps)
    dur = sum(x.duration_s for x in laps)
    hr = avg_hr or sum(x.avg_hr * x.duration_s for x in laps) / dur
    return R.RunData(
        "r", day, dist, dur, hr, None, tuple(laps), zones_s, FLOORS if zones_s else None, title
    )


# --------------------------------------------------------------------------- easy runs


def test_easy_run_pass():
    laps = [lap(330, 148), lap(325, 150), lap(322, 152), lap(320, 153)]
    r = run(laps, zones_s={"z1": 10, "z2": 60, "z3": 1100, "z4": 80, "z5": 0})
    ev = R.evaluate_easy_run(r, ZONES, RC.easy)
    assert ev.passed and ev.pct_source == "garmin_zones"
    assert ev.pct_at_or_below_cap == pytest.approx(100 * 1170 / 1250, abs=0.5)
    assert ev.efficiency and not ev.reasons


def test_easy_run_fails_on_avg_hr_above_ceiling():
    r = run([lap(300, 160)] * 4, zones_s={"z1": 0, "z2": 0, "z3": 100, "z4": 1100, "z5": 0})
    ev = R.evaluate_easy_run(r, ZONES, RC.easy)
    assert not ev.passed and any("above the easy ceiling 156" in x for x in ev.reasons)


def test_easy_run_fails_on_time_share_even_with_low_average():
    # avg 154 (<= 156) but a third of the time above the cap
    r = run(
        [lap(310, 154)] * 4, avg_hr=154, zones_s={"z1": 0, "z2": 0, "z3": 800, "z4": 450, "z5": 0}
    )
    ev = R.evaluate_easy_run(r, ZONES, RC.easy)
    assert not ev.passed and ev.pct_at_or_below_cap == pytest.approx(65.8, abs=0.5)
    assert any("need 80%" in x for x in ev.reasons)


def test_fraction_split_uses_laps_when_no_zone_data():
    laps = [lap(330, 150), lap(330, 150), lap(330, 150), lap(300, 165)]
    frac, src = R.fraction_at_or_below(156, None, None, laps)
    assert src == "laps" and frac == pytest.approx(0.75, abs=0.02)
    assert R.fraction_at_or_below(156, None, None, []) == (None, "none")


def test_zone_straddling_the_cap_is_interpolated():
    # all time in Garmin z4 (156..175): cap 160 -> 5 of 20 bpm below
    frac, _ = R.fraction_at_or_below(160, {"z4": 100.0}, FLOORS)
    assert frac == pytest.approx(5 / 20)


def test_efficiency_is_speed_per_hr():
    assert R.efficiency(10000, 3000, 150) == pytest.approx((10000 / 50) / 150, abs=1e-4)
    assert R.efficiency(10000, 3000, None) is None


# --------------------------------------------------------------------------- decoupling


def test_decoupling_positive_when_hr_drifts_up_at_same_pace():
    laps = [lap(330, 140)] * 4 + [lap(330, 152)] * 4
    d = R.decoupling_pct(laps)
    assert d is not None and d == pytest.approx((152 / 140 - 1) / (152 / 140) * 100, abs=0.2)
    assert R.evaluate_easy_run(run(laps), ZONES, RC.easy).decoupling_flag  # > 5 %


def test_decoupling_zero_for_steady_run_and_none_for_few_laps():
    assert R.decoupling_pct([lap(330, 145)] * 8) == pytest.approx(0, abs=0.01)
    assert R.decoupling_pct([lap(330, 145)] * 3) is None
    flag = R.evaluate_easy_run(run([lap(330, 145)] * 8), ZONES, RC.easy)
    assert not flag.decoupling_flag


def test_decoupling_just_below_threshold_not_flagged():
    laps = [lap(330, 145)] * 4 + [lap(330, 151)] * 4  # ~4 %
    assert 0 < R.decoupling_pct(laps) < 5
    assert not R.evaluate_easy_run(run(laps), ZONES, RC.easy).decoupling_flag


# --------------------------------------------------------------------------- intervals


def session(n=5, work_pace=250, work_hr=166, rest_hr=150, intensity=True):
    laps = [lap(311, 142, intensity="warmup" if intensity else None)]
    for _ in range(n):
        laps.append(lap(work_pace, work_hr, 1000, "active" if intensity else None))
        laps.append(lap(370, rest_hr, 400, "recovery" if intensity else None))
    laps[-1] = lap(305, 155, 1000, "cooldown" if intensity else None)
    return laps


def test_split_by_fit_intensity():
    sp = R.split_intervals(session())
    assert sp.method == "intensity" and len(sp.work) == 5
    assert [r is not None for r in sp.rest] == [
        True,
        True,
        True,
        True,
        False,
    ]  # cooldown follows #5


def test_split_by_pace_heuristic_when_intensity_missing():
    sp = R.split_intervals(session(intensity=False))
    assert sp.method == "pace" and len(sp.work) == 5


def test_all_interval_labels_without_recovery_laps_is_not_an_interval_session():
    laps = [lap(320, 150, intensity="interval") for _ in range(8)]  # an easy run's auto laps
    assert R.split_intervals(laps) is None
    assert not R.is_interval_run(run(laps))


def test_evaluation_passes_in_zone_with_good_recovery():
    ev = R.evaluate_intervals(R.split_intervals(session()), ZONES, RC.intervals)
    assert ev.passed and ev.reps == 5 and ev.hr_in_zone
    assert ev.recovery_drop_bpm == pytest.approx(16) and ev.recovery_ok
    assert ev.work_pace_s_per_km == pytest.approx(250)


def test_evaluation_fails_when_hr_above_zone():
    ev = R.evaluate_intervals(
        R.split_intervals(session(work_hr=182, rest_hr=165)), ZONES, RC.intervals
    )
    assert ev.hr_too_high and not ev.passed


def test_evaluation_fails_on_poor_recovery():
    ev = R.evaluate_intervals(
        R.split_intervals(session(work_hr=170, rest_hr=165)), ZONES, RC.intervals
    )
    assert ev.recovery_ok is False and not ev.passed
    assert any("recovery" in x for x in ev.reasons)


def test_missing_prescribed_reps_fail():
    ev = R.evaluate_intervals(
        R.split_intervals(session(n=4)), ZONES, RC.intervals, prescribed_reps=5
    )
    assert not ev.passed and any("4 of 5" in x for x in ev.reasons)


MODEL = VdotPaceModel(10000, 52 * 60 + 30)


def passed_eval(reps: int, pace=250.0):
    ev = R.evaluate_intervals(
        R.split_intervals(session(n=reps, work_pace=pace)), ZONES, RC.intervals
    )
    assert ev.passed
    return ev


def test_progression_adds_a_rep_at_same_pace():
    plan = R.next_interval_plan(passed_eval(5), MODEL, RC.intervals)
    assert (plan.reps, plan.pace_s_per_km) == (6, 250) and plan.rule == "intervals:add_rep"


def test_progression_at_max_reps_goes_faster_and_resets_reps():
    plan = R.next_interval_plan(passed_eval(6), MODEL, RC.intervals)
    assert plan.reps == RC.intervals.reps_min == 4
    assert plan.pace_s_per_km < 250 and plan.rule == "intervals:faster_pace"


def test_progression_repeats_after_a_failed_session():
    bad = R.evaluate_intervals(
        R.split_intervals(session(work_hr=182, rest_hr=165)), ZONES, RC.intervals
    )
    plan = R.next_interval_plan(bad, MODEL, RC.intervals)
    assert plan.reps == 5 and plan.rule == "intervals:repeat" and plan.pace_s_per_km == 250


def test_first_session_uses_model_pace_and_start_reps():
    plan = R.next_interval_plan(None, MODEL, RC.intervals)
    assert plan.reps == RC.intervals.reps_start and plan.rule == "intervals:start"
    assert 260 < plan.pace_s_per_km < 285  # ~4:30/km for VDOT 41


# --------------------------------------------------------------------------- volume / long run


def week(w: date, km: float) -> R.RunData:
    return R.RunData("x", w, km * 1000, km * 330)


def test_weekly_volume_cap_is_10_percent_over_max_of_last_week_and_average():
    mon = date(2026, 10, 26)
    runs = [
        week(mon - timedelta(days=7), 20),
        week(mon - timedelta(days=14), 24),
        week(mon - timedelta(days=21), 16),
        week(mon - timedelta(days=28), 20),
    ]
    cap = R.volume_cap_km(R.weekly_km(runs), mon, 10)
    assert cap == pytest.approx(22.0)  # max(20, avg 20) * 1.1
    runs[0] = week(mon - timedelta(days=7), 10)  # a light last week: average (17.5) is the base
    assert R.volume_cap_km(R.weekly_km(runs), mon, 10) == pytest.approx(19.25)
    assert R.volume_cap_km({}, mon, 10) is None


def test_fit_to_cap_trims_long_run_first_then_easy():
    out, notes = R.fit_to_cap({"easy_run": 7, "intervals": 9, "long_run": 14}, 26, long_min_km=8)
    assert sum(out.values()) <= 26 and out["intervals"] == 9 and out["long_run"] == 10
    assert notes and "long_run" in notes[0]
    out, _ = R.fit_to_cap({"easy_run": 7, "intervals": 9, "long_run": 14}, 20, long_min_km=8)
    assert out["long_run"] == 8 and out["easy_run"] < 7 and out["intervals"] == 9
    same, notes = R.fit_to_cap({"easy_run": 5}, 30, long_min_km=8)
    assert same == {"easy_run": 5} and not notes
    assert R.fit_to_cap({"easy_run": 5}, None, 8)[0] == {"easy_run": 5}


def test_long_run_progression():
    lc = RC.long_run
    assert R.next_long_run_km(None, True, lc)[0] == lc.start_km
    assert R.next_long_run_km(10.0, True, lc)[0] == 11.0
    assert R.next_long_run_km(10.0, False, lc)[0] == 10.0  # hold
    assert R.next_long_run_km(21.0, True, lc)[0] == lc.cap_km  # capped
    assert R.next_long_run_km(5.0, False, lc)[0] == lc.min_km


# --------------------------------------------------------------------------- stagnation / efforts


def test_stagnation_flags_flat_or_falling_metric():
    end = date(2026, 10, 25)
    flat = [(end - timedelta(days=d), 1.00) for d in (1, 5, 9, 13, 17, 22, 26, 30, 34, 40)]
    s = R.stagnation(flat, end, 4, 1.0)
    assert s and s["change_pct"] == pytest.approx(0)
    rising = [
        (end - timedelta(days=d), 1.0 + (40 - d) * 0.005)
        for d in (1, 5, 9, 13, 17, 22, 26, 30, 34, 40)
    ]
    assert R.stagnation(rising, end, 4, 1.0) is None
    assert R.stagnation(flat[:3], end, 4, 1.0) is None  # not enough points


def test_best_effort_picks_fastest_5k_stretch():
    laps = [lap(330, 140)] * 3 + [lap(290, 165)] * 6 + [lap(330, 145)] * 2
    o = R.best_effort(run(laps), 5000)
    assert (
        o.distance_m == 5000
        and o.duration_s == pytest.approx(290 * 5)
        and o.avg_hr == pytest.approx(165)
    )
    assert R.best_effort(run(laps[:3]), 5000) is None  # 3 km only
    no_laps = R.RunData("r", D, 8000, 2400, 150)
    assert R.best_effort(no_laps, 5000).distance_m == 8000
