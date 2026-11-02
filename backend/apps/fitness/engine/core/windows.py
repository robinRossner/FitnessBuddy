"""Deload windows as stored in the decision log, and the user's cancellation of a week's deload.
Pure: the loaders in `engine.store.data` read the rows and hand the dicts / weeks to these."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from apps.fitness.engine.core import lifting as L

DELOAD_CANCEL = "deload_cancel"  # decisions_log kind: the user cancelled the deload of a week
CANCELLABLE = ("multi_lift_stall", "fatigue_index", "legacy_reset")  # persisted deload windows


def window_from(d: dict) -> L.DeloadWindow:
    """A deload window from its stored dict (the `window` of a decisions_log row)."""
    return L.DeloadWindow(
        date.fromisoformat(d["start"]),
        date.fromisoformat(d["until"]),
        d.get("reason") or "multi_lift_stall",
        {
            int(k): L.Retest(
                float(v["load"]), tuple(int(x) for x in v.get("reps") or ()), v.get("best_e1rm")
            )
            for k, v in (d.get("retest") or {}).items()
        },
        bool(d.get("deload", True)),
        bool(d.get("cancelled", False)),
    )


def window_dict(w: L.DeloadWindow) -> dict[str, Any]:
    """The stored form of a window (inverse of `window_from`)."""
    return {
        "start": w.start.isoformat(),
        "until": w.until.isoformat(),
        "reason": w.reason,
        "deload": w.deload,
        "cancelled": w.cancelled,
        "retest": {
            str(k): {"load": r.load, "reps": list(r.reps), "best_e1rm": r.best_e1rm}
            for k, r in w.retest.items()
        },
    }


def cancel_window(w: L.DeloadWindow, week_start: date) -> L.DeloadWindow | None:
    """The window as it is after the user cancelled the deload of `week_start`: the soft deload
    is gone (this week and later; an earlier part that was already trained stays), the retest of
    the old loads starts right away. None when the window does not deload in that week."""
    end = week_start + timedelta(days=6)
    if not w.deload or w.reason not in CANCELLABLE or w.start > end or w.until < week_start:
        return None
    if w.start < week_start:  # started in an earlier week: keep that part, cancel from here on
        return replace(w, until=week_start - timedelta(days=1), cancelled=True)
    return replace(w, until=w.start - timedelta(days=1), deload=False, cancelled=True)


def apply_cancellations(
    windows: list[L.DeloadWindow], cancelled: dict[date, str | None]
) -> list[L.DeloadWindow]:
    out = []
    for w in windows:
        for ws in sorted(cancelled):
            w = cancel_window(w, ws) or w
        out.append(w)
    return out
