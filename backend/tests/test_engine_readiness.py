"""Readiness scoring, missing data, anomaly, deload window."""

from datetime import date, timedelta

from app.engine.config import ReadinessConfig, load_engine_config
from app.engine.readiness import (
    GREEN,
    RED,
    YELLOW,
    HealthDay,
    deload_status,
    hrv_drop_anomaly,
    red_streak,
    score_day,
)
from app.settings import BACKEND_DIR

CFG = load_engine_config(BACKEND_DIR / "config" / "engine.v3.yaml").readiness_cfg
TODAY = date(2026, 10, 20)


def baseline(hrv=70.0, rhr=53.0, n=7) -> list[HealthDay]:
    return [
        HealthDay(
            TODAY - timedelta(days=i), resting_hr=rhr, hrv_ms=hrv, sleep_s=8 * 3600, sleep_score=85
        )
        for i in range(1, n + 1)
    ]


def today(**kw) -> HealthDay:
    base = {"resting_hr": 53.0, "hrv_ms": 70.0, "sleep_s": 8 * 3600, "sleep_score": 85}
    return HealthDay(TODAY, **{**base, **kw})


def score(**kw):
    return score_day(TODAY, [*baseline(), today(**kw)], CFG)


def test_all_normal_is_green():
    r = score()
    assert r.color == GREEN and r.known and "normal" in r.reason


def test_config_values_match_spec():
    assert (CFG.hrv.yellow_pct, CFG.hrv.red_pct) == (-10, -20)
    assert (CFG.rhr.yellow_bpm, CFG.rhr.red_bpm) == (5, 8)
    assert (CFG.sleep.yellow_h, CFG.sleep.red_h, CFG.sleep.yellow_score, CFG.sleep.red_score) == (
        6,
        5,
        60,
        45,
    )


def test_single_yellow_signal_stays_green_with_a_note():
    r = score(hrv_ms=61.0)  # -12.9 %
    assert r.color == GREEN and "1 minor flag" in r.reason
    assert [s.level for s in r.signals if s.name == "hrv"] == [YELLOW]


def test_two_yellow_signals_make_yellow():
    r = score(hrv_ms=61.0, resting_hr=59.0)
    assert r.color == YELLOW and "HRV" in r.reason and "resting HR" in r.reason


def test_hrv_red_threshold_is_red():
    r = score(hrv_ms=54.0)  # -22.9 %
    assert r.color == RED


def test_rhr_thresholds():
    assert score(resting_hr=58.0).signals[1].level == YELLOW  # +5
    assert score(resting_hr=61.0).color == RED  # +8


def test_sleep_thresholds_duration_and_score():
    assert score(sleep_s=5.5 * 3600).signals[-1].level == YELLOW
    assert score(sleep_s=8 * 3600, sleep_score=55).signals[-1].level == YELLOW
    r = score(sleep_s=4.5 * 3600)  # red sleep alone
    assert r.signals[-1].level == RED
    assert r.color == YELLOW and "short night" in r.reason  # needs corroboration for red
    r = score(sleep_s=4.5 * 3600, resting_hr=59.0)  # red sleep + a yellow signal
    assert r.color == RED
    assert score(sleep_score=40).signals[-1].level == RED


def test_sleep_corroboration_can_be_disabled():
    cfg = ReadinessConfig.model_validate(
        {**CFG.model_dump(), "sleep_red_needs_corroboration": False}
    )
    r = score_day(TODAY, [*baseline(), today(sleep_s=4 * 3600)], cfg)
    assert r.color == RED


def test_garmin_hrv_status_counts():
    r = score(hrv_status="POOR")
    assert r.color == RED
    r = score(hrv_status="UNBALANCED", resting_hr=59.0)
    assert r.color == YELLOW
    assert score(hrv_status="BALANCED").color == GREEN


def test_missing_signals_use_what_is_there():
    only_sleep = score_day(TODAY, [HealthDay(TODAY, sleep_s=5.5 * 3600, sleep_score=50)], CFG)
    assert only_sleep.known and only_sleep.color == GREEN  # one yellow signal only
    just_rhr = score_day(TODAY, [*baseline(), HealthDay(TODAY, resting_hr=62.0)], CFG)
    assert just_rhr.color == RED and len(just_rhr.signals) == 1


def test_no_data_is_unknown_green():
    r = score_day(TODAY, [], CFG)
    assert (r.color, r.known, r.reason) == (GREEN, False, "no data")
    # a row for another day does not count
    r = score_day(TODAY, [HealthDay(TODAY - timedelta(days=1), hrv_ms=50)], CFG)
    assert not r.known
    r = score_day(TODAY, [HealthDay(TODAY)], CFG)  # row exists but empty
    assert not r.known


def test_hrv_without_enough_baseline_uses_garmin_range():
    t = HealthDay(TODAY, hrv_ms=55.0, hrv_baseline_low=70.0, hrv_baseline_high=100.0)
    r = score_day(TODAY, [t], CFG)
    assert r.signals[0].level == YELLOW and "Garmin range" in r.signals[0].detail


def test_hrv_drop_anomaly_needs_consecutive_days():
    days = baseline(hrv=70.0, n=10)
    # three days at ~-20 % vs the 7 days before each
    low = [HealthDay(TODAY - timedelta(days=i), hrv_ms=54.0, resting_hr=53.0) for i in range(3)]
    hist = [h for h in days if h.day < TODAY - timedelta(days=2)] + low
    a = hrv_drop_anomaly(hist, TODAY, CFG)
    assert a and a["days"] == 3
    ok_day = [h for h in hist if h.day != TODAY - timedelta(days=1)] + [
        HealthDay(TODAY - timedelta(days=1), hrv_ms=70.0)
    ]
    assert hrv_drop_anomaly(ok_day, TODAY, CFG) is None


# --------------------------------------------------------------------------- deload


def reds(*offsets_ago: int) -> dict:
    return {TODAY - timedelta(days=o): RED for o in offsets_ago}


def test_red_streak_counts_consecutive_days():
    assert red_streak(reds(0, 1, 2, 3), TODAY) == 4
    assert red_streak(reds(0, 1, 3, 4), TODAY) == 2
    assert red_streak({}, TODAY) == 0


def test_deload_after_four_red_days_lasts_a_week():
    colors = reds(0, 1, 2, 3)
    st = deload_status(colors, TODAY, TODAY, 4)
    assert st.active and st.trigger_day == TODAY and st.until == TODAY + timedelta(days=6)
    # the following days are still inside the window, the day after it is not
    assert deload_status(colors, TODAY + timedelta(days=6), TODAY, 4).active
    assert not deload_status(colors, TODAY + timedelta(days=7), TODAY, 4).active
    assert not deload_status(reds(0, 1, 2), TODAY, TODAY, 4).active  # only 3 days
    assert not deload_status(reds(0, 1, 3, 4), TODAY, TODAY, 4).active  # not consecutive


def test_deload_trigger_day_is_when_the_streak_reached_the_limit():
    colors = reds(0, 1, 2, 3, 4, 5)  # 6 red days
    st = deload_status(colors, TODAY, TODAY, 4)
    assert st.trigger_day == TODAY - timedelta(days=2) and st.streak == 6


# ---- the Why list (analytics.health._details): every factor, decisive first


def why(sleep_baseline_h=8.0, **kw):
    from app.analytics.health import readiness_view

    return readiness_view(score(**kw), sleep_baseline_h)["details"]


def factor_lines(lines):
    return [ln for ln in lines if ln.startswith(("HRV", "Resting HR", "Sleep"))]


def test_why_lists_every_factor_when_all_normal():
    lines = why()
    assert lines[0] == "All signals normal"
    assert [ln.split(" ")[0] for ln in factor_lines(lines)] == ["HRV", "Resting", "Sleep"]
    assert all(ln.endswith("neutral") for ln in factor_lines(lines))
    assert "baseline" in lines[1] and "usual 8.0 h" in lines[3]


def test_why_keeps_the_other_factors_when_one_is_red():
    lines = why(hrv_ms=44.0)  # -37 %: red, rhr and sleep normal
    assert len(factor_lines(lines)) == 3
    assert lines[0].startswith("HRV 44 ms") and lines[0].endswith("pulls it down")
    assert lines[1].startswith("Resting HR") and lines[1].endswith("neutral")
    assert lines[2].startswith("Sleep") and lines[2].endswith("neutral")


def test_why_puts_the_decisive_factor_first():
    lines = why(resting_hr=62.0, sleep_s=5.5 * 3600, sleep_score=70)  # rhr +9 red, sleep yellow
    f = factor_lines(lines)
    assert [ln.split(" ")[0] for ln in f] == ["Resting", "Sleep", "HRV"]
    assert f[0].endswith("pulls it down") and f[1].endswith("pulls it down a bit")
    assert f[2].endswith("neutral")


def test_why_minor_flag_still_lists_all_three_and_the_note():
    lines = why(hrv_ms=61.0)  # single yellow -> green with a note
    assert len(factor_lines(lines)) == 3
    assert lines[0].endswith("pulls it down a bit")
    assert lines[-1] == "Only a minor flag, so no change"


def test_why_missing_factor_gets_a_no_data_line():
    day = HealthDay(TODAY, hrv_ms=70.0, sleep_s=8 * 3600, sleep_score=85)  # no resting HR
    from app.analytics.health import readiness_view

    lines = readiness_view(score_day(TODAY, [*baseline(), day], CFG), 8.0)["details"]
    assert lines[-1] == "Resting HR: no data"
    assert len(factor_lines(lines)) == 3


def test_why_missing_flagged_and_unflagged_order():
    day = HealthDay(TODAY, hrv_ms=44.0, sleep_s=8 * 3600)  # hrv red, no resting HR
    from app.analytics.health import readiness_view

    lines = readiness_view(score_day(TODAY, [*baseline(), day], CFG), None)["details"]
    f = factor_lines(lines)
    assert f[0].startswith("HRV 44") and f[1].startswith("Sleep") and f[2] == "Resting HR: no data"
    assert "usual" not in f[1]  # no sleep baseline known


def test_why_no_data_at_all():
    r = score_day(TODAY, [], CFG)
    from app.analytics.health import readiness_view

    assert readiness_view(r, None)["details"] == ["No data"]


def test_zero_resting_hr_diff_is_not_negative_zero():
    r = score(resting_hr=53.4)
    assert "±0 vs 53" in next(s.detail for s in r.signals if s.name == "rhr")
