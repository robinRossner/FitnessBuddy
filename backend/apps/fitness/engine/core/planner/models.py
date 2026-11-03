"""Planner input and output: plain dataclasses, no logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from apps.fitness.engine.core import lifting as L
from apps.fitness.engine.core import quality as Q
from apps.fitness.engine.core import running as R
from apps.fitness.engine.core import scheduling as S
from apps.fitness.engine.core import vdot as V
from apps.fitness.engine.core.config import EngineConfig
from apps.fitness.engine.core.program import Program
from apps.fitness.engine.core.readiness import (
    HealthDay,
    Readiness,
)


@dataclass
class VariantInfo:
    id: int
    key: str | None
    name: str
    base_name: str
    exercise_slug: str
    exercise_name: str
    equipment_type: str
    load_convention: str
    weight_ladder: list[float] | None = None
    progression: str = "load"  # load | reps | time
    increment_kg: float | None = None  # the user's load step for this variant (app setting)


@dataclass
class LiftContext:
    variant: VariantInfo | None
    history: list[L.Exposure] = field(default_factory=list)
    ladder: list[L.LadderEvent] = field(default_factory=list)  # stall-ladder steps so far
    # no history here yet: (gym name, weight kg, reps) of the same exercise at another gym
    previous: tuple[str, float, int | None] | None = None


@dataclass
class PlanInputs:
    today: date
    week_start: date
    cfg: EngineConfig
    config_version: str
    program: Program
    lifts: dict[str, LiftContext]  # by program variant base name
    health: list[HealthDay]
    runs: list[R.RunData]  # running activities, oldest first
    actual_lower_days: set[date] = field(default_factory=set)
    frozen_slots: set[str] = field(default_factory=set)
    # placements decided by the missed-session policy / the coach (planned_sessions.schedule_lock)
    slot_locks: dict[str, S.SlotLock] = field(default_factory=dict)
    excluded_slots: set[str] = field(default_factory=set)  # skipped / deleted: no training load
    gym_name: str | None = None
    # engine state from decisions_log (data.py)
    windows: list[L.DeloadWindow] = field(default_factory=list)
    quality_log: list[Q.QualityRecord] = field(default_factory=list)
    vdot_log: list[V.VdotRecord] = field(default_factory=list)
    cycle_start: date | None = None
    last_step_back: date | None = None  # Monday of the last long-run step-back week
    # weeks whose soft deload the user cancelled (`engine deload cancel`) -> the reason
    cancelled_weeks: dict[date, str | None] = field(default_factory=dict)
    bodyweight_kg: float | None = None
    zone_floors: tuple[float, ...] | None = None  # Garmin's latest z1..z5 floors


@dataclass
class DecisionRec:
    kind: str
    rule: str
    rationale: str
    inputs: dict[str, Any]
    output: dict[str, Any]
    subject_type: str | None = None
    subject_id: str | None = None
    slot_id: str | None = None


@dataclass
class TriggerRec:
    type: str
    dedupe_key: str
    payload: dict[str, Any]
    subject_type: str | None = None
    subject_id: str | None = None
    day: date | None = None
    slot_id: str | None = None


@dataclass
class DayPlan:
    slot_id: str
    day: date
    kind: str  # final kind: upper | lower | easy_run | intervals | long_run | rest
    template_kind: str
    title: str
    prescription: dict[str, Any]
    readiness: dict[str, Any] | None = None
    adjustments: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    moved_from: date | None = None
    change_reason: str | None = None
    changed_by: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "moved_from": self.moved_from.isoformat() if self.moved_from else None,
            "change_reason": self.change_reason,
            "slot_id": self.slot_id,
            "date": self.day.isoformat(),
            "kind": self.kind,
            "template_kind": self.template_kind,
            "title": self.title,
            "prescription": self.prescription,
            "readiness": self.readiness,
            "adjustments": self.adjustments,
            "notes": self.notes,
        }


@dataclass
class WeekPlan:
    week_start: date
    today: date
    config_version: str
    readiness: Readiness
    days: list[DayPlan] = field(default_factory=list)
    decisions: list[DecisionRec] = field(default_factory=list)
    triggers: list[TriggerRec] = field(default_factory=list)
    pace: dict[str, Any] = field(default_factory=dict)
    zones: dict[str, Any] = field(default_factory=dict)
    readiness_history: dict[date, str] = field(default_factory=dict)
    volume: dict[str, Any] = field(default_factory=dict)
    lifting: dict[str, Any] = field(default_factory=dict)  # per-lift states for the summaries
    running: dict[str, Any] = field(default_factory=dict)  # phase, VDOT, long-run block
