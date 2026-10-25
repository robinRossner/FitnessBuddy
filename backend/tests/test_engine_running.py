"""Easy-run evaluation (+ the day-after-lower exemption), decoupling, interval evaluation by pace,
the 4-week-mean volume cap, the single-run cap and the long run (step-back, drift gate)."""

from datetime import date, timedelta

import pytest

from app.engine import running as R
from app.engine.config import load_engine_config
from app.engine.hr import zones_from_config
from app.settings import BACKEND_DIR

CFG = load_engine_config(BACKEND_DIR / "config" / "engine.v3.yaml")
RC = CFG.running_cfg
RC6 = load_engine_config(BACKEND_DIR / "config" / "engine.v6.yaml").running_cfg
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


def ev_of(laps, **kw):
    kw.setdefault("target_pace", 250.0)
    kw.setdefault("pace_tol_pct", RC.interval_pace_tolerance_pct)
    return R.evaluate_intervals(R.split_intervals(laps), ZONES, RC.intervals, **kw)


def test_interval_pass_by_pace_reports_hr_only_as_context():
    ev = ev_of(session(), hr_ceiling=189, green_hr=179)
    assert ev.passed and ev.reps == 5 and ev.reps_on_pace == 5 and ev.pace_ok_share == 1.0
    assert ev.recovery_drop_bpm == pytest.approx(16) and ev.recovery_ok
    assert ev.work_pace_s_per_km == pytest.approx(250) and ev.verdict == "green"


def test_faster_than_target_is_fine():
    ev = ev_of(session(work_pace=235))
    assert ev.passed and ev.reps_on_pace == 5


def test_interval_fail_by_pace_share():
    laps = [lap(311, 142, intensity="warmup")]
    for p in (250, 252, 262, 263, 264):  # 2 of 5 within 3 % of 250 (257.5)
        laps += [lap(p, 166, 1000, "active"), lap(370, 150, 400, "recovery")]
    ev = ev_of(laps)
    assert not ev.passed and ev.verdict == "fail" and ev.reps_on_pace == 2
    assert any("2 of 5 reps on pace" in r for r in ev.reasons)


def test_interval_fail_when_the_last_rep_fades_more_than_three_percent():
    laps = [lap(311, 142, intensity="warmup")]
    for p in (240, 242, 245, 248, 250):  # all on pace (<= 257.5) but +4.2 % fade
        laps += [lap(p, 166, 1000, "active"), lap(370, 150, 400, "recovery")]
    ev = ev_of(laps)
    assert ev.reps_on_pace == 5 and not ev.passed and ev.pace_fade_pct > 3


def test_hr_above_the_zone_or_a_poor_recovery_no_longer_fail_a_session():
    hot = ev_of(session(work_hr=182, rest_hr=165))
    assert hot.hr_too_high and hot.passed
    slow = ev_of(session(work_hr=170, rest_hr=165))
    assert slow.recovery_ok is False and slow.passed
    assert any("trend" in n for n in slow.notes)


def test_hr_safety_ceiling_makes_it_passed_hard():
    ev = ev_of(session(work_hr=186), hr_ceiling=189, green_hr=179)  # last rep max HR 192
    assert ev.passed and ev.hr_safety_exceeded and ev.verdict == "pass_hard"


def test_missing_prescribed_reps_fail():
    ev = ev_of(session(n=4), prescribed_reps=5)
    assert not ev.passed and any("4 of 5" in x for x in ev.reasons)


def test_without_target_or_hr_the_engine_degrades_gracefully():
    ev = R.evaluate_intervals(R.split_intervals(session()), ZONES, RC.intervals)
    assert ev.passed and ev.verdict == "no_target" and ev.reps_on_pace is None
    no_hr = [R.LapData(x.duration_s, x.distance_m, None, None, x.intensity) for x in session()]
    ev = ev_of(no_hr, hr_ceiling=189)
    assert ev.passed and ev.hr_safety_exceeded is None and not ev.green
    assert any("pace only" in n for n in ev.notes)


def test_lap_on_pace_helper():
    assert R.lap_on_pace(257, 250, 3) and not R.lap_on_pace(258, 250, 3)
    assert R.lap_on_pace(None, 250, 3) is None


# --------------------------------------------------------------------------- easy run exemption


def test_easy_run_the_day_after_lower_is_exempt_from_a_hard_hr_fail():
    laps = [lap(330, 160), lap(325, 161), lap(322, 162), lap(320, 163)]
    r = run(laps)
    assert not R.evaluate_easy_run(r, ZONES, RC.easy).passed
    ev = R.evaluate_easy_run(r, ZONES, RC.easy, after_lower=True)
    assert ev.passed and ev.exempt and not ev.reasons
    assert ev.notes and "day after lower body" in ev.notes[0]


# --------------------------------------------------------------------------- volume / long run


def week(w: date, km: float) -> R.RunData:
    return R.RunData("x", w, km * 1000, km * 330)


def test_weekly_volume_cap_from_the_4_week_actual_mean():
    mon = date(2026, 10, 26)
    runs = [
        week(mon - timedelta(days=7), 20),
        week(mon - timedelta(days=14), 24),
        week(mon - timedelta(days=21), 16),
        week(mon - timedelta(days=28), 20),
    ]
    assert R.volume_cap_km(R.weekly_km(runs), mon, 10) == pytest.approx(22.0)  # mean 20 x 1.1
    runs = [week(mon - timedelta(days=7), 8), week(mon - timedelta(days=21), 4)]  # 2 empty weeks
    assert R.mean_km(R.weekly_km(runs), mon) == 3.0
    assert R.volume_cap_km(R.weekly_km(runs), mon, 10) == pytest.approx(5.0)  # mean + 2 km
    assert R.volume_cap_km({}, mon, 10) is None


def test_single_run_cap_is_1_1_x_the_longest_of_30_days():
    today = date(2026, 10, 25)
    runs = [week(today - timedelta(days=40), 20), week(today - timedelta(days=10), 10.3)]
    assert R.single_run_cap_km(runs, today, 1.1) == pytest.approx(11.3)
    assert R.single_run_cap_km(runs, today, 1.1, abs_km=1.5) == pytest.approx(11.8)
    assert R.single_run_cap_km([], today, 1.1) is None


def test_fit_to_cap_trims_the_easy_run_first_then_the_long_run():
    out, notes = R.fit_to_cap({"easy_run": 7, "intervals": 6, "long_run": 12}, 22, 3, 3)
    assert out == {"easy_run": 4.0, "intervals": 6, "long_run": 12} and "easy_run" in notes[0]
    out, _ = R.fit_to_cap({"easy_run": 7, "intervals": 5, "long_run": 11}, 11, 3, 3)
    assert out == {"easy_run": 3, "intervals": 5, "long_run": 3}
    assert R.fit_to_cap({"easy_run": 5}, None, 3)[0] == {"easy_run": 5}


def test_long_run_progression_step_back_drift_gate_and_caps():
    lc = RC6.long_run
    assert R.next_long_run_km(None, lc).km == lc.start_km
    assert R.next_long_run_km(10.0, lc).km == 11.0
    back = R.next_long_run_km(16.0, lc, step_back=True)
    assert back.km == 12.0 and back.step_back  # -25 %
    hold = R.next_long_run_km(14.0, lc, decoupling=6.0, duration_min=80)
    assert hold.km == 14.0 and hold.drift == "hold"
    cut = R.next_long_run_km(14.0, lc, decoupling=9.0, duration_min=80)
    assert cut.km == 12.5 and cut.drift == "cut"  # -10 %, rounded down to 0.5 km
    short = R.next_long_run_km(10.0, lc, decoupling=9.0, duration_min=50)
    assert short.km == 11.0 and short.drift is None  # drift ignored below 60 min
    assert R.next_long_run_km(20.0, lc).km == 20.0  # cap 20 km
    capped = R.next_long_run_km(10.3, lc, single_cap=10.8)
    assert capped.km == 10.5 and "single-run cap" in capped.reason


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


# --------------------------------------------------------------------------- volume hold


def _hold_world(prior=(14, 8, 6, 0), last=20, hr=140):
    """Weeks before `mon`: `last` km (an 8 km easy run + the rest as a long run), then `prior`."""
    mon = date(2026, 10, 26)
    runs = [
        run([lap(330, hr)] * 8, day=mon - timedelta(days=7) + timedelta(days=2)),
        run([lap(330, hr)] * int(last - 8), day=mon - timedelta(days=7) + timedelta(days=6)),
    ]
    runs += [week(mon - timedelta(days=7 * (i + 2)), km) for i, km in enumerate(prior) if km]
    return mon, runs, R.weekly_km(runs)


def test_hold_applies_the_last_weeks_km_over_the_mean_cap():
    mon, runs, weekly = _hold_world()
    h = R.hold_volume(weekly, runs, mon, limit_factor=1.1, flagged=lambda r: False)
    assert h.last_week_km == 20 and h.limit_km == pytest.approx(15.4) and h.blocked is None
    assert h.km == pytest.approx(15.4)  # limited to 1.1 x the highest of the 4 weeks before
    assert R.mean_km(weekly, mon) == 12.0
    assert R.volume_cap_km(weekly, mon, 10) == pytest.approx(14.0)  # without the hold
    assert R.volume_cap_km(weekly, mon, 10, hold_km=h.km) == pytest.approx(15.4)
    assert R.volume_cap_km(weekly, mon, 10, hold_km=9) == pytest.approx(14.0)  # never lowers


def test_hold_is_the_whole_last_week_when_under_the_limit():
    mon, runs, weekly = _hold_world(prior=(18, 16, 14, 12), last=20)
    h = R.hold_volume(weekly, runs, mon, limit_factor=1.1, flagged=lambda r: False)
    assert h.km == 19.8 and h.limit_km == pytest.approx(19.8)  # 1.1 x 18 < 20: limited
    mon, runs, weekly = _hold_world(prior=(20, 16, 14, 12), last=18)
    h = R.hold_volume(weekly, runs, mon, limit_factor=1.1, flagged=lambda r: False)
    assert h.km == 18 and h.limit_km == pytest.approx(22.0)  # last week is below the limit


def test_no_hold_after_a_flagged_week_or_without_runs():
    mon, runs, weekly = _hold_world()
    h = R.hold_volume(weekly, runs, mon, limit_factor=1.1, flagged=lambda r: r.km >= 12)
    assert h.km is None and "HR fail or HR drift" in h.blocked
    assert R.volume_cap_km(weekly, mon, 10, hold_km=h.km) == pytest.approx(14.0)
    assert R.hold_volume({}, [], mon, limit_factor=1.1, flagged=lambda r: False) is None
    # intervals are not judged by the easy-run rule
    iv = run(
        [lap(311, 142, 1000, "warmup")]
        + [lap(250, 170, 1000, "active"), lap(370, 150, 400, "recovery")] * 4
        + [lap(305, 150, 1000, "cooldown")],
        day=mon - timedelta(days=3),
    )
    h = R.hold_volume(R.weekly_km([iv]), [iv], mon, limit_factor=1.1, flagged=lambda r: True)
    assert h.blocked is None
