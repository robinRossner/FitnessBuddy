"""Typed loader for config/engine.v*.yaml."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, field_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)


class E1RMConfig(_Model):
    formula: str = "epley"
    max_reps: int = 15
    min_reps: int = 1


class DeloadConfig(_Model):
    sets_pct: float = 40
    load_pct: float = 10


class StrengthConfig(_Model):
    rep_range: tuple[int, int] = (7, 12)
    rir_target: tuple[int, int] = (0, 1)
    stall_exposures: int = 3
    stall_reset_pct: float = 10
    decrease_load_if_first_set_below_range: bool = True
    timed_step_s: float = 5
    deload: DeloadConfig = DeloadConfig()
    e1rm: E1RMConfig = E1RMConfig()


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


class VdotConfig(_Model):
    seed_race: SeedRace = SeedRace()
    lookback_weeks: int = 12
    min_effort_km: float = 5
    min_effort_hr_pct_lthr: float = 0.88
    pace_step: float = 0.5


class EasyRunConfig(_Model):
    distance_km: float = 7
    pass_pct_time_at_or_below_cap: float = 80
    decoupling_flag_pct: float = 5


class IntervalConfig(_Model):
    rep_distance_m: float = 1000
    recovery_distance_m: float = 400
    reps_start: int = 5
    reps_min: int = 4
    reps_max: int = 6
    warmup_km: float = 2.0
    cooldown_km: float = 1.5
    warmup_min_km: float = (
        1.0  # warm-up / cool-down shrink to these when the week's budget is tight
    )
    cooldown_min_km: float = 0.5
    hr_recovery_min_bpm: float = 12
    pace_tolerance_s: float = 4


class LongRunConfig(_Model):
    start_km: float = 10
    step_km: float = 1.0
    cap_km: float = 21
    min_km: float = 8


class RunningConfig(_Model):
    pace_model: str = "vdot"
    weekly_volume_cap_pct: float = 10
    weekly_volume_floor_km: float = 0
    stagnation_weeks: int = 4
    stagnation_min_improvement_pct: float = 1.0
    heart_rate: HeartRateConfig = HeartRateConfig()
    vdot: VdotConfig = VdotConfig()
    easy: EasyRunConfig = EasyRunConfig()
    intervals: IntervalConfig = IntervalConfig()
    long_run: LongRunConfig = LongRunConfig()


class RecoveryConfig(_Model):
    deload_red_days: int = 4
    hard_after_heavy_lower_h: float = 24


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

    # Typed views over the free-form sections (the raw dicts stay for old callers / old versions).
    @property
    def readiness_cfg(self) -> ReadinessConfig:
        return ReadinessConfig.model_validate(self.readiness)

    @property
    def running_cfg(self) -> RunningConfig:
        return RunningConfig.model_validate(self.running)

    @property
    def recovery_cfg(self) -> RecoveryConfig:
        return RecoveryConfig.model_validate(self.recovery)


def load_raw(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_engine_config(path: Path) -> EngineConfig:
    return EngineConfig.model_validate(load_raw(path))


def content_hash(raw: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
