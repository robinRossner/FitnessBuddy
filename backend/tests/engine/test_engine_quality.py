"""The Friday quality cycle (phases, lever order) and VDOT hysteresis (fitness/engine/quality.py,
apps/fitness/engine/vdot.py; docs/research/running-progression.md sections 2-3)."""

from datetime import date, timedelta

import pytest

from apps.fitness.engine.core import quality as Q
from apps.fitness.engine.core import vdot as V
from apps.fitness.engine.core.config import load_engine_config
from apps.fitness.settings import FITNESS_DIR

CFG = load_engine_config(FITNESS_DIR / "config" / "engine.v6.yaml")
RC = CFG.running_cfg
START = date(2026, 1, 5)  # a Monday


def weeks(n: int, start: date = START) -> list[date]:
    return [start + timedelta(weeks=i) for i in range(n)]


# --------------------------------------------------------------------------- phases


def test_cycle_starts_with_twelve_threshold_weeks():
    ps = Q.phase_state(RC.quality, START, START, [])
    assert (ps.phase, ps.phase_week, ps.phase_weeks, ps.session_type) == (
        "threshold",
        1,
        12,
        "threshold",
    )
    ps = Q.phase_state(RC.quality, START, START + timedelta(weeks=11), weeks(11))
    assert (ps.phase, ps.phase_week) == ("threshold", 12)


def test_after_threshold_the_sessions_alternate_threshold_and_vo2max():
    ps = Q.phase_state(RC.quality, START, START + timedelta(weeks=12), weeks(12))
    assert (ps.phase, ps.phase_week, ps.session_type) == ("threshold_vo2max", 1, "threshold")
    ps = Q.phase_state(RC.quality, START, START + timedelta(weeks=13), weeks(13))
    assert (ps.phase_week, ps.session_type) == (2, "vo2max")


def test_5k_specific_then_time_trial_then_the_cycle_repeats():
    ps = Q.phase_state(RC.quality, START, START + timedelta(weeks=20), weeks(20))
    assert (ps.phase, ps.session_type) == ("5k_specific", "race_5k")
    ps = Q.phase_state(RC.quality, START, START + timedelta(weeks=26), weeks(26))
    assert (ps.phase, ps.session_type) == ("time_trial", "time_trial")
    ps = Q.phase_state(RC.quality, START, START + timedelta(weeks=27), weeks(27))
    assert (ps.cycle, ps.phase, ps.phase_week) == (2, "threshold", 1)


def test_a_missed_week_does_not_advance_the_phase():
    done = weeks(5)
    done.remove(START + timedelta(weeks=3))  # Friday of week 4 was missed
    ps = Q.phase_state(RC.quality, START, START + timedelta(weeks=5), done)
    assert ps.phase_week == 5  # not 6


def test_phase_weeks_can_be_tuned_per_phase():
    qc = RC.quality.model_copy(update={"phase_weeks": {"threshold": 4}})
    ps = Q.phase_state(qc, START, START + timedelta(weeks=4), weeks(4))
    assert ps.phase == "threshold_vo2max"


# --------------------------------------------------------------------------- lever order


T = Q.type_spec(RC, "threshold")
VO2 = Q.type_spec(RC, "vo2max")


def rec(t: str, reps: int, m: float, rec_s=None) -> Q.QualityRecord:
    return Q.QualityRecord(date(2026, 10, 23), t, reps, m, rec_s, 300.0)


def test_first_session_of_a_type_starts_at_the_bottom():
    p = Q.next_quality(T, None, None, pace=300)
    assert (p.reps, p.rep_m, p.recovery_s, p.rule) == (3, 1000, 90, "quality:start")


def test_lever_one_is_reps():
    p = Q.next_quality(T, rec("threshold", 3, 1000, 90), "pass", pace=300)
    assert (p.reps, p.rep_m, p.lever) == (4, 1000, "reps")
    assert p.note == "+1 rep: last session passed on pace"


def test_lever_two_is_rep_length_when_reps_are_at_max():
    p = Q.next_quality(T, rec("threshold", 5, 1000, 90), "green", pace=300)
    assert (p.lever, p.rep_m) == ("rep_length", 1200)
    assert p.reps == 4  # about the same work, within the 30 hard minutes


def test_lever_three_is_a_shorter_recovery_for_threshold_only():
    p = Q.next_quality(T, rec("threshold", 3, 2000, 90), "pass", pace=300)
    assert (p.lever, p.recovery_s, p.reps, p.rep_m) == ("recovery", 75, 3, 2000)
    v = Q.next_quality(VO2, rec("vo2max", 5, 1200), "pass", pace=280)  # 28 min > 20 cap
    assert v.lever is None and v.rule == "quality:hold_max"


def test_lever_four_is_pace_via_a_vdot_step_only_with_evidence():
    last = rec("threshold", 3, 2000, 60)
    hold = Q.next_quality(T, last, "pass", pace=300, vdot_step_ok=False)
    assert hold.rule == "quality:hold_max" and not hold.vdot_step
    step = Q.next_quality(T, last, "green", pace=300, vdot_step_ok=True)
    assert step.lever == "pace" and step.vdot_step and step.reps == T.reps_min


def test_fail_repeats_and_two_fails_step_one_rep_back():
    last = rec("threshold", 4, 1000, 90)
    assert Q.next_quality(T, last, "fail", pace=300).rule == "quality:repeat"
    back = Q.next_quality(T, last, "fail", pace=300, fails_same=2)
    assert back.rule == "quality:step_back" and back.reps == 3


def test_passed_hard_and_missing_laps_hold():
    last = rec("threshold", 4, 1000, 90)
    assert Q.next_quality(T, last, "pass_hard", pace=300).rule == "quality:hold"
    assert Q.next_quality(T, last, None, pace=300).rule == "quality:repeat"


def test_time_trial_session():
    p = Q.next_quality(Q.type_spec(RC, "time_trial"), None, None, pace=270)
    assert p.rule == "quality:time_trial" and p.reps == 1 and p.rep_m == 5000


def test_target_paces_follow_vdot():
    t, i, k5 = (Q.target_pace(42, x) for x in ("threshold", "vo2max", "race_5k"))
    assert 290 < t < 310 and 265 < i < 285 and i < k5 < t  # T ~5:00, I ~4:35


# --------------------------------------------------------------------------- VDOT


VC = RC.vdot
TODAY = date(2026, 10, 26)


def rec_v(day: date, v: float, src="initial", delta=0.0) -> V.VdotRecord:
    return V.VdotRecord(day, v, src, delta)


def test_first_vdot_record_anchors_the_fit():
    u = V.update_vdot([], 43.44, VC, today=TODAY)
    assert u.record.source == "initial" and u.vdot == 43.44


def test_vdot_step_is_capped_at_half_a_point_per_update():
    log = [rec_v(TODAY - timedelta(days=60), 42.0)]
    u = V.update_vdot(log, 42.0, VC, today=TODAY, efforts=[(TODAY - timedelta(days=2), 44.0)])
    assert u.vdot == 42.5 and u.record.source == "effort" and u.record.delta == pytest.approx(0.5)


def test_vdot_rises_at_most_one_point_per_four_weeks():
    log = [
        rec_v(TODAY - timedelta(days=60), 42.0),
        rec_v(TODAY - timedelta(days=20), 42.5, "effort", 0.5),
        rec_v(TODAY - timedelta(days=10), 43.0, "effort", 0.5),
    ]
    u = V.update_vdot(log, 42.0, VC, today=TODAY, efforts=[(TODAY - timedelta(days=1), 45.0)])
    assert u.record is None and u.vdot == 43.0 and "used up" in u.reason


def test_green_sessions_plus_efficiency_trend_raise_vdot():
    log = [rec_v(TODAY - timedelta(days=60), 42.0)]
    greens = [TODAY - timedelta(days=10), TODAY - timedelta(days=3)]
    flat = V.update_vdot(log, 42, VC, today=TODAY, greens=greens, ef_trend_pct=0.2)
    assert flat.record is None
    up = V.update_vdot(log, 42, VC, today=TODAY, greens=greens, ef_trend_pct=2.0)
    assert up.vdot == 42.5 and up.record.source == "green_sessions"
    lever = V.update_vdot(log, 42, VC, today=TODAY, greens=greens, pace_lever=True)
    assert lever.vdot == 42.5 and lever.record.source == "interval_pace"


def test_one_bad_session_never_lowers_vdot_but_three_fails_do():
    log = [rec_v(TODAY - timedelta(days=60), 42.0)]
    one = V.update_vdot(log, 42, VC, today=TODAY, fails=[TODAY - timedelta(days=3)])
    assert one.vdot == 42.0 and one.record is None
    fails = [TODAY - timedelta(days=d) for d in (3, 10, 17)]
    three = V.update_vdot(log, 42, VC, today=TODAY, fails=fails)
    assert three.vdot == 41.5 and three.record.source == "fails"


def test_layoff_table_lowers_vdot_once_per_break():
    log = [rec_v(TODAY - timedelta(days=90), 42.0)]
    last_run = TODAY - timedelta(days=35)
    u = V.update_vdot(log, 42, VC, today=TODAY, last_run_day=last_run)
    assert u.vdot == pytest.approx(39.9) and u.record.source == "layoff"  # -5 %
    log.append(u.record)
    again = V.update_vdot(log, 42, VC, today=TODAY + timedelta(days=7), last_run_day=last_run)
    assert again.record is None
    assert (
        V.update_vdot(log[:1], 42, VC, today=TODAY, last_run_day=TODAY - timedelta(days=7)).record
        is None
    )


def test_one_vdot_update_per_week():
    log = [rec_v(TODAY - timedelta(days=60), 42.0), rec_v(TODAY, 42.5, "effort", 0.5)]
    u = V.update_vdot(log, 42, VC, today=TODAY + timedelta(days=2), efforts=[(TODAY, 44.0)])
    assert u.record is None and u.vdot == 42.5
