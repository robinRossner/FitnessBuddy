"""Weekly scheduling: the config template (Mon..Sun) plus the "no hard run right after heavy
legs" rule. Pure functions over dates.

Slots keep the identity of their *template* position (`mon_upper`, `fri_intervals`), so a slot
that is moved to another day keeps its id (and therefore the planned_session id the app knows).

Swap algorithm (`apply_recovery_rules`), per interval slot in date order:
  1. heavy-lower days = days with a lower-body session (planned lower slots from today on, plus
     lower sessions that were actually done, also in the days before the week);
  2. an interval day violates the rule if it is within `min_gap_days` of a heavy-lower day
     (0 <= interval_day - lower_day < min_gap_days; 24 h -> 2 calendar days, because session times
     are not known and "the next morning" can be < 24 h);
  3. then it is swapped with the EARLIEST later day of the same week that holds an easy run or a
     rest day and does not violate the rule itself (preferring one that is not directly before
     the long run); the displaced easy/rest slot takes the interval slot's old day;
  4. if there is no such day, the interval slot is downgraded to an easy run and a `rule_conflict`
     event is reported.
Only days from `today` on can move; past slots are never touched.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
LIFTING = ("upper", "lower")
HARD_RUNS = ("intervals",)
SWAPPABLE = ("rest", "easy_run")


@dataclass(frozen=True)
class SlotLock:
    """A placement decided outside the template (an engine make-up or an approved coach change),
    stored on the planned session. The planner puts the slot there and never moves it again."""

    day: date
    kind: str | None = None  # final kind (upper | lower | easy_run | ...); None = template kind
    distance_km: float | None = None  # run cap set by a `shorten`
    moved_from: date | None = None
    reason: str | None = None
    source: str = "engine"  # engine | advisor


@dataclass
class Slot:
    id: str  # template identity, e.g. "thu_upper"
    kind: str  # upper | lower | easy_run | intervals | long_run | rest
    template_day: date
    day: date
    notes: list[str] = field(default_factory=list)
    moved_from: date | None = None
    change_reason: str | None = None
    changed_by: str | None = None
    distance_cap_km: float | None = None


def apply_locks(slots: list[Slot], locks: dict[str, SlotLock]) -> None:
    """Put locked slots where their lock says (day / kind / distance cap / reason)."""
    for s in slots:
        lk = locks.get(s.id)
        if lk is None:
            continue
        s.day = lk.day
        if lk.kind:
            s.kind = lk.kind
        s.distance_cap_km = lk.distance_km
        s.moved_from, s.change_reason, s.changed_by = lk.moved_from, lk.reason, lk.source


@dataclass
class ScheduleEvent:
    rule: str  # "swap" | "downgrade"
    slot: str
    from_day: date
    to_day: date | None
    reason: str
    swapped_with: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "slot": self.slot,
            "from": self.from_day.isoformat(),
            "to": self.to_day.isoformat() if self.to_day else None,
            "swapped_with": self.swapped_with,
            "reason": self.reason,
        }


def build_week(week_start: date, template: dict[str, str]) -> list[Slot]:
    if week_start.weekday() != 0:
        raise ValueError("week_start must be a Monday")
    slots = []
    for i, wd in enumerate(WEEKDAYS):
        kind = template.get(wd)
        if not kind:
            continue
        d = week_start + timedelta(days=i)
        slots.append(Slot(f"{wd}_{kind}", kind, d, d))
    return slots


def min_gap_days(hours: float) -> int:
    return math.ceil(hours / 24) + 1


def _violates(day: date, heavy: Iterable[date], gap: int) -> date | None:
    for h in heavy:
        if 0 <= (day - h).days < gap:
            return h
    return None


def apply_recovery_rules(
    slots: list[Slot],
    *,
    today: date,
    actual_lower_days: Iterable[date],
    hard_after_heavy_lower_h: float = 24,
    locked: Iterable[str] = (),
) -> tuple[list[Slot], list[ScheduleEvent]]:
    gap = min_gap_days(hard_after_heavy_lower_h)
    events: list[ScheduleEvent] = []
    locked = set(locked)  # placed by a make-up / coach change: never swapped by this rule

    def heavy_days() -> list[date]:
        planned = [s.day for s in slots if s.kind == "lower" and s.day >= today]
        return sorted({*planned, *actual_lower_days})

    for slot in sorted((s for s in slots if s.kind in HARD_RUNS), key=lambda s: s.day):
        if slot.day < today or slot.id in locked:
            continue
        culprit = _violates(slot.day, heavy_days(), gap)
        if culprit is None:
            continue
        candidates = [
            s
            for s in sorted(slots, key=lambda s: s.day)
            if s.day > slot.day
            and s.day >= today
            and s.kind in SWAPPABLE
            and s.id not in locked
            and _violates(s.day, heavy_days(), gap) is None
        ]
        long_days = {s.day for s in slots if s.kind == "long_run"}
        candidates.sort(
            key=lambda s: (s.day + timedelta(days=1)) in long_days
        )  # stable: earliest first
        why = (
            f"{slot.kind} on {slot.day} is only {(slot.day - culprit).days * 24} h after the "
            f"lower-body day {culprit} (rule: >= {hard_after_heavy_lower_h:g} h)"
        )
        if candidates:
            other = candidates[0]
            old_day = slot.day
            other_old = other.day
            slot.day, other.day = other.day, old_day
            slot.notes.append(f"moved from {old_day} to {slot.day}: lower-body day {culprit}")
            other.notes.append(f"moved from {slot.day} to {old_day}")
            slot.moved_from, slot.change_reason, slot.changed_by = old_day, why, "engine"
            other.moved_from, other.changed_by = other_old, "engine"
            other.change_reason = f"swapped with {slot.kind} ({why})"
            events.append(
                ScheduleEvent("swap", slot.id, old_day, slot.day, why, swapped_with=other.id)
            )
        else:
            events.append(
                ScheduleEvent("downgrade", slot.id, slot.day, None, why + "; no later free day")
            )
            slot.kind = "easy_run"
            slot.notes.append(f"intervals dropped to an easy run: {why}")
    return sorted(slots, key=lambda s: s.day), events
