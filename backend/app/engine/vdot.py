"""VDOT as persistent state with hysteresis (docs/research/running-progression.md, scenario 8).

The pace model's fit (seed race raised by the best hard effort) only ANCHORS the first record.
After that the VDOT moves only through records in decisions_log (`output.vdot_record`):

* up: a time trial / race effort better than the current VDOT, or `green_sessions` quality
  sessions passed easily within `green_window_weeks` together with a rising aerobic-efficiency
  trend, or the pace lever of the quality session (its own green sessions). Each update is at
  most `max_step`, and all increases within 28 days at most `max_per_4w`. One update per week.
* down: never after one bad session; only by the layoff table (once per break) or
  `fails_to_lower` genuine fails within 4 weeks (`fail_drop`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.engine.config import VdotConfig


@dataclass(frozen=True)
class VdotRecord:
    day: date
    vdot: float
    source: str  # initial | effort | green_sessions | interval_pace | layoff | fails
    delta: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "vdot": round(self.vdot, 2),
            "source": self.source,
            "delta": round(self.delta, 2),
        }


@dataclass
class VdotUpdate:
    vdot: float
    record: VdotRecord | None  # a NEW record to persist (None: unchanged)
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def update_vdot(
    log: list[VdotRecord],
    fitted: float,
    cfg: VdotConfig,
    *,
    today: date,
    efforts: Sequence[tuple[date, float]] = (),
    greens: Sequence[date] = (),
    ef_trend_pct: float | None = None,
    pace_lever: bool = False,
    fails: Sequence[date] = (),
    last_run_day: date | None = None,
) -> VdotUpdate:
    log = sorted(log, key=lambda r: r.day)
    if not log:
        rec = VdotRecord(today, round(fitted, 2), "initial")
        return VdotUpdate(rec.vdot, rec, f"initial VDOT {fitted:.1f} from the pace model's fit")
    cur = log[-1].vdot
    ev: dict[str, Any] = {"current": round(cur, 2), "last_record": log[-1].as_dict()}
    if any(r.day >= _monday(today) and r.source != "initial" for r in log):
        return VdotUpdate(cur, None, "one VDOT update per week: already updated this week", ev)

    # layoff (once per break)
    if last_run_day is not None:
        gap = (today - last_run_day).days
        tier = None
        for t in sorted(cfg.layoff, key=lambda t: t.min_days):
            if gap >= t.min_days:
                tier = t
        done = any(r.source == "layoff" and r.day > last_run_day for r in log)
        if tier is not None and not done:
            new = round(cur * (1 - tier.pct / 100), 2)
            rec = VdotRecord(today, new, "layoff", new - cur)
            return VdotUpdate(
                new,
                rec,
                f"{gap} days without a run: VDOT -{tier.pct:g}% ({cur:.1f} -> {new:.1f})",
                {**ev, "break_days": gap},
            )

    month = today - timedelta(days=28)
    recent_fails = [d for d in fails if d >= month]
    if len(recent_fails) >= cfg.fails_to_lower and not any(
        r.source == "fails" and r.day >= month for r in log
    ):
        new = round(cur - cfg.fail_drop, 2)
        rec = VdotRecord(today, new, "fails", new - cur)
        return VdotUpdate(
            new,
            rec,
            f"{len(recent_fails)} quality sessions missed the pace within 4 weeks: VDOT "
            f"-{cfg.fail_drop:g}",
            {**ev, "fails": [d.isoformat() for d in recent_fails]},
        )

    last_up = max((r.day for r in log if r.delta > 0), default=log[0].day)
    used = sum(r.delta for r in log if r.delta > 0 and r.day >= month and r.source != "initial")
    room = round(cfg.max_per_4w - used, 3)
    new_efforts = [(d, v) for d, v in efforts if d > log[-1].day and v > cur + 1e-6]
    window = today - timedelta(weeks=cfg.green_window_weeks)
    fresh_greens = [d for d in greens if d > last_up and d >= window]
    candidate = None
    source = ""
    why = ""
    if new_efforts:
        d, v = max(new_efforts, key=lambda x: x[1])
        candidate, source = v, "effort"
        why = f"hard effort on {d} worth VDOT {v:.1f}"
    elif len(fresh_greens) >= cfg.green_sessions and (
        pace_lever or (ef_trend_pct is not None and ef_trend_pct >= cfg.ef_trend_min_pct)
    ):
        candidate = cur + cfg.max_step
        source = "interval_pace" if pace_lever else "green_sessions"
        why = (
            f"{len(fresh_greens)} quality sessions passed easily"
            + (" with every lever at its maximum" if pace_lever else "")
            + (f", aerobic efficiency {ef_trend_pct:+.1f}%" if ef_trend_pct is not None else "")
        )
    ev.update(
        {
            "efforts": [[d.isoformat(), round(v, 2)] for d, v in new_efforts],
            "greens": [d.isoformat() for d in fresh_greens],
            "ef_trend_pct": ef_trend_pct,
            "room_4w": room,
        }
    )
    if candidate is None:
        return VdotUpdate(cur, None, f"VDOT {cur:.1f} unchanged: no new evidence", ev)
    step = min(candidate - cur, cfg.max_step, max(room, 0.0))
    if step < 0.05:
        return VdotUpdate(
            cur,
            None,
            f"VDOT {cur:.1f} unchanged: {why}, but +{cfg.max_per_4w:g} in 4 weeks is used up",
            ev,
        )
    new = round(cur + step, 2)
    rec = VdotRecord(today, new, source, new - cur)
    return VdotUpdate(new, rec, f"VDOT {cur:.1f} -> {new:.1f}: {why}", ev)
