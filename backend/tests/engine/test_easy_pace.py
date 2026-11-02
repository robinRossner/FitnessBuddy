"""Easy-pace calibration (engine/easy_pace.py): the easy / long pace range from the athlete's own
easy runs, and the planner using it for easy / long runs only (quality paces unchanged)."""

from datetime import date, timedelta

import pytest

from apps.fitness.engine import easy_pace as EP
from apps.fitness.engine import quality as Q
from apps.fitness.engine import running as R
from apps.fitness.engine import vdot as V
from apps.fitness.engine.config import EasyCalibrationConfig, EngineConfig
from apps.fitness.engine.hr import zones_from_config
from apps.fitness.engine.pace import FRAC_EASY_FAST, easy_range_for_vdot, vdot_for_pace
from apps.fitness.engine.planner import PlanInputs, generate_week
from tests.support.db import alembic_config

CAL = EasyCalibrationConfig()  # window 28 d, 3 runs, >= 25 min, HR <= 154, +4 VDOT max
TODAY = date(2026, 10, 28)
Q_VDOT = 42.0


def lap(pace: float, hr: float, dist: float = 1000) -> R.LapData:
    return R.LapData(pace * dist / 1000, dist, hr, hr + 6)


def run(day: date, pace: float = 320, hr: float = 150, km: int = 8, **kw) -> R.RunData:
    laps = kw.pop("laps", None) or [lap(pace, hr) for _ in range(km)]
    dist = sum(x.distance_m for x in laps)
    dur = sum(x.duration_s for x in laps)
    avg = sum(x.avg_hr * x.duration_s for x in laps) / dur
    return R.RunData(f"r-{day}-{pace}", day, dist, dur, avg, None, tuple(laps), **kw)


@pytest.fixture
def calibrate(engine_config: EngineConfig):
    """calibrate(runs, cfg=CAL, q=Q_VDOT, **kw) with the pinned config's zones and easy range."""

    def _calibrate(runs, cfg=CAL, q=Q_VDOT, **kw) -> EP.EasyCalibration:
        return EP.calibrate_easy(
            runs, zones(engine_config), easy(engine_config), cfg, q, today=TODAY, **kw
        )

    return _calibrate


def zones(cfg: EngineConfig):
    return zones_from_config(cfg)  # easy ceiling 156 (LTHR 171)


def easy(cfg: EngineConfig):
    return cfg.running_cfg.easy


def days_ago(n: int) -> date:
    return TODAY - timedelta(days=n)


# --------------------------------------------------------------------------- qualifying runs


def test_only_steady_genuinely_easy_runs_qualify(engine_config):
    interval = run(
        days_ago(3),
        laps=[lap(330, 140), lap(250, 150), lap(380, 140, 400), lap(250, 150), lap(330, 140)],
        title="5x1000 intervals",
    )
    hot_share = run(days_ago(4), laps=[lap(320, 140)] * 3 + [lap(320, 165)] * 2)  # avg 150
    drift = run(days_ago(5), laps=[lap(320, 140)] * 4 + [lap(320, 156)] * 4)  # avg 148, ~10 %
    runs = [
        run(days_ago(1)),  # counts
        run(days_ago(2), hr=155),  # avg HR above 156 - 2
        interval,
        hot_share,  # only 60 % of the time <= 156
        drift,
        run(days_ago(6), km=4),  # 21 min < 25 min
        run(days_ago(7), laps=[lap(320, 150, 3000)] * 3),  # 3 laps: no decoupling
        run(days_ago(8), elevation_gain_m=400),  # 50 m/km
        run(days_ago(9)),  # a quality-session day
        run(days_ago(28)),  # outside the 28-day window (not counted at all)
    ]
    samples, skipped = EP.qualifying_runs(
        runs,
        zones(engine_config),
        easy(engine_config),
        CAL,
        today=TODAY,
        exclude_days={days_ago(9)},
    )
    assert [s.day for s in samples] == [days_ago(1)]
    assert skipped == {
        "hr_high": 1,
        "quality": 2,
        "hr_rule": 1,
        "drift": 1,
        "short": 1,
        "no_laps": 1,
        "hilly": 1,
    }


def test_an_hr_fail_never_counts_even_if_the_plan_would_exempt_it(engine_config):
    hot = run(days_ago(1), laps=[lap(320, 140)] * 3 + [lap(320, 165)] * 2)
    samples, skipped = EP.qualifying_runs(
        [hot], zones(engine_config), easy(engine_config), CAL, today=TODAY
    )
    assert samples == [] and skipped == {"hr_rule": 1}


# --------------------------------------------------------------------------- the calibration


def test_median_pace_becomes_the_fast_end_of_the_easy_range(calibrate):
    runs = [run(days_ago(19), 336, 154), run(days_ago(17), 311, 152), run(days_ago(13), 322, 145)]
    runs.append(run(days_ago(6), 320, 151))
    cal = calibrate(runs)
    assert cal.calibrated and cal.basis == "calibrated" and not cal.capped
    assert cal.median_pace_s_per_km == 321  # median of 311, 320, 322, 336
    assert abs(cal.vdot - vdot_for_pace(321, FRAC_EASY_FAST)) < 1e-9
    assert round(cal.easy_range().min_s_per_km) == 321
    assert Q_VDOT < cal.vdot <= Q_VDOT + CAL.max_vdot_gain
    assert cal.quality_vdot == Q_VDOT
    assert cal.reason.startswith("Easy pace from your 4 easy runs of 28 days at HR <= 154")
    assert "5:11-5:36/km, median 5:21" in cal.reason and "42.0 for quality sessions" in cal.reason
    assert [s["date"] for s in cal.as_dict()["runs"]] == [
        d.isoformat() for d in (days_ago(19), days_ago(17), days_ago(13), days_ago(6))
    ]


def test_the_gain_over_the_quality_vdot_is_capped(calibrate):
    cal = calibrate([run(days_ago(i), 270, 145) for i in (2, 5, 9)])
    assert cal.calibrated and cal.capped
    assert cal.vdot == Q_VDOT + 4.0 and cal.estimate_vdot > cal.vdot
    assert "(capped at +4)" in cal.reason


def test_never_slower_than_daniels(calibrate):
    cal = calibrate([run(days_ago(i), 400, 140) for i in (2, 5, 9)])
    assert not cal.calibrated and cal.basis == "daniels" and cal.vdot == Q_VDOT
    assert cal.estimate_vdot < Q_VDOT
    assert cal.easy_range() == easy_range_for_vdot(Q_VDOT)
    assert cal.reason.startswith("Daniels easy pace for VDOT 42.0: your 3 easy runs")


def test_too_few_runs_keep_daniels(calibrate):
    cal = calibrate([run(days_ago(2), 300), run(days_ago(5), 300), run(days_ago(30), 300)])
    assert not cal.calibrated and cal.vdot == Q_VDOT and cal.estimate_vdot is None
    assert "only 2 of 2 runs in 28 days count as genuinely easy" in cal.reason
    assert cal.reason.endswith("; need 3")


def test_disabled_keeps_daniels(calibrate):
    runs = [run(days_ago(i), 300) for i in (2, 5, 9)]
    cal = calibrate(runs, cfg=EasyCalibrationConfig(enabled=False))
    assert not cal.calibrated and cal.vdot == Q_VDOT and cal.samples == ()
    assert "off" in cal.reason


def test_the_calibration_is_deterministic(calibrate):
    runs = [run(days_ago(i), 300 + i, 150) for i in (2, 5, 9, 14)]
    a, b = calibrate(runs), calibrate(list(reversed(runs)))
    assert a.as_dict() == b.as_dict()


# --------------------------------------------------------------------------- planner

MON = date(2026, 10, 26)


def _plan(cfg, runs=None):
    from tests.engine.test_engine_planner import PROGRAM, health, lifts

    last_fri = MON - timedelta(days=3)
    return generate_week(
        PlanInputs(
            today=MON,
            week_start=MON,
            cfg=cfg,
            config_version="v6",
            program=PROGRAM,
            lifts=lifts(),
            health=health(None),
            runs=runs
            if runs is not None
            else [run(MON - timedelta(days=d), 320, 150) for d in (19, 12, 5, 1)],
            gym_name="Gym 3",
            quality_log=[Q.QualityRecord(last_fri, "threshold", 3, 1000, 90, 300.0)],
            vdot_log=[V.VdotRecord(MON - timedelta(days=30), Q_VDOT, "initial")],
            cycle_start=MON - timedelta(weeks=1),
        )
    )


def _off(cfg):
    raw = cfg.model_dump()
    raw["running"]["easy_calibration"] = {"enabled": False}
    return cfg.__class__.model_validate(raw)


def _day(plan, slot):
    return next(d for d in plan.days if d.slot_id == slot)


def test_planner_moves_easy_and_long_ranges_but_not_the_quality_session(engine_config):
    on, off = _plan(engine_config), _plan(_off(engine_config))
    for slot in ("wed_easy_run", "sun_long_run"):
        p_on, p_off = _day(on, slot).prescription["pace"], _day(off, slot).prescription["pace"]
        assert p_on["basis"] == "calibrated" and p_off["basis"] == "daniels"
        assert p_on["min_s_per_km"] == 320 < p_off["min_s_per_km"]  # 5:20 = the median
        assert p_on["max_s_per_km"] < p_off["max_s_per_km"]
        assert p_on["reason"].startswith("Easy pace from your 4 easy runs")
    assert _day(on, "fri_intervals").prescription == _day(off, "fri_intervals").prescription
    assert on.running["vdot"] == off.running["vdot"] == Q_VDOT
    assert on.pace == off.pace  # the pace model (quality VDOT) is untouched
    assert on.running["easy_pace"]["basis"] == "calibrated"
    rec = next(d for d in on.decisions if d.rule == "running:easy_pace_calibration")
    assert rec.subject_type == "week" and rec.subject_id == MON.isoformat()
    assert rec.output["range"]["min_s_per_km"] == 320
    again = next(
        d for d in _plan(engine_config).decisions if d.rule == "running:easy_pace_calibration"
    )
    assert (again.inputs, again.output, again.rationale) == (rec.inputs, rec.output, rec.rationale)


def test_planner_without_enough_easy_runs_keeps_daniels(engine_config):
    plan = _plan(engine_config, runs=[run(MON - timedelta(days=d), 320, 150) for d in (5, 1)])
    pace = _day(plan, "wed_easy_run").prescription["pace"]
    assert pace["basis"] == "daniels" and "need 3" in pace["reason"]
    rng = easy_range_for_vdot(Q_VDOT)
    assert (pace["min_s_per_km"], pace["max_s_per_km"]) == (
        round(rng.min_s_per_km),
        round(rng.max_s_per_km),
    )


# --------------------------------------------------------------------------- allowlist + migration


def test_calibration_keys_are_on_the_advisor_allowlist(engine_config):
    from apps.fitness.advisor import validator as VA

    def check(param, value):
        return VA.validate_change(param, value, VA.Context(engine_config.model_dump()))

    off = check("running.easy_calibration.enabled", False)
    assert off.valid and off.risk == "low" and off.current is True
    assert check("running.easy_calibration.max_vdot_gain", 6).risk == "high"
    assert check("running.easy_calibration.max_vdot_gain", 2).risk == "medium"
    assert not check("running.easy_calibration.max_vdot_gain", 8).valid
    assert check("running.easy_calibration.min_runs", 2).risk == "high"
    assert check("running.easy_calibration.min_runs", 5).risk == "medium"
    assert check("running.easy_calibration.window_days", 42).valid
    assert check("running.easy_calibration.hr_margin_bpm", 4).valid
    assert "running.easy_calibration.max_vdot_gain" in VA.describe_allowlist()


def test_migration_0013_adds_the_calibration_keys(tmp_path):
    import json

    from alembic import command
    from sqlalchemy import create_engine, text

    from apps.fitness.engine.config import EngineConfig

    url = f"sqlite:///{tmp_path / 'm.db'}"
    acfg = alembic_config(url)
    command.upgrade(acfg, "0012")
    content = {"version": "v6.1", "running": {"easy_calibration": {"max_vdot_gain": 2.0}}}
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO config_versions (version, content, content_hash, is_active, "
                "created_at, author) VALUES ('v6.1', :c, 'h', 1, '2026-10-01 00:00:00', 'seed')"
            ),
            {"c": json.dumps(content)},
        )
    command.upgrade(acfg, "0013")
    command.downgrade(acfg, "0012")
    command.upgrade(acfg, "0013")  # a second run changes nothing: the keys exist
    with eng.connect() as c:
        rows = c.execute(
            text("SELECT version, is_active, author, content FROM config_versions ORDER BY id")
        ).all()
    eng.dispose()
    assert [(r[0], r[1], r[2]) for r in rows] == [("v6.1", 0, "seed"), ("v6.2", 1, "engine")]
    new = json.loads(rows[1][3]) if isinstance(rows[1][3], str) else rows[1][3]
    cal = EngineConfig.model_validate(new).running_cfg.easy_calibration
    assert cal.enabled is True and cal.window_days == 28 and cal.min_runs == 3
    assert cal.max_vdot_gain == 2.0  # existing values stay
