"""Readiness: today's decisions and HRV anomaly trigger, and the adjustments written into a day."""

from __future__ import annotations

from apps.fitness.engine.core import lifting as L
from apps.fitness.engine.core import scheduling as S
from apps.fitness.engine.core.planner.models import (
    DayPlan,
    DecisionRec,
    TriggerRec,
)
from apps.fitness.engine.core.planner.state import PlannerState
from apps.fitness.engine.core.readiness import (
    GREEN,
    RED,
    YELLOW,
    Readiness,
    hrv_drop_anomaly,
)


class ReadinessMixin(PlannerState):
    """Readiness decisions (whole week) and per-day readiness adjustments."""

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
            .endswith(("add_rep", "faster_pace", "longer_reps", "shorter_recovery"))
            and r.color == RED
        ):
            self._conflict(
                plan,
                "readiness red but progression says add reps / faster pace",
                [plan.prescription["progression"]["rule"]],
            )
        if r.color == YELLOW:
            km = self.rc.easy.distance_km
            reason = f"readiness yellow: quality session replaced by easy + strides ({r.reason})"
        else:
            km = float(self.rd.red.get("easy_run_km", 4)) if isinstance(self.rd.red, dict) else 4.0
            reason = f"readiness red: short easy run or rest ({r.reason})"
        pres = self._easy_prescription(km, reason, strides=r.color == YELLOW)
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
