"""The weekly planner: plain data in, a `WeekPlan` (sessions + decisions + advisor triggers) out.

No database access here (see `app/engine/data.py` for loading and `persist.py` for writing), and
everything tunable comes from the config object. Steps for each (not yet frozen) day from today:

1. template week (config `weekly_template`) -> recovery rules (no intervals right after heavy
   legs, swap algorithm in `scheduling.py`);
2. readiness for today (deterministic, `readiness.py`), red-streak -> deload window;
3. lifting (`lifting.py`): every lift is analysed first (stall, censored misses, ladder); three
   or more stalls within 14 days become ONE soft deload week + a retest instead of per-lift
   resets, a high fatigue index becomes a reactive deload; then the per-slot prescriptions;
4. running: VDOT state (`vdot.py`), the Friday quality cycle (`quality.py`), the long run
   (step-back week, drift gate), the easy run with strides, all fitted under the weekly cap
   (4-week actual mean, `running.py`) and the single-run cap;
5. today's readiness adjustments are written INTO the planned session (yellow: -1 set / easy
   run, red: rest or short easy run) together with the reason;
6. every rule that fired is returned as a decision record; stalls, deloads, rule conflicts,
   HRV anomalies, stagnating running metrics and a possible 4th run also as advisor triggers.

Specs: docs/research/lifting-progression.md, docs/research/running-progression.md and the
decisions in docs/spec/decisions.md ("Progression engine v2").
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from statistics import fmean
from typing import Any

from app.engine import lifting as L
from app.engine import quality as Q
from app.engine import running as R
from app.engine import scheduling as S
from app.engine import vdot as V
from app.engine.config import EngineConfig
from app.engine.hr import HrZones, zones_from_config
from app.engine.pace import PaceModel, fmt_pace, make_pace_model, vdot_from_race
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
    ladder: list[L.LadderEvent] = field(default_factory=list)  # stall-ladder steps so far


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
    # placements decided by the missed-session policy / the coach (planned_sessions.schedule_lock)
    slot_locks: dict[str, S.SlotLock] = field(default_factory=dict)
    excluded_slots: set[str] = field(default_factory=set)  # skipped / deleted: no training load
    gym_name: str | None = None
    # engine state from decisions_log (data.py)
    windows: list[L.DeloadWindow] = field(default_factory=list)
    quality_log: list[Q.QualityRecord] = field(default_factory=list)
    vdot_log: list[V.VdotRecord] = field(default_factory=list)
    cycle_start: date | None = None
    last_step_back: date | None = None  # Monday of the last long-run step-back week
    bodyweight_kg: float | None = None


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
    moved_from: date | None = None
    change_reason: str | None = None
    changed_by: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "moved_from": self.moved_from.isoformat() if self.moved_from else None,
            "change_reason": self.change_reason,
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
    lifting: dict[str, Any] = field(default_factory=dict)  # per-lift states for the summaries
    running: dict[str, Any] = field(default_factory=dict)  # phase, VDOT, long-run block


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


def work_km(reps: int, rep_m: float, recovery_m: float | None) -> float:
    """Rep + jog-recovery distance of a quality block (no warm-up / cool-down)."""
    return (reps * rep_m + max(reps - 1, 0) * (recovery_m or 0.0)) / 1000


def _round1(x: float) -> float:
    return round(x + 1e-9, 1)


class _Planner:
    def __init__(self, inp: PlanInputs):
        self.inp = inp
        self.cfg = inp.cfg
        self.rc = inp.cfg.running_cfg
        self.rd = inp.cfg.readiness_cfg
        self.rec = inp.cfg.recovery_cfg
        self.st = inp.cfg.strength
        self.zones = zones_from_config(inp.cfg)
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
            lthr = self.zones.lthr or 0
            ev = R.evaluate_intervals(
                sp,
                self.zones,
                self.rc.intervals,
                prescribed_reps=rec.reps,
                target_pace=rec.pace_s_per_km,
                pace_tol_pct=self.rc.interval_pace_tolerance_pct,
                hr_ceiling=lthr * spec.hr_ceiling_pct_lthr if lthr else None,
                green_hr=lthr * spec.green_hr_pct_lthr if lthr else None,
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
            "quality": self.quality_plan.as_dict(),
            "long_run": getattr(self, "_long_info", {}),
        }
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
            increment_kg=pe.increment_kg,
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
            w = L.DeloadWindow(start, until, "multi_lift_stall", retest)
            self.windows.append(w)
            self._created_window = w
            names = [ctx.variant.name for ctx, _an in stalled]  # type: ignore[union-attr]
            self.decisions.append(
                DecisionRec(
                    "deload",
                    "deload:multi_lift_stall",
                    f"{n_stalled} lifts stalled within {st.multi_stall_days} days "
                    f"({', '.join(names)}): no per-lift resets; soft deload {start} .. {until} "
                    "(sets halved, loads kept), then a retest of the old loads",
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
        elif not recent_window and not any(w.covers(first) for w in self.windows):
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
            self._long_eval = R.evaluate_easy_run(last_long, self.zones, self.rc.easy)
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
        cap = R.volume_cap_km(
            weekly, ws, rc.weekly_volume_cap_pct, weeks_avg=vol.mean_weeks, abs_km=vol.cap_abs_km
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
        reasoning = (
            f"4-week mean {mean:g} km -> cap max({mean:g} x {factor:g}, "
            f"{mean:g} + {vol.cap_abs_km:g}) = {cap:g} km"
            if cap is not None
            else "no runs in the last 4 weeks: no cap"
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

    # ------------------------------------------------------------------ runs
    def _phase_fields(self) -> dict[str, Any]:
        return {"phase": self.phase.phase, "phase_week": self.phase.phase_week}

    def _strides(self) -> dict[str, Any] | None:
        s = self.rc.easy.strides
        if s.reps <= 0:
            return None
        return {
            "reps": s.reps,
            "duration_s": s.duration_s,
            "recovery_s": s.recovery_s,
            "note": f"{s.reps} x {s.duration_s:g} s relaxed fast strides near the end, "
            f"{s.recovery_s:g} s easy jog / walk between",
        }

    def _easy_prescription(self, km: float, reason: str, *, strides: bool = True) -> dict[str, Any]:
        last = next((r for r in reversed(self.inp.runs) if R.split_intervals(r.laps) is None), None)
        after_lower = (
            last is not None and (last.day - timedelta(days=1)) in self.inp.actual_lower_days
        )
        ev = (
            R.evaluate_easy_run(last, self.zones, self.rc.easy, after_lower=after_lower)
            if last
            else None
        )
        st = self._strides() if strides else None
        out: dict[str, Any] = {
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
            **self._phase_fields(),
            "progression_note": f"easy {round(km, 1):g} km"
            + (f" + {st['reps']} x {st['duration_s']:g} s strides" if st else ""),
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
            "pace": {**_paces(self.model.easy_pace(self.zones.easy_high)), "note": "HR leads"},
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

    # ------------------------------------------------------------------ stagnation / 4th run
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

    def _fourth_run_trigger(self) -> None:
        fr = self.rc.fourth_run
        ws = self.inp.week_start
        weeks = [ws - timedelta(days=7 * i) for i in range(1, fr.consistent_weeks + 1)]
        counts = {w: 0 for w in weeks}
        for r in self.inp.runs:
            w = R.iso_week_start(r.day)
            if w in counts:
                counts[w] += 1
        consistent = all(c >= fr.min_runs_per_week for c in counts.values())
        mean4 = R.mean_km(R.weekly_km(self.inp.runs), ws, 4)
        longest = max(
            (r.km for r in self.inp.runs if r.day >= ws - timedelta(days=28)), default=0.0
        )
        if consistent and mean4 >= fr.min_weekly_km and longest >= fr.min_long_run_km:
            self.triggers.append(
                TriggerRec(
                    "fourth_run",
                    f"fourth_run:{ws.year}-{ws.month:02d}",
                    {
                        "weeks_consistent": fr.consistent_weeks,
                        "mean_km_4w": mean4,
                        "longest_km_4w": round(longest, 1),
                        "suggestion": "an easy 30-40 min run on Thursday after upper or on "
                        "Monday, never Tuesday; lifting stays at 3 per week",
                    },
                    "week",
                    ws.isoformat(),
                    ws,
                )
            )
            self.decisions.append(
                DecisionRec(
                    "running",
                    "running:fourth_run_ready",
                    f"{fr.consistent_weeks} consistent weeks, {mean4:g} km/week, longest "
                    f"{longest:.1f} km: a 4th run is suggested to the coach (not added)",
                    {"runs_per_week": {w.isoformat(): c for w, c in sorted(counts.items())}},
                    {"suggested": True},
                    "week",
                    ws.isoformat(),
                )
            )


def _window_dict(w: L.DeloadWindow) -> dict[str, Any]:
    return {
        "start": w.start.isoformat(),
        "until": w.until.isoformat(),
        "reason": w.reason,
        "deload": w.deload,
        "retest": {
            str(k): {"load": r.load, "reps": list(r.reps), "best_e1rm": r.best_e1rm}
            for k, r in w.retest.items()
        },
    }


def _ladder_dict(e: L.LadderEvent) -> dict[str, Any]:
    return {
        "day": e.day.isoformat(),
        "step": e.step,
        "load": e.load,
        "peak_e1rm": e.peak_e1rm,
        "from_load": e.from_load,
    }


def generate_week(inp: PlanInputs) -> WeekPlan:
    """The whole weekly plan (days >= today that are not frozen) with decisions and triggers."""
    return _Planner(inp).run()
