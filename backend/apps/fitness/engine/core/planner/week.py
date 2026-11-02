"""The week skeleton: shared planner state, the order of the steps, one day's dispatch."""

from __future__ import annotations

from datetime import date, timedelta

from apps.fitness.engine.core import running as R
from apps.fitness.engine.core import scheduling as S
from apps.fitness.engine.core.hr import zone_book
from apps.fitness.engine.core.pace import PaceModel, make_pace_model
from apps.fitness.engine.core.planner.lifting_week import LiftingMixin
from apps.fitness.engine.core.planner.models import (
    DayPlan,
    DecisionRec,
    PlanInputs,
    TriggerRec,
    WeekPlan,
)
from apps.fitness.engine.core.planner.paces import PaceMixin
from apps.fitness.engine.core.planner.readiness_rules import ReadinessMixin
from apps.fitness.engine.core.planner.run_days import RunDaysMixin
from apps.fitness.engine.core.planner.run_volume import RunVolumeMixin
from apps.fitness.engine.core.planner.triggers import TriggersMixin
from apps.fitness.engine.core.readiness import score_day


class _Planner(
    PaceMixin, ReadinessMixin, LiftingMixin, RunVolumeMixin, RunDaysMixin, TriggersMixin
):
    """One week's planning run. The steps live in the mixins (one module per seam); this
    class holds the shared state (`__init__`), the order of the steps (`run`) and the
    dispatch of one day (`_day`)."""

    def __init__(self, inp: PlanInputs):
        self.inp = inp
        self.cfg = inp.cfg
        self.rc = inp.cfg.running_cfg
        self.rd = inp.cfg.readiness_cfg
        self.rec = inp.cfg.recovery_cfg
        self.st = inp.cfg.strength
        # `zones` for what is planned, `zone_book.for_run` for judging a stored run
        self.zone_book = zone_book(inp.cfg, inp.zone_floors)
        self.zones = self.zone_book.current
        self.model: PaceModel = make_pace_model(inp.cfg)
        self.decisions: list[DecisionRec] = []
        self.triggers: list[TriggerRec] = []
        self._increases: dict[str, list[str]] = {}  # slot id -> variants whose load goes up
        self.today = inp.today
        self.readiness_by_day: dict[date, str] = {}
        for i in range(0, 15):
            d = self.today - timedelta(days=i)
            r = score_day(d, inp.health, self.rd)
            if r.known:
                self.readiness_by_day[d] = r.color
        self.today_readiness = score_day(self.today, inp.health, self.rd)
        # readiness on every day with health data (censoring of lifting misses)
        self.colors: dict[date, str] = dict(self.readiness_by_day)
        for h in inp.health:
            if h.day not in self.colors and h.day <= self.today:
                r = score_day(h.day, inp.health, self.rd)
                if r.known:
                    self.colors[h.day] = r.color
        cen = self.st.censor
        self.hard_run_days = {
            r.day for r in inp.runs if R.is_interval_run(r) or r.km >= cen.hard_run_min_km
        }
        self._fit_model()
        self._prepare_running()
        self._calibrate_easy()

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
        S.apply_locks(slots, inp.slot_locks)
        slots = [s for s in slots if s.id not in inp.excluded_slots]
        slots, events = S.apply_recovery_rules(
            slots,
            today=self.today,
            actual_lower_days=inp.actual_lower_days,
            hard_after_heavy_lower_h=self.rec.hard_after_heavy_lower_h,
            locked=inp.slot_locks.keys(),
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

        self._prepare_lifts(todo)
        run_km = self._plan_run_volume(todo)
        for s in todo:
            plan.days.append(self._day(s, run_km))
        plan.days.sort(key=lambda d: d.day)
        self._stagnation_triggers()
        self._anomaly_trigger()
        self._fourth_run_trigger()
        plan.decisions = self.decisions
        plan.triggers = list({t.dedupe_key: t for t in self.triggers}.values())  # once per key
        plan.readiness_history = dict(self.readiness_by_day)
        plan.volume = getattr(self, "_volume", {})
        plan.lifting = getattr(self, "_lift_summary", {})
        plan.running = {
            "phase": self.phase.as_dict(),
            "vdot": round(self.vdot_update.vdot, 2),
            "vdot_reason": self.vdot_update.reason,
            "vdot_from_intervals": self.rep_estimate.as_dict(),
            "quality": self.quality_plan.as_dict(),
            "long_run": getattr(self, "_long_info", {}),
            "easy_pace": self.easy_cal.as_dict() if self.easy_cal else None,
        }
        return plan

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
            plan = self._long_day(slot, run_km.get("long_run", self._long.km))
        else:
            plan = DayPlan(slot.id, slot.day, "rest", kind, "Rest", {"type": "rest"})
        plan.notes.extend(slot.notes)
        plan.moved_from, plan.change_reason = slot.moved_from, slot.change_reason
        plan.changed_by = slot.changed_by
        if slot.distance_cap_km is not None and "distance_km" in plan.prescription:
            plan.prescription["distance_km"] = min(
                plan.prescription["distance_km"], slot.distance_cap_km
            )
            if plan.kind == "easy_run":
                plan.title = f"Easy run {plan.prescription['distance_km']:g} km"
            elif plan.kind == "long_run":
                plan.title = f"Long run {plan.prescription['distance_km']:g} km"
        if slot.day == self.today:
            self._apply_readiness(plan)
        else:
            plan.readiness = {"color": None, "known": False, "reason": "forecast not available yet"}
        return plan


def generate_week(inp: PlanInputs) -> WeekPlan:
    """The whole weekly plan (days >= today that are not frozen) with decisions and triggers."""
    return _Planner(inp).run()
