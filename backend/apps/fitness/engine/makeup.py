"""Missed-session policy: what happens to the rest of the week when a planned session was missed
(`persist.reconcile` marked it `skipped`). All thresholds are in the config `schedule` section.

Priority (`schedule.makeup_priority`, default lower, intervals, upper, long, easy):

- lower / intervals: take over the slot of a later easy run (`makeup_replaceable`) or the rest day
  of the same week, the earliest slot that breaks no hard rule;
- upper: the same, but only if such a later slot exists;
- runs (intervals, long run; v2, docs/research/running-progression.md scenario 6): a missed run
  never adds volume. It moves by at most `run_makeup_max_shift_days` (1) day, onto a later easy
  run or the rest day, if the hard rules allow; otherwise it is dropped and the SAME session is
  planned again next week (the quality cycle only advances on a session that was run, and the
  long run is planned from the last long run that happened). A dropped run needs no advisor;
- easy: dropped, no make-up (`makeup_never`; the day after Wednesday is an upper day anyway).

Hard rules (`schedule_rules.py`): max 1 hard session per day, never two hard days in a row, the
24 h rule after lower, max 2 sessions per day, nothing in the past, the weekly volume cap. A
make-up never doubles up a day: it takes over a slot. Make-ups are placed as a lock on the taken
slot (`planned_sessions.schedule_lock`), so every later engine run keeps them. If no valid
placement exists the session is dropped and an advisor trigger `missed_session` is recorded.
Every decision is written to decisions_log with a human reason.

Progression after a miss needs no code: interval progression and lift targets are computed from
the last COMPLETED session, running volume from the km actually run, and the long run from the last
long run that happened, so a skipped session never advances anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from apps.fitness.db.models import PlannedSession
from apps.fitness.engine import schedule_edit as E
from apps.fitness.engine.config import EngineConfig
from apps.fitness.engine.decisions import add_trigger, log_decision
from apps.fitness.engine.schedule_rules import (
    PRETTY,
    REST,
    RUN_KINDS,
    Edit,
    Layout,
    Rules,
    apply_edits,
    new_violations,
    past_problem,
    short_kind,
    volume_problem,
    weekday,
)

log = logging.getLogger(__name__)


@dataclass
class MakeupResult:
    session_id: str
    missed_day: date
    kind: str
    action: str  # moved | dropped
    reason: str
    target_id: str | None = None
    target_day: date | None = None
    trigger: bool = False


@dataclass
class MakeupSummary:
    results: list[MakeupResult] = field(default_factory=list)

    @property
    def moved(self) -> list[MakeupResult]:
        return [r for r in self.results if r.action == "moved"]

    @property
    def dropped(self) -> list[MakeupResult]:
        return [r for r in self.results if r.action == "dropped"]


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _priority(cfg: EngineConfig, kind: str) -> int:
    order = list(cfg.schedule_cfg.makeup_priority)
    short = short_kind(kind)
    return order.index(short) if short in order else len(order)


def _candidate_days(
    kind: str, ws: date, today: date, cfg: EngineConfig, missed_day: date | None = None
) -> list[date]:
    sc = cfg.schedule_cfg
    if kind in RUN_KINDS and missed_day is not None:
        days = [missed_day + timedelta(days=k) for k in range(1, sc.run_makeup_max_shift_days + 1)]
        return [d for d in days if d >= today and _monday(d) == ws]
    return [ws + timedelta(days=i) for i in range(7) if ws + timedelta(days=i) >= today]


def _takeover_slots(layout: Layout, day: date, cfg: EngineConfig) -> list:
    """Sessions on `day` whose slot a make-up may take over, best first (easy run, then rest)."""
    sc = cfg.schedule_cfg
    out = []
    for s in layout.on(day):
        if not s.is_open or s.locked:
            continue
        if short_kind(s.kind) in sc.makeup_replaceable:
            out.append((0, s))
        elif s.kind == REST and sc.makeup_use_rest_day:
            out.append((1, s))
    out.sort(key=lambda t: t[0])
    return [s for _p, s in out]


def _describe_slot(s) -> str:
    return "the rest day" if s.kind == REST else f"the {PRETTY.get(s.kind, s.kind)}"


def _place(
    db: Session,
    missed: PlannedSession,
    cfg: EngineConfig,
    today: date,
    ws: date,
) -> tuple[Edit | None, str | None, list[str]]:
    """The first valid make-up edit for `missed`, or (None, None, why every candidate failed)."""
    rules = Rules.from_config(cfg)
    layout = E.load_layout(db, ws, today)
    room = E.volume_room(db, cfg, today, ws)
    why: list[str] = []
    for day in _candidate_days(missed.kind, ws, today, cfg, missed.day):
        for slot in _takeover_slots(layout, day, cfg):
            edit = Edit(
                session_id=slot.id,
                kind=missed.kind,
                moved_from=missed.day,
                makeup_for=missed.id,
            )
            after = apply_edits(layout, [edit])
            problems = new_violations(layout, after, rules, today=today)
            problem = past_problem([after.get(slot.id)], today) if after.get(slot.id) else None
            vol = volume_problem(layout, after, cfg, today=today, week_start=ws, room_km=room)
            problems += [p for p in (problem, vol) if p]
            if problems:
                why.append(f"{weekday(day)}: {problems[0]}")
                continue
            return edit, _describe_slot(slot), why
    return None, None, why


def _log(db, missed, *, rule, reason, output, planned_id, config_version_id):
    log_decision(
        db,
        kind="scheduling",
        rule=rule,
        inputs={
            "missed_session": missed.id,
            "missed_day": missed.day.isoformat(),
            "kind": missed.kind,
        },
        output=output,
        rationale=reason,
        subject_type="planned_session",
        subject_id=missed.id,
        config_version_id=config_version_id,
        planned_session_id=planned_id,
    )


def _drop(
    db: Session,
    missed: PlannedSession,
    reason: str,
    *,
    config_version_id: int | None,
    trigger: bool,
    context: dict,
) -> MakeupResult:
    _log(
        db,
        missed,
        rule="makeup:dropped",
        reason=reason,
        output={"action": "dropped", "kind": missed.kind, "day": missed.day.isoformat()},
        planned_id=missed.id,
        config_version_id=config_version_id,
    )
    if trigger:
        add_trigger(
            db,
            type="missed_session",
            dedupe_key=f"missed_session:{missed.id}",
            payload={
                "missed": {
                    "date": missed.day.isoformat(),
                    "kind": missed.kind,
                    "title": (missed.prescription or {}).get("title"),
                },
                "week_start": _monday(missed.day).isoformat(),
                "reason": reason,
                **context,
            },
            subject_type="planned_session",
            subject_id=missed.id,
            day=missed.day,
        )
    return MakeupResult(missed.id, missed.day, missed.kind, "dropped", reason, trigger=trigger)


def handle_missed(
    db: Session,
    *,
    today: date,
    missed: list[PlannedSession],
    cfg: EngineConfig,
    config_version_id: int | None = None,
) -> MakeupSummary:
    """Decide the make-up (or the drop) of every newly missed session. Idempotent per session:
    it only runs for sessions `reconcile` just marked skipped."""
    summary = MakeupSummary()
    sc = cfg.schedule_cfg
    ordered = sorted(missed, key=lambda p: (_priority(cfg, p.kind), p.day))
    for p in ordered:
        kind, short = p.kind, short_kind(p.kind)
        label = PRETTY.get(kind, kind).capitalize()
        when = f"{weekday(p.day)}"
        if kind == REST:
            continue
        if (p.schedule_lock or {}).get("makeup_for"):
            summary.results.append(
                _drop(
                    db,
                    p,
                    f"{label} (a make-up) missed {when} → not planned again",
                    config_version_id=config_version_id,
                    trigger=False,
                    context={},
                )
            )
            continue
        if short in sc.makeup_never:
            summary.results.append(
                _drop(
                    db,
                    p,
                    f"{label} missed {when} → dropped, no make-up for this kind",
                    config_version_id=config_version_id,
                    trigger=False,
                    context={},
                )
            )
            continue
        is_run = kind in RUN_KINDS
        repeat = "; the same session is planned again next week, no volume is made up"
        ws = _monday(p.day)
        if ws != _monday(today):
            tail = repeat if is_run else ""
            summary.results.append(
                _drop(
                    db,
                    p,
                    f"{label} missed {when} {p.day:%d %b} → dropped, that week is over{tail}",
                    config_version_id=config_version_id,
                    # history is not worth a consultation; a run simply repeats next week
                    trigger=(today - p.day).days <= 7 and not is_run,
                    context={"remaining_days": []},
                )
            )
            continue
        edit, took, why = _place(db, p, cfg, today, ws)
        if edit is None:
            shift = sc.run_makeup_max_shift_days
            detail = (
                f" ({why[0]})"
                if why
                else (
                    f" (nothing within {shift} day{'s' if shift != 1 else ''})"
                    if is_run
                    else " (no free slot is left in the week)"
                )
            )
            summary.results.append(
                _drop(
                    db,
                    p,
                    f"{label} missed {when} → dropped: no valid slot{detail}"
                    + (repeat if is_run else ""),
                    config_version_id=config_version_id,
                    trigger=not is_run,
                    context={"checked": why[:6]},
                )
            )
            continue
        row = db.get(PlannedSession, edit.session_id)
        assert row is not None
        reason = f"{label} missed {when} → moved to {weekday(row.day)}, replacing {took}"
        if took == "the rest day":
            reason = f"{label} missed {when} → moved to {weekday(row.day)}, using the rest day"
        edit.reason = reason
        E.write_edit(row, edit, source="engine", action="makeup")
        _log(
            db,
            p,
            rule="makeup:moved",
            reason=reason,
            output={
                "action": "moved",
                "kind": kind,
                "from": p.day.isoformat(),
                "to": row.day.isoformat(),
                "slot": row.id,
                "replaced": took,
            },
            planned_id=row.id,
            config_version_id=config_version_id,
        )
        summary.results.append(MakeupResult(p.id, p.day, kind, "moved", reason, row.id, row.day))
        db.flush()
    return summary
