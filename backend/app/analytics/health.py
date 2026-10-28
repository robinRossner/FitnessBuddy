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
from app.engine import service
from app.engine.readiness import GREEN, RED, YELLOW, Readiness, score_day
from app.settings import get_settings

BASELINE_LOOKBACK_DAYS = 45  # same history the engine loads for a readiness score
WEIGHT_CHANGE_WINDOW_DAYS = 28
WEIGHT_CHANGE_TOLERANCE_DAYS = 14  # the reference weigh-in may be this far from "4 weeks ago"
STEPS_GOAL = 10_000
SLEEP_BASELINE_DAYS = 7  # same window as the HRV / RHR baselines in the engine
SLEEP_BASELINE_MIN_DAYS = 3

HEADLINE = {GREEN: "Ready", YELLOW: "Take it easier", RED: "Recover", "unknown": "No data"}
STATUS = {GREEN: "ok", YELLOW: "warn", RED: "bad"}


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


def _days_over(rows: dict[date, DailyHealth], attr: str, goal: float, today: date, days: int):
    """Days in the last `days` with attr >= goal; None when none of them has a value."""
    vals = [
        v
        for i in range(days)
        if (row := rows.get(today - timedelta(days=i))) is not None
        and (v := getattr(row, attr)) is not None
    ]
    return sum(v >= goal for v in vals) if vals else None


def _sleep_baseline_h(rows: dict[date, DailyHealth], day: date) -> float | None:
    """Mean sleep (h) of the 7 nights before `day` (needs 3), like the engine's HRV baseline."""
    vals = [
        row.sleep_s / 3600
        for i in range(1, SLEEP_BASELINE_DAYS + 1)
        if (row := rows.get(day - timedelta(days=i))) is not None and row.sleep_s
    ]
    return mean(vals) if len(vals) >= SLEEP_BASELINE_MIN_DAYS else None


FACTORS = (("hrv", "HRV"), ("rhr", "Resting HR"), ("sleep", "Sleep"))  # every factor the score uses
_EFFECT = {GREEN: "neutral", YELLOW: "pulls it down a bit", RED: "pulls it down"}
_SEVERITY = {RED: 0, YELLOW: 1, GREEN: 2}  # decisive factor first; factors without data last


def _details(r: Readiness, sleep_baseline_h: float | None = None) -> list[str]:
    """The Why lines: one per factor the score uses (HRV, resting HR, sleep), with its value vs the
    personal baseline and what it did to the score; red first, then yellow, neutral, no data.
    Leading / trailing lines carry the engine's overall note."""
    if not r.known:
        return ["No data"]
    signals = {s.name: s for s in r.signals}
    rows: list[tuple[int, str]] = []
    for key, label in FACTORS:
        sig = signals.get(key)
        if sig is None:
            rows.append((3, f"{label}: no data"))
            continue
        text = sig.detail[:1].upper() + sig.detail[1:]
        if key == "sleep" and sig.value is not None and sleep_baseline_h:
            text += f" (usual {sleep_baseline_h:.1f} h)"
        rows.append((_SEVERITY[sig.level], f"{text} - {_EFFECT[sig.level]}"))
    rows.sort(key=lambda row: row[0])  # stable: ties keep the hrv, rhr, sleep order
    lines = [text for _, text in rows]
    flagged = [s for s in r.signals if s.level != GREEN]
    if not flagged:
        lines.insert(0, "All signals normal")
    elif r.color == GREEN:
        lines.append("Only a minor flag, so no change")
    elif r.color == YELLOW and any(s.level == RED for s in flagged):
        lines.append("Short night alone: yellow")
    return lines


METRICS = (("hrv", "HRV", "ms"), ("rhr", "Resting HR", "bpm"), ("sleep", "Sleep", "h"))


def readiness_view(r: Readiness, sleep_baseline_h: float | None) -> dict:
    """Compact readiness for the Today / Health screens (`readiness_today`).

    `metrics` has one entry per signal the engine scored (hrv, rhr, sleep), in that order;
    `details` lists every factor, including those without data.
    """
    signals = {s.name: s for s in r.signals}
    metrics = []
    for key, label, unit in METRICS:
        sig = signals.get(key)
        if sig is None or sig.value is None:
            continue
        base = sig.baseline if key != "sleep" else sleep_baseline_h
        delta = (sig.value - base) / base * 100 if base else None
        metrics.append(
            {
                "key": key,
                "label": label,
                "value": num(sig.value, 1),
                "unit": unit,
                "baseline": num(base, 1),
                "delta_pct": num(delta, 1),
                "status": STATUS[sig.level],
            }
        )
    level = r.color if r.known else "unknown"
    return {
        "level": level,
        "headline": HEADLINE[level],
        "metrics": metrics,
        "details": _details(r, sleep_baseline_h),
    }


def readiness_today(db: Session, day: date, r: Readiness | None = None) -> dict:
    """`readiness_view` for `day` (used by GET /plan/today, which passes the score it has)."""
    r = r or service.readiness_for(db, day)
    rows = {
        h.day: h
        for h in db.scalars(
            select(DailyHealth).where(
                DailyHealth.source == "garmin",
                DailyHealth.day >= day - timedelta(days=SLEEP_BASELINE_DAYS),
                DailyHealth.day < day,
            )
        )
    }
    return readiness_view(r, _sleep_baseline_h(rows, day))


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
                DailyHealth.day >= min(today - timedelta(days=27), start - timedelta(days=6)),
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
                "steps_7d_avg": num(_window_mean(rows, "steps", d, 7), 0),
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
        "steps_7d_avg": avg("steps", 7, 0),
        "days_10k_last_7": _days_over(rows, "steps", STEPS_GOAL, today, 7),
        "bodyweight_latest": num(pts[-1][1]) if pts else None,
        "bodyweight_change_4w_pct": num(bodyweight_change_pct(pts)),
    }
    return {
        "generated_at": common.generated_at(),
        "days": out_days,
        "bodyweight": bodyweight,
        "summary": summary,
        "readiness_today": readiness_view(ready[today], _sleep_baseline_h(rows, today)),
    }
