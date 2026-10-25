"""Tiny in-process cache for the stats endpoints.

An entry is valid while the database fingerprint is unchanged, so correctness never depends on a
hook being called: every table the stats read from is covered (see `_FINGERPRINT_SQL`).
`invalidate()` is additionally called from the explicit "new data" paths (Garmin `on_new_data`,
sync push) so the next request recomputes immediately. One process, SQLite: a dict + lock is enough.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.analytics import common

# One round trip. `sync_seq` moves on every sessions / sets / body_measurements / planned_sessions
# / gyms change (SQLite triggers); the rest have no change sequence, so use counts + max stamps.
_FINGERPRINT_SQL = text(
    """
    SELECT
      (SELECT max(seq) FROM sync_seq),
      (SELECT count(*) || '/' || coalesce(max(updated_at), '') FROM runs),
      (SELECT count(*) || '/' || coalesce(max(updated_at), '') FROM laps),
      (SELECT count(*) || '/' || coalesce(max(updated_at), '') FROM daily_health),
      (SELECT count(*) || '/' || coalesce(max(updated_at), '') FROM athlete_metrics),
      (SELECT count(*) || '/' || coalesce(max(computed_at), '') FROM exercise_session_stats),
      (SELECT count(*) || '/' || coalesce(max(updated_at), '') FROM exercise_variants),
      (SELECT coalesce(max(id), 0) FROM config_versions WHERE is_active),
      (SELECT coalesce(max(id), 0) FROM decisions_log),
      (SELECT count(*) || '/' || coalesce(sum(status = 'open'), 0) || '/' || coalesce(max(id), 0)
         FROM advisor_triggers WHERE type = 'lift_stall')
    """
)

_MAX_ENTRIES = 64
_lock = threading.Lock()
_store: dict[tuple, tuple[tuple, Any]] = {}


def fingerprint(db: Session) -> tuple:
    return tuple(db.execute(_FINGERPRINT_SQL).one())


def invalidate() -> None:
    with _lock:
        _store.clear()


def cached(db: Session, name: str, params: tuple, compute: Callable[[], Any]) -> Any:
    """`compute()` once per (database, endpoint, params, day, data fingerprint)."""
    key = (str(db.get_bind().url), name, params)
    fp = (fingerprint(db), common.today().isoformat())
    with _lock:
        hit = _store.get(key)
        if hit is not None and hit[0] == fp:
            return hit[1]
    value = compute()
    with _lock:
        if len(_store) >= _MAX_ENTRIES:
            _store.pop(next(iter(_store)))
        _store[key] = (fp, value)
    return value
