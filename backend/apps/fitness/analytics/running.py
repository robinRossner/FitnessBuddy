"""`GET /stats/running`: weekly km + time in zones, efficiency, VDOT and interval evaluations.

All rules come from the engine (`engine/running.py`, `hr.py`, `pace.py`, `vdot.py`); this module
only loads the runs, calls those functions per run and shapes the result. The headline VDOT is the
engine's quality VDOT (the last `running:vdot` record of the decision log, the value the plan's
paces come from); the per-run pace-model fit is kept as the secondary `vdot_fit` series.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.analytics import common, planned
from apps.fitness.analytics.common import num
from apps.fitness.analytics.zones import ZONES5, time_in_zones
from apps.fitness.db.models import PlannedSession, Run
from apps.fitness.engine import config_service as cs
from apps.fitness.engine import data as D
from apps.fitness.engine import running as R
from apps.fitness.engine.config import EngineConfig
from apps.fitness.engine.hr import HrZones, zones_from_config
from apps.fitness.engine.pace import VdotPaceModel, make_pace_model
from apps.fitness.engine.vdot import VdotRecord
from apps.fitness.settings import get_settings

PLANNED_TYPES = {"easy_run": "easy", "long_run": "long", "intervals": "intervals"}
RUN_TYPE_KEYS = (
    "easy",
    "intervals",
    "long",
    "other",
)  # `other`: reserved, classify() never yields it
KPI_WINDOW_DAYS = 28
EFFICIENCY_RUNS = 3  # runs averaged per efficiency KPI
EFFICIENCY_TOLERANCE_DAYS = 14  # the "4 weeks ago" runs may be this far from that date


@dataclass
class Planned:
    kind: str
    prescription: dict[str, Any] = field(default_factory=dict)

    @property
    def work(self) -> dict[str, Any]:
        return ((self.prescription.get("session") or {}).get("structure") or {}).get("work") or {}


@dataclass
class Ctx:
    cfg: EngineConfig
    zones: HrZones
    runs: list[R.RunData]  # oldest first, from (since - VDOT lookback)
    planned: dict[str, Planned]  # run id -> linked planned session

    @property
    def rc(self):
        return self.cfg.running_cfg


def load_ctx(db: Session, since: date, until: date) -> Ctx:
    cs.ensure_seeded(db, get_settings().engine_config_path)
    cfg, _ = cs.load_active(db)
    lookback = cfg.running_cfg.vdot.lookback_weeks
    runs = D.load_runs(db, since - timedelta(weeks=lookback), until)
    planned = {
        rid: Planned(kind, prescription or {})
        for rid, kind, prescription in db.execute(
            select(Run.id, PlannedSession.kind, PlannedSession.prescription)
            .join(PlannedSession, Run.planned_session_id == PlannedSession.id)
            .where(Run.deleted_at.is_(None))
        )
    }
    return Ctx(cfg, zones_from_config(cfg), runs, planned)


def classify(run: R.RunData, ctx: Ctx, planned: Planned | None = None) -> str:
    """'easy' | 'long' | 'intervals': the linked plan's kind, else the engine's interval detection
    (title / laps), else long above `running.long_run.min_km`, else easy."""
    planned = planned or ctx.planned.get(run.id)
    if planned and planned.kind in PLANNED_TYPES:
        return PLANNED_TYPES[planned.kind]
    if R.is_interval_run(run):
        return "intervals"
    return "long" if run.km >= ctx.rc.long_run.min_km else "easy"


def model_at(ctx: Ctx, day: date) -> VdotPaceModel:
    """The pace model as the planner would have fitted it on `day`."""
    model = make_pace_model(ctx.cfg)
    since = day - timedelta(weeks=ctx.rc.vdot.lookback_weeks)
    obs = [
        o
        for r in ctx.runs
        if since <= r.day <= day and (o := R.best_effort(r, ctx.rc.vdot.min_effort_km * 1000))
    ]
    model.fit(obs)
    return model


def target_interval_pace(ctx: Ctx, run: R.RunData) -> float | None:
    planned = ctx.planned.get(run.id)
    if planned and planned.work.get("pace_s_per_km"):
        return float(planned.work["pace_s_per_km"])
    rng = model_at(ctx, run.day).interval_pace(ctx.rc.intervals.rep_distance_m)
    return (rng.min_s_per_km + rng.max_s_per_km) / 2 if rng else None


def prescribed_reps(ctx: Ctx, run: R.RunData) -> int | None:
    planned = ctx.planned.get(run.id)
    reps = planned.work.get("reps") if planned else None
    return int(reps) if reps else None


def session_type(ctx: Ctx, run: R.RunData) -> str:
    planned = ctx.planned.get(run.id)
    st = ((planned.prescription.get("session") or {}).get("structure") or {}) if planned else {}
    return st.get("session_type") or "vo2max"


def hr_lines(ctx: Ctx, run: R.RunData) -> tuple[float | None, float | None]:
    """(green line, safety ceiling) in bpm of the run's quality session type (None w/o LTHR)."""
    from apps.fitness.engine.quality import type_spec

    lthr = ctx.zones.lthr
    if not lthr:
        return None, None
    spec = type_spec(ctx.rc, session_type(ctx, run))
    # round half up, like the HR zones (apps/fitness/engine/hr.py)
    return int(lthr * spec.green_hr_pct_lthr + 0.5), int(lthr * spec.hr_ceiling_pct_lthr + 0.5)


def evaluate_interval_run(ctx: Ctx, run: R.RunData):
    """(split, evaluation) or (None, None) when the laps do not show an interval structure.
    Judged by pace against the planned (else the model's) target; HR is a ceiling only."""
    split = R.split_intervals(run.laps)
    if split is None:
        return None, None
    green, ceiling = hr_lines(ctx, run)
    ev = R.evaluate_intervals(
        split,
        ctx.zones,
        ctx.rc.intervals,
        prescribed_reps=prescribed_reps(ctx, run),
        target_pace=target_interval_pace(ctx, run),
        pace_tol_pct=ctx.rc.interval_pace_tolerance_pct,
        hr_ceiling=ceiling,
        green_hr=green,
    )
    return split, ev


def _efficiency(r: R.RunData) -> float | None:
    return R.efficiency(r.distance_m, r.duration_s, r.avg_hr)


def _efficiency_kpis(ctx: Ctx, today: date) -> dict:
    """Mean efficiency of the last 3 easy / long runs vs the 3 closest to 28 days earlier (not
    the same runs, within +-14 days of that date). None when there are fewer than 3 of either."""
    eff = [
        (r, e)
        for r in ctx.runs
        if r.day <= today and classify(r, ctx) in ("easy", "long") and (e := _efficiency(r))
    ]  # oldest first
    current = eff[-EFFICIENCY_RUNS:]
    target = today - timedelta(days=KPI_WINDOW_DAYS)
    taken = {r.id for r, _ in current}
    pool = [
        (r, e)
        for r, e in eff
        if r.id not in taken and abs((r.day - target).days) <= EFFICIENCY_TOLERANCE_DAYS
    ]
    pool.sort(key=lambda p: (abs((p[0].day - target).days), p[0].day))
    before = pool[:EFFICIENCY_RUNS]
    cur = common.mean([e for _, e in current]) if len(current) == EFFICIENCY_RUNS else None
    ago = common.mean([e for _, e in before]) if len(before) == EFFICIENCY_RUNS else None
    change = (cur - ago) / ago * 100 if cur is not None and ago else None
    return {
        "efficiency_current": num(cur, 4),
        "efficiency_4w_ago": num(ago, 4),
        "efficiency_change_pct": num(change),
    }


def _fit_at(ctx: Ctx, day: date) -> float | None:
    """The pace model's per-run fit on `day` (a race-fit estimate, NOT what the plan uses); None
    while it is still only the seed race (no hard effort)."""
    model = model_at(ctx, day)
    return model.vdot if model.efforts_used else None


def vdot_state_at(history: list[tuple[VdotRecord, str | None]], day: date) -> VdotRecord | None:
    """The engine's VDOT state on `day`: its last record on or before it."""
    return next((rec for rec, _why in reversed(history) if rec.day <= day), None)


def vdot_series(history: list[tuple[VdotRecord, str | None]]) -> list[dict]:
    """The engine's VDOT as a step series: one point per record of the decision log."""
    return [
        {
            "date": rec.day.isoformat(),
            "value": num(rec.vdot, 2),
            "source": rec.source,
            "delta": num(rec.delta, 2),
            "reason": why,
        }
        for rec, why in history
    ]


def running_kpis(
    ctx: Ctx,
    today: date,
    week_runs: list[R.RunData],
    planned_runs: int,
    vdot_history: list[tuple[VdotRecord, str | None]],
) -> dict:
    now = vdot_state_at(vdot_history, today)
    then = vdot_state_at(vdot_history, today - timedelta(days=KPI_WINDOW_DAYS))
    cur, ago = (now.vdot if now else None), (then.vdot if then else None)
    since = today - timedelta(days=KPI_WINDOW_DAYS - 1)
    drift = [
        r
        for r in ctx.runs
        if since <= r.day <= today
        and classify(r, ctx) == "easy"
        and (d := R.decoupling_pct(r.laps)) is not None
        and d > ctx.rc.easy.decoupling_flag_pct
    ]
    return {
        "vdot_current": num(cur, 2),
        "vdot_4w_ago": num(ago, 2),
        "vdot_change": num(cur - ago, 2) if cur is not None and ago is not None else None,
        "vdot_source": now.source if now else None,
        "vdot_fit_current": num(_fit_at(ctx, today), 1),
        **_efficiency_kpis(ctx, today),
        "easy_hr_drift_flags_4w": len(drift),
        "runs_this_week": len(week_runs),
        "runs_planned_this_week": planned_runs,
        "km_this_week": num(sum(r.km for r in week_runs)),
    }


def running_stats(db: Session, weeks: int) -> dict:
    today = common.today()
    starts = common.week_starts(weeks, today)
    first = starts[0]
    # the KPIs need 4 weeks (+ the VDOT lookback) of history even for a 1-week chart
    ctx = load_ctx(db, min(first, today - timedelta(days=KPI_WINDOW_DAYS)), today)
    window = [r for r in ctx.runs if r.day >= first]

    weekly = {
        w: {
            "week_start": w.isoformat(),
            "km": 0.0,
            "time_s": 0.0,
            "runs": 0,
            "detail": [],
            "by_type": dict.fromkeys(RUN_TYPE_KEYS, 0),
            "z": dict.fromkeys(ZONES5, 0),
        }
        for w in starts
    }
    efficiency, intervals = [], []
    for r in window:
        b = weekly[common.week_start(r.day)]
        b["km"] += r.km
        b["time_s"] += r.duration_s
        b["runs"] += 1
        secs, _src = time_in_zones(r, ctx.zones)
        for z, s in secs.items():
            b["z"][z] += s
        typ = classify(r, ctx)
        b["by_type"][typ] += 1
        b["detail"].append(
            {"run_id": r.id, "date": r.day.isoformat(), "type": typ, "km": num(r.km)}
        )
        if typ in ("easy", "long"):
            efficiency.append(
                {
                    "date": r.day.isoformat(),
                    "run_id": r.id,
                    "type": typ,
                    "distance_m": num(r.distance_m, 0),
                    "avg_hr": num(r.avg_hr, 1),
                    "avg_pace_s_per_km": num(r.pace, 1),
                    "efficiency": num(R.efficiency(r.distance_m, r.duration_s, r.avg_hr), 4),
                    "decoupling_pct": num(R.decoupling_pct(r.laps)),
                }
            )
        else:
            _split, ev = evaluate_interval_run(ctx, r)
            if ev is None:
                continue
            intervals.append(
                {
                    "date": r.day.isoformat(),
                    "run_id": r.id,
                    "reps": ev.reps,
                    "target_pace_s_per_km": num(target_interval_pace(ctx, r), 1),
                    "avg_work_pace_s_per_km": num(ev.work_pace_s_per_km, 1),
                    "avg_work_hr": num(ev.work_hr, 1),
                    "target_hr_low": ctx.zones.interval_low,
                    "target_hr_high": ctx.zones.interval_high,
                    "hr_in_range": ev.hr_in_zone,
                    "recovery_ok": ev.recovery_ok,
                    "passed": ev.passed,
                }
            )
    vdot_fit = [
        {"date": d.isoformat(), "value": num(model_at(ctx, d).vdot, 1)}
        for d in sorted({r.day for r in window})
    ]
    vdot_history = D.load_vdot_history(db)
    this_week = common.week_start(today)
    planned_runs = planned.planned_counts(db, [this_week])[this_week][1]
    kpis = running_kpis(
        ctx, today, [r for r in window if r.day >= this_week], planned_runs, vdot_history
    )
    return {
        "generated_at": common.generated_at(),
        "kpis": kpis,
        "weekly": [
            {
                "week_start": b["week_start"],
                "km": num(b["km"]),
                "time_s": round(b["time_s"]),
                "runs": b["runs"],
                "time_in_zones_s": b["z"],
                "runs_detail": b["detail"],
                "runs_by_type": b["by_type"],
            }
            for b in weekly.values()
        ],
        "efficiency": efficiency,
        "vdot": vdot_series([(rec, why) for rec, why in vdot_history if rec.day <= today]),
        "vdot_fit": vdot_fit,
        "intervals": intervals,
    }
