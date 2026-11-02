"""Pace model, VDOT state, quality-session evaluation and easy-pace calibration."""

from __future__ import annotations

from datetime import date, timedelta
from statistics import fmean
from typing import Any

from apps.fitness.engine.core import easy_pace as EP
from apps.fitness.engine.core import interval_vdot as IV
from apps.fitness.engine.core import quality as Q
from apps.fitness.engine.core import running as R
from apps.fitness.engine.core import vdot as V
from apps.fitness.engine.core.pace import vdot_from_race
from apps.fitness.engine.core.planner.helpers import _paces
from apps.fitness.engine.core.planner.models import DecisionRec
from apps.fitness.engine.core.planner.state import PlannerState


class PaceMixin(PlannerState):
    """Running state the week is planned from (set up in `__init__`)."""

    # ------------------------------------------------------------------ pace model + VDOT
    def _fit_model(self) -> None:
        since = self.today - timedelta(weeks=self.rc.vdot.lookback_weeks)
        obs = []
        for r in self.inp.runs:
            if r.day >= since:
                o = R.best_effort(r, self.rc.vdot.min_effort_km * 1000)
                if o:
                    obs.append(o)
        self.model.fit(obs)

    def _set_vdot(self, v: float) -> None:
        if hasattr(self.model, "vdot"):
            self.model.vdot = v  # type: ignore[attr-defined]

    def _quality_evals(self) -> list[tuple[Q.QualityRecord, R.RunData | None, Any]]:
        """Every recorded quality session with its run and its evaluation (by pace)."""
        out = []
        by_day: dict[date, list[R.RunData]] = {}
        for r in self.inp.runs:
            by_day.setdefault(r.day, []).append(r)
        for rec in sorted(self.inp.quality_log, key=lambda x: x.day):
            if rec.day >= self.today:
                continue
            runs = by_day.get(rec.day, [])
            run = next((r for r in runs if R.split_intervals(r.laps) is not None), None)
            run = run or (max(runs, key=lambda r: r.km) if runs else None)
            if run is None:
                continue  # not run: missed, never judged
            if rec.session_type == "time_trial":
                out.append((rec, run, None))
                continue
            sp = R.split_intervals(run.laps)
            if sp is None:
                out.append((rec, run, None))
                continue
            spec = Q.type_spec(self.rc, rec.session_type)
            run_zones = self.zone_book.for_run(run)
            green_hr, hr_ceiling = Q.hr_lines(run_zones, spec)
            ev = R.evaluate_intervals(
                sp,
                run_zones,
                self.rc.intervals,
                prescribed_reps=rec.reps,
                target_pace=rec.pace_s_per_km,
                pace_tol_pct=self.rc.interval_pace_tolerance_pct,
                hr_ceiling=hr_ceiling,
                green_hr=green_hr,
            )
            out.append((rec, run, ev))
        return out

    def _ef_trend(self) -> float | None:
        series = []
        for r in self.inp.runs:
            if r.km >= 3 and R.split_intervals(r.laps) is None:
                ef = R.efficiency(r.distance_m, r.duration_s, r.avg_hr)
                if ef:
                    series.append((r.day, ef))
        w = self.rc.vdot.ef_trend_weeks
        recent_from = self.today - timedelta(days=7 * w - 1)
        prior_from = recent_from - timedelta(days=7 * w)
        recent = [v for d, v in series if recent_from <= d <= self.today]
        prior = [v for d, v in series if prior_from <= d < recent_from]
        if len(recent) < 2 or len(prior) < 2:
            return None
        return round((fmean(recent) - fmean(prior)) / fmean(prior) * 100, 2)

    def _prepare_running(self) -> None:
        """VDOT state, the phase of the quality cycle and the Friday session (before volume)."""
        inp, rc = self.inp, self.rc
        fitted = getattr(self.model, "vdot", None) or 40.0
        self._qevals = self._quality_evals()
        greens = [rec.day for rec, _r, ev in self._qevals if ev is not None and ev.green]
        fails = [rec.day for rec, _r, ev in self._qevals if ev is not None and ev.verdict == "fail"]
        log = sorted(inp.vdot_log, key=lambda r: r.day)
        cur = log[-1].vdot if log else fitted
        # efforts: hard best efforts (and time trials) since the last record
        efforts: list[tuple[date, float]] = []
        min_hr = (self.zones.lthr or 0) * rc.vdot.min_effort_hr_pct_lthr
        tts = {rec.day for rec in inp.quality_log if rec.session_type == "time_trial"}
        since = log[-1].day if log else self.today
        for r in inp.runs:
            if r.day <= since and log:
                continue
            o = R.best_effort(r, rc.vdot.min_effort_km * 1000)
            if o is None:
                continue
            if r.day in tts or (o.avg_hr is not None and o.avg_hr >= min_hr):
                efforts.append((r.day, vdot_from_race(o.distance_m, o.duration_s)))
        # phase of the quality cycle
        ws = inp.week_start
        cycle_start = inp.cycle_start or ws
        if rc.quality.cycle_start:
            cycle_start = date.fromisoformat(rc.quality.cycle_start)
        done_weeks = {Q.week_of(r.day) for r in inp.runs if R.is_interval_run(r)}
        done_weeks |= {Q.week_of(rec.day) for rec, run, _ev in self._qevals if run is not None}
        self.phase = Q.phase_state(rc.quality, cycle_start, ws, done_weeks)
        # the session of this week
        t = self.phase.session_type
        spec = Q.type_spec(rc, t)
        last = Q.recent_of_type(inp.quality_log, t, ws)
        last_ev = next(
            (ev for rec, _r, ev in self._qevals if last is not None and rec == last), None
        )
        verdict = last_ev.verdict if last_ev is not None else None
        if last is not None and not any(rec == last for rec, _r, _e in self._qevals):
            verdict = "missed"
        fails_same = 0
        if last is not None:
            three = ws - timedelta(weeks=3)
            fails_same = sum(
                1
                for rec, _r, ev in self._qevals
                if ev is not None
                and ev.verdict == "fail"
                and rec.day >= three
                and Q.same_prescription(rec, last)
            )
        green_window = self.today - timedelta(weeks=rc.vdot.green_window_weeks)
        last_up = max((r.day for r in log if r.delta > 0), default=None)
        fresh = [d for d in greens if d >= green_window and (last_up is None or d > last_up)]
        step_ok = len(fresh) >= rc.vdot.green_sessions

        def make(v: float, ok: bool) -> Q.QualityPlan:
            pace = Q.target_pace(v, t)
            if last is not None and verdict == "missed":
                # the session was not run: repeat it (phase and levers do not advance)
                p = Q.next_quality(spec, last, "pass_hard", pace=pace)
                p.rule, p.lever = "quality:repeat_missed", None
                p.reason = p.note = "last session was missed: same session again"
                return p
            return Q.next_quality(
                spec,
                last,
                verdict if last is not None else None,
                pace=pace,
                fails_same=fails_same,
                fails_to_step_back=rc.intervals.fails_to_step_back,
                vdot_step_ok=ok,
            )

        plan = make(cur, step_ok)
        last_run = max((r.day for r in inp.runs if r.day < self.today), default=None)
        # measured VDOT from recent interval reps (engine/core/interval_vdot.py)
        self.rep_estimate = IV.estimate_from_reps(
            inp.runs,
            self.zones,
            rc.vdot.from_intervals,
            today=self.today,
            after=V.last_drop_day(log),
            zones_of=self.zone_book.for_run,
        )
        best = self.rep_estimate.best
        measured = (
            V.Measured(
                best.day,
                best.vdot,
                rc.vdot.from_intervals.max_step,
                self.rep_estimate.reason,
                {"best": best.as_dict(), "sessions": len(self.rep_estimate.sessions)},
            )
            if best is not None and log
            else None
        )
        upd = V.update_vdot(
            log,
            fitted,
            rc.vdot,
            today=self.today,
            efforts=efforts,
            greens=greens,
            ef_trend_pct=self._ef_trend(),
            pace_lever=plan.vdot_step,
            fails=fails,
            last_run_day=last_run,
            measured=measured,
        )
        if plan.vdot_step and (upd.record is None or upd.record.delta <= 0):
            plan = make(upd.vdot, False)
        elif abs(upd.vdot - cur) > 1e-9:
            plan = make(upd.vdot, step_ok and plan.vdot_step)
        self._set_vdot(upd.vdot)
        self.vdot_update = upd
        self.quality_plan = plan
        self.quality_last = (last, last_ev)
        # first week back after a long break: no quality session
        before = [r.day for r in inp.runs if r.day < ws]
        gap = (ws - max(before)).days if before else None
        self.quality_off = gap is not None and gap >= rc.quality.no_quality_after_break_days
        self.break_days = gap
        if upd.record is not None:  # only a NEW record is logged (re-runs stay idempotent)
            self.decisions.append(
                DecisionRec(
                    "running",
                    "running:vdot",
                    upd.reason,
                    upd.evidence,
                    {"vdot": round(upd.vdot, 2), "vdot_record": upd.record.as_dict()},
                    "vdot",
                    upd.record.day.isoformat(),
                )
            )
        self.decisions.append(
            DecisionRec(
                "running",
                "running:quality_phase",
                f"{self.phase.phase} week {self.phase.phase_week}/{self.phase.phase_weeks} "
                f"(cycle {self.phase.cycle}): {Q.LABELS.get(t, t).lower()} session",
                {"done_weeks": sorted(d.isoformat() for d in done_weeks if d >= cycle_start)},
                {"phase_state": self.phase.as_dict(), "week_start": ws.isoformat()},
                "week",
                ws.isoformat(),
            )
        )

    def _calibrate_easy(self) -> None:
        """The easy / long run pace range from the athlete's own easy runs
        (engine/core/easy_pace.py).
        Quality paces keep the quality VDOT; only VDOT-based pace models are calibrated."""
        self.easy_cal: EP.EasyCalibration | None = None
        if not hasattr(self.model, "vdot"):
            return
        q = self.vdot_update.vdot
        cal = EP.calibrate_easy(
            self.inp.runs,
            self.zones,
            self.rc.easy,
            self.rc.easy_calibration,
            q,
            today=self.today,
            exclude_days={rec.day for rec in self.inp.quality_log},
            zones_of=self.zone_book.for_run,
        )
        self.easy_cal = cal
        ws = self.inp.week_start
        self.decisions.append(
            DecisionRec(
                "running",
                "running:easy_pace_calibration",
                cal.reason,
                {
                    "quality_vdot": round(q, 2),
                    "hr_ceiling": cal.hr_ceiling,
                    "config": self.rc.easy_calibration.model_dump(),
                },
                {**cal.as_dict(), "range": _paces(cal.easy_range()), "week_start": ws.isoformat()},
                "week",
                ws.isoformat(),
            )
        )

    def _easy_paces(self) -> dict[str, Any]:
        """Pace range of easy and long runs (calibrated when the easy runs support it)."""
        cal = self.easy_cal
        if cal is None:
            return {**_paces(self.model.easy_pace(self.zones.easy_high)), "basis": "model"}
        return {**_paces(cal.easy_range()), "basis": cal.basis, "reason": cal.reason}
