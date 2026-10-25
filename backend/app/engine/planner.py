"""The weekly planner: plain data in, a `WeekPlan` (sessions + decisions + advisor triggers) out.

No database access here (see `app/engine/data.py` for loading and `persist.py` for writing), and
everything tunable comes from the config object. Steps for each (not yet frozen) day from today:

1. template week (config `weekly_template`) -> recovery rules (no intervals right after heavy
   legs, swap algorithm in `scheduling.py`);
2. readiness for today (deterministic, `readiness.py`), red-streak -> deload window;
3. per session: lifting progression per exercise (`lifting.py`) or run prescription
   (`running.py` + the pace model), with the weekly-volume cap over all planned runs;
4. today's readiness adjustments are written INTO the planned session (yellow: -1 set / easy
   run, red: rest or short easy run) together with the reason;
5. every rule that fired is returned as a decision record; stalls, deloads, rule conflicts,
   HRV anomalies and stagnating running metrics also as advisor triggers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.engine import lifting as L
from app.engine import running as R
from app.engine import scheduling as S
from app.engine.config import EngineConfig
from app.engine.hr import HrZones, zones_from_config
from app.engine.pace import PaceModel, fmt_pace, make_pace_model
from app.engine.program import Program
from app.engine.readiness import (
    GREEN,
    RED,
    YELLOW,
    HealthDay,
    Readiness,
    deload_status,
    hrv_drop_anomaly,
    score_day,
)

# --------------------------------------------------------------------------- input / output


@dataclass
class VariantInfo:
    id: int
    key: str | None
    name: str
    base_name: str
    exercise_slug: str
    exercise_name: str
    equipment_type: str
    load_convention: str
    weight_ladder: list[float] | None = None
    is_timed: bool = False


@dataclass
class LiftContext:
    variant: VariantInfo | None
    history: list[L.Exposure] = field(default_factory=list)
    stall_reset: L.StallReset | None = None


@dataclass
class PlanInputs:
    today: date
    week_start: date
    cfg: EngineConfig
    config_version: str
    program: Program
    lifts: dict[str, LiftContext]  # by program variant base name
    health: list[HealthDay]
    runs: list[R.RunData]  # running activities, oldest first
    actual_lower_days: set[date] = field(default_factory=set)
    frozen_slots: set[str] = field(default_factory=set)
    gym_name: str | None = None


@dataclass
class DecisionRec:
    kind: str
    rule: str
    rationale: str
    inputs: dict[str, Any]
    output: dict[str, Any]
    subject_type: str | None = None
    subject_id: str | None = None
    slot_id: str | None = None


@dataclass
class TriggerRec:
    type: str
    dedupe_key: str
    payload: dict[str, Any]
    subject_type: str | None = None
    subject_id: str | None = None
    day: date | None = None
    slot_id: str | None = None


@dataclass
class DayPlan:
    slot_id: str
    day: date
    kind: str  # final kind: upper | lower | easy_run | intervals | long_run | rest
    template_kind: str
    title: str
    prescription: dict[str, Any]
    readiness: dict[str, Any] | None = None
    adjustments: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "date": self.day.isoformat(),
            "kind": self.kind,
            "template_kind": self.template_kind,
            "title": self.title,
            "prescription": self.prescription,
            "readiness": self.readiness,
            "adjustments": self.adjustments,
            "notes": self.notes,
        }


@dataclass
class WeekPlan:
    week_start: date
    today: date
    config_version: str
    readiness: Readiness
    days: list[DayPlan] = field(default_factory=list)
    decisions: list[DecisionRec] = field(default_factory=list)
    triggers: list[TriggerRec] = field(default_factory=list)
    pace: dict[str, Any] = field(default_factory=dict)
    zones: dict[str, Any] = field(default_factory=dict)
    readiness_history: dict[date, str] = field(default_factory=dict)
    volume: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- helpers

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
            "zone": "Z1-Z2",
            "low": zones.easy_low,
            "high": zones.easy_high,
            "basis": zones.easy_basis,
        }
    return {"zone": "Z4-Z5a", "low": zones.interval_low, "high": zones.interval_high}


def work_km(reps: int, cfg) -> float:
    """Rep + jog-recovery distance of an interval block (no warm-up / cool-down)."""
    ic = cfg.running_cfg.intervals
    return (reps * ic.rep_distance_m + (reps - 1) * ic.recovery_distance_m) / 1000


def interval_km(
    reps: int, cfg, warmup_km: float | None = None, cooldown_km: float | None = None
) -> float:
    ic = cfg.running_cfg.intervals
    wu = ic.warmup_km if warmup_km is None else warmup_km
    cd = ic.cooldown_km if cooldown_km is None else cooldown_km
    return round(wu + cd + work_km(reps, cfg), 2)


class _Planner:
    def __init__(self, inp: PlanInputs):
        self.inp = inp
        self.cfg = inp.cfg
        self.rc = inp.cfg.running_cfg
        self.rd = inp.cfg.readiness_cfg
        self.rec = inp.cfg.recovery_cfg
        self.zones = zones_from_config(inp.cfg)
        self.model: PaceModel = make_pace_model(inp.cfg)
        self.decisions: list[DecisionRec] = []
        self.triggers: list[TriggerRec] = []
        self._increases: dict[str, list[str]] = {}  # slot id -> variants whose load goes up
        self._stall_first: dict[int, date] = {}  # variant id -> first day with a stall reset
        self.today = inp.today
        self._fit_model()
        self.readiness_by_day: dict[date, str] = {}
        for i in range(0, 15):
            d = self.today - timedelta(days=i)
            r = score_day(d, inp.health, self.rd)
            if r.known:
                self.readiness_by_day[d] = r.color
        self.today_readiness = score_day(self.today, inp.health, self.rd)

    # ------------------------------------------------------------------ pace model
    def _fit_model(self) -> None:
        since = self.today - timedelta(weeks=self.rc.vdot.lookback_weeks)
        obs = []
        for r in self.inp.runs:
            if r.day >= since:
                o = R.best_effort(r, self.rc.vdot.min_effort_km * 1000)
                if o:
                    obs.append(o)
        self.model.fit(obs)

    # ------------------------------------------------------------------ main
    def run(self) -> WeekPlan:
        inp = self.inp
        plan = WeekPlan(
            inp.week_start,
            self.today,
            inp.config_version,
            self.today_readiness,
            pace=self.model.describe(),
            zones=self.zones.as_dict(),
        )
        self._readiness_decisions()

        slots = S.build_week(inp.week_start, self.cfg.weekly_template)
        slots, events = S.apply_recovery_rules(
            slots,
            today=self.today,
            actual_lower_days=inp.actual_lower_days,
            hard_after_heavy_lower_h=self.rec.hard_after_heavy_lower_h,
        )
        for ev in events:
            self.decisions.append(
                DecisionRec(
                    "scheduling",
                    f"recovery:{ev.rule}",
                    ev.reason,
                    {"week_start": inp.week_start.isoformat()},
                    ev.as_dict(),
                    "slot",
                    ev.slot,
                    ev.slot,
                )
            )
            if ev.rule == "downgrade":
                self.triggers.append(
                    TriggerRec(
                        "rule_conflict",
                        f"rule_conflict:schedule:{ev.slot}:{inp.week_start}",
                        {"conflict": "intervals vs heavy lower day", **ev.as_dict()},
                        "slot",
                        ev.slot,
                        ev.from_day,
                        ev.slot,
                    )
                )
        todo = [s for s in slots if s.day >= self.today and s.id not in inp.frozen_slots]

        run_km = self._plan_run_volume(todo)
        for s in todo:
            plan.days.append(self._day(s, run_km))
        plan.days.sort(key=lambda d: d.day)
        self._stagnation_triggers()
        self._anomaly_trigger()
        plan.decisions = self.decisions
        plan.triggers = list({t.dedupe_key: t for t in self.triggers}.values())  # once per key
        plan.readiness_history = dict(self.readiness_by_day)
        plan.volume = getattr(self, "_volume", {})
        return plan

    # ------------------------------------------------------------------ readiness
    def _readiness_decisions(self) -> None:
        r = self.today_readiness
        self.decisions.append(
            DecisionRec(
                "readiness",
                "readiness:score",
                f"{r.color}: {r.reason}",
                {
                    "day": r.day.isoformat(),
                    "signals": [s.as_dict() for s in r.signals],
                    "thresholds": self.rd.model_dump(mode="json", exclude={"red"}),
                },
                {"color": r.color, "known": r.known},
                "day",
                r.day.isoformat(),
            )
        )

    def _anomaly_trigger(self) -> None:
        a = hrv_drop_anomaly(self.inp.health, self.today, self.rd)
        if a:
            self.triggers.append(
                TriggerRec(
                    "data_anomaly",
                    f"hrv_drop:{self.today}",
                    {"signal": "hrv", **a},
                    "day",
                    self.today.isoformat(),
                    self.today,
                )
            )
            self.decisions.append(
                DecisionRec(
                    "readiness",
                    "readiness:hrv_drop_anomaly",
                    f"HRV >= {abs(self.rd.hrv_drop_anomaly.pct):g}% below baseline for "
                    f"{a['days']} days: possible illness/overreaching or a sensor issue",
                    {"day": self.today.isoformat()},
                    a,
                    "day",
                    self.today.isoformat(),
                )
            )

    # ------------------------------------------------------------------ running volume
    def _run_hist(self, before: date) -> list[R.RunData]:
        return [r for r in self.inp.runs if r.day < before]

    def _last_interval_eval(self) -> tuple[R.RunData | None, R.IntervalEval | None]:
        for r in reversed(self.inp.runs):
            sp = R.split_intervals(r.laps)
            if sp is not None:
                return r, R.evaluate_intervals(sp, self.zones, self.rc.intervals)
        return None, None

    def _fit_interval_session(self, remaining: float | None, run_slots: dict) -> None:
        """Make the interval session fit its share of the weekly budget.

        Share = the remaining weekly cap minus the minimum easy and long run. 1) shrink warm-up and
        cool-down (proportionally, down to their minimums); 2) if the planned rep count still does
        not fit and the progression only ADDED a rep, keep the last session's rep count instead
        (rule `intervals:volume_hold`); 3) otherwise minimum warm-up/cool-down (the week then
        exceeds the cap and the usual rule_conflict trigger is raised).
        """
        ic, lc = self.rc.intervals, self.rc.long_run
        ip = self._interval_plan
        if remaining is None:
            return
        others = (3.0 if "easy_run" in run_slots else 0.0) + (
            lc.min_km if "long_run" in run_slots else 0.0
        )
        budget = remaining - others
        full, minimum = ic.warmup_km + ic.cooldown_km, ic.warmup_min_km + ic.cooldown_min_km

        def shrink(reps: int) -> tuple[float, float] | None:
            """warm-up/cool-down that fit with `reps`, or None if even the minimums do not."""
            room = budget - work_km(reps, self.cfg)
            if room >= full:
                return ic.warmup_km, ic.cooldown_km
            if room >= minimum:
                f = room / full
                return (
                    max(ic.warmup_min_km, round(ic.warmup_km * f, 1)),
                    max(ic.cooldown_min_km, round(ic.cooldown_km * f, 1)),
                )
            return None

        fit = shrink(ip.reps)
        if fit is not None:
            if fit != (ic.warmup_km, ic.cooldown_km):
                ip.reason += (
                    f"; warm-up/cool-down shortened to {fit[0]:g}/{fit[1]:g} km to fit the "
                    f"{budget:.1f} km interval share of the weekly cap"
                )
            self._wu, self._cd = fit
            return
        last = self._interval_eval
        if last is not None and ip.rule == "intervals:add_rep":
            held = shrink(last.reps)
            why = (
                f"{ip.reps} reps do not fit the {budget:.1f} km interval share of the weekly cap "
                f"({remaining:.1f} km minus minimum easy/long run): keep {last.reps} reps"
            )
            ip.reps, ip.rule, ip.reason = last.reps, "intervals:volume_hold", why
            self._wu, self._cd = held or (ic.warmup_min_km, ic.cooldown_min_km)
            if held is None:
                ip.reason += "; minimum warm-up/cool-down"
            return
        self._wu, self._cd = ic.warmup_min_km, ic.cooldown_min_km
        ip.reason += "; minimum warm-up/cool-down, week exceeds the volume cap"

    def _plan_run_volume(self, todo: list[S.Slot]) -> dict[str, float]:
        """km per run slot after the weekly cap; also fills the run decisions."""
        ic, lc = self.rc.intervals, self.rc.long_run
        run_slots = {s.kind: s for s in todo if s.kind in ("easy_run", "intervals", "long_run")}
        km: dict[str, float] = {}
        self._interval_run, self._interval_eval = self._last_interval_eval()
        self._interval_plan = R.next_interval_plan(self._interval_eval, self.model, ic)
        # long run
        since = self.today - timedelta(days=21)
        recent = [r for r in self.inp.runs if r.day >= since and R.split_intervals(r.laps) is None]
        longest = max(recent, key=lambda r: r.km, default=None)
        passed = False
        self._long_eval = None
        if longest is not None and longest.km >= lc.min_km:
            self._long_eval = R.evaluate_easy_run(longest, self.zones, self.rc.easy)
            passed = self._long_eval.passed and not self._long_eval.decoupling_flag
        base = longest.km if longest is not None and longest.km >= lc.min_km else None
        self._long_km, self._long_reason = R.next_long_run_km(base, passed, lc)
        weekly = R.weekly_km(self.inp.runs)
        cap = R.volume_cap_km(
            weekly,
            self.inp.week_start,
            self.rc.weekly_volume_cap_pct,
            floor_km=self.rc.weekly_volume_floor_km,
        )
        done = sum(r.km for r in self.inp.runs if self.inp.week_start <= r.day <= self.today)
        remaining = None if cap is None else max(cap - done, 0.0)
        self._wu, self._cd = ic.warmup_km, ic.cooldown_km
        if "easy_run" in run_slots:
            km["easy_run"] = self.rc.easy.distance_km
        if "intervals" in run_slots:
            self._fit_interval_session(remaining, run_slots)
            km["intervals"] = interval_km(self._interval_plan.reps, self.cfg, self._wu, self._cd)
        if "long_run" in run_slots:
            km["long_run"] = self._long_km
        fitted, notes = R.fit_to_cap(km, remaining, lc.min_km)
        self._volume = {
            "cap_km": cap,
            "done_km_this_week": round(done, 2),
            "planned_km_before_cap": {k: round(v, 2) for k, v in km.items()},
            "planned_km": {k: round(v, 2) for k, v in fitted.items()},
            "notes": notes,
        }
        if remaining is not None and fitted and sum(fitted.values()) > remaining + 0.5:
            over = {
                "conflict": "weekly volume cap vs the weekly template",
                "cap_km": cap,
                "planned_km": self._volume["planned_km"],
                "note": "the interval session and the minimum easy / long run alone exceed the cap",
            }
            self.triggers.append(
                TriggerRec(
                    "rule_conflict",
                    f"rule_conflict:volume_cap:{self.inp.week_start}",
                    over,
                    "week",
                    self.inp.week_start.isoformat(),
                    self.inp.week_start,
                )
            )
        if km:
            self.decisions.append(
                DecisionRec(
                    "running",
                    "running:weekly_volume_cap",
                    ("; ".join(notes) if notes else "planned volume within the weekly cap")
                    + (
                        f" (cap {cap:g} km)"
                        if cap is not None
                        else " (no running history to cap against)"
                    ),
                    {
                        "weekly_km": {
                            d.isoformat(): round(v, 2) for d, v in sorted(weekly.items())[-6:]
                        },
                        "cap_pct": self.rc.weekly_volume_cap_pct,
                    },
                    self._volume,
                    "week",
                    self.inp.week_start.isoformat(),
                )
            )
        return fitted

    # ------------------------------------------------------------------ one day
    def _day(self, slot: S.Slot, run_km: dict[str, float]) -> DayPlan:
        kind = slot.kind
        if kind in S.LIFTING:
            plan = self._lifting_day(slot)
        elif kind == "easy_run":
            plan = self._easy_day(slot, run_km.get("easy_run", self.rc.easy.distance_km))
        elif kind == "intervals":
            plan = self._interval_day(slot, run_km)
        elif kind == "long_run":
            plan = self._long_day(slot, run_km.get("long_run", self._long_km))
        else:
            plan = DayPlan(slot.id, slot.day, "rest", kind, "Rest", {"type": "rest"})
        plan.notes.extend(slot.notes)
        if slot.day == self.today:
            self._apply_readiness(plan)
        else:
            plan.readiness = {"color": None, "known": False, "reason": "forecast not available yet"}
        return plan

    # ------------------------------------------------------------------ lifting
    def _lifting_day(self, slot: S.Slot) -> DayPlan:
        inp = self.inp
        kind = slot.kind
        ds = deload_status(self.readiness_by_day, slot.day, self.today, self.rec.deload_red_days)
        exercises: list[dict[str, Any]] = []
        increases: list[str] = []
        for order, pe in enumerate(inp.program.exercises(kind), start=1):
            ctx = inp.lifts.get(pe.variant)
            if ctx is None or ctx.variant is None:
                exercises.append(
                    {
                        "order": order,
                        "name": pe.variant,
                        "exercise": pe.exercise,
                        "unresolved": True,
                        "reason": "no variant at the current gym yet: log it once to get targets",
                        "sets": [
                            {
                                "index": i + 1,
                                "set_type": "working",
                                "target_weight": None,
                                "target_reps": None,
                            }
                            for i in range(pe.sets)
                        ],
                    }
                )
                continue
            v = ctx.variant
            spec = L.LiftSpec(
                variant=v,
                rep_range=pe.rep_range or self.cfg.strength.rep_range,
                n_sets=pe.sets,
                increment_kg=pe.increment_kg,
                timed=v.is_timed,
            )
            hist = [e for e in ctx.history if e.day < slot.day]
            presc = L.prescribe(spec, hist, self.cfg, deload=ds.active, stall_reset=ctx.stall_reset)
            if "increase_load" in presc.rule:
                increases.append(v.name)
            exercises.append(
                {
                    "order": order,
                    "variant_id": v.id,
                    "variant_key": v.key,
                    "name": v.name,
                    "exercise": v.exercise_slug,
                    "equipment_type": v.equipment_type,
                    "load_convention": v.load_convention,
                    "rep_range": list(spec.rep_range),
                    "sets": [s.as_dict() for s in presc.sets],
                    "last": presc.previous.as_dict() if presc.previous else None,
                    "rule": presc.rule,
                    "reason": presc.reason,
                    "flags": presc.flags,
                }
            )
            self.decisions.append(
                DecisionRec(
                    "progression",
                    presc.rule,
                    presc.reason,
                    presc.inputs,
                    {
                        "day": slot.day.isoformat(),
                        "sets": [s.as_dict() for s in presc.sets],
                        "flags": presc.flags,
                        **(
                            {
                                # the first slot of the week that got the reset (upper days repeat)
                                "reset_day": self._stall_first.setdefault(
                                    v.id, slot.day
                                ).isoformat(),
                                "reset_load": presc.load,
                            }
                            if presc.stall
                            else {}
                        ),
                    },
                    "variant",
                    str(v.id),
                    slot.id,
                )
            )
            if presc.stall:
                self.triggers.append(
                    TriggerRec(
                        "lift_stall",
                        f"lift_stall:{v.id}:{inp.week_start}",
                        {
                            "variant": v.name,
                            "variant_id": v.id,
                            "recent_e1rm": presc.inputs.get("recent_e1rm"),
                            "last": presc.inputs.get("last"),
                            "reset_load_kg": presc.load,
                            "flags": presc.flags,
                            "reason": presc.reason,
                        },
                        "variant",
                        str(v.id),
                        slot.day,
                        slot.id,
                    )
                )
        if ds.active:
            self.decisions.append(
                DecisionRec(
                    "deload",
                    "deload:red_streak",
                    f"{ds.streak} consecutive red days (>= {self.rec.deload_red_days}): deload "
                    f"{ds.trigger_day} .. {ds.until}: sets -{self.cfg.strength.deload.sets_pct:g}%, "  # noqa: E501
                    f"load -{self.cfg.strength.deload.load_pct:g}%",
                    {
                        "red_days": {
                            d.isoformat(): c for d, c in sorted(self.readiness_by_day.items())
                        }
                    },
                    {"trigger_day": ds.trigger_day.isoformat(), "until": ds.until.isoformat()},
                    "week",
                    self.inp.week_start.isoformat(),
                    slot.id,
                )
            )
            self.triggers.append(
                TriggerRec(
                    "deload",
                    f"deload:{ds.trigger_day}",
                    {"trigger_day": ds.trigger_day.isoformat(), "streak": ds.streak},
                    "week",
                    self.inp.week_start.isoformat(),
                    ds.trigger_day,
                    slot.id,
                )
            )
        pres = {
            "type": "lifting",
            "kind": kind,
            "gym": inp.gym_name,
            "exercises": exercises,
            "deload": (
                {"active": True, "from": ds.trigger_day.isoformat(), "until": ds.until.isoformat()}
                if ds.active
                else None
            ),
        }
        self._increases[slot.id] = increases
        return DayPlan(
            slot.id, slot.day, kind, kind, _TITLES[kind] + (" (deload)" if ds.active else ""), pres
        )

    # ------------------------------------------------------------------ runs
    def _easy_prescription(self, km: float, reason: str) -> dict[str, Any]:
        last = next((r for r in reversed(self.inp.runs) if R.split_intervals(r.laps) is None), None)
        ev = R.evaluate_easy_run(last, self.zones, self.rc.easy) if last else None
        return {
            "type": "easy_run",
            "distance_km": round(km, 1),
            "hr": _hr(self.zones, "easy"),
            "pace": {
                **_paces(self.model.easy_pace(self.zones.easy_high)),
                "note": "HR leads: slow down if HR passes the ceiling",
            },
            "reason": reason,
            "last_easy_run": (
                {"date": last.day.isoformat(), "km": round(last.km, 1), **ev.as_dict()}
                if last and ev
                else None
            ),
        }

    def _easy_day(self, slot: S.Slot, km: float) -> DayPlan:
        reason = (
            f"easy run below {self.zones.easy_high} bpm ({self.zones.easy_basis}-based ceiling)"
        )
        pres = self._easy_prescription(km, reason)
        last = pres["last_easy_run"]
        if last:
            self.decisions.append(
                DecisionRec(
                    "running",
                    "running:easy_run_evaluation",
                    ("passed" if last["passed"] else "failed: " + "; ".join(last["reasons"]))
                    + (
                        f"; decoupling {last['decoupling_pct']}%"
                        if last.get("decoupling_pct") is not None
                        else ""
                    ),
                    {"run_date": last["date"], "easy_ceiling": self.zones.easy_high},
                    last,
                    "run",
                    last["date"],
                    slot.id,
                )
            )
        return DayPlan(
            slot.id, slot.day, "easy_run", "easy_run", f"Easy run {pres['distance_km']:g} km", pres
        )

    def _interval_day(self, slot: S.Slot, run_km: dict[str, float]) -> DayPlan:
        ic = self.rc.intervals
        ip = self._interval_plan
        pace = ip.pace_s_per_km
        km = run_km.get("intervals", interval_km(ip.reps, self.cfg, self._wu, self._cd))
        pres = {
            "type": "intervals",
            "structure": {
                "warmup": {"distance_km": self._wu, "hr": _hr(self.zones, "easy")},
                "work": {
                    "reps": ip.reps,
                    "distance_m": ic.rep_distance_m,
                    "pace_s_per_km": pace,
                    "pace_label": fmt_pace(pace),
                    "pace_range_s_per_km": [pace - ic.pace_tolerance_s, pace + ic.pace_tolerance_s],
                    "hr": _hr(self.zones, "intervals"),
                },
                "recovery": {
                    "distance_m": ic.recovery_distance_m,
                    "type": "jog",
                    "hr_drop_min_bpm": ic.hr_recovery_min_bpm,
                },
                "cooldown": {"distance_km": self._cd},
            },
            "total_km": km,
            "progression": {
                "rule": ip.rule,
                "reason": ip.reason,
                "last_session": (
                    {"date": self._interval_run.day.isoformat(), **self._interval_eval.as_dict()}
                    if self._interval_run and self._interval_eval
                    else None
                ),
            },
        }
        self.decisions.append(
            DecisionRec(
                "running",
                ip.rule,
                ip.reason,
                {
                    "last": pres["progression"]["last_session"],
                    "pace_model": self.model.describe(),
                    "interval_zone": _hr(self.zones, "intervals"),
                },
                {"reps": ip.reps, "pace_s_per_km": pace, "day": slot.day.isoformat()},
                "slot",
                slot.id,
                slot.id,
            )
        )
        return DayPlan(
            slot.id,
            slot.day,
            "intervals",
            "intervals",
            f"Intervals {ip.reps}x{ic.rep_distance_m:g} m @ {fmt_pace(pace)}",
            pres,
        )

    def _long_day(self, slot: S.Slot, km: float) -> DayPlan:
        pres = {
            "type": "long_run",
            "distance_km": round(km, 1),
            "hr": _hr(self.zones, "easy"),
            "pace": {**_paces(self.model.easy_pace(self.zones.easy_high)), "note": "HR leads"},
            "progression": {
                "reason": self._long_reason,
                "last_long_run": self._long_eval.as_dict() if self._long_eval else None,
            },
        }
        self.decisions.append(
            DecisionRec(
                "running",
                "running:long_run_progression",
                f"{km:g} km: {self._long_reason}",
                {"last": pres["progression"]["last_long_run"]},
                {"distance_km": km, "cap_km": self.rc.long_run.cap_km},
                "slot",
                slot.id,
                slot.id,
            )
        )
        return DayPlan(
            slot.id, slot.day, "long_run", "long_run", f"Long run {pres['distance_km']:g} km", pres
        )

    # ------------------------------------------------------------------ readiness adjustments
    def _apply_readiness(self, plan: DayPlan) -> None:
        r = self.today_readiness
        plan.readiness = {"color": r.color, "known": r.known, "reason": r.reason}
        pres = plan.prescription
        increases = self._increases.get(plan.slot_id, [])
        if r.color == GREEN:
            return
        kind = plan.kind
        note = f"readiness {r.color}: {r.reason}"
        if kind in S.LIFTING:
            if r.color == YELLOW:
                for ex in pres["exercises"]:
                    sets = [
                        L.SetTarget(
                            s["index"],
                            s["target_weight"],
                            s["target_reps"],
                            s["set_type"],
                            s.get("target_duration_s"),
                        )
                        for s in ex["sets"]
                    ]
                    ex["sets"] = [s.as_dict() for s in L.drop_sets(sets, 1)]
                plan.adjustments.append("same effort, -1 working set per exercise (min 1)")
                plan.title += " (-1 set)"
                self._log_adjustment(plan, "readiness:yellow_minus_one_set", note)
            else:
                if increases:
                    self._conflict(
                        plan, "readiness red but progression says increase load", increases
                    )
                original = {"kind": kind, "prescription": pres}
                plan.prescription = {
                    "type": "rest",
                    "optional": "mobility 10-15 min",
                    "original": original,
                }
                plan.kind, plan.title = "rest", f"Rest (was {kind.title()})"
                plan.adjustments.append("lifting replaced by rest / optional mobility")
                self._log_adjustment(plan, "readiness:red_rest", note)
        elif kind == "intervals":
            self._convert_run(
                plan,
                r,
                "readiness:intervals_to_easy"
                if r.color == YELLOW
                else "readiness:red_easy_or_rest",
            )
        elif kind in ("long_run", "easy_run") and r.color == RED:
            self._convert_run(plan, r, "readiness:red_easy_or_rest")

    def _convert_run(self, plan: DayPlan, r: Readiness, rule: str) -> None:
        original = {"kind": plan.kind, "prescription": plan.prescription}
        if (
            plan.kind == "intervals"
            and plan.prescription.get("progression", {})
            .get("rule", "")
            .endswith(("add_rep", "faster_pace"))
            and r.color == RED
        ):
            self._conflict(
                plan,
                "readiness red but progression says add reps / faster pace",
                [plan.prescription["progression"]["rule"]],
            )
        if r.color == YELLOW:
            km = self.rc.easy.distance_km
            reason = f"readiness yellow: intervals replaced by an easy run ({r.reason})"
        else:
            km = float(self.rd.red.get("easy_run_km", 4)) if isinstance(self.rd.red, dict) else 4.0
            reason = f"readiness red: short easy run or rest ({r.reason})"
        pres = self._easy_prescription(km, reason)
        pres["converted_from"] = original
        if r.color == RED:
            pres["alternative"] = "rest"
        plan.prescription = pres
        plan.kind = "easy_run"
        plan.title = (
            f"Easy run {pres['distance_km']:g} km (was {original['kind'].replace('_', ' ')})"
        )
        plan.adjustments.append(reason)
        self._log_adjustment(plan, rule, reason)

    def _log_adjustment(self, plan: DayPlan, rule: str, reason: str) -> None:
        self.decisions.append(
            DecisionRec(
                "readiness",
                rule,
                reason,
                {"template_kind": plan.template_kind, "color": self.today_readiness.color},
                {"day": plan.day.isoformat(), "kind": plan.kind, "adjustments": plan.adjustments},
                "slot",
                plan.slot_id,
                plan.slot_id,
            )
        )

    def _conflict(self, plan: DayPlan, what: str, items: list[str]) -> None:
        payload = {
            "conflict": what,
            "items": items,
            "readiness": self.today_readiness.as_dict(),
            "slot": plan.slot_id,
        }
        self.triggers.append(
            TriggerRec(
                "rule_conflict",
                f"rule_conflict:{plan.slot_id}:{plan.day}",
                payload,
                "slot",
                plan.slot_id,
                plan.day,
                plan.slot_id,
            )
        )
        self.decisions.append(
            DecisionRec(
                "conflict",
                "conflict:readiness_vs_progression",
                what,
                payload,
                {"resolved": "readiness wins"},
                "slot",
                plan.slot_id,
                plan.slot_id,
            )
        )

    # ------------------------------------------------------------------ stagnation
    def _stagnation_triggers(self) -> None:
        rc = self.rc
        series = []
        for r in self.inp.runs:
            if r.km >= 3 and R.split_intervals(r.laps) is None:
                ef = R.efficiency(r.distance_m, r.duration_s, r.avg_hr)
                if ef:
                    series.append((r.day, ef))
        s = R.stagnation(series, self.today, rc.stagnation_weeks, rc.stagnation_min_improvement_pct)
        if s:
            self.triggers.append(
                TriggerRec(
                    "running_stagnation",
                    f"running_stagnation:ef:{self.inp.week_start}",
                    {"metric": "aerobic_efficiency", **s},
                    "week",
                    self.inp.week_start.isoformat(),
                    self.today,
                )
            )
            self.decisions.append(
                DecisionRec(
                    "running",
                    "running:stagnation",
                    f"aerobic efficiency {s['change_pct']:+.1f}% vs the previous {s['weeks']} weeks",  # noqa: E501
                    {"series_points": len(series)},
                    s,
                    "week",
                    self.inp.week_start.isoformat(),
                )
            )


def generate_week(inp: PlanInputs) -> WeekPlan:
    """The whole weekly plan (days >= today that are not frozen) with decisions and triggers."""
    return _Planner(inp).run()
