"""The advisor side of the progression engine v2: the allowlist keys, the mapping of the new
engine triggers onto the existing proposal types, the `progression` input section and the
frozen system prompt."""

from __future__ import annotations

from types import SimpleNamespace

import yaml

from apps.fitness.advisor import prompt, summary
from apps.fitness.advisor import service as S
from apps.fitness.advisor import validator as V
from apps.fitness.engine.store import service as engine
from apps.fitness.settings import FITNESS_DIR
from tests.support.world import TODAY

V6 = yaml.safe_load((FITNESS_DIR / "config" / "engine.v6.yaml").read_text())


def check(param, value):
    return V.validate_change(param, value, V.Context(dict(V6)))


def test_v2_keys_are_on_the_allowlist_with_bounds_and_risks():
    ok = {
        "strength.stall_exposures_2x": (5, "medium"),
        "strength.stall_min_days": (14, "medium"),
        "strength.stall_reset_small_pct": (7.5, "medium"),
        "strength.multi_stall_lifts": (4, "medium"),
        "strength.rung_min_reps": (7, "medium"),
        "strength.rep_ceiling_cap": (14, "medium"),
        "strength.deload.sets_pct": (40, "medium"),
        "running.interval_pace_tolerance_pct": (4, "medium"),
        "running.volume.cap_abs_km": (3, "high"),
        "running.volume.single_run_factor": (1.2, "high"),
        "running.long_run.step_back_every": (3, "medium"),
        "running.vdot.max_step": (1.0, "high"),
        "running.vdot.max_per_4w": (0.5, "medium"),
        "running.easy.strides.reps": (4, "low"),
        "running.threshold.reps_max": (6, "low"),
        "running.quality.phase_weeks.threshold": (8, "medium"),
    }
    for param, (value, risk) in ok.items():
        v = check(param, value)
        assert v.valid and v.risk == risk, (param, v)
    assert check("running.quality.phase_weeks.threshold", 8).current == 12
    for param, value in (
        ("strength.stall_exposures_2x", 9),
        ("strength.rep_ceiling_cap", 25),
        ("running.vdot.max_step", 2),
        ("running.volume.single_run_factor", 1.5),
        ("running.quality.phase_weeks.threshold", 30),
        ("strength.rir_target", [1, 2]),  # never: the athlete's fixed 0-1 RIR
    ):
        assert not check(param, value).valid, param
    assert "running.vdot.max_step" in V.describe_allowlist()


def test_volume_hold_keys_are_on_the_allowlist():
    off = check("running.volume.hold_last_week", False)
    assert off.valid and off.risk == "low" and off.current is True  # default on
    assert not check("running.volume.hold_last_week", True).valid  # already true
    assert not check("running.volume.hold_last_week", "no").valid
    assert check("running.volume.hold_limit_factor", 1.2).risk == "high"
    assert not check("running.volume.hold_limit_factor", 1.5).valid
    assert "running.volume.hold_last_week" in V.describe_allowlist()


def _row(type_, payload):
    return SimpleNamespace(type=type_, payload=payload, day=None, subject_id="1", id=1)


def test_new_engine_triggers_map_onto_existing_proposal_types():
    assert S.TRIGGER_MAP["multi_lift_stall"] == S.LIFT_STALL
    assert S.TRIGGER_MAP["fourth_run"] == S.RUNNING_STAGNATION
    title, detail, _ref = S._describe(
        _row(
            "multi_lift_stall",
            {"lifts": ["A", "B", "C"], "window": {"start": "2026-10-26", "until": "2026-11-01"}},
        )
    )
    assert title == "3 lifts stalled together" and "retest of the old loads" in detail
    title, detail, _ref = S._describe(
        _row("fourth_run", {"weeks_consistent": 8, "mean_km_4w": 29, "longest_km_4w": 15})
    )
    assert title == "Ready for a 4th run?" and "never Tuesday" in detail
    title, detail, _ref = S._describe(
        _row(
            "lift_stall", {"variant": "Bench", "reason": "x.", "proposal": "rep_range_or_variation"}
        )
    )
    assert "rep range change or a close variation" in detail


def test_the_coach_input_has_a_progression_section(db, world, env):
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()
    doc = summary.build_input(db, trigger={"type": "weekly_review"}, question=None, settings=env)
    prog = doc["progression"]
    assert set(prog) >= {"lifts", "stall_ladder", "censored_misses_14d", "deload", "running"}
    assert prog["lifts"] and all(len(v) == 3 for v in prog["lifts"].values())
    states = {v[0] for v in prog["lifts"].values()}
    assert states <= {"progressing", "grinding", "stalled", "deload", "retest", "rebuild"}
    run = prog["running"]
    assert run["quality"]["phase"] == "threshold" and run["quality"]["session_type"]
    assert "cap" in run["volume"]["reasoning"]


def test_system_prompt_knows_the_v2_rules_and_the_coach_role():
    p = prompt.SYSTEM_PROMPT
    for text in (
        "0-1 reps in reserve",
        "Multi-lift stalls",
        "Variation choice",
        "Pain, illness or injury",
        "Running vs lifting trade-offs",
        "judged by PACE",
        "`progression`",
    ):
        assert text in p, text
