"""Pace model interface (implementation is a later step).

Kept behind a Protocol so the model can be swapped (e.g. simple HR-pace regression first,
Riegel/VDOT-style race predictor later) without touching the planner.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PaceObservation:
    distance_m: float
    duration_s: float
    avg_hr: float | None = None
    elevation_gain_m: float | None = None


@dataclass(frozen=True)
class PaceRange:
    min_s_per_km: float  # faster bound
    max_s_per_km: float  # slower bound


@runtime_checkable
class PaceModel(Protocol):
    name: str

    def fit(self, history: Sequence[PaceObservation]) -> None:
        """(Re)fit from recent runs/laps."""
        ...

    def easy_pace(self, hr_ceiling: float) -> PaceRange | None:
        """Pace range expected at or below the easy-run HR ceiling."""
        ...

    def interval_pace(self, rep_distance_m: float) -> PaceRange | None:
        """Target pace for interval reps of the given length."""
        ...
