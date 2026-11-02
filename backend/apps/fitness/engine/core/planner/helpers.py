"""Small pure helpers shared by the planner modules (formatting, rounding, dicts)."""

from __future__ import annotations

from typing import Any

from apps.fitness.engine.core import lifting as L
from apps.fitness.engine.core.hr import HrZones
from apps.fitness.engine.core.pace import fmt_pace

_TITLES = {"upper": "Upper", "lower": "Lower", "rest": "Rest"}


def _paces(rng) -> dict[str, Any]:
    return {
        "min_s_per_km": round(rng.min_s_per_km),
        "max_s_per_km": round(rng.max_s_per_km),
        "label": f"{fmt_pace(rng.min_s_per_km)}-{fmt_pace(rng.max_s_per_km)}",
    }


def _hr(zones: HrZones, which: str) -> dict[str, Any]:
    if which == "easy":
        return {
            "zone": zones.easy_label,
            "low": zones.easy_low,
            "high": zones.easy_high,
            "basis": zones.easy_basis,
        }
    return {"zone": zones.interval_label, "low": zones.interval_low, "high": zones.interval_high}


def work_km(reps: int, rep_m: float, recovery_m: float | None) -> float:
    """Rep + jog-recovery distance of a quality block (no warm-up / cool-down)."""
    return (reps * rep_m + max(reps - 1, 0) * (recovery_m or 0.0)) / 1000


def _round1(x: float) -> float:
    return round(x + 1e-9, 1)


def _window_dict(w: L.DeloadWindow) -> dict[str, Any]:
    return {
        "start": w.start.isoformat(),
        "until": w.until.isoformat(),
        "reason": w.reason,
        "deload": w.deload,
        "cancelled": w.cancelled,
        "retest": {
            str(k): {"load": r.load, "reps": list(r.reps), "best_e1rm": r.best_e1rm}
            for k, r in w.retest.items()
        },
    }


def _ladder_dict(e: L.LadderEvent) -> dict[str, Any]:
    return {
        "day": e.day.isoformat(),
        "step": e.step,
        "load": e.load,
        "peak_e1rm": e.peak_e1rm,
        "from_load": e.from_load,
    }
