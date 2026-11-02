"""The hard scheduling rules, as pure functions over plain data. Shared by the engine's
missed-session policy (`makeup.py`) and the validator of the coach's schedule changes
(`apps/fitness/advisor/validator.py`), so both obey exactly the same rules (all thresholds from the
config `schedule` / `recovery` sections):

- at most `max_hard_per_day` hard session(s) per day (hard kinds: lower, intervals);
- never two hard days in a row (`no_consecutive_hard_days`);
- the 24 h rule: no intervals within `recovery.hard_after_heavy_lower_h` after a lower day
  (day-level: at least `scheduling.min_gap_days` calendar days apart);
- at most `max_sessions_per_day` sessions per day (rest does not count);
- nothing is placed in the past;
- the weekly run volume cap (`volume_problem`).

A skipped session carries no training load and is ignored by all of them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from apps.fitness.engine.core.config import EngineConfig, ScheduleConfig
from apps.fitness.engine.core.scheduling import min_gap_days

# planned_sessions.kind values
REST, EASY, UPPER, LOWER, INTERVALS, LONG = (
    "rest",
    "easy_run",
    "upper",
    "lower",
    "intervals",
    "long_run",
)
KINDS = (UPPER, LOWER, EASY, INTERVALS, LONG, REST)
SHORT = {
    REST: "rest",
    EASY: "easy",
    UPPER: "upper",
    LOWER: "lower",
    INTERVALS: "intervals",
    LONG: "long",
}
_ALIASES = {
    **{v: k for k, v in SHORT.items()},
    **{k: k for k in KINDS},
    "interval": INTERVALS,
    "easy run": EASY,
    "long run": LONG,
    "easy-run": EASY,
    "long-run": LONG,
}
PRETTY = {
    REST: "rest",
    EASY: "easy run",
    UPPER: "upper",
    LOWER: "lower",
    INTERVALS: "intervals",
    LONG: "long run",
}
RUN_KINDS = (EASY, INTERVALS, LONG)
OPEN = ("proposed", "approved")


def normalize_kind(value: str | None) -> str | None:
    """'easy' / 'easy_run' / 'Easy run' -> 'easy_run'; None if it is not a session kind."""
    if not isinstance(value, str):
        return None
    return _ALIASES.get(value.strip().lower())


def short_kind(kind: str) -> str:
    return SHORT.get(kind, kind)


def weekday(d: date) -> str:
    return d.strftime("%a")


@dataclass
class Sess:
    """One planned session of a week (plain data)."""

    id: str
    day: date
    kind: str
    status: str = "proposed"
    km: float | None = None  # planned run distance (None for lifting / unknown)
    locked: bool = False  # placed by a make-up / change already
    makeup_for: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN

    @property
    def counts(self) -> bool:
        """Carries training load: not rest, not skipped."""
        return self.kind != REST and self.status != "skipped"


@dataclass
class Layout:
    """The sessions around a week (a few days either side), plus lower days that really happened."""

    sessions: list[Sess]
    actual_lower_days: set[date] = field(default_factory=set)

    def copy(self) -> Layout:
        return Layout([replace(s) for s in self.sessions], set(self.actual_lower_days))

    def get(self, sid: str) -> Sess | None:
        return next((s for s in self.sessions if s.id == sid), None)

    def on(self, day: date) -> list[Sess]:
        return [s for s in self.sessions if s.day == day]


@dataclass
class Edit:
    """One change to one planned session (the unit both the make-up policy and the coach's
    schedule changes are made of). `None` fields stay as they are."""

    session_id: str
    day: date | None = None
    kind: str | None = None
    distance_km: float | None = None
    skip: bool = False
    moved_from: date | None = None
    makeup_for: str | None = None
    reason: str = ""


def apply_edits(layout: Layout, edits: Iterable[Edit]) -> Layout:
    """The layout after the edits (a copy; the input is untouched)."""
    out = layout.copy()
    for e in edits:
        s = out.get(e.session_id)
        if s is None:
            continue
        if e.skip:
            s.status = "skipped"
            continue
        if e.day is not None:
            s.day = e.day
        if e.kind is not None:
            s.kind = e.kind
            s.km = None
        if e.distance_km is not None:
            s.km = e.distance_km
        s.locked = True
        if e.makeup_for:
            s.makeup_for = e.makeup_for
    return out


@dataclass(frozen=True)
class Rules:
    cfg: ScheduleConfig
    min_gap: int  # calendar days between a lower day and the next intervals day

    @classmethod
    def from_config(cls, cfg: EngineConfig) -> Rules:
        return cls(cfg.schedule_cfg, min_gap_days(cfg.recovery_cfg.hard_after_heavy_lower_h))

    def is_hard(self, kind: str) -> bool:
        return short_kind(kind) in self.cfg.hard_kinds

    def load(self, kind: str) -> int:
        return self.cfg.kind_load.get(short_kind(kind), 0)


def violations(layout: Layout, rules: Rules, *, today: date) -> list[str]:
    """Every broken rule in the layout, as human sentences (stable text, so before / after
    layouts can be compared). Days before `today` are history and are not judged."""
    out: list[str] = []
    live = [s for s in layout.sessions if s.counts]
    days = sorted({s.day for s in live if s.day >= today})
    hard_days = {s.day for s in live if rules.is_hard(s.kind)}
    lower_days = {s.day for s in live if s.kind == LOWER} | set(layout.actual_lower_days)
    for d in days:
        here = [s for s in live if s.day == d]
        hard = [s for s in here if rules.is_hard(s.kind)]
        if len(hard) > rules.cfg.max_hard_per_day:
            out.append(
                f"{weekday(d)} {d:%d %b} would hold {len(hard)} hard sessions "
                f"(max {rules.cfg.max_hard_per_day} per day)"
            )
        if len(here) > rules.cfg.max_sessions_per_day:
            out.append(
                f"{weekday(d)} {d:%d %b} would hold {len(here)} sessions "
                f"(max {rules.cfg.max_sessions_per_day} per day)"
            )
        if rules.cfg.no_consecutive_hard_days and hard and (d + timedelta(days=1)) in hard_days:
            nxt = d + timedelta(days=1)
            out.append(
                f"hard sessions on {weekday(d)} and {weekday(nxt)} (never two hard days in a row)"
            )
        for s in here:
            if s.kind == INTERVALS:
                for ld in sorted(lower_days):
                    if 0 <= (d - ld).days < rules.min_gap:
                        out.append(
                            f"intervals on {weekday(d)} {d:%d %b} are too soon after the "
                            f"lower-body day {weekday(ld)} {ld:%d %b} (24 h rule)"
                        )
    return out


def new_violations(before: Layout, after: Layout, rules: Rules, *, today: date) -> list[str]:
    """Violations the change introduces (what was already broken before is not blamed on it)."""
    old = set(violations(before, rules, today=today))
    return [v for v in violations(after, rules, today=today) if v not in old]


def past_problem(moved: Iterable[Sess], today: date) -> str | None:
    for s in moved:
        if s.day < today:
            return f"{weekday(s.day)} {s.day:%d %b} is in the past; nothing can be placed there"
    return None


# --------------------------------------------------------------------------- weekly volume


def min_run_km(kind: str, cfg: EngineConfig) -> float:
    """The least the planner can shrink a run of this kind to (it never goes below this)."""
    rc = cfg.running_cfg
    if kind in (EASY, LONG):
        return rc.volume.min_run_km  # the planner shrinks easy and long runs to this under the cap
    if kind == INTERVALS:
        ic = rc.intervals
        work = (ic.reps_min * ic.rep_distance_m + (ic.reps_min - 1) * ic.recovery_distance_m) / 1000
        return round(ic.warmup_min_km + ic.cooldown_min_km + work, 2)
    return 0.0


def run_km_floor(layout: Layout, cfg: EngineConfig, *, today: date, week_start: date) -> float:
    """Least km the week's still-open runs add up to (the planner shrinks to these minimums)."""
    end = week_start + timedelta(days=7)
    total = 0.0
    for s in layout.sessions:
        if s.kind in RUN_KINDS and s.is_open and s.day >= today and week_start <= s.day < end:
            floor = min_run_km(s.kind, cfg)
            total += min(s.km, floor) if s.km is not None and s.km < floor else floor
    return round(total, 2)


def volume_problem(
    before: Layout,
    after: Layout,
    cfg: EngineConfig,
    *,
    today: date,
    week_start: date,
    room_km: float | None,
) -> str | None:
    """The change must not push the week's runs past the weekly volume cap.

    `room_km` = cap minus the km already run this week (None = no cap yet). Only a change that
    ADDS minimum km can fail."""
    if room_km is None:
        return None
    b = run_km_floor(before, cfg, today=today, week_start=week_start)
    a = run_km_floor(after, cfg, today=today, week_start=week_start)
    if a > b + 1e-9 and a > room_km + 1e-9:
        return (
            f"the week's runs would need at least {a:g} km but only {max(room_km, 0):g} km are "
            f"left under the weekly volume cap"
        )
    return None
