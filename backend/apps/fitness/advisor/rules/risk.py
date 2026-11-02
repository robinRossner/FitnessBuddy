"""Risk classification of an allowed change: low | medium | high, decided HERE (never by
Claude). `low` is deliberately narrow (the `auto-low-risk` approval mode applies only those):
rep-range shifts of at most 1 rep per end, interval rep counts of +-1, rest times, strides, a few
switches; schedule changes: skip, shorten and replace-by-an-easier-kind are low, everything else
medium. Rep ranges, working sets and exercise swaps classify inline in `lifting.py`.
"""

from __future__ import annotations

LOW, MEDIUM, HIGH = "low", "medium", "high"
RISKS = (LOW, MEDIUM, HIGH)


def risk_by_step(low: float, medium: float):
    """Risk from the absolute size of the move: <= low -> low, <= medium -> medium, else high."""

    def risk(cur: float, new: float) -> str:
        step = abs(new - cur)
        return LOW if step <= low else MEDIUM if step <= medium else HIGH

    return risk


def always(level: str):
    return lambda _c, _n: level


def cap_risk(cur: float, new: float) -> str:
    return HIGH if new > cur and new > 10 else MEDIUM


def floor_risk(cur: float, new: float) -> str:
    if cur <= 0:  # switching the floor on (it is off by default)
        return MEDIUM if new <= 25 else HIGH
    return HIGH if new - cur > 5 else MEDIUM


def long_cap_risk(cur: float, new: float) -> str:
    return HIGH if new > cur and new > 21 else MEDIUM


def step_risk(cur: float, new: float) -> str:
    return HIGH if new > 1.0 else MEDIUM


def increment_risk(cur: float, new: float) -> str:
    return HIGH if new >= cur * 2 - 1e-9 else MEDIUM


def reset_risk(cur: float, new: float) -> str:
    return HIGH if abs(new - cur) >= 4 else MEDIUM


def up_risk(threshold: float):
    """High when the value goes up past `threshold`, medium otherwise."""

    def risk(cur: float, new: float) -> str:
        return HIGH if new > cur and new > threshold + 1e-9 else MEDIUM

    return risk


def down_risk(threshold: float):
    """High when the value goes down below `threshold`, medium otherwise."""

    def risk(cur: float, new: float) -> str:
        return HIGH if new < cur and new < threshold - 1e-9 else MEDIUM

    return risk


def schedule_risk(action: str, *, easier: bool = False) -> str:
    if action in ("skip", "shorten") or (action == "replace" and easier):
        return LOW
    return MEDIUM
