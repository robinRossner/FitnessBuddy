"""The lifting program (live copy `settings.live_program_path`, seeded from
data/program/program.yaml): which exercises, in which order, how many sets.

The user trains a fixed Upper / Lower / Upper week; both upper days are the same full-upper
session. The file is generated once from recent history (`engine program generate`) and is then
the user's to edit (the app's "Update template" rewrites a day's list); the engine only reads it.
Per-exercise optional keys: `rep_range: [8, 15]`,
`sets`, `increment_kg` (flat load step instead of the equipment default), `active: false`,
`note`. `variant` is the variant name WITHOUT the gym suffix: the engine picks the generic
variant or the one at the current gym.
"""

from __future__ import annotations

import copy
import math
import os
import shutil
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from statistics import fmean, median
from typing import Any

from apps.fitness.engine.core.config_files import cached_yaml

KINDS = ("upper", "lower")
BACKUPS_KEEP = 20


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

    def by_exercise(self) -> dict[str, ProgramExercise]:
        """Active entries by catalog slug (upper before lower; the first entry of a slug wins)."""
        out: dict[str, ProgramExercise] = {}
        for kind in KINDS:
            for e in self.exercises(kind):
                out.setdefault(e.exercise, e)
        return out


def load_program(path: Path) -> Program:
    data = cached_yaml(path) or {}  # shared parse: read only
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
    return Program(days, copy.deepcopy(data.get("generated") or {}))


def seed_live_program(live: Path, seed: Path) -> None:
    """First use: the live copy starts as a copy of the repo's program.yaml (the seed). Editing the
    seed afterwards has no effect; delete the live file to re-seed."""
    if live.exists():
        return
    if not seed.exists():
        raise FileNotFoundError(
            f"{seed} does not exist: run `python -m apps.fitness.cli engine program generate`"
        )
    live.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(live, seed.read_text(encoding="utf-8"))


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def backup_program(live: Path, *, keep: int = BACKUPS_KEEP) -> Path | None:
    """Copy the live file to `program.backups/program_YYYYmmdd-HHMMSS.yaml` (last `keep` stay)."""
    if not live.exists():
        return None
    folder = live.with_name("program.backups")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = folder / f"program_{stamp}.yaml"
    n = 1
    while target.exists():  # several writes within one second
        target = folder / f"program_{stamp}-{n}.yaml"
        n += 1
    shutil.copy2(live, target)
    for old in sorted(folder.glob("program_*.yaml"))[:-keep]:
        old.unlink(missing_ok=True)
    return target


def write_program_text(live: Path, text: str) -> Path | None:
    """Replace the live file with `text` (a backup of the old one first, atomic rename)."""
    backup = backup_program(live)
    live.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(live, text)
    return backup


def set_kind_slots(live: Path, kind: str, slots: list[dict[str, Any]]) -> Path | None:
    """Rewrite only `days.<kind>` of the live file (comments and other keys stay), after a backup.
    Returns the backup path."""
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    yml = YAML()  # round trip: keeps comments and the flow style of the slots
    yml.width = 4096
    data = yml.load(live.read_text(encoding="utf-8")) or CommentedMap()
    if data.get("days") is None:
        data["days"] = CommentedMap()
    seq = CommentedSeq()
    for slot in slots:
        m = CommentedMap()
        for k, v in slot.items():
            if isinstance(v, list):
                inner = CommentedSeq(v)
                inner.fa.set_flow_style()
                v = inner
            m[k] = v
        m.fa.set_flow_style()
        seq.append(m)
    data["days"][kind] = seq
    backup = backup_program(live)
    tmp = live.with_name(live.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yml.dump(data, f)
    os.replace(tmp, live)
    return backup


def apply_overrides(program: Program, overrides: dict[str, Any] | None) -> Program:
    """Program with the config's per-exercise overrides applied (`exercise_overrides` in the engine
    config, keyed by catalog slug; set by the advisor): rep_range, sets, variant (`rest_s` is not
    a program matter: apps/fitness/engine/rest.py). They win over
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
