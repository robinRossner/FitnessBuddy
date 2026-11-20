"""The weekly planner on synthetic plain data (no database), progression engine v2."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from apps.fitness.engine.core import lifting as L
from apps.fitness.engine.core import quality as Q
from apps.fitness.engine.core import running as R
from apps.fitness.engine.core import vdot as V
from apps.fitness.engine.core.config import load_engine_config
from apps.fitness.engine.core.planner import LiftContext, PlanInputs, VariantInfo, generate_week
from apps.fitness.engine.core.program import Program, ProgramExercise
from apps.fitness.engine.core.readiness import HealthDay
from apps.fitness.settings import FITNESS_DIR

CFG = load_engine_config(FITNESS_DIR / "config" / "engine.v6.yaml")
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


def interval_run(day: date, reps=5, hr=162) -> R.RunData:
    laps = [lap(311, 138, 1000, "warmup")]
    for _ in range(reps):
        laps += [lap(250, hr, 1000, "active"), lap(370, 146, 400, "recovery")]
    laps[-1] = lap(305, 151, 1000, "cooldown")
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


def test_lifting_prescription_has_targets_state_and_previous_performance():
    plan = generate_week(inputs())
    mon = day_of(plan, "mon_upper")
    assert sets_of(mon, "Bench") == [(60, 11), (60, 10), (60, 9)]  # last 10/9/8 -> +1
    # all 12 @ 50 -> next rung 55 (+10 %): Epley predicts ~8 reps there, not blindly 7
    assert sets_of(mon, "Lat Pulldown") == [(55.0, 8)] * 3
    bench = mon.prescription["exercises"][0]
    assert bench["last"]["sets"][0] == {"weight": 60, "reps": 10, "duration_s": None}
    assert bench["rep_range"] == [7, 12] and bench["rule"] == "double_progression:add_reps"
    assert bench["effective_rep_range"] == [7, 12] and bench["progression_state"] == "grinding"
    assert bench["progression_note"].startswith("+1 rep per set")
    pull = mon.prescription["exercises"][1]
    assert pull["progression_state"] == "progressing" and "Epley" in pull["progression_note"]
    assert mon.prescription["gym"] == "Gym 3"


def test_both_upper_days_use_the_same_program():
    plan = generate_week(inputs())
    a, b = day_of(plan, "mon_upper"), day_of(plan, "thu_upper")
    assert [e["name"] for e in a.prescription["exercises"]] == [
        e["name"] for e in b.prescription["exercises"]
    ]


def test_rung_rule_shows_the_raised_rep_ceiling_in_the_plan():
    stack = VariantInfo(
        2,
        "k2",
        "Lat Pulldown (Cable) @ Gym 3",
        "Lat Pulldown (Cable)",
        "lat_pulldown",
        "Lat Pulldown",
        "cable",
        "total",
        [40.0, 46.0],
    )
    d = MON - timedelta(days=4)
    ctx = LiftContext(
        stack, [expo(d - timedelta(days=3), 40, [11, 11, 11]), expo(d, 40, [12, 12, 12])]
    )
    plan = generate_week(inputs(lifts=lifts(**{"Lat Pulldown (Cable)": ctx})))
    ex = day_of(plan, "mon_upper").prescription["exercises"][1]
    assert ex["effective_rep_range"] == [7, 14] and sets_of(day_of(plan, "mon_upper"), "Lat")[
        0
    ] == (40, 13)
    assert "ceiling 14" in ex["progression_note"]


# --------------------------------------------------------------------------- running


def test_first_quality_session_starts_the_threshold_phase():
    plan = generate_week(inputs())
    fri = day_of(plan, "fri_intervals")
    pres = fri.prescription
    work = pres["structure"]["work"]
    assert pres["structure"]["session_type"] == "threshold"
    assert (pres["phase"], pres["phase_week"]) == ("threshold", 1)
    assert work["reps"] == 3 and work["distance_m"] == 1000
    assert 280 < work["pace_s_per_km"] < 310  # T pace, ~5:00/km for VDOT ~42
    assert pres["structure"]["recovery"]["duration_s"] == 90
    assert pres["progression"]["rule"] == "quality:start" and pres["progression_note"]
    assert fri.title.startswith("Threshold 3x1000")
    assert work["hr"]["zone"] == "Z4" and "ceiling" in work["hr"]["note"]


def quality_inputs(pace: float = 300.0, reps: int = 3, **kw):
    last_fri = MON - timedelta(days=3)
    log = [Q.QualityRecord(last_fri, "threshold", reps, 1000, 90, pace)]
    return inputs(quality_log=log, cycle_start=MON - timedelta(weeks=1), **kw)


def test_quality_session_passed_on_pace_adds_one_rep():
    plan = generate_week(quality_inputs())  # reps ran at 4:10 vs 5:00: faster is fine
    pres = day_of(plan, "fri_intervals").prescription
    assert pres["structure"]["work"]["reps"] == 4
    assert (
        pres["progression"]["rule"] == "quality:add_rep" and pres["progression"]["lever"] == "reps"
    )
    assert pres["progression_note"] == "+1 rep: last session passed on pace"
    assert (pres["phase"], pres["phase_week"]) == ("threshold", 2)
    assert pres["progression"]["last_session"]["verdict"] == "pass"


def test_quality_session_missing_the_pace_is_repeated():
    plan = generate_week(quality_inputs(pace=240))  # target 4:00, reps ran 4:10 (+4 %)
    pres = day_of(plan, "fri_intervals").prescription
    assert (
        pres["structure"]["work"]["reps"] == 3 and pres["progression"]["rule"] == "quality:repeat"
    )
    assert pres["progression"]["last_session"]["verdict"] == "fail"


def test_a_missed_quality_session_is_repeated_and_the_phase_waits():
    last_fri = MON - timedelta(days=3)
    log = [Q.QualityRecord(last_fri, "threshold", 4, 1000, 90, 300)]
    runs = [r for r in runs_history() if r.day != last_fri]
    plan = generate_week(inputs(quality_log=log, runs=runs, cycle_start=MON - timedelta(weeks=1)))
    pres = day_of(plan, "fri_intervals").prescription
    assert (
        pres["structure"]["work"]["reps"] == 4
        and pres["progression"]["rule"] == "quality:repeat_missed"
    )
    assert pres["phase_week"] == 1


def test_easy_run_has_strides_and_phase_fields():
    plan = generate_week(inputs())
    easy = day_of(plan, "wed_easy_run").prescription
    assert easy["hr"]["high"] == 152 and easy["hr"]["low"] == 124 and easy["distance_km"] == 7
    assert (
        easy["structure"]["strides"]["reps"] == 6
        and easy["structure"]["strides"]["duration_s"] == 20
    )
    assert easy["phase"] == "threshold" and "strides" in easy["progression_note"]
    # the strides are inside the 7 km (weekly km and cap include them): 6 x (20 s + 60 s jog)
    pace = (easy["pace"]["min_s_per_km"] + easy["pace"]["max_s_per_km"]) / 2
    est = 6 * (20 / (pace * 0.65) + 60 / pace)
    assert easy["structure"]["strides"]["est_km"] == round(est, 1) and 1.0 < est < 2.0
    assert easy["last_easy_run"]["passed"] is True
    long = day_of(plan, "sun_long_run").prescription
    assert long["distance_km"] >= 10 and long["hr"]["high"] == 152
    assert long["progression_note"] and long["step_back"] is False


def test_wednesday_easy_run_after_tuesday_legs_is_exempt_from_a_hard_hr_fail():
    tue = MON - timedelta(days=6)
    hot = easy_run(tue + timedelta(days=1), 7, hr=162)
    runs = [r for r in runs_history() if r.day < hot.day] + [hot]
    plan = generate_week(inputs(runs=runs, actual_lower_days={tue}))
    last = day_of(plan, "wed_easy_run").prescription["last_easy_run"]
    assert last["date"] == hot.day.isoformat() and last["passed"] and last["exempt"]
    assert last["notes"]
    plain = generate_week(inputs(runs=runs))
    assert not day_of(plain, "wed_easy_run").prescription["last_easy_run"]["passed"]


def test_no_quality_session_in_the_first_week_back_after_a_long_break():
    runs = [easy_run(MON - timedelta(days=40), 8), easy_run(MON - timedelta(days=20), 6)]
    plan = generate_week(inputs(runs=runs))
    fri = day_of(plan, "fri_intervals")
    assert fri.kind == "easy_run" and "back after a break" in fri.title
    assert fri.prescription["structure"]["strides"]["reps"] == 6


def test_vdot_initial_record_is_logged_once():
    plan = generate_week(inputs())
    rec = [d for d in plan.decisions if d.rule == "running:vdot"]
    assert rec and rec[0].output["vdot_record"]["source"] == "initial"
    log = [V.VdotRecord(MON - timedelta(days=1), 42.0, "initial")]
    raw = CFG.model_dump()  # the history's interval reps would measure a higher VDOT: off here
    raw["running"]["vdot"]["from_intervals"] = {"enabled": False}
    again = generate_week(inputs(vdot_log=log, cfg=CFG.__class__.model_validate(raw)))
    assert not [d for d in again.decisions if d.rule == "running:vdot"]
    assert again.pace["vdot"] == 42.0  # the stored record wins over the fit
    measured = generate_week(inputs(vdot_log=log))
    rec = [d for d in measured.decisions if d.rule == "running:vdot"]
    assert [r.output["vdot_record"]["source"] for r in rec] == ["interval_reps"]


def test_decisions_are_logged_with_rules_and_inputs():
    plan = generate_week(inputs())
    kinds = {(d.kind, d.rule) for d in plan.decisions}
    assert ("progression", "double_progression:add_reps") in kinds
    assert ("progression", "double_progression:increase_load") in kinds
    assert ("running", "quality:start") in kinds and ("readiness", "readiness:score") in kinds
    assert ("running", "running:quality_phase") in kinds
    prog = next(d for d in plan.decisions if d.kind == "progression" and d.subject_id == "2")
    assert prog.inputs["rep_range"] == [7, 12] and prog.output["sets"] and prog.rationale
    assert prog.output["state"] == "progressing" and "censored" in prog.inputs


# readiness adjustments


def test_yellow_today_drops_a_set_and_turns_nothing_else():
    plan = generate_week(inputs(health=health(today_row(hrv_ms=61, resting_hr=59))))
    mon = day_of(plan, "mon_upper")
    assert plan.readiness.color == "yellow"
    assert len(sets_of(mon, "Bench")) == 2 and len(sets_of(mon, "Lat")) == 2
    assert mon.adjustments and "-1 working set" in mon.adjustments[0] and "(-1 set)" in mon.title
    assert mon.readiness["color"] == "yellow" and "HRV" in mon.readiness["reason"]
    thu = day_of(plan, "thu_upper")
    assert len(sets_of(thu, "Bench")) == 3 and thu.readiness["color"] is None


def test_yellow_converts_the_quality_session_to_easy_plus_strides():
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
    assert "easy + strides" in day.adjustments[0] and day.prescription["hr"]["high"] == 152
    assert day.prescription["structure"]["strides"]["reps"] == 6


def test_red_today_replaces_lifting_by_rest_and_flags_conflict_when_progression_wants_more():
    row = today_row(hrv_ms=52, sleep_s=7 * 3600)
    plan = generate_week(inputs(health=health(row)))
    mon = day_of(plan, "mon_upper")
    assert plan.readiness.color == "red" and mon.kind == "rest"
    assert mon.prescription["type"] == "rest" and mon.prescription["original"]["kind"] == "upper"
    # The app decodes `optional` as a bool (a string here broke the sync pull).
    assert mon.prescription["optional"] is True
    assert mon.prescription["suggestion"] == "mobility 10-15 min"
    conflicts = [t for t in plan.triggers if t.type == "rule_conflict"]
    assert conflicts and "Lat Pulldown (Cable) @ Gym 3" in conflicts[0].payload["items"]


def test_red_turns_hard_run_into_short_easy_run_or_rest():
    fri = MON + timedelta(days=4)
    p = generate_week(
        quality_inputs(
            today=fri, health=health(HealthDay(fri, resting_hr=53, hrv_ms=52, sleep_s=8 * 3600))
        )
    )
    day = day_of(p, "fri_intervals")
    assert day.kind == "easy_run" and day.prescription["distance_km"] == 4
    assert day.prescription["alternative"] == "rest"
    assert any(t.type == "rule_conflict" for t in p.triggers)  # the session wanted +1 rep


def test_unknown_readiness_changes_nothing():
    plan = generate_week(inputs(health=[]))
    assert all(not d.adjustments for d in plan.days)


# deloads, stalls, anomalies


def red_history(n_days: int) -> list[HealthDay]:
    rows = health(None, n=14)
    out = []
    for h in rows:
        if (MON - h.day).days < n_days:
            h = HealthDay(h.day, resting_hr=69, hrv_ms=35, sleep_s=8 * 3600, sleep_score=85)
        out.append(h)
    return out


def test_four_red_days_trigger_a_reactive_deload_with_loads_kept():
    hist = [
        *red_history(4),
        HealthDay(MON, resting_hr=69, hrv_ms=35, sleep_s=8 * 3600, sleep_score=85),
    ]
    plan = generate_week(inputs(today=MON, health=hist))
    thu = day_of(plan, "thu_upper")
    assert (
        thu.prescription["deload"]["active"]
        and thu.prescription["deload"]["reason"] == "red_streak"
    )
    assert sets_of(thu, "Bench") == [(60, 10), (60, 9)]  # half the sets (rounded up), same load
    assert "deload" in thu.title
    assert [e["progression_state"] for e in thu.prescription["exercises"]] == ["deload"] * 2
    assert [t for t in plan.triggers if t.type == "deload"]
    assert any(d.kind == "deload" for d in plan.decisions)


def test_three_red_days_is_no_deload():
    plan = generate_week(inputs(health=[*red_history(3), today_row(hrv_ms=35, resting_hr=69)]))
    assert not any(t.type == "deload" for t in plan.triggers)


def stalled(weight: float, start: date, n=6, step=3.5, reps=(10, 9, 9)) -> list[L.Exposure]:
    return [
        expo(
            start + timedelta(days=round(i * step)), weight, list(reps), weight * (1 + reps[0] / 30)
        )
        for i in range(n)
    ]


def test_one_stalled_lift_takes_the_first_ladder_step():
    hist = stalled(60, MON - timedelta(days=21))
    plan = generate_week(inputs(lifts=lifts(**{"Bench Press (Barbell)": LiftContext(BENCH, hist)})))
    mon = day_of(plan, "mon_upper")
    assert [w for w, _r in sets_of(mon, "Bench")] == [55.0] * 3  # 60 x 0.95 -> 55 on the grid
    ex = mon.prescription["exercises"][0]
    assert ex["progression_state"] == "rebuild" and ex["rule"] == "stall:reset_small"
    dec = next(x for x in plan.decisions if x.rule == "stall:reset_small")
    assert dec.output["ladder"] == {
        "day": MON.isoformat(),
        "step": 1,
        "load": 55.0,
        "peak_e1rm": 80.0,
        "from_load": 60.0,
    }
    thu = next(
        x for x in plan.decisions if x.rule == "stall:reset_small" and x.slot_id == "thu_upper"
    )
    assert thu.output["ladder"]["day"] == MON.isoformat()  # one ladder step for the week
    assert not [t for t in plan.triggers if t.type == "lift_stall"]  # step 1 needs no coach


def test_second_stall_asks_the_coach_for_a_rep_range_or_variation():
    hist = stalled(60, MON - timedelta(days=50)) + stalled(55, MON - timedelta(days=21))
    ladder = [L.LadderEvent(MON - timedelta(days=21), 1, 55.0, 80.0, 60.0)]
    ctx = LiftContext(BENCH, hist, ladder)
    plan = generate_week(inputs(lifts=lifts(**{"Bench Press (Barbell)": ctx})))
    ex = day_of(plan, "mon_upper").prescription["exercises"][0]
    assert ex["progression_state"] == "stalled" and ex["rule"] == "stall:advisor"
    trig = [t for t in plan.triggers if t.type == "lift_stall"]
    assert len(trig) == 1 and trig[0].payload["proposal"] == "rep_range_or_variation"


def test_three_stalled_lifts_become_one_soft_deload_and_a_retest():
    d0 = MON - timedelta(days=21)
    squat_hist = stalled(90, MON - timedelta(days=34), n=5, step=7)  # Tuesdays, no runs before
    ctxs = {
        "Bench Press (Barbell)": LiftContext(BENCH, stalled(60, d0)),
        "Lat Pulldown (Cable)": LiftContext(PULL, stalled(50, d0)),
        "Squat (Barbell)": LiftContext(SQUAT, squat_hist),
    }
    plan = generate_week(inputs(lifts=ctxs))
    mon, tue = day_of(plan, "mon_upper"), day_of(plan, "tue_lower")
    assert mon.prescription["deload"]["reason"] == "multi_lift_stall"
    assert sets_of(mon, "Bench") == [(60, 10), (60, 9)]  # no -5 %: half the sets, same load
    assert sets_of(tue, "Squat") == [(90, 10), (90, 9)]
    assert not any(d.rule.startswith("stall:") for d in plan.decisions)
    trig = [t for t in plan.triggers if t.type == "multi_lift_stall"]
    assert len(trig) == 1 and len(trig[0].payload["lifts"]) == 3
    win = next(d for d in plan.decisions if d.rule == "deload:multi_lift_stall").output["window"]
    assert win["start"] == MON.isoformat() and win["until"] == (MON + timedelta(days=6)).isoformat()
    assert win["retest"]["1"]["load"] == 60
    # next week: the retest of the old loads
    w = L.DeloadWindow(
        MON,
        MON + timedelta(days=6),
        "multi_lift_stall",
        {1: L.Retest(60, (10, 9, 9)), 2: L.Retest(50, (10, 9, 9)), 3: L.Retest(90, (10, 9, 9))},
    )
    nxt = MON + timedelta(days=7)
    plan2 = generate_week(inputs(lifts=ctxs, windows=[w], today=nxt, week_start=nxt))
    mon2 = day_of(plan2, "mon_upper")
    assert sets_of(mon2, "Bench") == [(60, 11), (60, 10), (60, 10)]
    assert mon2.prescription["exercises"][0]["progression_state"] == "retest"
    assert not [t for t in plan2.triggers if t.type == "multi_lift_stall"]


def _three_stalled():
    d0 = MON - timedelta(days=21)
    squat_hist = stalled(90, MON - timedelta(days=34), n=5, step=7)
    return {
        "Bench Press (Barbell)": LiftContext(BENCH, stalled(60, d0)),
        "Lat Pulldown (Cable)": LiftContext(PULL, stalled(50, d0)),
        "Squat (Barbell)": LiftContext(SQUAT, squat_hist),
    }


def test_a_cancelled_deload_week_goes_straight_to_the_retest_with_normal_sets():
    ctxs = _three_stalled()
    plan = generate_week(inputs(lifts=ctxs, cancelled_weeks={MON: "feeling fine"}))
    mon, tue, thu = (day_of(plan, s) for s in ("mon_upper", "tue_lower", "thu_upper"))
    assert mon.prescription["deload"] is None and "deload" not in mon.title
    assert sets_of(mon, "Bench") == [(60, 11), (60, 10), (60, 10)]  # 3 sets, beat 10/9/9
    assert sets_of(tue, "Squat") == [(90, 11), (90, 10), (90, 10)]
    ex = mon.prescription["exercises"][0]
    assert ex["progression_state"] == "retest"
    assert ex["progression_note"] == "Retest at old load 60 kg: beat 10/9/9"
    assert sets_of(thu, "Bench") == sets_of(mon, "Bench")
    rec = next(d for d in plan.decisions if d.rule == "deload:multi_lift_stall")
    assert "cancelled by the user" in rec.rationale
    assert rec.output["window"]["deload"] is False and rec.output["window"]["cancelled"] is True
    assert not any(d.rule.startswith("stall:") for d in plan.decisions)


def test_cancelling_a_stored_deload_retests_the_stalled_lifts_and_progresses_the_rest():
    from apps.fitness.engine.core.windows import apply_cancellations

    ctxs = _three_stalled()
    ctxs["Squat (Barbell)"] = LiftContext(SQUAT, [expo(MON - timedelta(days=6), 90, [10, 10, 9])])
    w = L.DeloadWindow(
        MON,
        MON + timedelta(days=6),
        "multi_lift_stall",
        {1: L.Retest(60, (10, 9, 9)), 2: L.Retest(50, (10, 9, 9))},
    )
    soft = generate_week(inputs(lifts=ctxs, windows=[w]))
    assert soft.days[0].prescription["deload"]["active"]
    cancelled = apply_cancellations([w], {MON: None})
    assert [(c.deload, c.cancelled, c.until) for c in cancelled] == [
        (False, True, MON - timedelta(days=1))
    ]
    assert apply_cancellations(cancelled, {MON: None}) == cancelled  # idempotent
    plan = generate_week(inputs(lifts=ctxs, windows=cancelled, cancelled_weeks={MON: None}))
    mon, tue = day_of(plan, "mon_upper"), day_of(plan, "tue_lower")
    assert [e["progression_state"] for e in mon.prescription["exercises"]] == ["retest"] * 2
    assert sets_of(mon, "Lat Pulldown") == [(50, 11), (50, 10), (50, 10)]
    assert mon.prescription["deload"] is None
    sq = tue.prescription["exercises"][0]  # not stalled: double progression, full sets
    assert sq["progression_state"] != "retest" and len(sq["sets"]) == 3
    assert sets_of(tue, "Squat") == [(90, 11), (90, 11), (90, 10)]
    cd = next(d for d in plan.decisions if d.rule == "deload:cancelled")
    assert cd.output["lifts_retesting"] == ["Bench Press (Barbell)", "Lat Pulldown (Cable) @ Gym 3"]
    assert "window" not in cd.output  # a window row would be read back as a deload window
    assert "progress by the normal rules" in cd.rationale


def test_the_running_step_back_no_longer_lines_up_with_a_cancelled_deload():
    w = L.DeloadWindow(MON, MON + timedelta(days=6), "multi_lift_stall")
    cfg = _with_running(long_run={"step_back_every": 3})
    base = {"cfg": cfg, "last_step_back": MON - timedelta(days=14)}  # 2 of 3 weeks: one early
    soft = generate_week(inputs(windows=[w], **base))
    long = day_of(soft, "sun_long_run").prescription
    assert (
        long["step_back"] is True and "aligned with the lifting deload" in long["progression_note"]
    )
    for cancelled in (replace(w, deload=False, cancelled=True), None):
        ws = [] if cancelled is None else [cancelled]
        plan = generate_week(inputs(windows=ws, cancelled_weeks={MON: None}, **base))
        assert day_of(plan, "sun_long_run").prescription["step_back"] is False


def test_fatigue_index_two_weeks_in_a_row_triggers_a_reactive_deload():
    def falling(weight, start, step):
        out = [
            expo(start, weight, [12, 11, 11]),
            expo(start + timedelta(days=3), weight, [12, 11, 11]),
        ]
        reps = [11, 11, 10]
        for i in range(4):
            day = MON - timedelta(days=14) + timedelta(days=round(i * step))
            reps = [max(r - 1, 1) if j == i % 3 else r for j, r in enumerate(reps)]
            out.append(expo(day, weight, list(reps)))
        return out

    ctxs = {
        "Bench Press (Barbell)": LiftContext(BENCH, falling(60, MON - timedelta(days=24), 3.5)),
        "Lat Pulldown (Cable)": LiftContext(PULL, falling(50, MON - timedelta(days=24), 3.5)),
        "Squat (Barbell)": LiftContext(SQUAT, [expo(MON - timedelta(days=13), 90, [10, 10, 9])]),
    }
    plan = generate_week(inputs(lifts=ctxs))
    mon = day_of(plan, "mon_upper")
    assert mon.prescription["deload"]["reason"] == "fatigue_index"
    assert [
        t for t in plan.triggers if t.type == "deload" and t.payload["reason"] == "fatigue_index"
    ]


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


# volume, long run, frozen, missing


def test_weekly_cap_from_the_4_week_mean_and_the_18_km_target():
    light = [easy_run(MON - timedelta(days=7 * w - 2), 6) for w in range(1, 5)]  # 6 km/week
    plan = generate_week(inputs(runs=light))
    v = plan.volume
    assert v["cap_km"] == pytest.approx(8.0)  # max(6 x 1.1, 6 + 2)
    assert v["mean_km_4w"] == 6.0 and v["target_km"] == 18 and v["single_run_cap_km"] == 6.6
    assert "target 18 km/week is reached step by step" in v["reasoning"]
    assert sum(v["planned_km"].values()) <= 8.0 + 4.5  # quality minimum + the 3 km floors
    long = day_of(plan, "sun_long_run").prescription["distance_km"]
    assert long <= 6.6  # never above 1.1 x the longest run of 30 days


def _with_running(**sections):
    raw = CFG.model_dump()
    for name, over in sections.items():
        raw["running"][name] = {**raw["running"].get(name, {}), **over}
    return CFG.__class__.model_validate(raw)


def _hold_runs(hr=140):
    """Last week 20 km (8 km easy + 12 km long), before that 14, 8, 6 km."""
    mon = MON - timedelta(days=7)
    runs = [easy_run(mon + timedelta(days=2), 8, hr), easy_run(mon + timedelta(days=6), 12, hr)]
    for w, km in ((2, 14), (3, 8), (4, 6)):
        runs.append(easy_run(MON - timedelta(days=7 * w - 2), km, 140))
    return sorted(runs, key=lambda r: r.day)


def test_the_cap_holds_last_weeks_completed_volume_up_to_the_limit():
    plan = generate_week(inputs(runs=_hold_runs()))
    v = plan.volume
    assert v["mean_km_4w"] == 12.0
    assert v["hold"]["hold_km"] == pytest.approx(15.4) and v["hold"]["limit_km"] == 15.4
    assert v["cap_km"] == pytest.approx(15.4)  # max(12 x 1.1, 12 + 2, 15.4): was 14
    assert "cap max(12 x 1.1, 12 + 2, hold 15.4) = 15.4 km" in v["reasoning"]
    assert "last week ran 20 km, held up to 15.4 km" in v["reasoning"]
    assert v["single_run_cap_km"] == 15.4  # the single-run cap stays 1.1 x the longest run (14)
    assert "single run <= 15.4 km" in v["reasoning"]
    assert day_of(plan, "sun_long_run").prescription["distance_km"] <= 15.4


def test_no_hold_after_a_week_with_an_easy_run_hr_fail():
    plan = generate_week(inputs(runs=_hold_runs(hr=175)))
    v = plan.volume
    assert v["hold"]["hold_km"] is None and "HR fail" in v["hold"]["blocked"]
    assert v["cap_km"] == pytest.approx(14.0)
    assert "hold" not in v["reasoning"].split("= 14 km")[0]
    assert "no hold of last week's 20 km" in v["reasoning"]


def test_hold_can_be_switched_off():
    cfg = _with_running(volume={"hold_last_week": False})
    plan = generate_week(inputs(cfg=cfg, runs=_hold_runs()))
    assert plan.volume["hold"] is None and plan.volume["cap_km"] == pytest.approx(14.0)
    assert "hold" not in plan.volume["reasoning"]


def test_cap_below_the_template_minimum_raises_a_rule_conflict_trigger():
    light = [easy_run(MON - timedelta(days=7 * w - 2), 3) for w in range(1, 5)]
    plan = generate_week(inputs(runs=light))
    conflicts = [
        t for t in plan.triggers if t.type == "rule_conflict" and "volume_cap" in t.dedupe_key
    ]
    assert conflicts and conflicts[0].payload["cap_km"] == pytest.approx(5.0)


def test_long_run_step_back_every_fourth_week():
    plan = generate_week(inputs(cycle_start=MON - timedelta(weeks=3)))
    long = day_of(plan, "sun_long_run").prescription
    assert long["step_back"] is True and "step-back week" in long["progression_note"]
    assert long["distance_km"] == 8.0  # longest recent 11 km x 0.75 -> 8.0 (0.5 km grid)
    plan = generate_week(
        inputs(cycle_start=MON - timedelta(weeks=3), last_step_back=MON - timedelta(weeks=1))
    )
    assert day_of(plan, "sun_long_run").prescription["step_back"] is False


def test_long_run_step_back_moves_into_a_lifting_deload_week():
    hist = [
        *red_history(4),
        HealthDay(MON, resting_hr=69, hrv_ms=35, sleep_s=8 * 3600, sleep_score=85),
    ]
    p = generate_week(inputs(health=hist, cycle_start=MON - timedelta(weeks=2)))
    long = day_of(p, "sun_long_run").prescription
    assert (
        long["step_back"] is True and "aligned with the lifting deload" in long["progression_note"]
    )


def test_fourth_run_is_suggested_after_eight_consistent_weeks():
    runs = []
    for w in range(1, 9):
        mon = MON - timedelta(days=7 * w)
        runs += [
            easy_run(mon + timedelta(days=2), 8),
            interval_run(mon + timedelta(days=4)),
            easy_run(mon + timedelta(days=6), 14),
        ]
    plan = generate_week(inputs(runs=sorted(runs, key=lambda r: r.day)))
    trig = [t for t in plan.triggers if t.type == "fourth_run"]
    assert trig and trig[0].payload["mean_km_4w"] >= 28
    assert len(plan.days) == 7  # suggested to the coach, never added
    few = generate_week(inputs(runs=sorted(runs, key=lambda r: r.day)[3:]))
    assert not [t for t in few.triggers if t.type == "fourth_run"]


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


def cfg_with(**running) -> object:
    raw = CFG.model_dump(mode="json")
    raw["running"] = {**CFG.running, **running}
    return CFG.__class__.model_validate(raw)


def test_quality_session_keeps_full_size_when_the_budget_is_roomy():
    plan = generate_week(quality_inputs())
    s = day_of(plan, "fri_intervals").prescription["structure"]
    assert s["work"]["reps"] == 4 and s["warmup"]["distance_km"] == 2.0
    assert s["cooldown"]["distance_km"] == 1.5


def test_warmup_and_cooldown_shrink_and_the_extra_rep_waits_when_the_cap_is_tight():
    runs = [interval_run(MON - timedelta(days=3))]  # ~8.6 km last week only: cap ~ 4.15 km
    log = [Q.QualityRecord(MON - timedelta(days=3), "threshold", 3, 1000, 90, 300)]
    plan = generate_week(inputs(runs=runs, quality_log=log, cycle_start=MON - timedelta(weeks=1)))
    pres = day_of(plan, "fri_intervals").prescription
    assert pres["structure"]["work"]["reps"] == 3
    assert pres["progression"]["rule"] == "quality:volume_hold"
    assert pres["structure"]["warmup"]["distance_km"] == 1.0
    assert pres["structure"]["cooldown"]["distance_km"] == 0.5
