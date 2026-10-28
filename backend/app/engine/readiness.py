"""Daily readiness (green / yellow / red) from Garmin daily_health. Pure functions.

Signals (each green / yellow / red, thresholds in config `readiness`):
  hrv    overnight HRV vs the mean of the previous 7 days (>= 3 values needed; otherwise Garmin's
         own balanced range), combined with Garmin's hrv_status: the worse of the two counts
  rhr    resting HR vs the mean of the previous 7 days
  sleep  the worse of sleep duration and sleep score

Colour: any red signal -> red (a red *sleep* signal alone, with HRV and resting HR normal, only
gives yellow: one short night is not a recovery problem); otherwise `yellow_signals_needed`
(2) yellow signals -> yellow; otherwise green. Missing signals are skipped; no data at all ->
"unknown", treated as green with the reason "no data".
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import fmean
from typing import Any

from app.engine.config import ReadinessConfig

GREEN, YELLOW, RED, UNKNOWN = "green", "yellow", "red", "unknown"
_RANK = {GREEN: 0, YELLOW: 1, RED: 2}


@dataclass(frozen=True)
class HealthDay:
    day: date
    resting_hr: float | None = None
    hrv_ms: float | None = None
    hrv_status: str | None = None
    sleep_s: float | None = None
    sleep_score: float | None = None
    hrv_baseline_low: float | None = None
    hrv_baseline_high: float | None = None


@dataclass
class Signal:
    name: str
    level: str
    detail: str
    value: float | None = None
    baseline: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "level": self.level,
            "detail": self.detail,
            "value": self.value,
            "baseline": self.baseline,
        }


@dataclass
class Readiness:
    day: date
    color: str  # green | yellow | red (unknown is reported as green with known=False)
    known: bool
    reason: str
    signals: list[Signal] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.day.isoformat(),
            "color": self.color,
            "known": self.known,
            "reason": self.reason,
            "signals": [s.as_dict() for s in self.signals],
        }


def _baseline(
    by_day: dict[date, HealthDay], day: date, attr: str, cfg: ReadinessConfig
) -> float | None:
    vals = [
        getattr(by_day[d], attr)
        for i in range(1, cfg.baseline_days + 1)
        if (d := day - timedelta(days=i)) in by_day and getattr(by_day[d], attr) is not None
    ]
    return fmean(vals) if len(vals) >= cfg.min_baseline_days else None


def _signed(x: float) -> str:
    """'+2' / '-3' / '±0' (never '-0')."""
    n = round(x)
    return f"{n:+d}" if n else "±0"


def _worse(a: str, b: str) -> str:
    return a if _RANK[a] >= _RANK[b] else b


def hrv_pct_vs_baseline(
    by_day: dict[date, HealthDay], day: date, cfg: ReadinessConfig
) -> float | None:
    h = by_day.get(day)
    if h is None or h.hrv_ms is None:
        return None
    base = _baseline(by_day, day, "hrv_ms", cfg)
    return None if not base else (h.hrv_ms - base) / base * 100


def score_day(day: date, history: Iterable[HealthDay], cfg: ReadinessConfig) -> Readiness:
    """Readiness for `day` from its own daily_health row and the days before it."""
    by_day = {h.day: h for h in history}
    today = by_day.get(day)
    signals: list[Signal] = []
    if today is not None:
        # ---- HRV
        level, detail, base = GREEN, None, None
        if today.hrv_ms is not None:
            base = _baseline(by_day, day, "hrv_ms", cfg)
            if base:
                pct = (today.hrv_ms - base) / base * 100
                detail = f"HRV {today.hrv_ms:.0f} ms ({_signed(pct)}% vs {base:.0f} baseline)"
                if pct <= cfg.hrv.red_pct:
                    level = RED
                elif pct <= cfg.hrv.yellow_pct:
                    level = YELLOW
            elif today.hrv_baseline_low is not None:
                detail = f"HRV {today.hrv_ms:.0f} ms (Garmin range {today.hrv_baseline_low:.0f}+)"
                if today.hrv_ms < today.hrv_baseline_low:
                    level = YELLOW
            else:
                detail = f"HRV {today.hrv_ms:.0f} ms (no baseline)"
        status = (today.hrv_status or "").upper()
        g_level = cfg.hrv.garmin_status.get(status, GREEN) if status else GREEN
        if g_level != GREEN:
            detail = f"{detail + '; ' if detail else ''}Garmin HRV status {status.lower()}"
            level = _worse(level, g_level)
        if detail:
            signals.append(Signal("hrv", level, detail, today.hrv_ms, base))
        # ---- resting HR
        if today.resting_hr is not None:
            base = _baseline(by_day, day, "resting_hr", cfg)
            if base is not None:
                diff = today.resting_hr - base
                level = (
                    RED
                    if diff >= cfg.rhr.red_bpm
                    else YELLOW
                    if diff >= cfg.rhr.yellow_bpm
                    else GREEN
                )
                signals.append(
                    Signal(
                        "rhr",
                        level,
                        f"resting HR {today.resting_hr:.0f} ({_signed(diff)} vs {base:.0f})",
                        today.resting_hr,
                        base,
                    )
                )
            else:
                signals.append(
                    Signal(
                        "rhr",
                        GREEN,
                        f"resting HR {today.resting_hr:.0f} (no baseline)",
                        today.resting_hr,
                    )
                )
        # ---- sleep
        parts: list[str] = []
        level = GREEN
        hours = today.sleep_s / 3600 if today.sleep_s is not None else None
        if hours is not None:
            parts.append(f"{hours:.1f} h")
            if hours < cfg.sleep.red_h:
                level = RED
            elif hours < cfg.sleep.yellow_h:
                level = YELLOW
        if today.sleep_score is not None:
            parts.append(f"score {today.sleep_score:.0f}")
            if today.sleep_score < cfg.sleep.red_score:
                level = _worse(level, RED)
            elif today.sleep_score < cfg.sleep.yellow_score:
                level = _worse(level, YELLOW)
        if parts:
            signals.append(Signal("sleep", level, "sleep " + ", ".join(parts), hours))

    if not signals:
        return Readiness(day, GREEN, False, "no data", [])

    reds = [s for s in signals if s.level == RED]
    yellows = [s for s in signals if s.level == YELLOW]
    others_flagged = [s for s in signals if s.level != GREEN and s.name != "sleep"]
    if reds and not (
        cfg.sleep_red_needs_corroboration
        and all(s.name == "sleep" for s in reds)
        and not others_flagged
    ):
        color = RED
    elif len(yellows) + len(reds) >= cfg.yellow_signals_needed or reds:
        color = YELLOW
    else:
        color = GREEN

    flagged = [s for s in signals if s.level != GREEN]
    if flagged:
        reason = "; ".join(s.detail for s in flagged)
        if color == GREEN:
            reason = f"1 minor flag only: {reason}"
        elif reds and color == YELLOW:
            reason += " (short night alone: yellow)"
    else:
        reason = "all signals normal: " + ", ".join(s.detail for s in signals)
    return Readiness(day, color, True, reason, signals)


def hrv_drop_anomaly(
    history: Sequence[HealthDay], end: date, cfg: ReadinessConfig
) -> dict[str, Any] | None:
    """HRV at least `pct` below its own baseline on each of the last `days` days ending `end`."""
    by_day = {h.day: h for h in history}
    need = cfg.hrv_drop_anomaly
    pcts: list[float] = []
    for i in range(need.days):
        p = hrv_pct_vs_baseline(by_day, end - timedelta(days=i), cfg)
        if p is None or p > need.pct:
            return None
        pcts.append(round(p, 1))
    return {"days": need.days, "end": end.isoformat(), "pct_vs_baseline": pcts[::-1]}


@dataclass(frozen=True)
class DeloadStatus:
    active: bool
    trigger_day: date | None = None  # day the red streak reached the required length
    until: date | None = None  # last day of the deload window
    streak: int = 0


def red_streak(colors: dict[date, str], end: date) -> int:
    n = 0
    d = end
    while colors.get(d) == RED:
        n += 1
        d -= timedelta(days=1)
    return n


def deload_status(colors: dict[date, str], day: date, today: date, red_days: int) -> DeloadStatus:
    """Deload applies on `day` if, within the 7 days up to `day` (and not after `today`), the red
    streak reached `red_days` consecutive days. The trigger day is when it first did."""
    for back in range(7):
        t = day - timedelta(days=back)
        if t > today:
            continue
        if red_streak(colors, t) >= red_days:
            s = red_streak(colors, t)
            first = t - timedelta(days=s - red_days)  # the day the streak reached `red_days`
            return DeloadStatus(True, first, first + timedelta(days=6), s)
    return DeloadStatus(False, None, None, red_streak(colors, min(day, today)))
