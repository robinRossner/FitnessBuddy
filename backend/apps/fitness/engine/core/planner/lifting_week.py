"""Lifting for the whole week: per-lift analysis, deload windows, per-slot prescriptions."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from apps.fitness.engine.core import lifting as L
from apps.fitness.engine.core import scheduling as S
from apps.fitness.engine.core.planner.helpers import (
    _TITLES,
    _ladder_dict,
    _window_dict,
)
from apps.fitness.engine.core.planner.models import (
    DayPlan,
    DecisionRec,
    LiftContext,
    TriggerRec,
    VariantInfo,
)
from apps.fitness.engine.core.readiness import deload_status


class LiftingMixin:
    """Lift analysis and the lifting days."""

    # ------------------------------------------------------------------ lifting (whole week)
    def _spec(self, pe, kind: str, v: VariantInfo) -> L.LiftSpec:
        tmpl = list(self.cfg.weekly_template.values())
        kinds = {
            k
            for k, items in self.inp.program.days.items()
            for e in items
            if e.variant == pe.variant
        }
        per_week = sum(tmpl.count(k) for k in kinds) or 1
        return L.LiftSpec(
            variant=v,
            rep_range=pe.rep_range or self.st.rep_range,
            n_sets=pe.sets,
            # the user's step for this machine wins over program.yaml's
            increment_kg=v.increment_kg or pe.increment_kg,
            timed=v.is_timed,
            per_week=per_week,
            lower_body=kind == "lower",
            bodyweight_kg=self.inp.bodyweight_kg or self.cfg.user.bodyweight_kg,
        )

    def _env(self, ctx: LiftContext, windows: list[L.DeloadWindow]) -> L.LiftEnv:
        return L.LiftEnv(
            variant_id=ctx.variant.id if ctx.variant else None,
            readiness=self.colors,
            hard_run_days=self.hard_run_days,
            windows=windows,
            ladder=list(ctx.ladder),
            previous_gym=ctx.previous[0] if ctx.previous else None,
            previous_weight_kg=ctx.previous[1] if ctx.previous else None,
            previous_reps=ctx.previous[2] if ctx.previous else None,
        )

    def _prepare_lifts(self, todo: list[S.Slot]) -> None:
        """Analyse every lift once for the week: multi-lift stall and fatigue-index deloads."""
        inp, st = self.inp, self.st
        lift_days = sorted(s.day for s in todo if s.kind in S.LIFTING)
        self.windows: list[L.DeloadWindow] = list(inp.windows)
        self._first_day: dict[int, date] = {}
        self._lift_summary: dict[str, Any] = {}
        self._created_window: L.DeloadWindow | None = None
        ws = inp.week_start
        names_by_id = {c.variant.id: c.variant.name for c in inp.lifts.values() if c.variant}
        for w in self.windows:
            if w.reason != "legacy_reset" or not ws <= w.start <= ws + timedelta(days=6):
                continue
            names = sorted(names_by_id.get(k, str(k)) for k in w.retest)
            what = (
                f"soft deload {w.start} .. {w.until} (sets halved, loads kept), then a retest of "
                "the old loads"
                if w.deload
                else f"a retest of the old load from {w.start}"
            )
            self.decisions.append(
                DecisionRec(
                    "deload",
                    "deload:legacy_reset_converted",
                    f"the planned -10% stall resets of {len(names)} lifts ({', '.join(names)}) "
                    f"are replaced by the v2 rules: {what}",
                    {"lifts": names},
                    {"window": _window_dict(w)},
                    "week",
                    ws.isoformat(),
                )
            )
            if w.deload:
                self.triggers.append(
                    TriggerRec(
                        "multi_lift_stall",
                        f"multi_lift_stall:{w.start}",
                        {
                            "lifts": names,
                            "variant_ids": sorted(w.retest),
                            "window": _window_dict(w),
                            "reason": "several lifts were flagged stalled together (and the "
                            "coach suspected copied or prefilled logs): soft deload, then a "
                            "retest of the old loads instead of -10% resets",
                        },
                        "week",
                        ws.isoformat(),
                        w.start,
                    )
                )
        cancelled = ws in inp.cancelled_weeks
        if cancelled:
            self._cancel_decision(ws)
        if not lift_days:
            return
        for d in lift_days:  # reactive deload from a red streak (also lines up the step-back)
            ds = deload_status(self.readiness_by_day, d, self.today, self.rec.deload_red_days)
            if ds.active and not any(
                w.reason == "red_streak" and w.start == ds.trigger_day for w in self.windows
            ):
                self.windows.append(L.DeloadWindow(ds.trigger_day, ds.until, "red_streak"))
        first = lift_days[0]
        analyses: dict[int, tuple[LiftContext, L.LiftAnalysis, L.LiftSpec]] = {}
        for kind in ("upper", "lower"):
            kd = [s.day for s in todo if s.kind == kind]
            for pe in inp.program.exercises(kind):
                ctx = inp.lifts.get(pe.variant)
                if ctx is None or ctx.variant is None or ctx.variant.id in analyses:
                    continue
                spec = self._spec(pe, kind, ctx.variant)
                if spec.timed or not kd:
                    continue
                day = kd[0]
                self._first_day[ctx.variant.id] = day
                hist = [e for e in ctx.history if e.day < day]
                an = L.analyze(spec, hist, self.cfg, self._env(ctx, self.windows))
                analyses[ctx.variant.id] = (ctx, an, spec)

        recent_window = any(
            w.deload
            and (first - w.start).days < st.deload.min_gap_days
            and w.start <= first + timedelta(days=6)
            for w in self.windows
        )
        # multi-lift stall: >= k lifts stalled within `multi_stall_days`
        stalled: list[tuple[LiftContext, L.LiftAnalysis]] = []
        for ctx, an, _spec in analyses.values():
            vid = ctx.variant.id  # type: ignore[union-attr]
            covered = any(
                vid in w.retest and w.until >= first - timedelta(days=7) for w in self.windows
            )
            if an.stalled and not covered:
                stalled.append((ctx, an))
        recent_resets = [
            ctx
            for ctx, _an, _s in analyses.values()
            if any(
                e.step != L.LADDER_ADVISOR and 0 <= (first - e.day).days <= st.multi_stall_days
                for e in ctx.ladder
            )
        ]
        n_stalled = len(stalled) + len(
            [c for c in recent_resets if c not in [s[0] for s in stalled]]
        )
        start = max(first, self.today)
        until = start + timedelta(days=st.deload.days - 1)
        if n_stalled >= st.multi_stall_lifts and stalled and not recent_window:
            retest = {}
            for ctx, an in stalled:
                last = an.last
                assert last is not None
                ref = max(
                    (x for x in an.evals if abs(x.load - last.load) < 1e-6), key=lambda x: x.total
                )
                retest[ctx.variant.id] = L.Retest(last.load, ref.reps, ref.e1rm)  # type: ignore[union-attr]
            if cancelled:  # the user cancelled this week's deload: straight to the retest
                w = L.DeloadWindow(
                    start, start - timedelta(days=1), "multi_lift_stall", retest, False, True
                )
            else:
                w = L.DeloadWindow(start, until, "multi_lift_stall", retest)
            self.windows.append(w)
            self._created_window = w
            names = [ctx.variant.name for ctx, _an in stalled]  # type: ignore[union-attr]
            plan_text = (
                "the deload is cancelled by the user: retest of the old loads right away"
                if cancelled
                else f"soft deload {start} .. {until} (sets halved, loads kept), then a retest "
                "of the old loads"
            )
            self.decisions.append(
                DecisionRec(
                    "deload",
                    "deload:multi_lift_stall",
                    f"{n_stalled} lifts stalled within {st.multi_stall_days} days "
                    f"({', '.join(names)}): no per-lift resets; {plan_text}",
                    {"stalled": {ctx.variant.name: an.counter for ctx, an in stalled}},  # type: ignore[union-attr]
                    {"window": _window_dict(w)},
                    "week",
                    self.inp.week_start.isoformat(),
                )
            )
            self.triggers.append(
                TriggerRec(
                    "multi_lift_stall",
                    f"multi_lift_stall:{start}",
                    {
                        "lifts": names,
                        "variant_ids": [ctx.variant.id for ctx, _an in stalled],  # type: ignore[union-attr]
                        "window": _window_dict(w),
                        "reason": "several lifts stalled together: look for a shared cause "
                        "(running load, sleep, energy intake, life stress, entry errors)",
                    },
                    "week",
                    self.inp.week_start.isoformat(),
                    start,
                )
            )
        elif not cancelled and not recent_window and not any(w.covers(first) for w in self.windows):
            self._fatigue_window(analyses, start, until)

        for vid, (ctx, an, _spec) in analyses.items():
            self._lift_summary[ctx.variant.exercise_slug] = {  # type: ignore[union-attr]
                "variant_id": vid,
                "stalled": an.stalled,
                "stall_counter": an.counter,
                "stall_n": an.n_required,
                "ladder_position": an.ladder_pos,
                "censored": an.censored(),
            }

    def _cancel_decision(self, ws: date) -> None:
        """Audit line for a week whose deload the user cancelled (only when it had one)."""
        week_end = ws + timedelta(days=6)
        done = [w for w in self.windows if w.cancelled and w.start <= week_end]
        if not done:
            return
        names_by_id = {c.variant.id: c.variant.name for c in self.inp.lifts.values() if c.variant}
        retest = sorted({names_by_id.get(k, str(k)) for w in done for k in w.retest})
        reason = self.inp.cancelled_weeks.get(ws)
        self.decisions.append(
            DecisionRec(
                "deload",
                "deload:cancelled",
                f"deload of the week of {ws} cancelled by the user"
                + (f" ({reason})" if reason else "")
                + ": normal set counts"
                + (
                    f"; {len(retest)} stalled lifts retest their old loads this week "
                    f"({', '.join(retest)})"
                    if retest
                    else ""
                )
                + "; the other lifts progress by the normal rules",
                {"week_start": ws.isoformat(), "reason": reason},
                {"lifts_retesting": retest},  # no `window` key: windows are rebuilt from the log
                "week",
                ws.isoformat(),
            )
        )

    def _fatigue_window(self, analyses, start: date, until: date) -> None:
        st = self.st
        ws = self.inp.week_start
        shares = []
        for k in range(1, st.deload.fatigue_weeks + 1):
            w0 = ws - timedelta(days=7 * k)
            bad = clean = 0
            for _ctx, an, _spec in analyses.values():
                b, c = L.fatigue_share(an, w0)
                bad += b
                clean += c
            shares.append((w0, bad, clean))
        if not shares or any(c < st.deload.fatigue_min_exposures for _w, _b, c in shares):
            return
        if all(b / c * 100 >= st.deload.fatigue_index_pct for _w, b, c in shares):
            w = L.DeloadWindow(start, until, "fatigue_index")
            self.windows.append(w)
            self._created_window = w
            detail = ", ".join(f"{w0}: {b}/{c}" for w0, b, c in shares)
            self.decisions.append(
                DecisionRec(
                    "deload",
                    "deload:fatigue_index",
                    f"fatigue index >= {st.deload.fatigue_index_pct:g}% in {len(shares)} weeks "
                    f"in a row ({detail} misses/regressions of the clean sessions): reactive "
                    f"deload {start} .. {until}",
                    {"weeks": [[w0.isoformat(), b, c] for w0, b, c in shares]},
                    {"window": _window_dict(w)},
                    "week",
                    ws.isoformat(),
                )
            )
            self.triggers.append(
                TriggerRec(
                    "deload",
                    f"deload:fatigue:{start}",
                    {
                        "reason": "fatigue_index",
                        "weeks": [[w0.isoformat(), b, c] for w0, b, c in shares],
                    },
                    "week",
                    ws.isoformat(),
                    start,
                )
            )

    def _lifting_day(self, slot: S.Slot) -> DayPlan:
        inp = self.inp
        kind = slot.kind
        ds = deload_status(self.readiness_by_day, slot.day, self.today, self.rec.deload_red_days)
        windows = list(getattr(self, "windows", inp.windows))
        if ds.active and not any(
            w.reason == "red_streak" and w.start == ds.trigger_day for w in windows
        ):
            windows.append(L.DeloadWindow(ds.trigger_day, ds.until, "red_streak"))
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
                        "reason": "no variant at the current gym yet: set up this machine",
                        "needs_setup": True,
                        "progression_note": (
                            f"Set up this machine for {inp.gym_name}"
                            if inp.gym_name
                            else "Set up this machine"
                        ),
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
            spec = self._spec(pe, kind, v)
            hist = [e for e in ctx.history if e.day < slot.day]
            presc = L.prescribe(spec, hist, self.cfg, day=slot.day, ctx=self._env(ctx, windows))
            if presc.ladder is not None:
                presc.ladder = replace(presc.ladder, day=self._first_day.get(v.id, slot.day))
            if "increase_load" in presc.rule:
                increases.append(v.name)
            err = presc.effective_rep_range or spec.rep_range
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
                    "effective_rep_range": list(err),
                    "progression_state": presc.state,
                    "progression_note": presc.note,
                    "sets": [s.as_dict() for s in presc.sets],
                    "last": presc.previous.as_dict() if presc.previous else None,
                    "rule": presc.rule,
                    "reason": presc.reason,
                    "flags": presc.flags,
                    "needs_setup": False,
                    **({"hint_weight_kg": presc.hint_weight_kg} if presc.hint_weight_kg else {}),
                }
            )
            out: dict[str, Any] = {
                "day": slot.day.isoformat(),
                "sets": [s.as_dict() for s in presc.sets],
                "flags": presc.flags,
                "state": presc.state,
                "effective_rep_range": list(err),
            }
            if presc.ladder is not None:
                out["ladder"] = _ladder_dict(presc.ladder)
            self.decisions.append(
                DecisionRec(
                    "progression",
                    presc.rule,
                    presc.reason,
                    presc.inputs,
                    out,
                    "variant",
                    str(v.id),
                    slot.id,
                )
            )
            summary = self._lift_summary.setdefault(v.exercise_slug, {"variant_id": v.id})
            summary.setdefault("state", presc.state)
            summary.setdefault("note", presc.note)
            summary.setdefault("effective_rep_range", list(err))
            if presc.ladder is not None and presc.ladder.step != L.LADDER_RESET_SMALL:
                self.triggers.append(
                    TriggerRec(
                        "lift_stall",
                        f"lift_stall:{v.id}:{presc.ladder.day}",
                        {
                            "variant": v.name,
                            "variant_id": v.id,
                            "ladder_step": presc.ladder.step,
                            "proposal": "rep_range_or_variation"
                            if presc.ladder.step == L.LADDER_ADVISOR
                            else "ladder_exhausted"
                            if presc.ladder.step > L.LADDER_RESET_LARGE
                            else "last_resort_reset",
                            "recent_e1rm": presc.inputs.get("recent_e1rm"),
                            "last": presc.inputs.get("last"),
                            "load_kg": presc.load,
                            "flags": presc.flags,
                            "reason": presc.reason,
                        },
                        "variant",
                        str(v.id),
                        slot.day,
                        slot.id,
                    )
                )
            if presc.rule == "rung_infeasible":
                self.triggers.append(
                    TriggerRec(
                        "lift_stall",
                        f"lift_stall:rung:{v.id}:{inp.week_start}",
                        {
                            "variant": v.name,
                            "variant_id": v.id,
                            "proposal": "smaller_increment_or_variation",
                            "reason": presc.reason,
                            "load_kg": presc.load,
                        },
                        "variant",
                        str(v.id),
                        slot.day,
                        slot.id,
                    )
                )
        cover = next((w for w in windows if w.covers(slot.day)), None)
        if ds.active:
            self.decisions.append(
                DecisionRec(
                    "deload",
                    "deload:red_streak",
                    f"{ds.streak} consecutive red days (>= {self.rec.deload_red_days}): deload "
                    f"{ds.trigger_day} .. {ds.until}: sets -{self.st.deload.sets_pct:g}%, "
                    "loads kept",
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
                {
                    "active": True,
                    "from": cover.start.isoformat(),
                    "until": cover.until.isoformat(),
                    "reason": cover.reason,
                }
                if cover is not None
                else None
            ),
        }
        self._increases[slot.id] = increases
        return DayPlan(
            slot.id,
            slot.day,
            kind,
            kind,
            _TITLES[kind] + (" (deload)" if cover is not None else ""),
            pres,
        )
