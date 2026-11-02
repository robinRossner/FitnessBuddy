"""Schedule rule family: the `schedule_changes` of the advisor output, move / skip / swap /
shorten / replace a planned session of the current or next week. Each is validated on its own
against the same hard rules the engine's missed-session policy uses (engine/core/schedule_rules.py),
in the order given, on a working layout that already contains the earlier valid changes. Claude
never sets a weight or a pace: a change only re-places or shortens a session, the engine
recomputes its content.

Rules: only the current and next week and never the past; the target must exist (a planned,
still open session of that kind on that date); a session is changed at most once per proposal;
move / swap stay inside one week; max 1 hard session per day, never two hard days in a row, the
24 h rule after lower, max 2 sessions per day; `shorten` only cuts runs, by at most
`schedule.shorten_max_pct` (50 %); `replace` only goes to an equal or easier kind
(`schedule.kind_load`) unless it makes up a missed session of that kind; the week's runs must
stay under the weekly volume cap.
Risk: skip, shorten and replace-by-an-easier-kind are low; move, swap and any other replace
are medium.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from apps.fitness.advisor.rules.base import fmt
from apps.fitness.advisor.rules.overrides import override_problem
from apps.fitness.advisor.rules.risk import LOW, MEDIUM, schedule_risk
from apps.fitness.engine.core import schedule_rules as SR
from apps.fitness.engine.core.config import EngineConfig
from apps.fitness.engine.store import data as D
from apps.fitness.engine.store import schedule_edit as SE

SCHEDULE_ACTIONS = ("move", "skip", "swap", "shorten", "replace")


@dataclass(frozen=True)
class ScheduleVerdict:
    valid: bool
    error: str | None
    risk: str | None
    label: str
    session_id: str | None  # the planned session the change is about
    edits: tuple[SR.Edit, ...] = ()
    touched: tuple[str, ...] = ()  # planned session ids the change moves / alters
    makeup_for: str | None = None


@dataclass
class ScheduleContext:
    """The schedule a change is validated against (`work` accumulates the accepted changes)."""

    cfg: EngineConfig
    today: date
    rules: SR.Rules
    base: SR.Layout  # the plan as Claude saw it
    work: SR.Layout  # base + accepted changes
    week_start: date  # Monday of the current week
    rooms: dict[date, float | None] = field(default_factory=dict)  # volume room per week
    touched: set[str] = field(default_factory=set)

    @property
    def horizon_end(self) -> date:
        return self.week_start + timedelta(weeks=self.cfg.schedule_cfg.horizon_weeks)


def build_schedule_context(db: Session, cfg: EngineConfig, today: date) -> ScheduleContext:
    ws = today - timedelta(days=today.weekday())
    weeks = tuple(ws + timedelta(weeks=i) for i in range(cfg.schedule_cfg.horizon_weeks))
    rows = [
        r
        for w in weeks
        for r in SE.load_rows(db, w)
        if r.day >= ws - timedelta(days=SE.WINDOW_BEFORE)
    ]
    seen: dict[str, Any] = {r.id: r for r in rows}
    layout = SR.Layout(
        [SE.sess_from_row(r) for r in seen.values()],
        D.lower_days(db, ws - timedelta(days=SE.WINDOW_BEFORE), today),
    )
    ctx = ScheduleContext(
        cfg,
        today,
        SR.Rules.from_config(cfg),
        layout,
        layout.copy(),
        ws,
    )
    for w in weeks:
        ctx.rooms[w] = SE.volume_room(db, cfg, today, w)
    return ctx


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _fmt_day(d: date) -> str:
    return SR.weekday(d)


def _pretty(kind: str) -> str:
    return SR.PRETTY.get(kind, kind)


def schedule_label(
    action: str,
    target: str,
    d: date | None,
    to_d: date | None,
    new_kind: str | None,
    distance: float | None,
    partner_kind: str | None = None,
) -> str:
    t = _pretty(SR.normalize_kind(target) or target)
    day = _fmt_day(d) if d else "?"
    if action == "move":
        return f"Move {t} {day} → {_fmt_day(to_d) if to_d else '?'}"
    if action == "swap":
        other = f"{_pretty(partner_kind)} " if partner_kind else ""
        return f"Swap {t} {day} ↔ {other}{_fmt_day(to_d) if to_d else '?'}"
    if action == "skip":
        return f"Skip {t} {day}"
    if action == "shorten":
        km = f" to {fmt(distance)} km" if distance is not None else ""
        return f"Shorten {t} {day}{km}"
    if action == "replace":
        nk = _pretty(SR.normalize_kind(new_kind) or new_kind or "?")
        return f"Replace {t} {day} with {nk}"
    return f"{action} {t} {day}"


def _bad(label: str, error: str) -> ScheduleVerdict:
    return ScheduleVerdict(False, error, None, label, None)


def _find(layout: SR.Layout, d: date, kind: str) -> SR.Sess | None:
    return next((s for s in layout.on(d) if s.kind == kind and s.is_open), None)


def _parse_day(raw: Any) -> date | None:
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def validate_schedule_change(
    ctx: ScheduleContext,
    *,
    day: Any,
    action: str,
    target: str,
    to_day: Any = None,
    new_kind: str | None = None,
    distance_km: float | None = None,
    reason: str = "",
) -> ScheduleVerdict:
    """Validate one schedule change. Pure over `ctx` (use `accept_schedule` to fold it in)."""
    d = _parse_day(day)
    to_d = _parse_day(to_day) if to_day not in (None, "") else None
    kind = SR.normalize_kind(target)
    nk = SR.normalize_kind(new_kind) if new_kind else None
    label = schedule_label(action, target, d, to_d, new_kind, distance_km)
    cfg = ctx.cfg
    sc = cfg.schedule_cfg
    if action not in SCHEDULE_ACTIONS:
        return _bad(label, f"unknown action '{action}' (use one of {', '.join(SCHEDULE_ACTIONS)})")
    if d is None:
        return _bad(label, "date must be YYYY-MM-DD")
    if kind is None:
        return _bad(label, f"unknown session kind '{target}'")
    if d < ctx.today:
        return _bad(label, f"{d.isoformat()} is in the past: only today and later can change")
    if d >= ctx.horizon_end:
        return _bad(label, "only the current and the next week can change")
    problem = override_problem(reason or "")
    if problem:
        return _bad(label, f"the reason {problem}: schedule changes never set weights or paces")
    # fields must match the action
    if action in ("move", "swap") and to_d is None:
        return _bad(label, f"{action} needs a valid to_date")
    if action not in ("move", "swap") and to_day not in (None, ""):
        return _bad(label, "to_date only applies to move and swap")
    if action == "replace" and not nk:
        return _bad(label, "replace needs a valid new_kind")
    if action != "replace" and new_kind:
        return _bad(label, "new_kind only applies to replace")
    if action == "shorten" and (distance_km is None or not math.isfinite(distance_km)):
        return _bad(label, "shorten needs distance_km")
    if action != "shorten" and distance_km is not None:
        return _bad(label, "distance_km only applies to shorten (changes never set paces)")

    sess = _find(ctx.base, d, kind)
    if sess is None:
        return _bad(label, f"no open planned {_pretty(kind)} on {d.isoformat()}")
    if sess.id in ctx.touched:
        return _bad(label, "this session is already changed by an earlier change in the proposal")
    if kind == SR.REST and action not in ("swap", "replace"):
        return _bad(label, "a rest day can only be swapped with a session or replaced")
    ws = _monday(d)
    edits: list[SR.Edit] = []
    touched = [sess.id]
    risk = MEDIUM
    makeup_for: str | None = None
    partner: SR.Sess | None = None

    if action == "skip":
        edits = [SR.Edit(sess.id, skip=True, reason=reason)]
        risk = LOW
    elif action == "shorten":
        if kind not in (SR.EASY, SR.LONG):
            return _bad(label, "only easy and long runs can be shortened")
        if sess.km is None:
            return _bad(label, "this run has no planned distance to shorten")
        new = round(float(distance_km), 1)  # type: ignore[arg-type]
        floor = sess.km * (1 - sc.shorten_max_pct / 100)
        if new >= sess.km - 1e-9:
            return _bad(label, f"shorten must reduce the distance (planned {fmt(sess.km)} km)")
        if new < floor - 1e-9:
            return _bad(
                label,
                f"shorten may cut at most {fmt(sc.shorten_max_pct)} % "
                f"(planned {fmt(sess.km)} km, minimum {fmt(round(floor, 1))} km)",
            )
        edits = [SR.Edit(sess.id, distance_km=new, reason=reason)]
        risk = LOW
    elif action == "replace":
        assert nk is not None
        if nk == kind:
            return _bad(label, f"already a {_pretty(kind)}: nothing to replace")
        missed = [
            m
            for m in ctx.base.sessions
            if m.status == "skipped"
            and m.kind == nk
            and _monday(m.day) == ws
            and m.day < ctx.today
            and not any(o.makeup_for == m.id for o in ctx.work.sessions)
        ]
        if ctx.rules.load(nk) <= ctx.rules.load(kind):
            easier = ctx.rules.load(nk) < ctx.rules.load(kind)
            risk = schedule_risk("replace", easier=easier)
            edits = [SR.Edit(sess.id, kind=nk, reason=reason)]
        elif missed:
            m = missed[0]
            makeup_for = m.id
            risk = MEDIUM
            edits = [SR.Edit(sess.id, kind=nk, moved_from=m.day, makeup_for=m.id, reason=reason)]
        else:
            return _bad(
                label,
                f"replace may only go to an equal or easier kind: {_pretty(nk)} is harder than "
                f"{_pretty(kind)} (only the make-up of a missed {_pretty(nk)} may)",
            )
    else:  # move | swap
        assert to_d is not None
        if to_d == d:
            return _bad(label, "to_date is the same day")
        if to_d < ctx.today:
            return _bad(label, f"{to_d.isoformat()} is in the past: nothing can be placed there")
        if to_d >= ctx.horizon_end:
            return _bad(label, "only the current and the next week can change")
        if _monday(to_d) != ws:
            return _bad(label, "a session can only move inside its own week")
        there = [s for s in ctx.work.on(to_d) if s.is_open]
        if action == "move":
            rest = next((s for s in there if s.kind == SR.REST and not s.locked), None)
            edits = [SR.Edit(sess.id, day=to_d, moved_from=d, reason=reason)]
            if rest is not None and sess.kind != SR.REST:
                edits.append(
                    SR.Edit(
                        rest.id, day=d, moved_from=to_d, reason=f"made room for a move: {reason}"
                    )
                )
                touched.append(rest.id)
                partner = rest
        else:
            if len(there) != 1:
                return _bad(
                    label,
                    f"swap needs exactly one open session on {to_d.isoformat()} "
                    f"(found {len(there)})",
                )
            partner = there[0]
            if partner.id in ctx.touched:
                return _bad(label, "the other session is already changed by an earlier change")
            edits = [
                SR.Edit(sess.id, day=to_d, moved_from=d, reason=reason),
                SR.Edit(partner.id, day=d, moved_from=to_d, reason=f"swapped: {reason}"),
            ]
            touched.append(partner.id)
        risk = MEDIUM
    if partner is not None and action == "swap":
        label = schedule_label(action, target, d, to_d, new_kind, distance_km, partner.kind)

    # hard rules on the working layout
    after = SR.apply_edits(ctx.work, edits)
    moved = [x for x in (after.get(e.session_id) for e in edits if not e.skip) if x]
    problem = SR.past_problem(moved, ctx.today)
    if problem is None:
        found = SR.new_violations(ctx.work, after, ctx.rules, today=ctx.today)
        problem = found[0] if found else None
    if problem is None and action != "skip":
        problem = SR.volume_problem(
            ctx.work, after, cfg, today=ctx.today, week_start=ws, room_km=ctx.rooms.get(ws)
        )
    if problem:
        return _bad(label, problem)
    return ScheduleVerdict(
        True, None, risk, label, sess.id, tuple(edits), tuple(touched), makeup_for
    )


def accept_schedule(ctx: ScheduleContext, verdict: ScheduleVerdict) -> None:
    """Fold a valid schedule change into the working layout (later changes see it)."""
    ctx.work = SR.apply_edits(ctx.work, verdict.edits)
    ctx.touched.update(verdict.touched)
