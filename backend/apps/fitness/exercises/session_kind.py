"""Infer a session's kind from the exercises done (for generic names like "Morning Workout").

Pure function over (movement pattern, primary muscle, bodyweight?, sets) tuples taken from
data/catalog/exercises.yaml. Core work is neutral; cardio is ignored.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from apps.fitness.db.types import SessionKind

LOWER_PATTERNS = frozenset(
    {"squat", "hinge", "lunge", "knee_extension", "knee_flexion", "hip_adduction", "calf_raise"}
)
LOWER_MUSCLES = frozenset({"quads", "hamstrings", "glutes", "adductors", "calves"})
CORE_MUSCLES = frozenset({"abs"})
UPPER_MUSCLES = frozenset(
    {"chest", "back", "lats", "shoulders", "biceps", "triceps", "forearms", "traps"}
)
IGNORED_PATTERNS = frozenset({"cardio"})

CALISTHENICS_SHARE = 0.6  # at least this share of the sets are bodyweight exercises
LOWER_SHARE_MAX_UPPER = 0.25  # lower share <= this => upper
LOWER_SHARE_MIN_LOWER = 0.75  # lower share >= this => lower; in between => full body


@dataclass(frozen=True)
class ExerciseUse:
    movement_pattern: str
    primary_muscle: str
    bodyweight: bool  # performed with bodyweight equipment (pull-up, dip, L-sit, ...)
    sets: int = 1


def _region(use: ExerciseUse) -> str | None:
    if use.movement_pattern in IGNORED_PATTERNS:
        return None
    if use.movement_pattern in LOWER_PATTERNS or use.primary_muscle in LOWER_MUSCLES:
        return "lower"
    if use.primary_muscle in CORE_MUSCLES or use.movement_pattern in {
        "trunk_flexion",
        "hip_flexion",
        "isometric_core",
    }:
        return "core"
    if use.primary_muscle in UPPER_MUSCLES:
        return "upper"
    return None


def infer_session_kind(uses: Iterable[ExerciseUse]) -> SessionKind:
    """upper | lower | full | calisthenics, or OTHER when there is nothing to go on."""
    counts = {"upper": 0, "lower": 0, "core": 0}
    bodyweight = 0
    for u in uses:
        region = _region(u)
        if region is None or u.sets <= 0:
            continue
        counts[region] += u.sets
        if u.bodyweight:
            bodyweight += u.sets
    main = counts["upper"] + counts["lower"]
    if main == 0:
        return SessionKind.OTHER  # nothing, or core only
    if bodyweight / (main + counts["core"]) >= CALISTHENICS_SHARE:
        return SessionKind.CALISTHENICS
    lower_share = counts["lower"] / main
    if lower_share <= LOWER_SHARE_MAX_UPPER:
        return SessionKind.UPPER
    if lower_share >= LOWER_SHARE_MIN_LOWER:
        return SessionKind.LOWER
    return SessionKind.FULL
