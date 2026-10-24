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


class StrengthConfig(_Model):
    rep_range: tuple[int, int] = (7, 12)
    rir_target: tuple[int, int] = (0, 1)
    stall_exposures: int = 3
    stall_reset_pct: float = 10
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


def load_raw(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_engine_config(path: Path) -> EngineConfig:
    return EngineConfig.model_validate(load_raw(path))


def content_hash(raw: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
