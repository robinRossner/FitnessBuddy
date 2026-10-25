"""`GET /stats/health`: daily Garmin health + engine readiness, bodyweight and rolling summaries."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import common
from app.analytics.common import mean, num
from app.db.models import BodyMeasurement, DailyHealth
from app.engine import config_service as cs
from app.engine import data as D
from app.engine.readiness import Readiness, score_day
from app.settings import get_settings

BASELINE_LOOKBACK_DAYS = 45  # same history the engine loads for a readiness score
WEIGHT_CHANGE_WINDOW_DAYS = 28
WEIGHT_CHANGE_TOLERANCE_DAYS = 14  # the reference weigh-in may be this far from "4 weeks ago"


def readiness_by_day(db: Session, start: date, end: date) -> dict[date, Readiness]:
    """Engine readiness for every day in [start, end] (history from 45 days before `start`)."""
    cs.ensure_seeded(db, get_settings().engine_config_path)
    cfg, _ = cs.load_active(db)
    health = D.load_health(db, start - timedelta(days=BASELINE_LOOKBACK_DAYS), end)
    out = {}
    d = start
    while d <= end:
        out[d] = score_day(d, health, cfg.readiness_cfg)
        d += timedelta(days=1)
    return out


def _signal(r: Readiness, name: str):
    return next((s for s in r.signals if s.name == name), None)


def _window_mean(rows: dict[date, DailyHealth], attr: str, today: date, days: int) -> float | None:
    vals = [
        v
        for i in range(days)
        if (row := rows.get(today - timedelta(days=i))) is not None
        and (v := getattr(row, attr)) is not None
    ]
    return mean(vals)


def bodyweight_change_pct(points: list[tuple[date, float]]) -> float | None:
    """Latest weigh-in vs the one nearest to 28 days before it (within +-14 days); else None.

    `points`: (day, kg), oldest first.
    """
    if len(points) < 2:
        return None
    latest_day, latest_kg = points[-1]
    target = latest_day - timedelta(days=WEIGHT_CHANGE_WINDOW_DAYS)
    older = [p for p in points[:-1] if p[0] < latest_day]
    if not older:
        return None
    day, kg = min(older, key=lambda p: (abs((p[0] - target).days), p[0]))
    if abs((day - target).days) > WEIGHT_CHANGE_TOLERANCE_DAYS or not kg:
        return None
    return (latest_kg - kg) / kg * 100


def health_stats(db: Session, days: int) -> dict:
    today = common.today()
    start = today - timedelta(days=days - 1)
    ready = readiness_by_day(db, start, today)
    rows = {
        r.day: r
        for r in db.scalars(
            select(DailyHealth).where(
                DailyHealth.source == "garmin",
                DailyHealth.day >= today - timedelta(days=27),
            )
        )
    }
    rows.update(
        {
            r.day: r
            for r in db.scalars(
                select(DailyHealth).where(
                    DailyHealth.source == "garmin",
                    DailyHealth.day >= start,
                    DailyHealth.day <= today,
                )
            )
        }
    )
    out_days = []
    d = start
    while d <= today:
        h, r = rows.get(d), ready[d]
        hrv_sig, rhr_sig = _signal(r, "hrv"), _signal(r, "rhr")
        hrv_base = hrv_sig.baseline if hrv_sig and hrv_sig.baseline else None
        if hrv_base is None and h is not None:
            hrv_base = h.hrv_weekly_avg_ms
        out_days.append(
            {
                "date": d.isoformat(),
                "hrv_ms": num(h.hrv_overnight_ms, 1) if h else None,
                "hrv_baseline_ms": num(hrv_base, 1),
                "rhr": num(h.resting_hr, 1) if h else None,
                "rhr_baseline": num(rhr_sig.baseline, 1) if rhr_sig else None,
                "sleep_s": h.sleep_s if h else None,
                "sleep_score": num(h.sleep_score, 0) if h else None,
                "body_battery_high": num(h.body_battery_max, 0) if h else None,
                "body_battery_low": num(h.body_battery_min, 0) if h else None,
                "stress_avg": num(h.stress_avg, 0) if h else None,
                "steps": h.steps if h else None,
                "readiness": {
                    "level": r.color if r.known else "unknown",
                    "reason": r.reason,
                },
            }
        )
        d += timedelta(days=1)

    measurements = list(
        db.execute(
            select(BodyMeasurement.day, BodyMeasurement.weight_kg, BodyMeasurement.source)
            .where(BodyMeasurement.deleted_at.is_(None))
            .order_by(BodyMeasurement.measured_at)
        )
    )
    bodyweight = [
        {
            "date": day.isoformat(),
            "weight_kg": num(kg),
            "source": str(src.value if hasattr(src, "value") else src),
        }
        for day, kg, src in measurements
        if day >= start
    ]
    pts = [(day, kg) for day, kg, _ in measurements]

    def avg(attr: str, n: int, nd: int = 1) -> float | None:
        return num(_window_mean(rows, attr, today, n), nd)

    summary = {
        "hrv_7d": avg("hrv_overnight_ms", 7),
        "hrv_28d": avg("hrv_overnight_ms", 28),
        "rhr_7d": avg("resting_hr", 7),
        "rhr_28d": avg("resting_hr", 28),
        "sleep_7d_s": avg("sleep_s", 7, 0),
        "sleep_28d_s": avg("sleep_s", 28, 0),
        "bodyweight_latest": num(pts[-1][1]) if pts else None,
        "bodyweight_change_4w_pct": num(bodyweight_change_pct(pts)),
    }
    return {
        "generated_at": common.generated_at(),
        "days": out_days,
        "bodyweight": bodyweight,
        "summary": summary,
    }
