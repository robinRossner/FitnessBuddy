"""`GET /runs/{id}/analysis`: one run, its laps judged against the plan, with the verdict."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import common
from app.analytics.common import num
from app.analytics.running import (
    Ctx,
    classify,
    evaluate_interval_run,
    load_ctx,
    target_interval_pace,
)
from app.db.models import Lap, PlannedSession, Run
from app.engine import running as R
from app.engine.pace import fmt_pace
from app.engine.persist import planned_to_dict
from app.sources.garmin.mapping import RUN_TYPES


def _lap_kinds(laps: tuple[R.LapData, ...], split: R.IntervalSplit | None) -> list[str]:
    """warmup | work | recovery | cooldown | other for every lap (same order as `laps`)."""
    intensity = [(lap.intensity or "").lower() for lap in laps]
    base = [
        "warmup" if i == "warmup" else "cooldown" if i == "cooldown" else "other" for i in intensity
    ]
    if split is None:
        return base
    work_ids = {id(lap) for lap in split.work}
    work_idx = [i for i, lap in enumerate(laps) if id(lap) in work_ids]
    first, last = work_idx[0], work_idx[-1]
    kinds = list(base)
    for i, lap in enumerate(laps):
        if i in work_idx:
            kinds[i] = "work"
        elif intensity[i] in R.REST_INTENSITIES:
            kinds[i] = "recovery"
        elif kinds[i] != "other":
            continue  # explicit warm-up / cool-down from the FIT lap intensity
        elif i < first:
            kinds[i] = "warmup"
        elif i > last:
            kinds[i] = "cooldown"
        elif lap.distance_m > 0:
            kinds[i] = "recovery"
    return kinds


def _laps_out(db, run_row: Run, rd: R.RunData | None, ctx: Ctx | None, typ: str, split) -> list:
    db_laps = list(db.scalars(select(Lap).where(Lap.run_id == run_row.id).order_by(Lap.lap_index)))
    if rd is not None and len(rd.laps) == len(db_laps):
        data = rd.laps
    else:
        data = tuple(
            R.LapData(
                lap.duration_s or 0.0, lap.distance_m or 0.0, lap.avg_hr, lap.max_hr, lap.intensity
            )
            for lap in db_laps
        )
    kinds = _lap_kinds(data, split)
    target = target_interval_pace(ctx, rd) if (ctx and rd and typ == "intervals") else None
    out = []
    for lap, d, kind in zip(db_laps, data, kinds, strict=True):
        t_pace = lo = hi = pace_ok = hr_ok = None
        if ctx is not None and kind == "work" and typ == "intervals":
            lo, hi = ctx.zones.interval_low, ctx.zones.interval_high
            t_pace = target
            if d.pace is not None and target is not None:
                pace_ok = d.pace <= target * (1 + ctx.rc.interval_pace_tolerance_pct / 100)
            if d.avg_hr is not None:
                hr_ok = lo <= d.avg_hr <= hi
        elif ctx is not None and kind == "other" and typ in ("easy", "long"):
            lo, hi = ctx.zones.easy_low, ctx.zones.easy_high
            if d.avg_hr is not None:
                hr_ok = d.avg_hr <= hi
        out.append(
            {
                "index": lap.lap_index,
                "kind": kind,
                "distance_m": num(d.distance_m, 1),
                "duration_s": num(d.duration_s, 1),
                "avg_pace_s_per_km": num(lap.avg_pace_s_per_km or d.pace, 1),
                "avg_hr": num(d.avg_hr, 1),
                "max_hr": num(d.max_hr, 1),
                "target_pace_s_per_km": num(t_pace, 1),
                "target_hr_low": lo,
                "target_hr_high": hi,
                "pace_ok": pace_ok,
                "hr_ok": hr_ok,
            }
        )
    return out


def _verdict(typ: str, rd: R.RunData, ctx: Ctx, ev, target: float | None) -> dict:
    zones = ctx.zones
    if typ == "intervals":
        if ev is None:
            return {
                "passed": None,
                "summary": "Interval session, but the laps do not show the work and recovery reps.",
                "reasons": [],
            }
        reasons = list(ev.reasons)
        if not reasons:
            if ev.hr_in_zone and ev.work_hr is not None:
                reasons.append(
                    f"work HR {ev.work_hr:.0f} inside the target zone "
                    f"{zones.interval_low}-{zones.interval_high}"
                )
            if ev.recovery_drop_bpm is not None:
                reasons.append(f"HR dropped {ev.recovery_drop_bpm:.0f} bpm in the recovery laps")
        if target:
            faster = (target - ev.work_pace_s_per_km) / target * 100
            if faster > ctx.rc.interval_fast_flag_pct:
                reasons.append(
                    f"Reps ran ~{faster:.0f}% faster than target {fmt_pace(target)}/km; "
                    "the target will catch up via VDOT"
                )
        hr = f", work HR {ev.work_hr:.0f}" if ev.work_hr is not None else ""
        head = f"{ev.reps} reps at {fmt_pace(ev.work_pace_s_per_km)}/km{hr}"
        return {
            "passed": ev.passed,
            "summary": f"{head}: {'criteria met' if ev.passed else 'criteria not met'}.",
            "reasons": reasons,
        }
    ev = R.evaluate_easy_run(rd, zones, ctx.rc.easy)
    reasons = list(ev.reasons)
    if not reasons:
        if ev.avg_hr is not None:
            reasons.append(f"avg HR {ev.avg_hr:.0f} within the easy ceiling {zones.easy_high}")
        if ev.pct_at_or_below_cap is not None:
            reasons.append(
                f"{ev.pct_at_or_below_cap:.0f}% of the time at or below {zones.easy_high}"
            )
    hr = f"avg HR {ev.avg_hr:.0f}" if ev.avg_hr is not None else "no HR data"
    share = (
        f", {ev.pct_at_or_below_cap:.0f}% at or below {zones.easy_high}"
        if ev.pct_at_or_below_cap is not None
        else ""
    )
    return {
        "passed": ev.passed,
        "summary": f"{'Easy' if typ == 'easy' else 'Long'} run: {hr}{share}: "
        f"{'passed' if ev.passed else 'not passed'}.",
        "reasons": reasons,
    }


def run_analysis(db: Session, run_id: str) -> dict | None:
    row = db.scalar(select(Run).where(Run.id == run_id, Run.deleted_at.is_(None)))
    if row is None:
        return None
    day = (row.started_at_local or row.started_at_utc).date()
    planned = db.get(PlannedSession, row.planned_session_id) if row.planned_session_id else None
    planned_out = (
        planned_to_dict(planned) if planned is not None and planned.deleted_at is None else None
    )
    is_run = row.activity_type in RUN_TYPES and row.distance_m is not None
    ctx = load_ctx(db, day, day) if is_run else None
    rd = next((r for r in ctx.runs if r.id == row.id), None) if ctx else None
    typ = "other"
    split = ev = None
    if ctx is not None and rd is not None:
        typ = classify(rd, ctx)
        if typ == "intervals":
            split, ev = evaluate_interval_run(ctx, rd)
        verdict = _verdict(typ, rd, ctx, ev, target_interval_pace(ctx, rd) if ev else None)
    else:
        verdict = {"passed": None, "summary": "Not a running activity: no verdict.", "reasons": []}
    return {
        "generated_at": common.generated_at(),
        "run": {
            "id": row.id,
            "date": day.isoformat(),
            "type": typ,
            "name": row.title,
            "distance_m": num(row.distance_m, 1),
            "duration_s": num(row.duration_s, 1),
            "avg_hr": num(row.avg_hr, 1),
            "max_hr": num(row.max_hr, 1),
            "avg_pace_s_per_km": num(row.avg_pace_s_per_km, 1),
            "training_effect_aerobic": num(row.training_effect_aerobic, 1),
            "training_effect_anaerobic": num(row.training_effect_anaerobic, 1),
        },
        "planned": planned_out,
        "laps": _laps_out(db, row, rd, ctx, typ, split),
        "verdict": verdict,
    }
