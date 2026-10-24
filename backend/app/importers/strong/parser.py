"""Parsing of Strong app CSV exports into workouts / rows. No DB access.

Handles the observed export (comma, `.` decimals, kg, no unit columns) plus the common
variants of other Strong versions: `;` delimiter, `,` decimals, lb units, `Weight Unit` /
`Distance Unit` columns, `Notes` / `Workout Notes`, W/D/F set markers.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.db.types import SessionKind, SetType

LB_TO_KG = 0.45359237
DIST_TO_M = {"km": 1000.0, "m": 1.0, "mi": 1609.344, "miles": 1609.344, "ft": 0.3048, "yd": 0.9144}
WEIGHT_TO_KG = {"kg": 1.0, "kgs": 1.0, "lb": LB_TO_KG, "lbs": LB_TO_KG}

DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")
SET_MARKERS = {"W": SetType.WARMUP, "D": SetType.DROP, "F": SetType.FAILURE}
SKIP_EXERCISES = {"rest timer"}

# canonical column key -> accepted header spellings (lowercase, unit suffix stripped)
COLUMNS = {
    "date": ("date",),
    "workout_name": ("workout name",),
    "duration": ("duration", "workout duration"),
    "exercise_name": ("exercise name", "exercise"),
    "set_order": ("set order", "set"),
    "weight": ("weight",),
    "weight_unit": ("weight unit",),
    "reps": ("reps",),
    "distance": ("distance",),
    "distance_unit": ("distance unit",),
    "seconds": ("seconds", "duration (s)", "time"),
    "rpe": ("rpe",),
    "notes": ("notes",),
    "workout_notes": ("workout notes",),
}
REQUIRED = ("date", "exercise_name")


class ParseError(ValueError):
    pass


# --------------------------------------------------------------------------- field parsers


def parse_number(raw: str | None) -> float | None:
    """'12.5', '12,5', '1,234.5', '' -> float/None."""
    if raw is None:
        return None
    s = raw.strip().replace(" ", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        # the later separator is the decimal one
        s = (
            s.replace(".", "").replace(",", ".")
            if s.rfind(",") > s.rfind(".")
            else s.replace(",", "")
        )
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError as exc:
        raise ParseError(f"not a number: {raw!r}") from exc


_DUR_RE = re.compile(r"^\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\s*(?:(\d+)\s*s)?\s*$", re.I)
_HMS_RE = re.compile(r"^\s*(\d+):(\d{1,2})(?::(\d{1,2}))?\s*$")


def parse_duration(raw: str | None) -> int | None:
    """'1h 7m' / '45m' / '1h' / '30s' / '1:07:00' -> seconds. Empty -> None."""
    if raw is None or not raw.strip():
        return None
    if m := _HMS_RE.match(raw):
        h, mnt, sec = int(m[1]), int(m[2]), int(m[3] or 0)
        if m[3] is None:  # H:MM
            return h * 3600 + mnt * 60
        return h * 3600 + mnt * 60 + sec
    m = _DUR_RE.match(raw)
    if not m or not any(m.groups()):
        raise ParseError(f"unrecognised duration: {raw!r}")
    h, mnt, sec = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mnt * 60 + sec


def parse_start(raw: str) -> datetime:
    s = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ParseError(f"unrecognised date: {raw!r}")


def parse_set_order(raw: str | None) -> tuple[SetType, str | None]:
    """Returns (set_type, problem). Numeric -> working; W/D/F markers -> warmup/drop/failure."""
    s = (raw or "").strip()
    if not s:
        return SetType.WORKING, "empty set order"
    if s.isdigit():
        return SetType.WORKING, None
    marker = SET_MARKERS.get(s.upper())
    if marker is not None:
        return marker, None
    return SetType.WORKING, f"unknown set order {s!r}"


def to_utc(local: datetime, tz_name: str) -> tuple[datetime, str | None]:
    """Naive local -> aware UTC. DST: ambiguous/nonexistent times resolved with fold=0, flagged."""
    tz = ZoneInfo(tz_name)
    first = local.replace(tzinfo=tz, fold=0)
    second = local.replace(tzinfo=tz, fold=1)
    utc = first.astimezone(UTC)
    flag = None
    if first.utcoffset() != second.utcoffset():
        roundtrip = utc.astimezone(tz).replace(tzinfo=None)
        flag = "nonexistent" if roundtrip != local else "ambiguous"
    return utc, flag


_KIND_PATTERNS: list[tuple[re.Pattern[str], SessionKind]] = [
    (re.compile(r"\bpush\b", re.I), SessionKind.PUSH),
    (re.compile(r"\bpull\b", re.I), SessionKind.PULL),
    (re.compile(r"\blegs?\b", re.I), SessionKind.LEGS),
    (re.compile(r"\bupper\b", re.I), SessionKind.UPPER),
    (re.compile(r"\blower\b", re.I), SessionKind.LOWER),
    (re.compile(r"cal(l)?isthenics", re.I), SessionKind.CALISTHENICS),
]


def derive_session_kind(workout_name: str | None) -> SessionKind:
    for pattern, kind in _KIND_PATTERNS:
        if workout_name and pattern.search(workout_name):
            return kind
    return SessionKind.OTHER


# --------------------------------------------------------------------------- data classes


@dataclass
class StrongRow:
    source_file: str
    line_no: int
    start_local: datetime
    workout_name: str | None
    duration_s: int | None
    exercise_name: str
    set_order_raw: str
    set_type: SetType
    weight_kg: float | None
    reps: int | None
    duration_hold_s: float | None
    distance_m: float | None
    rpe: float | None
    notes: str | None
    workout_notes: str | None
    issues: list[str] = field(default_factory=list)  # non-fatal oddities (for the report)
    # assigned by assign_blocks()
    exercise_order: int = 0
    block_occurrence: int = 0
    set_index: int = 0

    @property
    def is_cardio(self) -> bool:
        return bool(self.distance_m)

    @property
    def is_timed_hold(self) -> bool:
        return self.reps is None and bool(self.duration_hold_s) and not self.distance_m

    def content_key(self) -> tuple:
        return (
            self.exercise_name,
            self.set_type,
            self.weight_kg,
            self.reps,
            self.duration_hold_s,
            self.distance_m,
            self.rpe,
            self.notes,
        )


@dataclass
class Workout:
    start_local: datetime
    name: str | None
    duration_s: int | None
    notes: str | None
    source_file: str
    rows: list[StrongRow] = field(default_factory=list)

    def content_key(self) -> tuple:
        return (self.name, self.duration_s, self.notes, tuple(r.content_key() for r in self.rows))


@dataclass
class SkippedRow:
    source_file: str
    line_no: int
    reason: str
    exercise_name: str | None = None


@dataclass
class ParsedFile:
    path: Path
    delimiter: str
    columns: dict[str, str]
    rows_read: int = 0
    workouts: dict[datetime, Workout] = field(default_factory=dict)
    skipped: list[SkippedRow] = field(default_factory=list)
    is_strong: bool = True

    @property
    def max_date(self) -> datetime | None:
        return max(self.workouts, default=None)

    @property
    def min_date(self) -> datetime | None:
        return min(self.workouts, default=None)


# --------------------------------------------------------------------------- file parsing


def _normalise_header(h: str) -> tuple[str, str | None]:
    """'Weight (lbs)' -> ('weight', 'lbs')."""
    h = h.strip().lstrip("﻿")
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", h)
    if m and m[2].strip().lower() in {*WEIGHT_TO_KG, *DIST_TO_M}:
        return m[1].strip().lower(), m[2].strip().lower()
    return h.lower(), None


def sniff_delimiter(header_line: str) -> str:
    counts = {d: header_line.count(d) for d in (",", ";", "\t")}
    return max(counts, key=lambda d: counts[d]) if any(counts.values()) else ","


def _map_columns(header: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Returns (canonical key -> actual header, canonical key -> unit from header)."""
    mapping: dict[str, str] = {}
    units: dict[str, str] = {}
    for actual in header:
        norm, unit = _normalise_header(actual)
        for key, names in COLUMNS.items():
            if norm in names and key not in mapping:
                mapping[key] = actual
                if unit:
                    units[key] = unit
                break
    return mapping, units


def looks_like_strong(header: list[str]) -> bool:
    cols, _ = _map_columns(header)
    return all(k in cols for k in REQUIRED) and ("set_order" in cols or "reps" in cols)


def parse_file(
    path: Path,
    *,
    default_weight_unit: str = "kg",
    default_distance_unit: str = "km",
) -> ParsedFile:
    text = path.read_text(encoding="utf-8-sig")
    first_line = text.split("\n", 1)[0]
    delim = sniff_delimiter(first_line)
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delim)
    header = reader.fieldnames or []
    cols, header_units = _map_columns(header)
    parsed = ParsedFile(path=path, delimiter=delim, columns=cols)
    if not looks_like_strong(header):
        parsed.is_strong = False
        return parsed

    def get(rec: dict[str, str | None], key: str) -> str | None:
        col = cols.get(key)
        val = rec.get(col) if col else None
        return val.strip() if isinstance(val, str) else None

    for line_no, rec in enumerate(reader, start=2):
        parsed.rows_read += 1
        name = (get(rec, "exercise_name") or "").strip()
        try:
            if not name:
                raise ParseError("missing exercise name")
            if name.lower() in SKIP_EXERCISES:
                parsed.skipped.append(SkippedRow(path.name, line_no, "rest timer row", name))
                continue
            start = parse_start(get(rec, "date") or "")
            row = _parse_row(
                rec,
                get,
                path.name,
                line_no,
                start,
                name,
                header_units,
                default_weight_unit,
                default_distance_unit,
            )
        except ParseError as exc:
            parsed.skipped.append(SkippedRow(path.name, line_no, f"parse error: {exc}", name))
            continue
        if row is None:
            parsed.skipped.append(
                SkippedRow(path.name, line_no, "empty set (no reps, seconds or distance)", name)
            )
            continue
        wk = parsed.workouts.get(start)
        if wk is None:
            wk = parsed.workouts[start] = Workout(
                start_local=start,
                name=row.workout_name,
                duration_s=row.duration_s,
                notes=row.workout_notes,
                source_file=path.name,
            )
        wk.rows.append(row)
    for wk in parsed.workouts.values():
        assign_blocks(wk.rows)
    return parsed


def _parse_row(
    rec, get, file_name, line_no, start, name, header_units, default_wu, default_du
) -> StrongRow | None:
    issues: list[str] = []
    set_order_raw = get(rec, "set_order") or ""
    set_type, problem = parse_set_order(set_order_raw)
    if problem:
        issues.append(problem)

    w_unit = (get(rec, "weight_unit") or header_units.get("weight") or default_wu).lower()
    d_unit = (get(rec, "distance_unit") or header_units.get("distance") or default_du).lower()
    if w_unit not in WEIGHT_TO_KG:
        raise ParseError(f"unknown weight unit {w_unit!r}")
    if d_unit not in DIST_TO_M:
        raise ParseError(f"unknown distance unit {d_unit!r}")

    weight = parse_number(get(rec, "weight"))
    weight_kg = round(weight * WEIGHT_TO_KG[w_unit], 3) if weight is not None else None
    reps_f = parse_number(get(rec, "reps")) or 0.0
    seconds = parse_number(get(rec, "seconds")) or 0.0
    distance = parse_number(get(rec, "distance")) or 0.0
    rpe = parse_number(get(rec, "rpe"))

    if reps_f != int(reps_f):
        issues.append(f"fractional reps {reps_f}")
    reps: int | None = round(reps_f) if reps_f > 0 else None
    hold = seconds if seconds > 0 else None
    distance_m = round(distance * DIST_TO_M[d_unit], 1) if distance > 0 else None
    if reps is None and hold is None and distance_m is None:
        return None

    return StrongRow(
        source_file=file_name,
        line_no=line_no,
        start_local=start,
        workout_name=get(rec, "workout_name") or None,
        duration_s=parse_duration(get(rec, "duration")),
        exercise_name=name,
        set_order_raw=set_order_raw,
        set_type=set_type,
        weight_kg=weight_kg,
        reps=reps,
        duration_hold_s=hold,
        distance_m=distance_m,
        rpe=rpe,
        notes=get(rec, "notes") or None,
        workout_notes=get(rec, "workout_notes") or None,
        issues=issues,
    )


def assign_blocks(rows: list[StrongRow]) -> None:
    """Group consecutive rows of the same exercise into blocks.

    A block also ends when a numeric Set Order restarts (1,2,3,1,2 of the same exercise).
    Each row gets: exercise_order (block position in the workout), block_occurrence (n-th block
    of this exercise in the workout) and set_index (1-based position within its block; the
    Strong Set Order is not used because markers like 'F' replace the number).
    """
    occurrences: dict[str, int] = defaultdict(int)
    order = 0
    prev_name: str | None = None
    prev_num: int | None = None
    idx = 0
    for row in rows:
        num = int(row.set_order_raw) if row.set_order_raw.isdigit() else None
        new_block = row.exercise_name != prev_name or (
            num is not None and prev_num is not None and num <= prev_num
        )
        if new_block:
            order += 1
            occurrences[row.exercise_name] += 1
            idx = 0
            prev_num = None
        idx += 1
        row.exercise_order = order
        row.block_occurrence = occurrences[row.exercise_name]
        row.set_index = idx
        prev_name = row.exercise_name
        if num is not None:
            prev_num = num


def discover_files(paths: list[Path]) -> list[Path]:
    """Expand dirs to all *.csv below them (sorted, deduplicated)."""
    out: set[Path] = set()
    for p in paths:
        if p.is_dir():
            out.update(x for x in p.rglob("*.csv") if x.is_file())
        elif p.is_file():
            out.add(p)
    return sorted(out)
