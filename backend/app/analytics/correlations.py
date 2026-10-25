"""`GET /stats/correlations`: three small, honest scatter plots with a Pearson r and a caveat.

All pairs are matched by calendar day. Garmin stores a night's sleep under the day you woke up,
so "that day's sleep" is the previous night.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import common
from app.analytics import running as ar
from app.analytics.common import num, pearson
from app.analytics.health import readiness_by_day
from app.db.models import DailyHealth, ExerciseSessionStats
from app.engine import running as R

ROLLING_BEST_DAYS = 56  # 8 weeks
READINESS_VALUE = {"green": 2, "yellow": 1, "red": 0}


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


def _item(id_, title, x_label, y_label, points, extra="") -> dict:
    r = pearson([p["x"] for p in points], [p["y"] for p in points])
    return {
        "id": id_,
        "title": title,
        "x_label": x_label,
        "y_label": y_label,
        "points": points,
        "r": num(r, 3),
        "n": len(points),
        "note": _note(len(points), r, extra),
    }


def _sleep_vs_lifting(db: Session, health: dict[date, DailyHealth], start: date, end: date) -> list:
    rows = db.execute(
        select(
            ExerciseSessionStats.variant_id,
            ExerciseSessionStats.session_date,
            ExerciseSessionStats.best_e1rm_kg,
        )
        .where(
            ExerciseSessionStats.best_e1rm_kg.is_not(None),
            ExerciseSessionStats.session_date >= start - timedelta(days=ROLLING_BEST_DAYS),
            ExerciseSessionStats.session_date <= end,
        )
        .order_by(ExerciseSessionStats.session_date)
    ).all()
    by_variant: dict[int, list[tuple[date, float]]] = {}
    for vid, day, e1rm in rows:
        by_variant.setdefault(vid, []).append((day, e1rm))
    per_day: dict[date, list[float]] = {}
    for hist in by_variant.values():
        for day, e1rm in hist:
            if day < start:
                continue
            prior = [v for d, v in hist if day - timedelta(days=ROLLING_BEST_DAYS) <= d < day]
            if prior:  # best of the 8 weeks before the session, so 100 = matched, >100 = a PR
                per_day.setdefault(day, []).append(e1rm / max(prior) * 100)
    points = []
    for day in sorted(per_day):
        h = health.get(day)
        if h is not None and h.sleep_s:
            points.append(
                {
                    "date": day.isoformat(),
                    "x": num(h.sleep_s / 3600),
                    "y": num(common.mean(per_day[day]), 1),
                }
            )
    return points


def _hrv_vs_easy_efficiency(
    db: Session, health: dict[date, DailyHealth], start: date, end: date
) -> list:
    ctx = ar.load_ctx(db, start, end)
    points = []
    for r in ctx.runs:
        if r.day < start or ar.classify(r, ctx) != "easy":
            continue
        h = health.get(r.day)
        eff = R.efficiency(r.distance_m, r.duration_s, r.avg_hr)
        if h is not None and h.hrv_overnight_ms and eff:
            points.append(
                {"date": r.day.isoformat(), "x": num(h.hrv_overnight_ms, 1), "y": num(eff, 4)}
            )
    return points


def _sleep_score_vs_readiness(
    health: dict[date, DailyHealth], db: Session, start: date, end: date
) -> list:
    ready = readiness_by_day(db, start, end)
    points = []
    for day in sorted(health):
        h, r = health[day], ready.get(day)
        if r is not None and r.known and h.sleep_score is not None and day >= start:
            points.append(
                {"date": day.isoformat(), "x": num(h.sleep_score, 0), "y": READINESS_VALUE[r.color]}
            )
    return points


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
    items = [
        _item(
            "sleep_vs_lifting",
            "Sleep vs lifting performance",
            "Sleep the night before (h)",
            "Best e1RM as % of the 8-week best",
            _sleep_vs_lifting(db, health, start, end),
            "Each lift is compared with its own best of the previous 8 weeks, averaged per day.",
        ),
        _item(
            "hrv_vs_easy_efficiency",
            "HRV vs easy-run efficiency",
            "Overnight HRV (ms)",
            "Efficiency (m/min per bpm)",
            _hrv_vs_easy_efficiency(db, health, start, end),
            "Easy runs only; efficiency is speed per heartbeat, so pace differences cancel out.",
        ),
        _item(
            "sleep_score_vs_readiness",
            "Sleep score vs readiness",
            "Sleep score",
            "Readiness (0 red, 1 yellow, 2 green)",
            _sleep_score_vs_readiness(health, db, start, end),
            "Readiness already includes sleep, so some correlation is built in.",
        ),
    ]
    return {"generated_at": common.generated_at(), "items": items}
