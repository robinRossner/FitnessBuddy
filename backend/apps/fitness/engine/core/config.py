"""Typed loader for config/engine.v*.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from apps.fitness.engine.core.config_files import FileKey, file_key, raw_at, register_cache

# File reading and hashing live in the leaf module (seeding uses them without the models below).
from apps.fitness.engine.core.config_files import content_hash as content_hash
from apps.fitness.engine.core.config_files import load_raw as load_raw


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)


class E1RMConfig(_Model):
    formula: str = "epley"
    max_reps: int = 15
    min_reps: int = 1


class DeloadConfig(_Model):
    sets_pct: float = 40
    load_pct: float = 10
    # v6: "up" = sets halved and rounded UP (3 -> 2, 2 -> 1); "nearest" = the v1-v5 rounding
    sets_rounding: Literal["nearest", "up"] = "nearest"
    days: int = 7  # length of a deload window (reactive or multi-lift)
    min_gap_days: int = 28  # no new multi-lift / fatigue deload window within this many days
    # fatigue index (docs/research/lifting-progression.md): share of clean exposures in a week
    # that were a miss or a regression; >= `fatigue_index_pct` in `fatigue_weeks` complete weeks
    # in a row -> reactive deload
    fatigue_index_pct: float = 40
    fatigue_weeks: int = 2
    fatigue_min_exposures: int = 3  # weeks with fewer clean exposures do not count either way
    # line a lifting deload up with the running step-back week when the step-back is this close
    align_running_step_back: bool = True


class CensorConfig(_Model):
    """When a MISS does not count (hits always count). Reasons end up in decisions_log."""

    readiness: tuple[str, ...] = ("yellow", "red")
    hard_run_hours: float = 24  # lower-body lifts after intervals / a long run
    hard_run_min_km: float = 10  # a non-interval run this long counts as a long run
    calibration_sessions: int = 2  # first sessions after a variant swap, a break or a gym change
    break_days: int = 10


class BreakTier(_Model):
    """Return after a break (days since the lift's last exposure): from `min_days` on."""

    min_days: int
    load_pct: float = 0  # load cut (rounded down to an available load)
    sets_minus: int = 0  # working sets fewer ...
    sets_factor: float = 1.0  # ... or a factor (0.5 = half)
    days: int = 1  # how long after the return the set cut applies (first session / first week)


class StrengthConfig(_Model):
    rep_range: tuple[int, int] = (7, 12)
    # default range of a `reps`-progression exercise (bodyweight work, e.g. knee raises); the
    # program's / exercise_overrides' rep_range wins
    reps_rep_range: tuple[int, int] = (10, 20)
    rir_target: tuple[int, int] = (0, 1)
    # Stall = no progress event (load PR, rep PR at the same load, best-set e1RM +1 %) in N clean
    # exposures and at least `stall_min_days` days. `stall_exposures` is N for lifts trained once
    # a week (lower), `stall_exposures_2x` for lifts trained twice a week (upper).
    stall_exposures: int = 3
    stall_exposures_2x: int = 4
    stall_min_days: int = 12
    e1rm_progress_pct: float = 1.0
    # Stall ladder per lift: -small % (rebuild) -> advisor (rep range / variation) -> -large %.
    stall_reset_small_pct: float = 5
    stall_reset_pct: float = 10  # the last resort
    # Multi-lift stall: this many lifts stalled within `multi_stall_days` -> one soft deload week
    # (sets halved, loads kept) and a retest of the old loads, no per-lift resets.
    multi_stall_lifts: int = 3
    multi_stall_days: int = 14
    # Performance signal: reps at a load. A dip of up to `noise_reps` is noise; a drop of
    # `big_miss_reps` or two clean drops in a row -> one rung down and rebuild.
    noise_reps: int = 2
    big_miss_reps: int = 3
    # Rung rule (Epley): the next load must predict >= `rung_min_reps`; else the rep ceiling is
    # raised (up to `rep_ceiling_cap`) until the jump does.
    rung_min_reps: int = 8
    rep_ceiling_cap: int = 15
    # A first exposure at a new load whose first set is <= this many reps steps back at once
    # (else it gets a second exposure before stepping back).
    jump_abort_reps: int = 5
    decrease_load_if_first_set_below_range: bool = True
    timed_step_s: float = 5
    deload: DeloadConfig = DeloadConfig()
    e1rm: E1RMConfig = E1RMConfig()
    censor: CensorConfig = CensorConfig()
    break_tiers: tuple[BreakTier, ...] = (
        BreakTier(min_days=11, sets_minus=1, days=1),
        BreakTier(min_days=22, load_pct=5, sets_minus=1, days=7),
        BreakTier(min_days=43, load_pct=10, sets_factor=0.5, days=7),
    )


class IncrementConfig(_Model):
    barbell_kg: float = 5.0
    smith_kg: float = 5.0
    plate_loaded_kg: float = 5.0
    dumbbell_increment_kg: float = 2.0
    default_stack_step_kg: float = 5.0
    bodyweight_added_kg: float = 2.5
    other_kg: float = 2.5

    @field_validator("dumbbell_increment_kg")
    @classmethod
    def _db_step(cls, v: float) -> float:
        if v not in (2.0, 2.5):
            raise ValueError("dumbbell_increment_kg must be 2.0 or 2.5")
        return v


class UserConfig(_Model):
    timezone: str = "Europe/Vienna"
    bodyweight_kg: float | None = None


class BodyweightConfig(_Model):
    # Context signal for the advisor, not a goal (the user wants to maintain their weight).
    change_flag_pct_4w: float = 3.0


# --------------------------------------------------------------------------- readiness


class HrvThresholds(_Model):
    yellow_pct: float = -10
    red_pct: float = -20
    garmin_status: dict[str, str] = {"UNBALANCED": "yellow", "LOW": "yellow", "POOR": "red"}


class RhrThresholds(_Model):
    yellow_bpm: float = 5
    red_bpm: float = 8


class SleepThresholds(_Model):
    yellow_h: float = 6.0
    red_h: float = 5.0
    yellow_score: float = 60
    red_score: float = 45


class HrvDropAnomaly(_Model):
    pct: float = -15
    days: int = 3


class ReadinessConfig(_Model):
    baseline_days: int = 7
    min_baseline_days: int = 3
    hrv: HrvThresholds = HrvThresholds()
    rhr: RhrThresholds = RhrThresholds()
    sleep: SleepThresholds = SleepThresholds()
    yellow_signals_needed: int = 2
    sleep_red_needs_corroboration: bool = True
    hrv_drop_anomaly: HrvDropAnomaly = HrvDropAnomaly()
    red: dict[str, Any] = {}


# --------------------------------------------------------------------------- running


class EasyCeiling(_Model):
    pct_hrmax: float = 0.80
    pct_lthr: float = 0.89


class HeartRateConfig(_Model):
    max_hr: float | None = None
    lthr: float | None = None
    source: str = "manual"
    zones_pct_lthr: dict[str, float] = {
        "z1": 0.85,
        "z2": 0.89,
        "z3": 0.94,
        "z4": 0.99,
        "z5a": 1.02,
        "z5b": 1.06,
    }
    easy_ceiling: EasyCeiling = EasyCeiling()
    easy_low_pct_hrmax: float = 0.65
    interval_zones: tuple[str, str] = ("z4", "z5a")


class SeedRace(_Model):
    distance_m: float = 10000
    time_s: float = 3150


class LayoffTier(_Model):
    """VDOT after a running break: from `min_days` without a run, VDOT x (1 - pct / 100)."""

    min_days: int
    pct: float


class VdotFromIntervalsConfig(_Model):
    """A measured VDOT from completed interval sessions (engine/core/interval_vdot.py).

    A session counts when it has at least `min_reps` work reps of `rep_min_s`..`rep_max_s` and
    >= `min_rep_m` (GPS junk laps drop out), run evenly (slowest - fastest <= `max_spread_pct`
    of the median, last rep at most `max_fade_pct` slower than the first) and with a plausible
    hard effort by HR (median rep peak HR >= `min_peak_hr_pct_lthr` x LTHR). Its median rep pace
    is taken as (at most) the athlete's I pace: estimate = the VDOT whose I pace (98 %) it is.
    Evidence older than `age_free_days` loses `age_discount_per_week` VDOT per week; only
    sessions of the last `window_days` count. The best estimate may raise the quality VDOT in
    one update by at most `max_step` (instead of the inference path's `max_step` / `max_per_4w`)."""

    enabled: bool = True
    window_days: int = 70
    min_reps: int = 4
    rep_min_s: float = 120
    rep_max_s: float = 480
    min_rep_m: float = 300
    max_spread_pct: float = 8
    max_fade_pct: float = 5
    min_peak_hr_pct_lthr: float = 0.90
    age_free_days: int = 28
    age_discount_per_week: float = 0.25
    max_step: float = 3.0


class VdotConfig(_Model):
    seed_race: SeedRace = SeedRace()
    lookback_weeks: int = 12
    min_effort_km: float = 5
    min_effort_hr_pct_lthr: float = 0.88
    pace_step: float = 0.5
    # VDOT updates (hysteresis): at most +max_step per update and +max_per_4w per 28 days.
    # Triggers: a time trial / race effort, or `green_sessions` quality sessions passed easily
    # within `green_window_weeks` together with a rising aerobic-efficiency trend. Never lowered
    # by one bad session: only by the layoff table or `fails_to_lower` genuine fails in 4 weeks.
    max_step: float = 0.5
    max_per_4w: float = 1.0
    green_sessions: int = 2
    green_window_weeks: int = 3
    ef_trend_weeks: int = 4
    ef_trend_min_pct: float = 1.0
    fails_to_lower: int = 3
    fail_drop: float = 0.5
    layoff: tuple[LayoffTier, ...] = (
        LayoffTier(min_days=15, pct=2),
        LayoffTier(min_days=29, pct=5),
        LayoffTier(min_days=57, pct=8),
    )
    from_intervals: VdotFromIntervalsConfig = VdotFromIntervalsConfig()


class StridesConfig(_Model):
    reps: int = 6  # 0 = no strides
    duration_s: float = 20
    recovery_s: float = 60


class EasyRunConfig(_Model):
    distance_km: float = 7
    pass_pct_time_at_or_below_cap: float = 80
    decoupling_flag_pct: float = 5
    strides: StridesConfig = StridesConfig()
    # the day after a lower-body session an HR fail of the easy run is only a note
    exempt_after_lower: bool = True


class EasyCalibrationConfig(_Model):
    """Easy / long run pace range calibrated from the athlete's own easy runs
    (engine/core/easy_pace.py).

    A run qualifies when it is a steady run (not intervals, not a quality-session day) of at
    least `min_minutes` in the last `window_days`, passed the easy-run HR rule (not via the
    day-after-lower exemption), had an average HR <= easy ceiling - `hr_margin_bpm`, HR drift
    <= the easy run's `decoupling_flag_pct` (laps needed) and <= `max_elevation_m_per_km`
    climbing. With at least `min_runs` of them, their median pace becomes the fast end (74 % of
    VDOT) of the easy range: the "easy VDOT" is that pace's VDOT, never below the quality VDOT
    (calibration only speeds the range up) and at most `max_vdot_gain` above it. Quality paces
    always use the quality VDOT."""

    enabled: bool = True
    window_days: int = 28
    min_runs: int = 3
    min_minutes: float = 25
    hr_margin_bpm: float = 2
    max_vdot_gain: float = 4.0
    max_elevation_m_per_km: float = 25


class IntervalConfig(_Model):
    """The VO2max session type (I pace) plus the evaluation shared by every quality session."""

    rep_distance_m: float = 1000
    recovery_distance_m: float = 400
    reps_start: int = 5
    reps_min: int = 4
    reps_max: int = 6
    rep_ladder_m: tuple[float, ...] = (800, 1000, 1200)  # rep-length lever
    max_work_min: float = 20  # hard minutes per session (Daniels-style cap)
    warmup_km: float = 2.0
    cooldown_km: float = 1.5
    warmup_min_km: float = (
        1.0  # warm-up / cool-down shrink to these when the week's budget is tight
    )
    cooldown_min_km: float = 0.5
    hr_recovery_min_bpm: float = 12
    pace_tolerance_s: float = 4
    # evaluation by pace (HR is a safety ceiling only)
    pass_rep_share: float = 0.8  # >= 80 % of the work reps within the pace tolerance
    fade_max_pct: float = 3  # last rep at most 3 % slower than the first
    hr_ceiling_pct_lthr: float = 1.08  # last rep's peak HR above this = "passed hard": hold
    green_hr_pct_lthr: float = 1.02  # last rep's peak HR below this = "passed easily"
    fails_to_step_back: int = 2  # genuine fails of the same prescription within 3 weeks


class ThresholdConfig(_Model):
    """Threshold reps (T pace): reps -> rep length -> shorter recovery -> pace."""

    reps_start: int = 3
    reps_min: int = 3
    reps_max: int = 5
    rep_ladder_m: tuple[float, ...] = (1000, 1200, 1600, 2000)
    recovery_ladder_s: tuple[float, ...] = (90, 75, 60)
    max_work_min: float = 30
    hr_ceiling_pct_lthr: float = 0.99  # top of Z4: a threshold rep should not end above it
    green_hr_pct_lthr: float = 0.95  # bottom of Z4


class Race5kConfig(_Model):
    """5k-specific reps at 5k race pace (from VDOT): reps -> rep length -> pace."""

    reps_start: int = 6
    reps_min: int = 4
    reps_max: int = 6
    rep_ladder_m: tuple[float, ...] = (800, 1000, 1200)
    recovery_s: float = 120
    max_work_min: float = 20
    hr_ceiling_pct_lthr: float = 1.08
    green_hr_pct_lthr: float = 1.02


class TimeTrialConfig(_Model):
    distance_m: float = 5000
    warmup_km: float = 2.0
    cooldown_km: float = 1.5


class QualityPhase(_Model):
    name: str
    weeks: int
    sessions: tuple[str, ...]  # session types, alternating week by week


class QualityConfig(_Model):
    """The Friday quality cycle. A phase week counts only when its quality session was done,
    so a missed Friday repeats the same session next week. `cycle_start` (ISO date) pins the
    start; else the engine's first recorded cycle start (decisions_log) or this week."""

    cycle_start: str | None = None
    phases: tuple[QualityPhase, ...] = (
        QualityPhase(name="threshold", weeks=12, sessions=("threshold",)),
        QualityPhase(name="threshold_vo2max", weeks=8, sessions=("threshold", "vo2max")),
        QualityPhase(name="5k_specific", weeks=6, sessions=("race_5k", "threshold")),
        QualityPhase(name="time_trial", weeks=1, sessions=("time_trial",)),
    )
    # per-phase week counts (tunable by the coach): wins over `phases[].weeks`
    phase_weeks: dict[str, int] = {}
    no_quality_after_break_days: int = 15  # first week back after this long without a run: easy

    def weeks_of(self, phase: QualityPhase) -> int:
        return max(1, int(self.phase_weeks.get(phase.name, phase.weeks)))


class LongRunConfig(_Model):
    start_km: float = 10
    step_km: float = 1.0
    cap_km: float = 21
    min_km: float = 8
    # every `step_back_every`-th week is a step-back (-`step_back_pct` %)
    step_back_every: int = 4
    step_back_pct: float = 25
    # drift gate (decoupling of the last long run, steady runs >= drift_min_minutes only)
    drift_hold_pct: float = 5
    drift_cut_pct: float = 8
    drift_cut_by_pct: float = 10
    drift_min_minutes: float = 60
    recent_days: int = 21  # the base is the longest long run of this many days


class VolumeConfig(_Model):
    """Weekly cap = max(mean x (1 + weekly_volume_cap_pct %), mean + cap_abs_km, hold) of the last
    `mean_weeks` weeks of ACTUAL km; `weekly_volume_floor_km` is now a target, not a floor.
    `hold` (with `hold_last_week`) = the km of the last completed week, limited to
    `hold_limit_factor` x the highest week of the 4 weeks before it, and only when that week had
    no easy / long run with an HR fail or a drift flag (running.hold_volume_km)."""

    mean_weeks: int = 4
    cap_abs_km: float = 2.0
    single_run_factor: float = 1.1  # a single run <= 1.1 x the longest run of the last 30 days
    single_run_abs_km: float = 0.0  # ... or longest + this, whichever is larger
    single_run_days: int = 30
    min_run_km: float = 3.0  # the cap never shortens an easy or long run below this
    hold_last_week: bool = True  # the cap may hold the km of the last completed week
    hold_limit_factor: float = 1.1  # ... at most this x the max weekly km of the 4 weeks before


class FourthRunConfig(_Model):
    consistent_weeks: int = 8
    min_runs_per_week: int = 3
    min_weekly_km: float = 28
    min_long_run_km: float = 14


class RunningConfig(_Model):
    pace_model: str = "vdot"
    # Interval reps count as "on pace" unless slower than the target by more than this many %
    # (running faster is fine); reps this many % faster than the target get a verdict note.
    interval_pace_tolerance_pct: float = 3
    interval_fast_flag_pct: float = 5
    weekly_volume_cap_pct: float = 10
    weekly_volume_floor_km: float = 0  # v6: the weekly TARGET to progress toward (0 = none)
    stagnation_weeks: int = 4
    stagnation_min_improvement_pct: float = 1.0
    heart_rate: HeartRateConfig = HeartRateConfig()
    vdot: VdotConfig = VdotConfig()
    easy: EasyRunConfig = EasyRunConfig()
    easy_calibration: EasyCalibrationConfig = EasyCalibrationConfig()
    intervals: IntervalConfig = IntervalConfig()
    threshold: ThresholdConfig = ThresholdConfig()
    race_5k: Race5kConfig = Race5kConfig()
    time_trial: TimeTrialConfig = TimeTrialConfig()
    quality: QualityConfig = QualityConfig()
    long_run: LongRunConfig = LongRunConfig()
    volume: VolumeConfig = VolumeConfig()
    fourth_run: FourthRunConfig = FourthRunConfig()


class RecoveryConfig(_Model):
    deload_red_days: int = 4
    hard_after_heavy_lower_h: float = 24


class ScheduleConfig(_Model):
    """Missed-session policy + the hard scheduling rules (engine/store/makeup.py,
    engine/core/schedule_rules.py).
    Lives under `schedule:` in the config content; every key has a default, so versions without
    the section (v3) behave the same. Kinds use the short names: lower, intervals, upper, long,
    easy (rest where a rest day is meant)."""

    # who gets a make-up first when several sessions were missed (the rest of the list is the
    # order of importance; a missed `easy` run is never made up)
    makeup_priority: tuple[str, ...] = ("lower", "intervals", "upper", "long", "easy")
    makeup_never: tuple[str, ...] = ("easy",)  # missed sessions that are simply dropped
    makeup_replaceable: tuple[str, ...] = ("easy",)  # planned kinds a make-up may take the slot of
    makeup_use_rest_day: bool = True
    long_run_makeup_day: str = "sat"  # legacy (v3-v5); runs now use run_makeup_max_shift_days
    # a missed run moves by at most this many days (if the hard rules allow), else it is dropped
    # and the same session is planned again next week; lifting keeps the priority rules above
    run_makeup_max_shift_days: int = 1
    # hard rules (also used by the advisor's schedule changes)
    hard_kinds: tuple[str, ...] = ("lower", "intervals")
    max_hard_per_day: int = 1
    no_consecutive_hard_days: bool = True
    max_sessions_per_day: int = 2
    # coach schedule changes
    shorten_max_pct: float = 50  # `shorten` may cut a run by at most this share
    horizon_weeks: int = 2  # current + next week
    # how hard a kind is (a `replace` may only go to an equal or easier kind unless it makes up
    # a missed session)
    kind_load: dict[str, int] = {
        "rest": 0,
        "easy": 1,
        "upper": 2,
        "long": 3,
        "lower": 4,
        "intervals": 4,
    }


REST_MIN_S = 30  # bounds of a rest time (user endpoint, advisor validator)
REST_MAX_S = 600


class LiftingConfig(_Model):
    """Lifting settings that are not progression rules. Lives under `lifting:` in the config
    content; every key has a default, so versions without the section (v3, v4) behave the same.
    A per-exercise rest time is `exercise_overrides.<slug>.rest_s` (see engine/store/rest.py)."""

    rest_default_s: int = 120  # rest timer between sets, for every exercise without an override


class AdvisorConfig(_Model):
    """Claude advisor settings (app/advisor). Lives under `advisor:` in the config content; every
    key has a default, so versions without the section behave the same (v3 has none)."""

    approval_mode: Literal["manual", "auto-low-risk", "auto"] = "manual"
    max_calls_per_week: int = 4  # trigger + weekly review calls; "Ask coach" is not counted
    max_ask_per_day: int = 5
    outcome_after_days: tuple[int, int] = (21, 28)  # check an applied change in this age window
    trigger_max_age_days: int = 10  # older open triggers are dismissed, not consulted
    input_budget_tokens: int = 15000  # the compact input is trimmed to stay below this (chars/4)
    verdict_threshold_pct: float = 1.0  # +- this much change in the metric = improved / worse
    # Claude Opus 5.5 list prices in USD per million tokens (cache write = 1.25 x input, 5 min TTL)
    price_input_per_mtok: float = 4.0
    price_output_per_mtok: float = 20.0
    price_cache_read_per_mtok: float = 0.2
    price_cache_write_per_mtok: float = 5.0


class EngineConfig(_Model):
    version: str
    approval_mode: str = "manual"
    user: UserConfig = UserConfig()
    bodyweight: BodyweightConfig = BodyweightConfig()
    strength: StrengthConfig = StrengthConfig()
    increments: IncrementConfig = IncrementConfig()
    recovery: dict[str, Any] = {}
    readiness: dict[str, Any] = {}
    running: dict[str, Any] = {}
    weekly_template: dict[str, str] = {}
    # Per program exercise (catalog slug): {"rep_range": [8, 12], "sets": 3, "variant": "<name>"}
    # (set by the advisor; wins over program.yaml, see program.apply_overrides) and "rest_s": 180
    # (rest timer; set by the user or the advisor; any catalog slug, see engine/store/rest.py).
    exercise_overrides: dict[str, Any] = {}
    advisor: dict[str, Any] = {}
    schedule: dict[str, Any] = {}
    lifting: dict[str, Any] = {}

    # Typed views over the free-form sections (the raw dicts stay for old callers / old versions).
    @property
    def readiness_cfg(self) -> ReadinessConfig:
        return ReadinessConfig.model_validate(self.readiness)

    @property
    def running_cfg(self) -> RunningConfig:
        return RunningConfig.model_validate(self.running)

    @property
    def advisor_cfg(self) -> AdvisorConfig:
        return AdvisorConfig.model_validate(self.advisor)

    @property
    def schedule_cfg(self) -> ScheduleConfig:
        return ScheduleConfig.model_validate(self.schedule)

    @property
    def lifting_cfg(self) -> LiftingConfig:
        return LiftingConfig.model_validate(self.lifting)

    @property
    def recovery_cfg(self) -> RecoveryConfig:
        return RecoveryConfig.model_validate(self.recovery)


def load_engine_config(path: Path) -> EngineConfig:
    """The validated config file, cached until the file changes (see `config_files`). The model
    is frozen and shared between callers: build a changed copy with `model_copy`, never mutate
    its dicts."""
    return _validated(file_key(path))


@lru_cache(maxsize=16)
def _validated(key: FileKey) -> EngineConfig:
    return EngineConfig.model_validate(raw_at(key))


register_cache(_validated.cache_clear)
