"""The advisor's allowlist: types, limits, steps, swaps, risk classification, and that weights,
paces and heart-rate numbers can never be proposed."""

from __future__ import annotations

import pytest

from apps.fitness.advisor import validator as V


def check(ctx, param, value):
    return V.validate_change(param, value, ctx)


# --------------------------------------------------------------------------- allowlist


@pytest.mark.parametrize(
    "param",
    [
        "running.heart_rate.lthr",
        "running.heart_rate.max_hr",
        "running.vdot.seed_race.time_s",
        "running.vdot.pace_step",
        "strength.e1rm.formula",
        "weekly_template.mon",
        "advisor.approval_mode",  # Claude may not change its own permission level
        "advisor.max_calls_per_week",
        "approval_mode",
        "target_weight",
        "sets.0.target_weight",
        "exercise_overrides.bench_press.target_weight",
        "exercise_overrides.bench_press.pace_s_per_km",
        "exercise_overrides.bench_press.increment_kg",
        "exercise_overrides.Bench Press.rep_range",
        "exercise_overrides..rep_range",
        "running.intervals.target_pace_s_per_km",
        "running.pace_model",
        "version",
        "",
        "strength",
    ],
)
def test_anything_off_the_allowlist_is_rejected(ctx, param):
    v = check(ctx, param, 1)
    assert not v.valid and "allowlist" in v.error
    assert v.risk is None


def test_non_string_param_is_rejected(ctx):
    assert not check(ctx, None, 1).valid  # type: ignore[arg-type]
    assert not check(ctx, 12, 1).valid  # type: ignore[arg-type]


# --------------------------------------------------------------------------- numbers


@pytest.mark.parametrize(
    ("param", "good", "bad"),
    [
        ("strength.stall_exposures", 4, [1, 7, 5.5, "4", True, None, [4]]),
        ("strength.stall_reset_pct", 12.5, [4.9, 15.1, "10", False]),
        ("recovery.deload_red_days", 5, [1, 8, "x"]),
        ("running.weekly_volume_cap_pct", 12, [-1, 16, "12"]),
        ("running.weekly_volume_floor_km", 20, [9, 61, "20"]),
        ("running.long_run.cap_km", 23, [26, 7, 100]),
        ("running.long_run.step_km", 1.5, [0.4, 2.1]),
        ("running.intervals.reps_max", 7, [2, 11, 6.5]),
        ("running.intervals.rep_distance_m", 1200, [150, 2050, 1010]),
        ("running.intervals.recovery_distance_m", 300, [50, 850, 410]),
    ],
)
def test_numbers_respect_type_and_limits(ctx, param, good, bad):
    assert check(ctx, param, good).valid, check(ctx, param, good).error
    for value in bad:
        v = check(ctx, param, value)
        assert not v.valid, (param, value)
        assert v.error


def test_non_finite_numbers_are_rejected(ctx):
    for value in (float("nan"), float("inf")):
        assert not check(ctx, "running.weekly_volume_cap_pct", value).valid


def test_max_step_limits_a_single_change(ctx):
    # reset pct 10 -> 15 is exactly 5 (ok); the volume cap 10 -> 15 too; floor 0 -> 30 is too far
    assert check(ctx, "strength.stall_reset_pct", 15).valid
    assert check(ctx, "running.weekly_volume_cap_pct", 15).valid
    far = check(ctx, "running.weekly_volume_floor_km", 30)
    assert not far.valid and "at most" in far.error
    assert not check(ctx, "strength.stall_exposures", 6).valid  # 3 -> 6 is a step of 3 > 2
    assert not check(ctx, "running.long_run.cap_km", 25).valid  # 21 -> 25 is a step of 4 > 3
    assert not check(ctx, "running.intervals.reps_max", 10).valid  # 6 -> 10 is a step of 4 > 3


def test_no_change_is_rejected(ctx):
    v = check(ctx, "strength.stall_exposures", 3)
    assert not v.valid and "nothing to change" in v.error


def test_long_run_cap_never_above_25(ctx):
    for value in (25.5, 26, 30, 50):
        assert not check(ctx, "running.long_run.cap_km", value).valid
    assert check(ctx, "running.long_run.cap_km", 24).valid


def test_integer_valued_floats_are_accepted_for_int_params(ctx):
    v = check(ctx, "strength.stall_exposures", 4.0)
    assert v.valid and v.value == 4 and isinstance(v.value, int)


# --------------------------------------------------------------------------- increments


@pytest.mark.parametrize(
    ("key", "ok", "bad"),
    [
        ("barbell_kg", 2.5, [1.25, 3, 10]),
        ("smith_kg", 2.5, [2, 7.5]),
        ("plate_loaded_kg", 2.5, [4]),
        ("dumbbell_increment_kg", 2.5, [1, 3, 5]),
        ("default_stack_step_kg", 2.5, [1.25, 10]),
        ("bodyweight_added_kg", 1.25, [5, 0]),
        ("other_kg", 1.25, [2]),
    ],
)
def test_increments_only_from_the_equipment_options(ctx, key, ok, bad):
    assert check(ctx, f"increments.{key}", ok).valid
    for value in bad:
        assert not check(ctx, f"increments.{key}", value).valid


def test_increment_that_doubles_is_high_risk(ctx):
    assert check(ctx, "increments.barbell_kg", 2.5).risk == V.MEDIUM  # smaller step
    ctx.apply("increments.barbell_kg", 2.5)
    assert check(ctx, "increments.barbell_kg", 5.0).risk == V.HIGH  # doubled


# --------------------------------------------------------------------------- rep ranges


@pytest.mark.parametrize("value", [[7, 12], [3, 12], [7, 20], [8, 11]])
def test_rep_range_inside_3_to_20(ctx, value):
    v = check(ctx, "strength.rep_range", value)
    assert v.valid or "nothing to change" in v.error or "at most 3" in v.error


@pytest.mark.parametrize(
    "value", [[2, 12], [7, 21], [12, 7], [10, 10], [7], [7, 12, 15], "7-12", [7.5, 12], [None, 12]]
)
def test_rep_range_rejects_bad_shapes_and_limits(ctx, value):
    assert not check(ctx, "strength.rep_range", value).valid


def test_rep_range_low_must_be_below_high(ctx):
    v = check(ctx, "strength.rep_range", [9, 9])
    assert not v.valid and "below" in v.error


def test_rep_range_step_and_risk(ctx):
    assert check(ctx, "strength.rep_range", [8, 12]).risk == V.LOW  # one end +1
    assert check(ctx, "strength.rep_range", [8, 13]).risk == V.LOW  # both ends +1
    assert check(ctx, "strength.rep_range", [9, 12]).risk == V.MEDIUM  # +2
    assert check(ctx, "strength.rep_range", [10, 12]).risk == V.HIGH  # +3
    assert not check(ctx, "strength.rep_range", [11, 14]).valid  # +4 on an end


def test_per_exercise_rep_range(ctx):
    v = check(ctx, "exercise_overrides.bench_press.rep_range", [6, 10])
    assert v.valid and v.current == [7, 12] and v.risk == V.MEDIUM  # global range is the baseline
    assert v.label == "Bench Press (Barbell) rep range"
    # an exercise that is not in the program cannot be overridden
    assert not check(ctx, "exercise_overrides.calf_raise.rep_range", [8, 12]).valid
    assert not check(ctx, "exercise_overrides.bench_press.rep_range", [2, 12]).valid


def test_override_is_the_baseline_for_the_next_change(ctx):
    ctx.apply("exercise_overrides.bench_press.rep_range", [8, 12])
    v = check(ctx, "exercise_overrides.bench_press.rep_range", [9, 12])
    assert v.valid and v.current == [8, 12] and v.risk == V.LOW


def test_per_exercise_sets(ctx):
    assert check(ctx, "exercise_overrides.squat.sets", 4).risk == V.MEDIUM
    assert check(ctx, "exercise_overrides.squat.sets", 5).risk == V.HIGH
    assert not check(ctx, "exercise_overrides.squat.sets", 7).valid
    assert not check(ctx, "exercise_overrides.squat.sets", 0).valid
    assert not check(ctx, "exercise_overrides.squat.sets", 3).valid  # unchanged


# --------------------------------------------------------------------------- intervals


def test_interval_reps_plus_minus_one_is_low_risk(ctx):
    assert check(ctx, "running.intervals.reps_max", 7).risk == V.LOW
    assert check(ctx, "running.intervals.reps_max", 5).risk == V.LOW
    assert check(ctx, "running.intervals.reps_max", 8).risk == V.MEDIUM
    assert check(ctx, "running.intervals.reps_max", 9).risk == V.HIGH


def test_interval_reps_keep_their_order(ctx):
    # defaults: min 4, start 5, max 6
    v = check(ctx, "running.intervals.reps_max", 3)
    assert not v.valid and "reps_min" in v.error
    assert not check(ctx, "running.intervals.reps_min", 7).valid
    assert not check(ctx, "running.intervals.reps_start", 3).valid


def test_interval_distance_risk(ctx):
    assert check(ctx, "running.intervals.rep_distance_m", 1200).risk == V.MEDIUM
    assert check(ctx, "running.intervals.rep_distance_m", 1500).risk == V.HIGH


def test_volume_cap_raise_above_10_is_high_risk(ctx):
    assert check(ctx, "running.weekly_volume_cap_pct", 12).risk == V.HIGH
    assert check(ctx, "running.weekly_volume_cap_pct", 8).risk == V.MEDIUM


def test_only_the_narrow_set_is_low_risk(ctx):
    """auto-low-risk applies rep-range shifts of 1 and interval reps +-1, nothing else."""
    proposals = {
        "strength.stall_exposures": 4,
        "strength.stall_reset_pct": 12,
        "recovery.deload_red_days": 5,
        "running.weekly_volume_cap_pct": 8,
        "running.weekly_volume_floor_km": 20,
        "running.long_run.cap_km": 22,
        "running.long_run.step_km": 1.5,
        "increments.dumbbell_increment_kg": 2.5,
        "exercise_overrides.squat.sets": 4,
        "running.intervals.rep_distance_m": 900,
        "running.intervals.recovery_distance_m": 350,
    }
    for param, value in proposals.items():
        v = check(ctx, param, value)
        assert v.valid, (param, v.error)
        assert v.risk != V.LOW, param


# --------------------------------------------------------------------------- swaps


def swap(ctx, slug, name):
    return check(ctx, f"exercise_overrides.{slug}.variant", name)


def test_swap_to_an_active_variant_with_the_same_movement_pattern(ctx):
    v = swap(ctx, "incline_bench_press", "Incline Bench Press (Dumbbell)")
    assert v.valid and v.risk == V.MEDIUM
    assert v.current == "Incline Bench Press (Smith Machine)"
    assert v.label == "Incline Bench Press (Smith Machine) variant"


def test_swap_to_the_same_body_region_is_allowed(ctx):
    # chest_fly (horizontal_adduction) -> chest press (horizontal_push): same region (Chest)
    assert swap(ctx, "chest_fly", "Chest Press (Machine)").valid


def test_swap_rejects_other_regions_inactive_and_foreign_gym_variants(ctx):
    assert not swap(ctx, "bench_press", "Squat (Smith Machine)").valid  # different pattern + region
    assert not swap(ctx, "chest_fly", "Leg Curl (Machine)").valid
    inactive = swap(ctx, "chest_fly", "Chest Press (Retired)")
    assert not inactive.valid
    foreign = swap(ctx, "chest_fly", "Chest Press (Plate) @ Gym Two")
    assert not foreign.valid
    unknown = swap(ctx, "chest_fly", "Does Not Exist")
    assert not unknown.valid and "known variant" in unknown.error
    assert not swap(ctx, "chest_fly", "").valid
    assert not swap(ctx, "chest_fly", 5).valid


def test_swap_to_a_gym_bound_variant_of_the_current_gym(ctx):
    assert ctx.gym is not None and ctx.gym.name == "Gym One"
    assert swap(ctx, "chest_fly", "Chest Press (Plate)").valid


def test_swap_cannot_duplicate_an_exercise_of_the_same_day(ctx):
    v = swap(ctx, "chest_fly", "Bench Press (Barbell)")
    assert not v.valid and "already part of" in v.error


def test_swap_to_the_current_variant_is_no_change(ctx):
    assert not swap(ctx, "chest_fly", "Chest Fly (Machine)").valid


def test_swap_needs_a_program_slug(ctx):
    assert not swap(ctx, "calf_raise", "Calf Raise").valid


# --------------------------------------------------------------------------- chains + overrides


def test_changes_are_validated_against_the_earlier_valid_ones(ctx):
    first = check(ctx, "running.intervals.reps_max", 7)
    assert first.valid
    V.accept("running.intervals.reps_max", first, ctx)
    second = check(ctx, "running.intervals.reps_max", 8)  # 7 -> 8 now
    assert second.valid and second.current == 7


def test_session_overrides_may_not_prescribe_weights_or_paces():
    assert V.override_problem("Keep the first bench set easy.") is None
    assert V.override_problem("Swap the order of the two exercises") is None
    assert V.override_problem("Bench 82.5 kg for 5") is not None
    assert V.override_problem("do 100kg today") is not None
    assert V.override_problem("run at 4:30/km") is not None
    assert V.override_problem("hold 5:10 per km") is not None
    assert V.override_problem("target 5.0 min/km") is not None


def test_prompt_lists_every_allowlisted_param():
    from apps.fitness.advisor.prompt import SYSTEM_PROMPT

    for spec in V.SPECS:
        assert spec.template in SYSTEM_PROMPT
