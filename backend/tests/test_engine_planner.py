"""The weekly planner on synthetic plain data (no database)."""

from datetime import date, timedelta

import pytest

from app.engine import lifting as L
from app.engine import running as R
from app.engine.config import load_engine_config
from app.engine.planner import LiftContext, PlanInputs, VariantInfo, generate_week
from app.engine.program import Program, ProgramExercise
from app.engine.readiness import HealthDay
from app.settings import BACKEND_DIR

CFG = load_engine_config(BACKEND_DIR / "config" / "engine.v3.yaml")
MON = date(2026, 10, 26)

BENCH = VariantInfo(
    1,
    "k1",
    "Bench Press (Barbell)",
    "Bench Press (Barbell)",
    "bench_press",
    "Bench Press",
    "barbell",
    "total",
)
PULL = VariantInfo(
    2,
    "k2",
    "Lat Pulldown (Cable) @ Gym 3",
    "Lat Pulldown (Cable)",
    "lat_pulldown",
    "Lat Pulldown",
    "cable",
    "total",
    [40.0, 45.0, 50.0, 55.0, 60.0],
)
SQUAT = VariantInfo(
    3, "k3", "Squat (Barbell)", "Squat (Barbell)", "squat", "Squat", "barbell", "total"
)

PROGRAM = Program(
    {
        "upper": [
            ProgramExercise("bench_press", "Bench Press (Barbell)", 3),
            ProgramExercise("lat_pulldown", "Lat Pulldown (Cable)", 3),
        ],
        "lower": [ProgramExercise("squat", "Squat (Barbell)", 3)],
    }
)


def expo(day: date, w: float, reps: list[int], e1rm: float | None = None) -> L.Exposure:
    return L.Exposure(day, tuple(L.SetResult(w, r) for r in reps), e1rm or w * (1 + max(reps) / 30))


def lifts(**override) -> dict[str, LiftContext]:
    d = MON - timedelta(days=4)
    base = {
        "Bench Press (Barbell)": LiftContext(BENCH, [expo(d, 60, [10, 9, 8])]),
        "Lat Pulldown (Cable)": LiftContext(PULL, [expo(d, 50, [12, 12, 12])]),
        "Squat (Barbell)": LiftContext(SQUAT, [expo(d - timedelta(days=1), 90, [10, 10, 9])]),
    }
    base.update(override)
    return base


def health(today_row: HealthDay | None, n=14) -> list[HealthDay]:
    rows = [
        HealthDay(
            MON - timedelta(days=i), resting_hr=53, hrv_ms=70, sleep_s=8 * 3600, sleep_score=85
        )
        for i in range(1, n + 1)
    ]
    return rows + ([today_row] if today_row else [])


def today_row(**kw) -> HealthDay:
    base = {"resting_hr": 53, "hrv_ms": 70, "sleep_s": 8 * 3600, "sleep_score": 85}
    return HealthDay(MON, **{**base, **kw})


def lap(pace, hr, dist=1000, intensity=None):
    return R.LapData(pace * dist / 1000, dist, hr, hr + 5, intensity)


def interval_run(day: date, reps=5, hr=166) -> R.RunData:
    laps = [lap(311, 142, 1000, "warmup")]
    for _ in range(reps):
        laps += [lap(250, hr, 1000, "active"), lap(370, 150, 400, "recovery")]
    laps[-1] = lap(305, 155, 1000, "cooldown")
    return run_from(day, laps, "5x1000 intervals")


def run_from(day: date, laps, title="Run") -> R.RunData:
    dist = sum(x.distance_m for x in laps)
    dur = sum(x.duration_s for x in laps)
    hr = sum(x.avg_hr * x.duration_s for x in laps) / dur
    return R.RunData(f"run-{day}", day, dist, dur, hr, None, tuple(laps), None, None, title)


def easy_run(day: date, km=8, hr=148, pace=320) -> R.RunData:
    return run_from(day, [lap(pace, hr) for _ in range(km)])


def runs_history() -> list[R.RunData]:
    out = []
    for w in range(1, 5):  # four previous weeks, ~22 km each
        mon = MON - timedelta(days=7 * w)
        out += [
            easy_run(mon + timedelta(days=2), 7),
            interval_run(mon + timedelta(days=4)),
            easy_run(mon + timedelta(days=6), 10 + w % 2),
        ]
    return sorted(out, key=lambda r: r.day)


def inputs(**kw) -> PlanInputs:
    base = {
        "today": MON,
        "week_start": MON,
        "cfg": CFG,
        "config_version": "v3",
        "program": PROGRAM,
        "lifts": lifts(),
        "health": health(None),
        "runs": runs_history(),
        "gym_name": "Gym 3",
    }
    base.update(kw)
    return PlanInputs(**base)


def day_of(plan, slot: str):
    return next(d for d in plan.days if d.slot_id == slot)


def sets_of(day, name: str):
    ex = next(e for e in day.prescription["exercises"] if e["name"].startswith(name))
    return [(s["target_weight"], s["target_reps"]) for s in ex["sets"]]


# normal week


def test_week_has_the_template_sessions_in_order():
    plan = generate_week(inputs())
    assert [d.kind for d in plan.days] == [
        "upper",
        "lower",
        "easy_run",
        "upper",
        "intervals",
        "rest",
        "long_run",
    ]
    assert [d.day for d in plan.days] == [MON + timedelta(days=i) for i in range(7)]
    assert not plan.readiness.known and plan.readiness.reason == "no data"


def test_lifting_prescription_has_targets_and_previous_performance():
    plan = generate_week(inputs())
    mon = day_of(plan, "mon_upper")
    assert sets_of(mon, "Bench") == [(60, 11), (60, 10), (60, 9)]  # last 10/9/8 -> +1
    assert sets_of(mon, "Lat Pulldown") == [(55.0, 7)] * 3  # all 12 @ 50 -> next rung
    bench = mon.prescription["exercises"][0]
    assert bench["last"]["sets"][0] == {"weight": 60, "reps": 10, "duration_s": None}
    assert bench["rep_range"] == [7, 12] and bench["rule"] == "double_progression:add_reps"
    assert mon.prescription["gym"] == "Gym 3"


def test_both_upper_days_use_the_same_program():
    plan = generate_week(inputs())
    a, b = day_of(plan, "mon_upper"), day_of(plan, "thu_upper")
    assert [e["name"] for e in a.prescription["exercises"]] == [
        e["name"] for e in b.prescription["exercises"]
    ]


def test_interval_session_prescription_from_last_session():
    plan = generate_week(inputs())
    fri = day_of(plan, "fri_intervals")
    work = fri.prescription["structure"]["work"]
    assert work["reps"] == 6 and work["distance_m"] == 1000  # last session: 5 reps, passed
    assert work["pace_s_per_km"] == 250 and work["hr"] == {
        "zone": "Z4-Z5a",
        "low": 166,
        "high": 179,
    }
    assert fri.prescription["structure"]["recovery"]["distance_m"] == 400
    assert fri.prescription["progression"]["rule"] == "intervals:add_rep"
    assert fri.title.startswith("Intervals 6x1000")


def test_easy_and_long_run_targets():
    plan = generate_week(inputs())
    easy = day_of(plan, "wed_easy_run").prescription
    assert easy["hr"]["high"] == 156 and easy["hr"]["low"] == 127 and easy["distance_km"] == 7
    assert easy["last_easy_run"]["passed"] is True
    long = day_of(plan, "sun_long_run").prescription
    assert long["distance_km"] >= 10 and long["hr"]["high"] == 156


def test_decisions_are_logged_with_rules_and_inputs():
    plan = generate_week(inputs())
    kinds = {(d.kind, d.rule) for d in plan.decisions}
    assert ("progression", "double_progression:add_reps") in kinds
    assert ("progression", "double_progression:increase_load") in kinds
    assert ("running", "intervals:add_rep") in kinds and ("readiness", "readiness:score") in kinds
    prog = next(d for d in plan.decisions if d.kind == "progression" and d.subject_id == "2")
    assert prog.inputs["rep_range"] == [7, 12] and prog.output["sets"] and prog.rationale


# readiness adjustments


def test_yellow_today_drops_a_set_and_turns_nothing_else():
    plan = generate_week(inputs(health=health(today_row(hrv_ms=61, resting_hr=59))))
    mon = day_of(plan, "mon_upper")
    assert plan.readiness.color == "yellow"
    assert len(sets_of(mon, "Bench")) == 2 and len(sets_of(mon, "Lat")) == 2
    assert mon.adjustments and "-1 working set" in mon.adjustments[0] and "(-1 set)" in mon.title
    assert mon.readiness["color"] == "yellow" and "HRV" in mon.readiness["reason"]
    # other days are forecasts: no adjustment, readiness unknown
    thu = day_of(plan, "thu_upper")
    assert len(sets_of(thu, "Bench")) == 3 and thu.readiness["color"] is None


def test_yellow_converts_intervals_to_easy_run_on_that_day():
    fri = MON + timedelta(days=4)
    p = generate_week(
        inputs(
            today=fri,
            health=health(
                HealthDay(fri, resting_hr=59, hrv_ms=61, sleep_s=8 * 3600, sleep_score=85)
            ),
        )
    )
    day = day_of(p, "fri_intervals")
    assert day.kind == "easy_run" and day.prescription["converted_from"]["kind"] == "intervals"
    assert (
        "intervals replaced by an easy run" in day.adjustments[0]
        and day.prescription["hr"]["high"] == 156
    )


def test_red_today_replaces_lifting_by_rest_and_flags_conflict_when_progression_wants_more():
    row = today_row(hrv_ms=52, sleep_s=7 * 3600)
    plan = generate_week(inputs(health=health(row)))
    mon = day_of(plan, "mon_upper")
    assert plan.readiness.color == "red" and mon.kind == "rest"
    assert mon.prescription["type"] == "rest" and mon.prescription["original"]["kind"] == "upper"
    assert "mobility" in mon.prescription["optional"]
    # lat pulldown was due to go up (12/12/12) while readiness says red
    conflicts = [t for t in plan.triggers if t.type == "rule_conflict"]
    assert conflicts and "Lat Pulldown (Cable) @ Gym 3" in conflicts[0].payload["items"]
    assert any(d.kind == "conflict" for d in plan.decisions)


def test_red_turns_hard_run_into_short_easy_run_or_rest():
    fri = MON + timedelta(days=4)
    p = generate_week(
        inputs(today=fri, health=health(HealthDay(fri, resting_hr=53, hrv_ms=52, sleep_s=8 * 3600)))
    )
    day = day_of(p, "fri_intervals")
    assert day.kind == "easy_run" and day.prescription["distance_km"] == 4
    assert day.prescription["alternative"] == "rest"
    assert any(t.type == "rule_conflict" for t in p.triggers)  # intervals wanted to progress


def test_unknown_readiness_changes_nothing():
    plan = generate_week(inputs(health=[]))
    assert all(not d.adjustments for d in plan.days)


# deload / stall / anomaly


def red_history(n_days: int) -> list[HealthDay]:
    rows = health(None, n=14)
    out = []
    for h in rows:
        if (MON - h.day).days < n_days:
            h = HealthDay(h.day, resting_hr=69, hrv_ms=35, sleep_s=8 * 3600, sleep_score=85)
        out.append(h)
    return out


def test_four_red_days_trigger_a_deload_week():
    hist = [
        *red_history(4),
        HealthDay(MON, resting_hr=69, hrv_ms=35, sleep_s=8 * 3600, sleep_score=85),
    ]
    plan = generate_week(inputs(today=MON, health=hist))
    thu = day_of(plan, "thu_upper")  # inside the 7-day window, readiness itself unknown
    assert thu.prescription["deload"]["active"]
    assert len(sets_of(thu, "Bench")) == 2 and "deload" in thu.title
    assert sets_of(thu, "Bench")[0][0] < 60  # lighter than the 60 kg of last time
    deload_trigger = [t for t in plan.triggers if t.type == "deload"]
    assert deload_trigger and any(d.kind == "deload" for d in plan.decisions)


def test_three_red_days_is_no_deload():
    plan = generate_week(inputs(health=[*red_history(3), today_row(hrv_ms=35, resting_hr=69)]))
    assert not any(t.type == "deload" for t in plan.triggers)


def test_stalled_lift_resets_load_and_creates_advisor_trigger():
    d = MON - timedelta(days=20)
    hist = [expo(d + timedelta(days=3 * i), 60, [10, 9, 9], e1rm=80.0 - i) for i in range(5)]
    plan = generate_week(inputs(lifts=lifts(**{"Bench Press (Barbell)": LiftContext(BENCH, hist)})))
    mon = day_of(plan, "mon_upper")
    assert sets_of(mon, "Bench") == [(50.0, 7)] * 3
    stall = [t for t in plan.triggers if t.type == "lift_stall"]
    assert (
        len(stall) == 1
        and stall[0].payload["variant"] == BENCH.name
        and stall[0].dedupe_key == f"lift_stall:1:{MON}"
    )
    dec = next(x for x in plan.decisions if x.rule == "stall_reset")
    assert dec.output["reset_load"] == 50.0 and dec.output["reset_day"] == MON.isoformat()


def test_hrv_anomaly_trigger():
    rows = []
    for i in range(1, 15):
        low = i <= 3
        rows.append(
            HealthDay(
                MON - timedelta(days=i), resting_hr=53, hrv_ms=48 if low else 70, sleep_s=8 * 3600
            )
        )
    rows.append(today_row(hrv_ms=48))
    plan = generate_week(inputs(health=rows))
    assert any(t.type == "data_anomaly" for t in plan.triggers)


# volume, frozen, missing


def test_weekly_volume_cap_trims_the_long_run():
    heavy = [
        easy_run(MON - timedelta(days=7 * w - 2), 6) for w in range(1, 5)
    ]  # only 6 km/week before
    plan = generate_week(inputs(runs=heavy))
    total = sum(plan.volume["planned_km"].values())
    assert plan.volume["cap_km"] == pytest.approx(6.6)
    assert plan.volume["notes"] and total <= 7 + 9.5 + 8  # trimmed, but never below the minimums
    assert day_of(plan, "sun_long_run").prescription["distance_km"] >= 8


def test_frozen_and_past_slots_are_not_regenerated():
    plan = generate_week(inputs(today=MON + timedelta(days=2), frozen_slots={"thu_upper"}))
    slots = [d.slot_id for d in plan.days]
    assert "mon_upper" not in slots and "tue_lower" not in slots  # past
    assert "thu_upper" not in slots  # closed by the user
    assert slots[0] == "wed_easy_run"


def test_unresolved_exercise_is_listed_without_targets():
    plan = generate_week(inputs(lifts=lifts(**{"Lat Pulldown (Cable)": LiftContext(None)})))
    ex = next(
        e
        for e in day_of(plan, "mon_upper").prescription["exercises"]
        if e["name"].startswith("Lat")
    )
    assert ex["unresolved"] and ex["sets"][0]["target_weight"] is None


def test_lower_before_intervals_swap_is_decided_and_logged():
    cfg = CFG.model_copy(update={"weekly_template": {**CFG.weekly_template, "thu": "lower"}})
    plan = generate_week(inputs(cfg=cfg))
    fri = day_of(plan, "fri_intervals")
    assert fri.day == MON + timedelta(days=5)
    assert any(d.kind == "scheduling" and d.rule == "recovery:swap" for d in plan.decisions)


def test_stagnating_efficiency_creates_trigger():
    flat = []
    for w in range(0, 8):  # same pace and HR for 8 weeks
        flat.append(easy_run(MON - timedelta(days=7 * w + 3), 6, hr=148, pace=320))
        flat.append(easy_run(MON - timedelta(days=7 * w + 5), 6, hr=148, pace=320))
    plan = generate_week(inputs(runs=sorted(flat, key=lambda r: r.day)))
    assert any(t.type == "running_stagnation" for t in plan.triggers)


def test_cap_below_the_template_minimum_raises_a_rule_conflict_trigger():
    light = [easy_run(MON - timedelta(days=7 * w - 2), 6) for w in range(1, 5)]
    plan = generate_week(inputs(runs=light))
    conflicts = [
        t for t in plan.triggers if t.type == "rule_conflict" and "volume_cap" in t.dedupe_key
    ]
    assert conflicts and conflicts[0].payload["cap_km"] == pytest.approx(6.6)


def cfg_with(**running) -> object:
    raw = CFG.model_dump(mode="json")
    raw["running"] = {**CFG.running, **running}
    return CFG.__class__.model_validate(raw)


def light_runs() -> list[R.RunData]:
    return [interval_run(MON - timedelta(days=5))]  # last week: one 5-rep session (8.6 km)


def intervals_of(plan):
    return day_of(plan, "fri_intervals")


def test_volume_floor_replaces_a_tiny_cap():
    weekly = {MON - timedelta(days=7): 6.0}
    assert R.volume_cap_km(weekly, MON, 10) == pytest.approx(6.6)
    assert R.volume_cap_km(weekly, MON, 10, floor_km=18) == 18
    assert R.volume_cap_km({MON - timedelta(days=7): 30.0}, MON, 10, floor_km=18) == pytest.approx(
        33
    )
    assert R.volume_cap_km({}, MON, 10, floor_km=18) == 18 and R.volume_cap_km({}, MON, 10) is None


def test_interval_session_keeps_full_size_when_the_budget_is_roomy():
    plan = generate_week(inputs(cfg=cfg_with(weekly_volume_floor_km=40), runs=light_runs()))
    s = intervals_of(plan).prescription["structure"]
    assert (
        s["work"]["reps"] == 6
        and s["warmup"]["distance_km"] == 2.0
        and s["cooldown"]["distance_km"] == 1.5
    )


def test_warmup_and_cooldown_shrink_to_fit_the_interval_share():
    # cap 21, minus easy 3 and long 8 -> 10 km for intervals: 6 x 1000 (8.0 km) + 2.0 km of wu/cd
    plan = generate_week(inputs(cfg=cfg_with(weekly_volume_floor_km=21), runs=light_runs()))
    pres = intervals_of(plan).prescription
    assert pres["structure"]["work"]["reps"] == 6
    wu, cd = (
        pres["structure"]["warmup"]["distance_km"],
        pres["structure"]["cooldown"]["distance_km"],
    )
    assert wu < 2.0 and cd < 1.5 and wu >= 1.0 and cd >= 0.5
    assert pres["total_km"] == pytest.approx(10.0, abs=0.05)
    assert "shortened" in pres["progression"]["reason"]


def test_extra_rep_is_dropped_when_even_minimal_warmup_does_not_fit():
    # cap 18 -> 7 km: 6 reps (8.0 km) cannot fit, so the last session's 5 reps are kept
    plan = generate_week(inputs(cfg=cfg_with(weekly_volume_floor_km=18), runs=light_runs()))
    pres = intervals_of(plan).prescription
    assert pres["structure"]["work"]["reps"] == 5
    assert pres["progression"]["rule"] == "intervals:volume_hold"
    assert "keep 5 reps" in pres["progression"]["reason"]
    assert (
        pres["structure"]["warmup"]["distance_km"] == 1.0
        and pres["structure"]["cooldown"]["distance_km"] == 0.5
    )
    dec = next(d for d in plan.decisions if d.rule == "intervals:volume_hold")
    assert "weekly cap" in dec.rationale
    assert plan.volume["cap_km"] == 18


def test_floor_cap_from_config_applies_to_the_whole_week():
    plan = generate_week(inputs(cfg=cfg_with(weekly_volume_floor_km=18), runs=light_runs()))
    assert plan.volume["cap_km"] == 18
    easy, long = plan.volume["planned_km"]["easy_run"], plan.volume["planned_km"]["long_run"]
    assert easy == 3 and long == 8  # minimums
