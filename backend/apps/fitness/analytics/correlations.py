"""`GET /stats/correlations`: small, honest scatter plots with a Pearson r, a caveat and an insight.

All pairs are matched by calendar day. Garmin stores a night's sleep (and the overnight HRV and
resting HR) under the day you woke up, so "that day's sleep" is the previous night.
Categories: `recovery` (how the body's state goes with performance) and `hybrid` (running vs
lifting interference).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.analytics import common
from apps.fitness.analytics import running as ar
from apps.fitness.analytics.common import num, pearson
from apps.fitness.db.models import DailyHealth, ExerciseSessionStats, Run, TrainingSession
from apps.fitness.engine.core import running as R
from apps.fitness.garmin.mapping import RUN_TYPES

ROLLING_BEST_DAYS = 56  # 8 weeks
RUN_LOAD_HOURS = 48
MIN_N = 5  # same minimum as `pearson`
EARLY_N = 15  # below this the insight says it is an early read
WEAK_R, CLEAR_R = 0.2, 0.4


@dataclass(frozen=True)
class Wording:
    """Insight phrases: what the pair is about, the x side, and what a positive / negative r
    means for y."""

    topic: str
    subject: str
    positive: str
    negative: str


def _note(n: int, r: float | None, extra: str = "") -> str:
    if r is None:
        text = f"n={n}, too few points for a correlation (need 5)"
    else:
        a = abs(r)
        strength = (
            "negligible" if a < 0.2 else "weak" if a < 0.4 else "moderate" if a < 0.6 else "strong"
        )
        sign = "" if strength == "negligible" else (" positive" if r > 0 else " negative")
        verdict = "not conclusive" if n < 30 else "correlation is not causation"
        text = f"n={n}, {strength}{sign}, {verdict}"
    return f"{text}. {extra}" if extra else text


def insight(w: Wording, r: float | None, n: int) -> str:
    """One cautious sentence from r and n (never claims causation)."""
    if r is None or n < MIN_N:
        return f"Not enough data yet (n={n})"
    early = ", early read" if n < EARLY_N else ""
    stats = f"(r={r:.2f}, n={n}{early})"
    if abs(r) < WEAK_R:
        return f"No clear link between {w.topic} {stats}"
    outcome = w.positive if r > 0 else w.negative
    if abs(r) < CLEAR_R:
        return f"Weak hint that {w.subject} goes with {outcome} {stats}"
    return f"{w.subject[:1].upper()}{w.subject[1:]} tends to go with {outcome} {stats}"


def _item(id_, category, wording, title, x_label, y_label, points, extra="") -> dict:
    r = pearson([p["x"] for p in points], [p["y"] for p in points])
    r = num(r, 3)
    return {
        "id": id_,
        "category": category,
        "title": title,
        "x_label": x_label,
        "y_label": y_label,
        "points": points,
        "r": r,
        "n": len(points),
        "note": _note(len(points), r, extra),
        "insight": insight(wording, r, len(points)),
    }


def _lift_performance(db: Session, start: date, end: date) -> dict[date, tuple[float, datetime]]:
    """Per lifting day: (performance, start of the first session with a scored lift that day).

    Performance = each lift's best e1RM as a % of its own best in the previous 8 weeks (100 =
    matched, >100 = a PR), averaged over the lifts of that day. A lift's first appearance has
    no history and is skipped.
    """
    rows = db.execute(
        select(
            ExerciseSessionStats.variant_id,
            ExerciseSessionStats.session_date,
            ExerciseSessionStats.best_e1rm_kg,
            TrainingSession.started_at_local,
        )
        .join(TrainingSession, ExerciseSessionStats.session_id == TrainingSession.id)
        .where(
            ExerciseSessionStats.best_e1rm_kg.is_not(None),
            ExerciseSessionStats.session_date >= start - timedelta(days=ROLLING_BEST_DAYS),
            ExerciseSessionStats.session_date <= end,
        )
        .order_by(ExerciseSessionStats.session_date)
    ).all()
    by_variant: dict[int, list[tuple[date, float]]] = {}
    for vid, day, e1rm, _started in rows:
        by_variant.setdefault(vid, []).append((day, e1rm))
    per_day: dict[date, list[float]] = {}
    starts: dict[date, datetime] = {}
    for vid, day, e1rm, started in rows:
        if day < start:
            continue
        prior = [
            v for d, v in by_variant[vid] if day - timedelta(days=ROLLING_BEST_DAYS) <= d < day
        ]
        if prior:
            per_day.setdefault(day, []).append(e1rm / max(prior) * 100)
            starts[day] = min(started, starts.get(day, started))
    return {d: (common.mean(v), starts[d]) for d, v in per_day.items()}


def _health_vs_lifting(
    perf: dict[date, tuple[float, datetime]],
    health: dict[date, DailyHealth],
    attr: str,
    scale: float,
) -> list:
    points = []
    for day in sorted(perf):
        h = health.get(day)
        v = getattr(h, attr) if h is not None else None
        if v:
            points.append(
                {
                    "date": day.isoformat(),
                    "x": num(v / scale, 2 if scale != 1 else 1),
                    "y": num(perf[day][0], 1),
                }
            )
    return points


def _run_load_vs_lifting(db: Session, perf: dict[date, tuple[float, datetime]]) -> list:
    """Running km in the 48 h before each lifting session (0 when there was none)."""
    if not perf:
        return []
    window = timedelta(hours=RUN_LOAD_HOURS)
    first = min(st for _, st in perf.values()) - window
    last = datetime.combine(max(perf) + timedelta(days=1), time.min)
    runs = db.execute(
        select(Run.started_at_local, Run.distance_m).where(
            Run.deleted_at.is_(None),
            Run.activity_type.in_(RUN_TYPES),
            Run.started_at_local >= first,
            Run.started_at_local < last,
        )
    ).all()
    points = []
    for day in sorted(perf):
        y, session_start = perf[day]
        km = sum((m or 0.0) / 1000 for t, m in runs if session_start - window <= t < session_start)
        points.append({"date": day.isoformat(), "x": num(km), "y": num(y, 1)})
    return points


def _health_vs_easy_efficiency(
    ctx: ar.Ctx, health: dict[date, DailyHealth], attr: str, start: date, nd: int
) -> list:
    points = []
    for r in ctx.runs:
        if r.day < start or ar.classify(r, ctx) != "easy":
            continue
        h = health.get(r.day)
        v = getattr(h, attr) if h is not None else None
        eff = R.efficiency(r.distance_m, r.duration_s, r.avg_hr)
        if v and eff:
            points.append({"date": r.day.isoformat(), "x": num(v, nd), "y": num(eff, 4)})
    return points


LIFT = ("better lifting days", "weaker lifting days")
EASY = ("more efficient easy runs", "less efficient easy runs")


def correlations(db: Session, days: int) -> dict:
    end = common.today()
    start = end - timedelta(days=days - 1)
    health = {
        h.day: h
        for h in db.scalars(
            select(DailyHealth).where(
                DailyHealth.source == "garmin", DailyHealth.day >= start, DailyHealth.day <= end
            )
        )
    }
    perf = _lift_performance(db, start, end)
    ctx = ar.load_ctx(db, start, end)
    y_lift = "Best e1RM as % of the 8-week best"
    lift_note = "Each lift is compared with its own best of the previous 8 weeks, averaged per day."
    easy_note = "Easy runs only; efficiency is speed per heartbeat, so pace differences cancel out."
    items = [
        _item(
            "sleep_vs_lifting",
            "recovery",
            Wording("sleep and lifting performance", "more sleep", *LIFT),
            "Sleep vs lifting performance",
            "Sleep the night before (h)",
            y_lift,
            _health_vs_lifting(perf, health, "sleep_s", 3600),
            lift_note,
        ),
        _item(
            "hrv_vs_lift_performance",
            "recovery",
            Wording("morning HRV and lifting performance", "higher HRV", *LIFT),
            "HRV vs lifting performance",
            "Overnight HRV (ms)",
            y_lift,
            _health_vs_lifting(perf, health, "hrv_overnight_ms", 1),
            lift_note,
        ),
        _item(
            "hrv_vs_easy_efficiency",
            "recovery",
            Wording("HRV and easy-run efficiency", "higher HRV", *EASY),
            "HRV vs easy-run efficiency",
            "Overnight HRV (ms)",
            "Efficiency (m/min per bpm)",
            _health_vs_easy_efficiency(ctx, health, "hrv_overnight_ms", start, 1),
            easy_note,
        ),
        _item(
            "rhr_vs_easy_efficiency",
            "recovery",
            Wording("resting HR and easy-run efficiency", "a higher resting HR", *EASY),
            "Resting HR vs easy-run efficiency",
            "Resting HR (bpm)",
            "Efficiency (m/min per bpm)",
            _health_vs_easy_efficiency(ctx, health, "resting_hr", start, 1),
            easy_note,
        ),
        _item(
            "run_load_vs_lift_performance",
            "hybrid",
            Wording(
                "recent running and lifting performance", "more running in the 48 h before", *LIFT
            ),
            "Running load vs lifting performance",
            "Running in the 48 h before the session (km)",
            y_lift,
            _run_load_vs_lifting(db, perf),
            "Interference effect: runs that started up to 48 h before the first lift of the day. "
            + lift_note,
        ),
    ]
    return {"generated_at": common.generated_at(), "items": items}
