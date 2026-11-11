"""Run days: easy (with strides), interval and long-run prescriptions."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from apps.fitness.engine.core import quality as Q
from apps.fitness.engine.core import running as R
from apps.fitness.engine.core import scheduling as S
from apps.fitness.engine.core.pace import fmt_pace
from apps.fitness.engine.core.planner.helpers import _hr
from apps.fitness.engine.core.planner.models import (
    DayPlan,
    DecisionRec,
)
from apps.fitness.engine.core.planner.state import PlannerState


class RunDaysMixin(PlannerState):
    """One planned run day of each kind."""

    # ------------------------------------------------------------------ runs
    def _phase_fields(self) -> dict[str, Any]:
        return {"phase": self.phase.phase, "phase_week": self.phase.phase_week}

    def _strides(self, easy_pace: dict[str, Any]) -> dict[str, Any] | None:
        """The strides of an easy run. They are part of its distance: `est_km` (reps x stride + jog,
        estimated at the easy pace) comes off the easy part, so the weekly km include them."""
        s = self.rc.easy.strides
        if s.reps <= 0:
            return None
        pace_s = (easy_pace["min_s_per_km"] + easy_pace["max_s_per_km"]) / 2
        return {
            "reps": s.reps,
            "duration_s": s.duration_s,
            "recovery_s": s.recovery_s,
            "est_km": round(R.strides_km(s.reps, s.duration_s, s.recovery_s, pace_s), 1),
            "note": f"{s.reps} x {s.duration_s:g} s relaxed fast strides near the end, "
            f"{s.recovery_s:g} s easy jog / walk between",
        }

    def _easy_prescription(self, km: float, reason: str, *, strides: bool = True) -> dict[str, Any]:
        last = next((r for r in reversed(self.inp.runs) if R.split_intervals(r.laps) is None), None)
        after_lower = (
            last is not None and (last.day - timedelta(days=1)) in self.inp.actual_lower_days
        )
        ev = (
            R.evaluate_easy_run(
                last, self.zone_book.for_run(last), self.rc.easy, after_lower=after_lower
            )
            if last
            else None
        )
        paces = self._easy_paces()
        st = self._strides(paces) if strides else None
        out: dict[str, Any] = {
            "type": "easy_run",
            "distance_km": round(km, 1),
            "hr": _hr(self.zones, "easy"),
            "pace": {
                **paces,
                "note": "HR leads: slow down if HR passes the ceiling",
            },
            "reason": reason,
            "last_easy_run": (
                {"date": last.day.isoformat(), "km": round(last.km, 1), **ev.as_dict()}
                if last and ev
                else None
            ),
            **self._phase_fields(),
            "progression_note": f"easy {round(km, 1):g} km"
            + (f" incl. {st['reps']} x {st['duration_s']:g} s strides" if st else ""),
        }
        if st:
            out["structure"] = {"strides": st}
        return out

    def _easy_day(self, slot: S.Slot, km: float) -> DayPlan:
        reason = (
            f"easy run below {self.zones.easy_high} bpm ({self.zones.easy_basis}-based ceiling)"
        )
        pres = self._easy_prescription(km, reason)
        last = pres["last_easy_run"]
        if last:
            verdict = "passed" if last["passed"] else "failed: " + "; ".join(last["reasons"])
            if last.get("exempt"):
                verdict = "HR high the day after lower body (a note, not a fail)"
            self.decisions.append(
                DecisionRec(
                    "running",
                    "running:easy_run_evaluation",
                    verdict
                    + (
                        f"; decoupling {last['decoupling_pct']}%"
                        if last.get("decoupling_pct") is not None
                        else ""
                    ),
                    {"run_date": last["date"], "easy_ceiling": last["hr_ceiling"]},
                    last,
                    "run",
                    last["date"],
                    slot.id,
                )
            )
        return DayPlan(
            slot.id, slot.day, "easy_run", "easy_run", f"Easy run {pres['distance_km']:g} km", pres
        )

    def _work_hr(self, t: str) -> dict[str, Any]:
        z = self.zones
        if t == "threshold" and z.lthr:
            spec = Q.type_spec(self.rc, t)
            return {
                "zone": "Z4",
                "low": int(z.lthr * spec.green_hr_pct_lthr + 0.5),
                "high": int(z.lthr * spec.hr_ceiling_pct_lthr + 0.5),
                "note": "pace leads; HR is a ceiling only (wrist HR lags on short reps)",
            }
        return {**_hr(z, "intervals"), "note": "pace leads; HR is a ceiling only"}

    def _interval_day(self, slot: S.Slot, run_km: dict[str, float]) -> DayPlan:
        ic = self.rc.intervals
        if self.quality_off:
            km = run_km.get("intervals", self.rc.easy.distance_km)
            reason = (
                f"first week back after {self.break_days} days without a run: no quality "
                "session yet, easy run with strides"
            )
            pres = self._easy_prescription(km, reason)
            pres["converted_from"] = {"kind": "intervals", "reason": "return after a break"}
            pres["progression_note"] = "back after a break: easy + strides, quality next week"
            self.decisions.append(
                DecisionRec(
                    "running",
                    "quality:after_break",
                    reason,
                    {"break_days": self.break_days},
                    {"day": slot.day.isoformat(), "distance_km": km},
                    "slot",
                    slot.id,
                    slot.id,
                )
            )
            return DayPlan(
                slot.id,
                slot.day,
                "easy_run",
                "intervals",
                f"Easy run {pres['distance_km']:g} km (back after a break)",
                pres,
            )
        qp = self.quality_plan
        pace = round(qp.pace_s_per_km)
        km = run_km.get("intervals", self._quality_km())
        last, last_ev = self.quality_last
        label = Q.LABELS.get(qp.session_type, "Intervals")
        rep_s = round(qp.rep_m / 1000 * pace)
        note = qp.note or qp.reason
        pres = {
            "type": "intervals",
            "structure": {
                "session_type": qp.session_type,
                "warmup": {"distance_km": self._wu, "hr": _hr(self.zones, "easy")},
                "work": {
                    "reps": qp.reps,
                    "distance_m": qp.rep_m,
                    "duration_s": rep_s,
                    "pace_s_per_km": pace,
                    "pace_label": fmt_pace(pace),
                    "pace_range_s_per_km": [pace - ic.pace_tolerance_s, pace + ic.pace_tolerance_s],
                    "pace_tolerance_pct": self.rc.interval_pace_tolerance_pct,
                    "hr": self._work_hr(qp.session_type),
                },
                "recovery": {
                    "distance_m": self._rec_m,
                    "duration_s": qp.recovery_s,
                    "type": "jog",
                    "hr_drop_min_bpm": ic.hr_recovery_min_bpm,
                },
                "cooldown": {"distance_km": self._cd},
            },
            "total_km": km,
            **self._phase_fields(),
            "progression_note": note,
            "progression": {
                "rule": qp.rule,
                "reason": qp.reason,
                "lever": qp.lever,
                "vdot": round(self.vdot_update.vdot, 2),
                "last_session": (
                    {
                        "date": last.day.isoformat(),
                        "session_type": last.session_type,
                        **(last_ev.as_dict() if last_ev is not None else {}),
                    }
                    if last is not None
                    else None
                ),
            },
        }
        if qp.session_type == "time_trial":
            pres["structure"]["recovery"] = None
            pres["structure"]["work"]["note"] = "all-out, even pace; the result sets the VDOT"
        self.decisions.append(
            DecisionRec(
                "running",
                qp.rule,
                qp.reason,
                {
                    "last": pres["progression"]["last_session"],
                    "pace_model": self.model.describe(),
                    "phase": self.phase.as_dict(),
                },
                {
                    "reps": qp.reps,
                    "rep_m": qp.rep_m,
                    "recovery_s": qp.recovery_s,
                    "pace_s_per_km": pace,
                    "session_type": qp.session_type,
                    "lever": qp.lever,
                    "day": slot.day.isoformat(),
                },
                "slot",
                slot.id,
                slot.id,
            )
        )
        if qp.session_type == "time_trial":
            title = f"Time trial {qp.rep_m / 1000:g} km @ {fmt_pace(pace)}"
        else:
            title = f"{label} {qp.reps}x{qp.rep_m:g} m @ {fmt_pace(pace)}"
        return DayPlan(slot.id, slot.day, "intervals", "intervals", title, pres)

    def _long_day(self, slot: S.Slot, km: float) -> DayPlan:
        lp = self._long
        note = lp.reason if km >= lp.km - 1e-9 else f"{lp.reason}; {km:g} km under the weekly cap"
        pres = {
            "type": "long_run",
            "distance_km": round(km, 1),
            "hr": _hr(self.zones, "easy"),
            "pace": {**self._easy_paces(), "note": "HR leads"},
            **self._phase_fields(),
            "progression_note": note,
            "step_back": lp.step_back,
            "progression": {
                "reason": lp.reason,
                "drift": lp.drift,
                "last_long_run": self._long_eval.as_dict() if self._long_eval else None,
            },
        }
        self.decisions.append(
            DecisionRec(
                "running",
                "running:long_run_progression",
                f"{km:g} km: {note}",
                {"last": pres["progression"]["last_long_run"]},
                {
                    "distance_km": km,
                    "cap_km": self.rc.long_run.cap_km,
                    "step_back": lp.step_back,
                    "drift": lp.drift,
                    "week_start": self.inp.week_start.isoformat(),
                },
                "slot",
                slot.id,
                slot.id,
            )
        )
        return DayPlan(
            slot.id, slot.day, "long_run", "long_run", f"Long run {pres['distance_km']:g} km", pres
        )
