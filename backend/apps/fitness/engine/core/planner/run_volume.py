"""Running volume: step-back weeks, long run, quality fit, the weekly km split over the slots."""

from __future__ import annotations

from datetime import timedelta

from apps.fitness.engine.core import running as R
from apps.fitness.engine.core import scheduling as S
from apps.fitness.engine.core.planner.helpers import (
    _round1,
    work_km,
)
from apps.fitness.engine.core.planner.models import (
    DecisionRec,
    TriggerRec,
)
from apps.fitness.engine.core.planner.state import PlannerState


class RunVolumeMixin(PlannerState):
    """Weekly running volume and its split over the run slots."""

    # ------------------------------------------------------------------ running volume
    def _step_back_due(self) -> tuple[bool, str]:
        lc = self.rc.long_run
        ws = self.inp.week_start
        every = max(2, lc.step_back_every)
        if self.inp.last_step_back is not None and self.inp.last_step_back < ws:
            weeks = (ws - self.inp.last_step_back).days // 7
            due, near = weeks >= every, weeks >= every - 1
            pos = f"{weeks} weeks since the last step-back"
        else:
            k = (ws - self.phase.cycle_start).days // 7
            due, near = k % every == every - 1, k % every == every - 2
            pos = f"week {k % every + 1} of {every}"
        if due:
            return True, f"step-back week ({pos})"
        lift_deload = any(
            w.deload and w.start <= ws + timedelta(days=6) and w.until >= ws
            for w in getattr(self, "windows", [])
        )
        if near and lift_deload and self.cfg.strength.deload.align_running_step_back:
            return True, f"step-back a week early, aligned with the lifting deload ({pos})"
        return False, pos

    def _plan_long(self, single: float | None) -> R.LongRunPlan:
        lc = self.rc.long_run
        since = self.today - timedelta(days=lc.recent_days)
        recent = [r for r in self.inp.runs if r.day >= since and R.split_intervals(r.laps) is None]
        longest = max(recent, key=lambda r: r.km, default=None)
        self._long_eval = None
        base = longest.km if longest is not None and longest.km >= lc.min_km else None
        last_long = max((r for r in recent if r.km >= lc.min_km), key=lambda r: r.day, default=None)
        dec = dur = None
        if last_long is not None:
            self._long_eval = R.evaluate_easy_run(
                last_long, self.zone_book.for_run(last_long), self.rc.easy
            )
            dec = self._long_eval.decoupling_pct
            dur = last_long.duration_s / 60
        due, pos = self._step_back_due() if base is not None else (False, "")
        plan = R.next_long_run_km(
            base, lc, decoupling=dec, duration_min=dur, step_back=due, single_cap=single
        )
        if due:
            plan.reason = f"{pos}: {plan.reason}"
        self._long_info = {
            "km": plan.km,
            "step_back": plan.step_back,
            "drift": plan.drift,
            "block_position": pos,
            "week_start": self.inp.week_start.isoformat(),
        }
        return plan

    def _fit_quality(self, remaining: float | None, run_slots: dict) -> None:
        """Make the quality session fit its share of the weekly budget: 1) shrink warm-up and
        cool-down (down to their minimums); 2) if the progression only ADDED a rep, keep the
        last session's rep count (`quality:volume_hold`); 3) else minimum warm-up / cool-down
        (the week then exceeds the cap and a rule_conflict trigger is raised)."""
        ic = self.rc.intervals
        qp = self.quality_plan
        if qp.session_type == "time_trial":
            tt = self.rc.time_trial
            self._wu, self._cd = tt.warmup_km, tt.cooldown_km
            return
        if remaining is None:
            return
        mn = self.rc.volume.min_run_km
        others = (mn if "easy_run" in run_slots else 0.0) + (mn if "long_run" in run_slots else 0.0)
        budget = remaining - others
        full, minimum = ic.warmup_km + ic.cooldown_km, ic.warmup_min_km + ic.cooldown_min_km

        def shrink(reps: int) -> tuple[float, float] | None:
            room = budget - work_km(reps, qp.rep_m, self._rec_m)
            if room >= full:
                return ic.warmup_km, ic.cooldown_km
            if room >= minimum:
                f = room / full
                return (
                    max(ic.warmup_min_km, round(ic.warmup_km * f, 1)),
                    max(ic.cooldown_min_km, round(ic.cooldown_km * f, 1)),
                )
            return None

        fit = shrink(qp.reps)
        if fit is not None:
            if fit != (ic.warmup_km, ic.cooldown_km):
                qp.reason += (
                    f"; warm-up/cool-down {fit[0]:g}/{fit[1]:g} km to fit the {budget:.1f} km "
                    "share of the weekly cap"
                )
            self._wu, self._cd = fit
            return
        last, _ev = self.quality_last
        if last is not None and qp.rule == "quality:add_rep":
            held = shrink(last.reps)
            qp.reason = (
                f"{qp.reps} reps do not fit the {budget:.1f} km share of the weekly cap: keep "
                f"{last.reps} reps"
            )
            qp.note = qp.reason
            qp.reps, qp.rule, qp.lever = last.reps, "quality:volume_hold", None
            self._wu, self._cd = held or (ic.warmup_min_km, ic.cooldown_min_km)
            return
        self._wu, self._cd = ic.warmup_min_km, ic.cooldown_min_km
        qp.reason += "; minimum warm-up/cool-down"

    def _quality_km(self) -> float:
        qp = self.quality_plan
        return _round1(self._wu + self._cd + work_km(qp.reps, qp.rep_m, self._rec_m))

    def _plan_run_volume(self, todo: list[S.Slot]) -> dict[str, float]:
        """km per run slot after the weekly and single-run caps; also fills the run decisions."""
        rc, vol, lc = self.rc, self.rc.volume, self.rc.long_run
        ws = self.inp.week_start
        run_slots = {s.kind: s for s in todo if s.kind in ("easy_run", "intervals", "long_run")}
        weekly = R.weekly_km(self.inp.runs)
        mean = R.mean_km(weekly, ws, vol.mean_weeks)
        hold = None
        if vol.hold_last_week:
            lower = self.inp.actual_lower_days

            def flagged(r: R.RunData) -> bool:
                ev = R.evaluate_easy_run(
                    r,
                    self.zone_book.for_run(r),
                    rc.easy,
                    after_lower=(r.day - timedelta(days=1)) in lower,
                )
                return not ev.passed or ev.decoupling_flag

            hold = R.hold_volume(
                weekly, self.inp.runs, ws, limit_factor=vol.hold_limit_factor, flagged=flagged
            )
        cap = R.volume_cap_km(
            weekly,
            ws,
            rc.weekly_volume_cap_pct,
            weeks_avg=vol.mean_weeks,
            abs_km=vol.cap_abs_km,
            hold_km=hold.km if hold else None,
        )
        single = R.single_run_cap_km(
            self.inp.runs,
            self.today,
            vol.single_run_factor,
            vol.single_run_abs_km,
            vol.single_run_days,
        )
        done = sum(r.km for r in self.inp.runs if ws <= r.day <= self.today)
        remaining = None if cap is None else max(cap - done, 0.0)
        qp = self.quality_plan
        ic = rc.intervals
        self._wu, self._cd = ic.warmup_km, ic.cooldown_km
        easy_mid = self.model.easy_pace(self.zones.easy_high)
        easy_pace = (easy_mid.min_s_per_km + easy_mid.max_s_per_km) / 2 if easy_mid else 400
        self._rec_m = (
            qp.recovery_m
            if qp.recovery_m is not None
            else round((qp.recovery_s or 0) / easy_pace * 1000 / 10) * 10
        )
        self._long = self._plan_long(single)
        km: dict[str, float] = {}
        if "easy_run" in run_slots:
            km["easy_run"] = min(rc.easy.distance_km, single) if single else rc.easy.distance_km
        if "intervals" in run_slots:
            if self.quality_off:
                km["intervals"] = km.get("easy_run", rc.easy.distance_km)
            else:
                self._fit_quality(remaining, run_slots)
                km["intervals"] = self._quality_km()
        if "long_run" in run_slots:
            km["long_run"] = self._long.km
        for kind, slot in run_slots.items():  # a coach `shorten` caps the run
            if slot.distance_cap_km is not None and kind in km and kind != "intervals":
                km[kind] = min(km[kind], slot.distance_cap_km)
        fitted, notes = R.fit_to_cap(km, remaining, vol.min_run_km, vol.min_run_km)
        target = rc.weekly_volume_floor_km or None
        factor = 1 + rc.weekly_volume_cap_pct / 100
        held_km = hold.km if hold is not None else None
        reasoning = (
            f"4-week mean {mean:g} km -> cap max({mean:g} x {factor:g}, "
            f"{mean:g} + {vol.cap_abs_km:g}{f', hold {held_km:g}' if held_km is not None else ''})"
            f" = {cap:g} km"
            if cap is not None
            else "no runs in the last 4 weeks: no cap"
        )
        if cap is not None and hold is not None:
            reasoning += (
                f"; hold: last week ran {hold.last_week_km:g} km, held up to {held_km:g} km "
                f"(limit {vol.hold_limit_factor:g} x the highest of the 4 weeks before it = "
                f"{hold.limit_km:g} km)"
                if held_km is not None
                else f"; no hold of last week's {hold.last_week_km:g} km: {hold.blocked}"
            )
        if target:
            reasoning += (
                f"; target {target:g} km/week is reached step by step, never as one jump"
                if cap is None or cap < target
                else f"; target {target:g} km/week is within the cap"
            )
        if single is not None:
            reasoning += f"; single run <= {single:g} km (1.1 x the longest of 30 days)"
        self._volume = {
            "cap_km": cap,
            "mean_km_4w": mean,
            "target_km": target,
            "single_run_cap_km": single,
            "hold": hold.as_dict() if hold else None,
            "done_km_this_week": round(done, 2),
            "planned_km_before_cap": {k: round(v, 2) for k, v in km.items()},
            "planned_km": {k: round(v, 2) for k, v in fitted.items()},
            "notes": notes,
            "reasoning": reasoning,
        }
        if remaining is not None and fitted and sum(fitted.values()) > remaining + 0.5:
            over = {
                "conflict": "weekly volume cap vs the weekly template",
                "cap_km": cap,
                "planned_km": self._volume["planned_km"],
                "note": "the quality session and the minimum easy / long run alone exceed the cap",
            }
            self.triggers.append(
                TriggerRec(
                    "rule_conflict",
                    f"rule_conflict:volume_cap:{ws}",
                    over,
                    "week",
                    ws.isoformat(),
                    ws,
                )
            )
        elif "long_run" in fitted and fitted["long_run"] < min(lc.min_km, km["long_run"]) - 1e-9:
            self.triggers.append(
                TriggerRec(
                    "rule_conflict",
                    f"rule_conflict:volume_cap:{ws}",
                    {
                        "conflict": "weekly volume cap vs the long run",
                        "cap_km": cap,
                        "planned_km": self._volume["planned_km"],
                        "note": f"the cap leaves the long run below {lc.min_km:g} km: dropping "
                        "the easy run would keep it longer",
                    },
                    "week",
                    ws.isoformat(),
                    ws,
                )
            )
        if km:
            self.decisions.append(
                DecisionRec(
                    "running",
                    "running:weekly_volume_cap",
                    reasoning + ("; " + "; ".join(notes) if notes else ""),
                    {
                        "weekly_km": {
                            d.isoformat(): round(v, 2) for d, v in sorted(weekly.items())[-6:]
                        },
                        "cap_pct": rc.weekly_volume_cap_pct,
                        "cap_abs_km": vol.cap_abs_km,
                    },
                    self._volume,
                    "week",
                    ws.isoformat(),
                )
            )
        return fitted
