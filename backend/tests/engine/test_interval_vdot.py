"""VDOT from interval reps (engine/core/interval_vdot.py + the measured path of
engine/core/vdot.py): the estimate from work reps, the state update (raise once, idempotent, no
oscillation, drops unchanged), the planner's quality paces, the allowlist, migration 0014 and POST
/engine/run."""

from datetime import date, timedelta

import pytest

from apps.fitness.engine.core import interval_vdot as IV
from apps.fitness.engine.core import quality as Q
from apps.fitness.engine.core import running as R
from apps.fitness.engine.core import vdot as V
from apps.fitness.engine.core.config import VdotConfig, VdotFromIntervalsConfig, load_engine_config
from apps.fitness.engine.core.hr import zones_from_config
from apps.fitness.engine.core.pace import (
    FRAC_INTERVAL,
    FRAC_THRESHOLD,
    pace_at_fraction,
    vdot_for_pace,
)
from apps.fitness.engine.core.planner import PlanInputs, generate_week
from apps.fitness.settings import FITNESS_DIR
from tests.support.db import alembic_config

CFG = load_engine_config(FITNESS_DIR / "config" / "engine.v6.yaml")
RC = CFG.running_cfg
ZONES = zones_from_config(CFG)  # LTHR 171: Z3 from 157, Z5 from 174; peak-HR floor 157.5
IC = VdotFromIntervalsConfig()  # 70 d window, >= 4 reps of 2-8 min, -0.25 / week after 28 d
TODAY = date(2026, 10, 28)


def lap(dist: float, pace: float, hr: float | None, peak: float | None, kind: str) -> R.LapData:
    return R.LapData(dist / 1000 * pace, dist, hr, peak, kind)


def session(
    day: date,
    paces=(253, 254, 245, 248, 249),
    hr: float | None = 166,
    peak: float | None = 173,
    rep_m: float = 1000,
) -> R.RunData:
    """Warm-up, reps with 400 m jogs, cool-down and two GPS junk laps (like the real 5x1000)."""
    laps = [lap(1000, 312, 142, 159, "warmup"), lap(500, 311, 152, 156, "warmup")]
    for i, p in enumerate(paces):
        laps.append(lap(rep_m, p, hr, peak, "active"))
        if i < len(paces) - 1:
            laps.append(lap(400, 370, 148, 172, "recovery"))
    laps += [lap(1000, 305, 155, 173, "cooldown"), lap(17, 700, 145, 147, "cooldown")]
    laps += [lap(1, 976, 143, 143, "cooldown")]
    dist = sum(x.distance_m for x in laps)
    dur = sum(x.duration_s for x in laps)
    return R.RunData(f"iv-{day}", day, dist, dur, 155, 179, tuple(laps), title="5x1000 Intervals")


def ago(n: int) -> date:
    return TODAY - timedelta(days=n)


def estimate(runs, cfg=IC, **kw) -> IV.RepEstimate:
    return IV.estimate_from_reps(runs, ZONES, cfg, today=TODAY, **kw)


# --------------------------------------------------------------------------- the estimate


def test_median_rep_pace_is_taken_as_i_pace():
    est = estimate([session(ago(10))])
    s = est.best
    assert s is not None and s.reps == 5 and s.rep_m == 1000  # warm-up, jogs, junk laps out
    assert s.median_pace_s_per_km == pytest.approx(249)
    assert s.raw_vdot == pytest.approx(vdot_for_pace(249, FRAC_INTERVAL))
    assert s.discount == 0 and est.vdot == pytest.approx(s.raw_vdot)
    assert 46.0 < est.vdot < 46.5  # 4:22/km = I pace of VDOT ~46.3
    assert s.effort == "submaximal" and "sub-maximal" in est.reason


def test_reps_at_z5_are_taken_as_i_pace_exactly():
    s = estimate([session(ago(10), hr=176, peak=186)]).best
    assert s is not None and s.effort == "maximal"
    assert s.raw_vdot == pytest.approx(vdot_for_pace(249, FRAC_INTERVAL))


@pytest.mark.parametrize(
    ("run", "why"),
    [
        (session(ago(5), paces=(250, 250, 250)), "few_reps"),  # 3 reps < 4
        (session(ago(5), paces=(70, 70, 70, 70, 70), rep_m=1000), "few_reps"),  # 70 s: too short
        (session(ago(5), paces=(240, 245, 250, 268)), "uneven"),  # 11 % spread
        (session(ago(5), paces=(240, 244, 248, 253)), "fade"),  # last 5.4 % slower than first
        (session(ago(5), hr=None, peak=None), "no_hr"),
        (session(ago(5), hr=140, peak=150), "hr_low"),  # "4:09" at an easy HR: GPS, not fitness
    ],
)
def test_sessions_that_do_not_measure_the_i_pace(run, why):
    est = estimate([run])
    assert est.best is None and est.vdot is None and est.skipped == {why: 1}
    assert why in est.reason


def test_easy_runs_are_not_considered():
    easy = R.RunData(
        "e",
        ago(3),
        8000,
        2560,
        148,
        156,
        tuple(lap(1000, 320, 148, 156, "active") for _ in range(8)),
    )
    est = estimate([easy])
    assert est.best is None and est.skipped == {}


def test_age_discount_window_and_best_session():
    old = session(ago(42))  # 2 weeks past the free 28 days: -0.5
    s = estimate([old]).best
    assert s is not None and s.discount == pytest.approx(0.5)
    assert s.vdot == pytest.approx(s.raw_vdot - 0.5)
    assert estimate([session(ago(71))]).best is None  # outside the 70-day window
    newer = session(ago(7), paces=(251, 251, 251, 251, 251))  # slower, but fresh
    best = estimate([old, newer]).best
    assert best is not None and best.day == ago(7)  # 45.9 fresh beats 46.3 - 0.5
    assert estimate([old], after=ago(30)).best is None  # before the last VDOT drop


def test_estimate_is_deterministic():
    runs = [session(ago(42)), session(ago(20), paces=(250, 251, 252, 250))]
    assert estimate(runs).as_dict() == estimate(list(reversed(runs))).as_dict()
    assert estimate(runs, cfg=VdotFromIntervalsConfig(enabled=False)).best is None


def test_three_real_sessions_give_about_vdot_45_8():
    """The athlete's 08-26 6x800, 09-02 6x800 and 09-16 5x1000 (rep paces and HR as recorded)."""
    runs = [
        session(date(2026, 8, 26), (246, 246, 249, 245, 239, 245), 165, 173, rep_m=800),
        session(date(2026, 9, 2), (252, 250, 247, 247, 248, 245), 158, 168, rep_m=800),
        session(date(2026, 9, 16), (253, 254, 245, 248, 249), 166, 174),
    ]
    est = estimate(runs)
    raw = {s.day: round(s.raw_vdot, 1) for s in est.sessions}
    assert raw == {date(2026, 8, 26): 47.1, date(2026, 9, 2): 46.6, date(2026, 9, 16): 46.3}
    assert est.vdot == pytest.approx(45.8, abs=0.05)  # 08-26: 47.05 - 1.25 for 63 days


# --------------------------------------------------------------------------- VDOT state

VC = VdotConfig()  # inference: +0.5 per update, +1.0 per 28 days


def measured(day: date, v: float) -> V.Measured:
    return V.Measured(day, v, IC.max_step, f"reps on {day}")


def test_measured_reps_raise_straight_up_capped_at_max_step():
    log = [V.VdotRecord(ago(3), 43.44, "initial")]
    up = V.update_vdot(log, 43.44, VC, today=TODAY, measured=measured(ago(42), 45.8))
    assert up.record is not None and up.record.source == "interval_reps"
    assert up.vdot == pytest.approx(44.51) and up.record.delta == pytest.approx(3.0)
    assert "measured from interval reps" in up.reason and "capped at +3" in up.reason
    small = V.update_vdot(log, 43.44, VC, today=TODAY, measured=measured(ago(42), 43.4))
    assert small.vdot == pytest.approx(43.4)  # beyond max_step / max_per_4w of inference


def test_measured_raise_is_idempotent_and_never_oscillates():
    log = [V.VdotRecord(ago(30), 43.44, "initial")]
    first = V.update_vdot(log, 43.44, VC, today=TODAY, measured=measured(ago(42), 44.0))
    assert first.record is not None
    log.append(first.record)
    again = V.update_vdot(log, 43.44, VC, today=TODAY, measured=measured(ago(42), 44.0))
    assert again.record is None and again.vdot == pytest.approx(44.0)  # same week
    later = TODAY + timedelta(days=7)
    decayed = V.update_vdot(log, 43.44, VC, today=later, measured=measured(ago(42), 43.75))
    assert decayed.record is None and decayed.vdot == pytest.approx(44.0)  # never down by age
    # inference rises see the 4-week room used up by the measured jump
    greens = [ago(-2), ago(-5)]
    inf = V.update_vdot(log, 43.44, VC, today=later, greens=greens, ef_trend_pct=2.0)
    assert inf.record is None and "used up" in inf.reason


def test_drops_are_unchanged_and_older_reps_cannot_undo_them():
    log = [V.VdotRecord(ago(60), 45.0, "initial")]
    fails = [ago(20), ago(13), ago(6)]
    drop = V.update_vdot(log, 45.0, VC, today=TODAY, fails=fails, measured=measured(ago(25), 46))
    assert drop.record is not None and drop.record.source == "fails"
    assert drop.vdot == pytest.approx(44.5)  # the fail rule wins in its week
    log.append(drop.record)
    nxt = TODAY + timedelta(days=7)
    back = V.update_vdot(log, 45.0, VC, today=nxt, fails=fails, measured=measured(ago(25), 46))
    assert back.record is None and back.vdot == pytest.approx(44.5)  # reps before the drop
    fresh = V.update_vdot(log, 45.0, VC, today=nxt, fails=fails, measured=measured(ago(-3), 45))
    assert fresh.record is not None and fresh.vdot == pytest.approx(45.0)  # new evidence counts
    assert V.last_drop_day(log) == TODAY


def test_measured_below_current_or_without_log_changes_nothing():
    log = [V.VdotRecord(ago(30), 46.0, "initial")]
    same = V.update_vdot(log, 46.0, VC, today=TODAY, measured=measured(ago(10), 45.8))
    assert same.record is None and same.vdot == 46.0
    first = V.update_vdot([], 42.0, VC, today=TODAY, measured=measured(ago(10), 45.8))
    assert first.record is not None and first.record.source == "initial" and first.vdot == 42.0


# --------------------------------------------------------------------------- planner

MON = date(2026, 10, 26)


def _plan(cfg=CFG, extra=()):
    from tests.engine.test_easy_pace import run as easy_run
    from tests.engine.test_engine_planner import PROGRAM, health, lifts

    runs = [easy_run(MON - timedelta(days=d), 320, 150) for d in (19, 12, 5, 1)]
    return generate_week(
        PlanInputs(
            today=MON,
            week_start=MON,
            cfg=cfg,
            config_version="v6",
            program=PROGRAM,
            lifts=lifts(),
            health=health(None),
            runs=runs + list(extra),
            gym_name="Gym 3",
            quality_log=[Q.QualityRecord(MON - timedelta(days=3), "threshold", 3, 1000, 90, 300.0)],
            vdot_log=[V.VdotRecord(MON - timedelta(days=30), 42.0, "initial")],
            cycle_start=MON - timedelta(weeks=1),
        )
    )


def _off():
    raw = CFG.model_dump()
    raw["running"]["vdot"]["from_intervals"] = {"enabled": False}
    return CFG.__class__.model_validate(raw)


def test_planner_moves_threshold_and_interval_paces_and_keeps_the_easy_invariant():
    reps = session(MON - timedelta(days=40))  # 5 x 1000 at 4:09: VDOT 46.3 - 0.4 for 40 days
    on, off = _plan(extra=[reps]), _plan(_off(), extra=[reps])
    assert off.running["vdot"] == 42.0 and on.running["vdot"] == pytest.approx(45.0)  # +3 cap
    fri_on = next(d for d in on.days if d.slot_id == "fri_intervals").prescription
    fri_off = next(d for d in off.days if d.slot_id == "fri_intervals").prescription
    p_on, p_off = fri_on["structure"]["work"], fri_off["structure"]["work"]
    assert p_off["pace_s_per_km"] == round(pace_at_fraction(42.0, FRAC_THRESHOLD))  # 4:53
    assert p_on["pace_s_per_km"] == round(pace_at_fraction(45.0, FRAC_THRESHOLD))  # 4:38
    assert Q.target_pace(on.running["vdot"], "vo2max") < Q.target_pace(42.0, "vo2max")
    rec = next(d for d in on.decisions if d.rule == "running:vdot")
    assert rec.output["vdot_record"]["source"] == "interval_reps"
    assert rec.inputs["interval_reps"]["best"]["reps"] == 5  # advisor-visible evidence
    easy = on.running["easy_pace"]
    assert easy["vdot"] >= on.running["vdot"] and easy["quality_vdot"] == on.running["vdot"]
    assert on.running["vdot_from_intervals"]["vdot"] == pytest.approx(45.85, abs=0.01)


def test_planner_without_interval_sessions_is_unchanged():
    assert _plan().running["vdot"] == _plan(_off()).running["vdot"] == 42.0


# --------------------------------------------------------------------------- allowlist + migration


def test_keys_are_on_the_advisor_allowlist():
    from apps.fitness.advisor import validator as VA

    def check(param, value):
        return VA.validate_change(param, value, VA.Context(CFG.model_dump()))

    off = check("running.vdot.from_intervals.enabled", False)
    assert off.valid and off.risk == "low" and off.current is True
    assert check("running.vdot.from_intervals.max_step", 4).risk == "high"
    assert check("running.vdot.from_intervals.max_step", 2).risk == "medium"
    assert not check("running.vdot.from_intervals.max_step", 6).valid
    assert check("running.vdot.from_intervals.min_reps", 3).risk == "high"
    assert check("running.vdot.from_intervals.window_days", 84).risk == "high"
    assert check("running.vdot.from_intervals.window_days", 56).risk == "medium"
    assert check("running.vdot.from_intervals.age_discount_per_week", 0).risk == "high"
    assert check("running.vdot.from_intervals.age_free_days", 35).risk == "high"
    assert "running.vdot.from_intervals.max_step" in VA.describe_allowlist()


def test_migration_0014_adds_the_keys(tmp_path):
    import json

    from alembic import command
    from sqlalchemy import create_engine, text

    from apps.fitness.engine.core.config import EngineConfig

    url = f"sqlite:///{tmp_path / 'm.db'}"
    acfg = alembic_config(url)
    command.upgrade(acfg, "0013")
    content = {"version": "v6.1", "running": {"vdot": {"max_step": 0.5}}}
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO config_versions (version, content, content_hash, is_active, "
                "created_at, author) VALUES ('v6.1', :c, 'h', 1, '2026-10-01 00:00:00', 'seed')"
            ),
            {"c": json.dumps(content)},
        )
    command.upgrade(acfg, "0014")
    command.downgrade(acfg, "0013")
    command.upgrade(acfg, "0014")  # a second run changes nothing: the keys exist
    with eng.connect() as c:
        rows = c.execute(
            text("SELECT version, is_active, author, content FROM config_versions ORDER BY id")
        ).all()
    eng.dispose()
    assert [(r[0], r[1], r[2]) for r in rows] == [("v6.1", 0, "seed"), ("v6.2", 1, "engine")]
    new = json.loads(rows[1][3]) if isinstance(rows[1][3], str) else rows[1][3]
    vd = EngineConfig.model_validate(new).running_cfg.vdot
    assert vd.max_step == 0.5  # existing values stay
    fi = vd.from_intervals
    assert fi.enabled is True and fi.window_days == 70 and fi.max_step == 3.0
    assert fi.age_free_days == 28 and fi.age_discount_per_week == 0.25 and fi.min_reps == 4


# --------------------------------------------------------------------------- POST /engine/run


def test_engine_run_endpoint_replans_and_returns_a_summary(client, monkeypatch):
    from fastapi.testclient import TestClient

    from apps.fitness.jobs import engine_jobs
    from server import app

    pushes: list[str] = []
    monkeypatch.setattr(engine_jobs, "push_runs_soon", pushes.append)
    assert TestClient(app).post("/api/v1/engine/run").status_code == 401
    r = client.post("/api/v1/engine/run")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config_version"] and len(body["weeks"]) == 2
    week = body["weeks"][0]
    assert isinstance(week["quality_vdot"], float) and week["vdot_reason"]
    assert week["easy_pace"]["basis"] in ("daniels", "calibrated")
    assert week["easy_pace"]["vdot"] >= week["quality_vdot"]
    assert set(week["vdot_from_intervals"]) == {"vdot", "reason"}
    assert week["persisted"]["created"] + week["persisted"]["updated"] > 0
    assert pushes == ["engine run (API)"]  # the Garmin workout push hook
    # the first run anchors the VDOT record (week 1) and may update it once (week 2), which the
    # second run carries into week 1; from then on a re-run changes nothing
    second = client.post("/api/v1/engine/run").json()
    third = client.post("/api/v1/engine/run").json()
    assert all(w["persisted"]["created"] == w["persisted"]["updated"] == 0 for w in third["weeks"])
    for a, b in zip(second["weeks"], third["weeks"], strict=True):
        assert (a["quality_vdot"], a["runs"]) == (b["quality_vdot"], b["runs"])
