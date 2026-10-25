"""The lifting program (data/program/program.yaml): which exercises, in which order, how many sets.

The user trains a fixed Upper / Lower / Upper week; both upper days are the same full-upper
session. The file is generated once from recent history (`engine program generate`) and is then
the user's to edit; the engine only reads it. Per-exercise optional keys: `rep_range: [8, 15]`,
`sets`, `increment_kg` (flat load step instead of the equipment default), `active: false`,
`note`. `variant` is the variant name WITHOUT the gym suffix: the engine picks the generic
variant or the one at the current gym.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from statistics import fmean, median
from typing import Any

import yaml

KINDS = ("upper", "lower")


@dataclass
class ProgramExercise:
    exercise: str  # catalog slug
    variant: str  # variant name without the " @ gym" suffix
    sets: int = 3
    rep_range: tuple[int, int] | None = None
    increment_kg: float | None = None
    active: bool = True
    note: str | None = None


@dataclass
class Program:
    days: dict[str, list[ProgramExercise]] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def exercises(self, kind: str) -> list[ProgramExercise]:
        return [e for e in self.days.get(kind, []) if e.active]


def load_program(path: Path) -> Program:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    days: dict[str, list[ProgramExercise]] = {}
    for kind, items in (data.get("days") or {}).items():
        out = []
        for i, it in enumerate(items or []):
            if not it.get("variant") or not it.get("exercise"):
                raise ValueError(f"program {kind}[{i}]: `exercise` and `variant` are required")
            rr = it.get("rep_range")
            out.append(
                ProgramExercise(
                    exercise=str(it["exercise"]),
                    variant=str(it["variant"]),
                    sets=int(it.get("sets", 3)),
                    rep_range=(int(rr[0]), int(rr[1])) if rr else None,
                    increment_kg=float(it["increment_kg"]) if it.get("increment_kg") else None,
                    active=bool(it.get("active", True)),
                    note=it.get("note"),
                )
            )
        days[str(kind)] = out
    return Program(days, data.get("generated") or {})


def apply_overrides(program: Program, overrides: dict[str, Any] | None) -> Program:
    """Program with the config's per-exercise overrides applied (`exercise_overrides` in the engine
    config, keyed by catalog slug; set by the advisor): rep_range, sets, variant (`rest_s` is not
    a program matter: app/engine/rest.py). They win over
    program.yaml, which stays untouched; rolling the config back removes them."""
    if not overrides:
        return program
    days: dict[str, list[ProgramExercise]] = {}
    for kind, items in program.days.items():
        out = []
        for pe in items:
            ov = overrides.get(pe.exercise) or {}
            if ov:
                rr = ov.get("rep_range")
                pe = replace(
                    pe,
                    rep_range=(int(rr[0]), int(rr[1])) if rr else pe.rep_range,
                    sets=int(ov["sets"]) if ov.get("sets") else pe.sets,
                    variant=str(ov["variant"]) if ov.get("variant") else pe.variant,
                )
            out.append(pe)
        days[kind] = out
    return Program(days, program.meta)


# --------------------------------------------------------------------------- derivation


@dataclass(frozen=True)
class ExerciseUse:
    exercise: str
    variant: str  # base variant name (no gym suffix)
    working_sets: int
    position: int  # 0-based block position in the session


@dataclass(frozen=True)
class SessionUses:
    day: date
    kind: str
    uses: tuple[ExerciseUse, ...]


def derive_program(sessions: Sequence[SessionUses], *, min_share: float = 0.4) -> Program:
    """Exercise list per kind from recent sessions: exercises present in at least `min_share` of
    the sessions of that kind, ordered by their mean relative position, with the median number of
    working sets."""
    days: dict[str, list[ProgramExercise]] = {}
    counts: dict[str, int] = {}
    for kind in KINDS:
        ks = [s for s in sessions if s.kind == kind]
        counts[kind] = len(ks)
        if not ks:
            days[kind] = []
            continue
        pos: dict[tuple[str, str], list[float]] = defaultdict(list)
        sets: dict[tuple[str, str], list[int]] = defaultdict(list)
        for s in ks:
            n = max(len(s.uses), 1)
            for u in s.uses:
                key = (u.exercise, u.variant)
                pos[key].append(u.position / n)
                sets[key].append(u.working_sets)
        keep = [k for k, p in pos.items() if len(p) / len(ks) >= min_share]
        keep.sort(key=lambda k: (fmean(pos[k]), k[1]))
        days[kind] = [
            ProgramExercise(
                exercise=k[0],
                variant=k[1],
                sets=max(1, math.floor(median(sets[k]) + 0.5)),
            )
            for k in keep
        ]
    meta = {
        "from": min((s.day for s in sessions), default=None),
        "to": max((s.day for s in sessions), default=None),
        "sessions": counts,
    }
    return Program(days, meta)


def render_program(program: Program, *, gym: str | None = None) -> str:
    m = program.meta
    lines = [
        "# FitnessBuddy lifting program: the engine reads this file, you own it.",
        "# Generated from your recent Upper / Lower sessions (exercises in at least 40 % of them,",
        "# ordered by where you usually do them, median number of working sets).",
        "# Both upper days (Mon, Thu) use the `upper` list.",
        "# Optional per exercise: rep_range: [8, 15] | sets: 4 | increment_kg: 2.5 | active: false | note: ...",  # noqa: E501
        "# `variant` = variant name without the gym suffix; the engine uses the generic variant or",
        "# the one at your current gym.",
        "version: 1",
        "generated:",
        f"  from: {m.get('from')}",
        f"  to: {m.get('to')}",
        f"  gym: {gym or 'n/a'}",
        f"  sessions: {{upper: {m.get('sessions', {}).get('upper', 0)}, lower: {m.get('sessions', {}).get('lower', 0)}}}",  # noqa: E501
        "days:",
    ]
    for kind in KINDS:
        lines.append(f"  {kind}:")
        for e in program.days.get(kind, []):
            parts = [f"exercise: {e.exercise}", f"variant: {_q(e.variant)}", f"sets: {e.sets}"]
            lines.append("    - {" + ", ".join(parts) + "}")
    return "\n".join(lines) + "\n"


def _q(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"' if any(c in s for c in ",:#{}[]'") else s
