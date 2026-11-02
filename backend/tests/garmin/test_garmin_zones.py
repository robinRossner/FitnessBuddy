"""Heart-rate zones from Garmin's floors; every stored run judged by the zones it was run in."""

import logging
from dataclasses import replace
from datetime import date, timedelta

import pytest

from apps.fitness.analytics.zones import time_in_zones
from apps.fitness.db.models import AthleteMetric
from apps.fitness.engine.core import easy_pace as EP
from apps.fitness.engine.core import hr as H
from apps.fitness.engine.core import interval_vdot as IV
from apps.fitness.engine.core import quality as Q
from apps.fitness.engine.core import running as R
from apps.fitness.engine.core.hr import GARMIN_ZONES_SINCE, zone_book, zones_from_config
from apps.fitness.engine.core.planner import _hr, generate_week
from apps.fitness.engine.store import config_service as cs
from apps.fitness.engine.store import data as D
from apps.fitness.garmin.base import HR_ZONES
from apps.fitness.jobs.garmin_jobs import DAY_KINDS, LIGHT_KINDS
from apps.fitness.jobs.scheduler import build_scheduler
from apps.fitness.settings import FITNESS_DIR, Settings
from tests.engine.test_engine_planner import CFG, easy_run, inputs, interval_run

OLD = (98.0, 117.0, 137.0, 156.0, 176.0)  # % of max HR: easy ceiling 155
NEW = (86.0, 121.0, 130.0, 151.0, 172.0)  # %HRR: easy ceiling 150
PERCENT = (50.0, 60.0, 70.0, 80.0, 90.0)
HR_CFG = CFG.running_cfg.heart_rate
MON = date(2026, 11, 30)  # well after GARMIN_ZONES_SINCE


def manual_cfg():
    raw = CFG.model_dump()
    raw["running"]["heart_rate"]["source"] = "manual"
    return CFG.__class__.model_validate(raw)


def run_on(day: date, floors, hr=153, km=8) -> R.RunData:
    return replace(easy_run(day, km, hr), zone_floors=floors)


# ------------------------------------------------------------------- quality HR lines


def test_quality_lines_stay_on_lthr_with_garmin_floors():
    rc = CFG.running_cfg
    new = zones_from_config(CFG, NEW)
    green, ceiling = Q.hr_lines(new, Q.type_spec(rc, "threshold"))
    assert green == pytest.approx(171 * 0.95) and ceiling == pytest.approx(171 * 0.99)
    green, ceiling = Q.hr_lines(new, Q.type_spec(rc, "vo2max"))
    assert green == pytest.approx(171 * 1.02) and ceiling == pytest.approx(171 * 1.08)


# ------------------------------------------------------------------- floors -> targets


def test_floors_set_the_easy_target_and_quality_stays_on_lthr():
    z = zones_from_config(CFG, NEW)
    assert z.source == "garmin" and z.easy_basis == "garmin"
    assert (z.easy_low, z.easy_high) == (121, 150)
    assert (z.interval_low, z.interval_high) == (166, 179)
    assert [(x.name, x.low, x.high) for x in z.zones] == [
        ("z1", 0, 120),
        ("z2", 121, 129),
        ("z3", 130, 150),
        ("z4", 151, 171),
        ("z5", 172, 190),
    ]
    assert z.lthr == 171 and z.z5_low == 174  # hard efforts are judged on LTHR's Z5
    assert z.zone_of(150) == "z3" and z.zone_of(151) == "z4"
    assert z.as_dict()["source"] == "garmin"


def test_planned_sessions_use_the_latest_floors():
    plan = generate_week(inputs(zone_floors=NEW))
    easy = next(d for d in plan.days if d.kind == "easy_run").prescription
    assert easy["hr"] == {"zone": "Z2-Z3", "low": 121, "high": 150, "basis": "garmin"}
    assert "below 150 bpm" in easy["reason"]
    long = next(d for d in plan.days if d.kind == "long_run").prescription
    assert long["hr"]["high"] == 150
    st = next(d for d in plan.days if d.kind == "intervals").prescription["structure"]
    assert st["session_type"] == "threshold"
    assert (st["work"]["hr"]["zone"], st["work"]["hr"]["low"], st["work"]["hr"]["high"]) == (
        "Z4",
        166,
        173,
    )
    assert st["warmup"]["hr"]["high"] == 150
    assert plan.zones["easy"]["high"] == 150


def test_vo2_and_5k_work_target_stays_on_lthr():
    assert _hr(H.garmin_zones(HR_CFG, NEW), "intervals") == {
        "zone": "Z4-Z5a",
        "low": 166,
        "high": 179,
    }


def test_without_floors_the_targets_stay_on_lthr():
    z = zones_from_config(CFG)
    assert z.source == "lthr" and (z.easy_low, z.easy_high) == (127, 156)
    assert z.z5_low == 174
    plan = generate_week(inputs())
    easy = next(d for d in plan.days if d.kind == "easy_run").prescription
    assert easy["hr"] == {"zone": "Z1-Z2", "low": 127, "high": 156, "basis": "lthr"}


def test_a_manual_config_ignores_garmin_floors():
    cfg = manual_cfg()
    assert zones_from_config(cfg, NEW).easy_high == 156
    assert zone_book(cfg, NEW).for_run(run_on(MON, NEW)).easy_high == 156


# ------------------------------------------------------------------- validating the floors


@pytest.mark.parametrize(
    "floors",
    [
        PERCENT,
        (86.0, 121.0, 130.0, 151.0),  # four values
        (86.0, 121.0, 121.0, 151.0, 172.0),  # not strictly ascending
        (172.0, 151.0, 130.0, 121.0, 86.0),  # descending
        (40.0, 121.0, 130.0, 151.0, 172.0),  # Z1 floor too low for bpm
        (86.0, 121.0, 130.0, 151.0, 200.0),  # Z5 floor above max HR 190
    ],
)
def test_invalid_floors_fall_back_to_lthr(floors):
    assert not H.valid_floors(floors, 190)
    assert zones_from_config(CFG, floors).easy_high == 156
    assert zone_book(CFG, floors).for_run(run_on(MON, floors)).easy_high == 156


def test_invalid_floors_warn_once_and_never_crash(caplog, monkeypatch):
    H._warned_floors.clear()
    monkeypatch.setattr(H.log, "disabled", False)  # alembic's fileConfig may have disabled it
    with caplog.at_level(logging.WARNING, logger="apps.fitness.engine.core.hr"):
        for _ in range(3):
            zones_from_config(CFG, PERCENT)
    assert sum("ignoring Garmin zone floors" in r.message for r in caplog.records) == 1
    plan = generate_week(inputs(zone_floors=PERCENT))
    assert plan.zones["easy"]["high"] == 156


def test_percent_floors_are_not_rebinned_as_bpm():
    zones = zones_from_config(CFG, NEW)
    run = replace(
        easy_run(MON, 2, 140),
        time_in_zones_s={"z1": 0, "z2": 500, "z3": 0, "z4": 0, "z5": 0},
        zone_floors=PERCENT,
    )
    assert time_in_zones(run, zones)[1] == "laps"


# ------------------------------------------------------------------- past runs keep their zones


def test_a_run_keeps_the_ceiling_of_its_own_floors():
    book = zone_book(CFG, NEW)  # today: the new zones
    before = run_on(MON, OLD)
    after = run_on(MON + timedelta(days=7), NEW)
    assert book.for_run(before).easy_high == 155
    assert book.for_run(after).easy_high == 150
    old_ev = R.evaluate_easy_run(before, book.for_run(before), CFG.running_cfg.easy)
    assert old_ev.passed and old_ev.hr_ceiling == 155
    new_ev = R.evaluate_easy_run(after, book.for_run(after), CFG.running_cfg.easy)
    assert not new_ev.passed and new_ev.hr_ceiling == 150
    assert "avg HR 153 above the easy ceiling 150" in new_ev.reasons


def test_runs_before_garmin_zones_were_adopted_keep_the_lthr_ceiling():
    """The engine targeted 156 back then; Garmin's floors of that time would give 155."""
    book = zone_book(CFG, NEW)
    day = GARMIN_ZONES_SINCE - timedelta(days=1)
    run = run_on(day, OLD, hr=156)
    assert book.for_run(run).easy_high == 156
    ev = R.evaluate_easy_run(run, book.for_run(run), CFG.running_cfg.easy)
    assert ev.passed and ev.hr_ceiling == 156
    assert book.for_run(run_on(GARMIN_ZONES_SINCE, OLD)).easy_high == 155


def test_calibration_keeps_old_samples_and_names_the_limits_applied():
    book = zone_book(CFG, NEW)
    rc = CFG.running_cfg
    today = MON + timedelta(days=14)
    runs = [run_on(MON + timedelta(days=i), OLD, hr=152, km=8) for i in (0, 3, 6)]
    runs.append(run_on(MON + timedelta(days=10), NEW, hr=147, km=8))
    runs.append(run_on(MON + timedelta(days=12), NEW, hr=152, km=8))  # above the new limit
    cal = EP.calibrate_easy(
        runs, book.current, rc.easy, rc.easy_calibration, 42.0, today=today, zones_of=book.for_run
    )
    margin = rc.easy_calibration.hr_margin_bpm
    assert len(cal.samples) == 4 and cal.skipped == {"hr_high": 1}
    assert cal.hr_ceiling == 150
    assert f"HR <= {150 - margin:g}-{155 - margin:g}" in cal.reason
    # judged by today's ceiling, the three old runs would be gone: that is what must not happen
    today_only = EP.calibrate_easy(
        runs, book.current, rc.easy, rc.easy_calibration, 42.0, today=today
    )
    assert len(today_only.samples) == 1


def test_interval_effort_is_judged_on_lthr_whatever_the_floors():
    book = zone_book(CFG, NEW)
    cfg = CFG.running_cfg.vdot.from_intervals
    today = MON + timedelta(days=1)
    for hr, effort in ((173, "submaximal"), (174, "maximal")):  # Garmin's Z5 would start at 172
        run = replace(interval_run(MON, reps=5, hr=hr), zone_floors=NEW)
        best = IV.estimate_from_reps([run], book.current, cfg, today=today, zones_of=book.for_run)
        assert best.best.effort == effort


def test_a_zone_change_alone_does_not_touch_the_volume_hold():
    """Last week's easy runs at 153 passed under their own zones. New floors must not flag them."""
    last = MON - timedelta(days=7)
    runs = [
        run_on(last + timedelta(days=2), OLD, km=8),
        run_on(last + timedelta(days=6), OLD, km=12),
    ]
    runs += [
        run_on(MON - timedelta(days=7 * w - 2), OLD, 140, km) for w, km in ((2, 14), (3, 8), (4, 6))
    ]
    runs.sort(key=lambda r: r.day)
    args = {"runs": runs, "today": MON, "week_start": MON}
    before = generate_week(inputs(zone_floors=OLD, **args))
    after = generate_week(inputs(zone_floors=NEW, **args))
    assert before.volume["hold"]["blocked"] is None and before.volume["hold"]["hold_km"]
    assert after.volume == before.volume
    for key in ("basis", "vdot", "runs"):
        assert after.running["easy_pace"][key] == before.running["easy_pace"][key]
    assert [d.prescription.get("distance_km") for d in after.days] == [
        d.prescription.get("distance_km") for d in before.days
    ]
    # the same runs inside the new zones are flagged: the hold really does read the verdicts
    newer = [replace(r, zone_floors=NEW) for r in runs]
    blocked = generate_week(inputs(zone_floors=NEW, **{**args, "runs": newer}))
    assert blocked.volume["hold"]["hold_km"] is None


def test_the_same_floors_give_garmins_seconds_unchanged():
    zones = zones_from_config(CFG, NEW)
    secs = {"z1": 100, "z2": 200, "z3": 1500, "z4": 300, "z5": 60}
    run = replace(easy_run(MON, 5), time_in_zones_s=secs, zone_floors=NEW)
    assert time_in_zones(run, zones) == (secs, "garmin_zones")


# ------------------------------------------------------------------- the dated rows


def _floors(db, floors, day):
    for i, v in enumerate(floors, 1):
        db.add(AthleteMetric(day=day, metric=f"hr_zone{i}_floor", value=v, source="garmin"))
    db.flush()


def test_stored_rows_date_the_floors_by_run_and_the_latest_are_current(db):
    _floors(db, OLD, date(2026, 10, 25))
    _floors(db, NEW, date(2026, 12, 1))
    assert D.latest_zone_floors(db) == NEW
    assert D._zone_floors(db, date(2026, 11, 30)) == OLD
    assert D._zone_floors(db, date(2026, 12, 1)) == NEW
    assert D._zone_floors(db, date(2026, 10, 1)) == OLD  # earlier than the first row: the first row


def test_a_zone_change_creates_no_config_version(db):
    cs.ensure_seeded(db, FITNESS_DIR / "config" / "engine.v3.yaml")
    db.commit()
    db.add(AthleteMetric(day=date(2026, 10, 25), metric="max_hr", value=190, source="garmin"))
    db.add(AthleteMetric(day=date(2026, 10, 25), metric="lthr", value=171, source="garmin"))
    _floors(db, OLD, date(2026, 10, 25))
    _floors(db, NEW, date(2026, 11, 2))
    assert cs.apply_garmin_hr(db) is None


# ------------------------------------------------------------------- the schedule


def test_scheduled_day_runs_fetch_the_zones_but_the_manual_sync_does_not():
    assert HR_ZONES in DAY_KINDS and HR_ZONES not in LIGHT_KINDS
    assert len(LIGHT_KINDS) == 4 and len(DAY_KINDS) == 5
    jobs = {j.id: j for j in build_scheduler(Settings(_env_file=None)).get_jobs()}
    day_jobs = [j for jid, j in jobs.items() if jid.startswith("garmin_day_")]
    assert day_jobs and all(j.args == (DAY_KINDS,) for j in day_jobs)
