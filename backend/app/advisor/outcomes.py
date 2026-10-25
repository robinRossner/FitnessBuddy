"""Outcome tracking: did an advisor change help?

A daily job looks at config versions made by the advisor that are 21-28 days old
(`advisor.outcome_after_days`) and have no outcome yet, compares a metric for the 28 days before
the change with the days since, and stores an `outcomes` row (verdict improved / neutral / worse,
metric before and after, notes). Future advisor input lists these outcomes.

Metric per changed parameter:
- `exercise_overrides.<slug>.*`: best e1RM of that exercise's variant (a swap is not scored: a
  different exercise has a different e1RM)
- other strength / increments / recovery parameters: best e1RM across the program's lifts
  (mean of the per-lift changes)
- `running.intervals.*`: share of interval sessions that passed
- other `running.*` parameters: aerobic efficiency of easy and long runs

Verdict: +-`verdict_threshold_pct` (default 1 %) for e1RM and efficiency, +-15 points for the
interval pass rate. A version whose metrics have too little data at age 21 is retried daily until
age 28, then recorded as neutral ("not enough data").
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import fmean
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.advisor import service
from app.analytics import common, lifts, running
from app.db.models import AdvisorChange, ConfigVersion, Outcome
from app.engine import config_service as cs
from app.engine import data as D
from app.engine import running as R
from app.engine.program import apply_overrides
from app.settings import Settings, get_settings

log = logging.getLogger(__name__)

BEFORE_DAYS = 28
PASS_RATE_THRESHOLD = 0.15
MIN_RUNS = 2
OVERRIDE_RE = re.compile(r"^exercise_overrides\.(?P<slug>[a-z0-9_]+)\.(?P<key>[a-z_]+)$")


@dataclass
class Metric:
    name: str
    unit: str
    before: float | None = None
    after: float | None = None
    change: float | None = None  # % for e1rm / efficiency, points for the pass rate
    verdict: str | None = None  # None = could not be scored
    note: str = ""
    params: list[str] = field(default_factory=list)


def _verdict(change: float | None, threshold: float) -> str | None:
    if change is None:
        return None
    return "improved" if change >= threshold else "worse" if change <= -threshold else "neutral"


def _pct(before: float, after: float) -> float:
    return (after - before) / before * 100


def _windows(applied: date, today: date) -> tuple[tuple[date, date], tuple[date, date]]:
    return (applied - timedelta(days=BEFORE_DAYS), applied - timedelta(days=1)), (applied, today)


def _best_e1rm(rows, start: date, end: date) -> float | None:
    vals = [r.e1rm for r in rows if r.e1rm and start <= r.day <= end]
    return max(vals) if vals else None


def lift_metric(
    name: str, rows_by_variant, variant_ids: list[int], windows, threshold: float
) -> Metric:
    (b0, b1), (a0, a1) = windows
    pairs = []
    for vid in variant_ids:
        rows = rows_by_variant.get(vid, [])
        b, a = _best_e1rm(rows, b0, b1), _best_e1rm(rows, a0, a1)
        if b and a:
            pairs.append((b, a))
    m = Metric(name, "kg")
    if not pairs:
        m.note = "no training of this lift both before and after the change"
        return m
    m.before, m.after = round(fmean(p[0] for p in pairs), 1), round(fmean(p[1] for p in pairs), 1)
    m.change = round(fmean(_pct(b, a) for b, a in pairs), 2)
    m.verdict = _verdict(m.change, threshold)
    return m


def efficiency_metric(db: Session, windows, threshold: float, today: date) -> Metric:
    (b0, _b1), (a0, a1) = windows
    ctx = running.load_ctx(db, b0, today)
    before, after = [], []
    for r in ctx.runs:
        if running.classify(r, ctx) not in ("easy", "long"):
            continue
        ef = R.efficiency(r.distance_m, r.duration_s, r.avg_hr)
        if ef is None:
            continue
        if b0 <= r.day < a0:
            before.append(ef)
        elif a0 <= r.day <= a1:
            after.append(ef)
    m = Metric("aerobic_efficiency", "m/min per bpm")
    if len(before) < MIN_RUNS or len(after) < MIN_RUNS:
        m.note = (
            f"fewer than {MIN_RUNS} easy / long runs before ({len(before)}) or after ({len(after)})"
        )
        return m
    m.before, m.after = round(fmean(before), 4), round(fmean(after), 4)
    m.change = round(_pct(m.before, m.after), 2)
    m.verdict = _verdict(m.change, threshold)
    return m


def interval_metric(db: Session, windows, today: date) -> Metric:
    (b0, _b1), (a0, a1) = windows
    ctx = running.load_ctx(db, b0, today)
    before, after = [], []
    for r in ctx.runs:
        if running.classify(r, ctx) != "intervals":
            continue
        _split, ev = running.evaluate_interval_run(ctx, r)
        if ev is None:
            continue
        if b0 <= r.day < a0:
            before.append(1.0 if ev.passed else 0.0)
        elif a0 <= r.day <= a1:
            after.append(1.0 if ev.passed else 0.0)
    m = Metric("interval_pass_rate", "share")
    if not before or not after:
        m.note = f"interval sessions before ({len(before)}) or after ({len(after)}) are missing"
        return m
    m.before, m.after = round(fmean(before), 2), round(fmean(after), 2)
    m.change = round((m.after - m.before) * 100, 1)  # points
    m.verdict = _verdict(m.change / 100, PASS_RATE_THRESHOLD)
    return m


def evaluate_version(
    db: Session,
    version: ConfigVersion,
    changes: list[AdvisorChange],
    settings: Settings,
    today: date,
) -> list[Metric]:
    cfg_adv = cs.load_active(db)[0].advisor_cfg
    threshold = cfg_adv.verdict_threshold_pct
    tz = ZoneInfo(settings.user_tz)
    applied = version.created_at.astimezone(tz).date()
    windows = _windows(applied, today)

    rows = lifts.rows_by_variant(db)
    cfg = cs.load_active(db)[0]
    program = service.program_raw(settings)
    prog = apply_overrides(program, cfg.exercise_overrides) if program else None
    gym = D.current_gym(db, settings.gyms_path)

    def variant_ids(slug: str | None) -> list[int]:
        if prog is None:
            return []
        ids = []
        for items in prog.days.values():
            for pe in items:
                if slug is not None and pe.exercise != slug:
                    continue
                v = D.resolve_variant(db, pe.variant, gym)
                if v is not None and v.id not in ids:
                    ids.append(v.id)
        return ids

    metrics: dict[str, Metric] = {}
    for ch in changes:
        m_over = OVERRIDE_RE.match(ch.param)
        if m_over and m_over["key"] in ("variant", "rest_s"):
            key = f"{m_over['key']}:{m_over['slug']}"
            if key not in metrics:
                what = "an exercise swap" if m_over["key"] == "variant" else "a rest time"
                metrics[key] = Metric(
                    f"{m_over['slug']}_{'swap' if m_over['key'] == 'variant' else 'rest'}",
                    "",
                    note=f"{what} is not scored",
                )
        elif ch.param.startswith("lifting."):
            key = "lifting_setting"
            if key not in metrics:
                metrics[key] = Metric("lifting_settings", "", note="a rest time is not scored")
        elif m_over:
            key = f"lift:{m_over['slug']}"
            if key not in metrics:
                metrics[key] = lift_metric(
                    f"{m_over['slug']}_e1rm_kg",
                    rows,
                    variant_ids(m_over["slug"]),
                    windows,
                    threshold,
                )
        elif ch.param.startswith("running.intervals."):
            key = "intervals"
            if key not in metrics:
                metrics[key] = interval_metric(db, windows, today)
        elif ch.param.startswith("running."):
            key = "efficiency"
            if key not in metrics:
                metrics[key] = efficiency_metric(db, windows, threshold, today)
        else:
            key = "all_lifts"
            if key not in metrics:
                metrics[key] = lift_metric(
                    "program_avg_e1rm_kg", rows, variant_ids(None), windows, threshold
                )
        metrics[key].params.append(ch.param)
    return list(metrics.values())


def overall_verdict(metrics: list[Metric]) -> str:
    verdicts = [m.verdict for m in metrics if m.verdict]
    if "worse" in verdicts:
        return "worse"
    if "improved" in verdicts:
        return "improved"
    return "neutral"


def _describe(m: Metric) -> str:
    if m.verdict is None:
        return f"{m.name}: not scored ({m.note})"
    unit = " points" if m.name == "interval_pass_rate" else " %"
    return f"{m.name} {m.before:g} -> {m.after:g} ({m.change:+.1f}{unit}) = {m.verdict}"


def run_outcome_check(
    db: Session, settings: Settings | None = None, *, today: date | None = None
) -> list[Outcome]:
    s = settings or get_settings()
    today = today or common.today()
    lo, hi = service.advisor_config(db, s).outcome_after_days
    tz = ZoneInfo(s.user_tz)
    done = set(
        db.scalars(select(Outcome.config_version_id).where(Outcome.config_version_id.is_not(None)))
    )
    made: list[Outcome] = []
    for v in db.scalars(
        select(ConfigVersion).where(ConfigVersion.author == "advisor").order_by(ConfigVersion.id)
    ):
        if v.id in done:
            continue
        age = (today - v.created_at.astimezone(tz).date()).days
        if age < lo:
            continue
        changes = list(
            db.scalars(
                select(AdvisorChange)
                .where(AdvisorChange.applied_config_version_id == v.id)
                .order_by(AdvisorChange.position)
            )
        )
        if not changes:
            continue
        metrics = evaluate_version(db, v, changes, s, today)
        scored = [m for m in metrics if m.verdict]
        if not scored and age < hi:
            continue  # not enough data yet: look again tomorrow
        verdict = overall_verdict(metrics)
        notes = f"{age} days after the change: " + "; ".join(_describe(m) for m in metrics)
        if not scored:
            notes = f"{age} days after the change: not enough data to judge. " + notes
        row = Outcome(
            kind="advisor_change",
            verdict=verdict,
            consultation_id=int(v.meta["consultation_id"])
            if (v.meta or {}).get("consultation_id")
            else None,
            config_version_id=v.id,
            notes=notes[:1500],
            metrics={
                "age_days": age,
                "before": {m.name: m.before for m in metrics},
                "after": {m.name: m.after for m in metrics},
                "metrics": [
                    {
                        "name": m.name,
                        "params": m.params,
                        "before": m.before,
                        "after": m.after,
                        "change": m.change,
                        "verdict": m.verdict,
                        "note": m.note,
                    }
                    for m in metrics
                ],
            },
        )
        db.add(row)
        made.append(row)
        log.info("advisor outcome for config %s: %s", v.version, verdict)
    if made:
        db.commit()
    return made
