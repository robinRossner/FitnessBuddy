"""Guard for the manual Garmin sync (`POST /garmin/sync`): cooldown, daily cap, Garmin 429 block.

In memory and thread safe; a restart resets it (the server is a single uvicorn worker). The day
rolls over at local midnight of `USER_TZ`. A decision is made atomically with the reservation, so
two simultaneous taps cannot both reach Garmin.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

UNKNOWN_BLOCK_S = 15 * 60  # Garmin 429 without a usable Retry-After


@dataclass(frozen=True)
class Verdict:
    """`go` = the caller may contact Garmin; else why not (`blocked` = Garmin said 429)."""

    status: str  # "go" | "cooldown" | "limit_reached" | "blocked"
    retry_after_s: int | None = None
    count: int = 0  # attempts so far today, this one included when status is "go"
    _prev_last: float | None = None  # state before this reservation (for `refund`)
    _day: date | None = None


def seconds_to_next_midnight(tz: str, now: datetime | None = None) -> int:
    zone = ZoneInfo(tz)
    now = (now or datetime.now(zone)).astimezone(zone)
    nxt = datetime.combine(now.date() + timedelta(days=1), dtime.min, tzinfo=zone)
    return max(1, math.ceil((nxt.astimezone(UTC) - now.astimezone(UTC)).total_seconds()))


class ManualSyncGuard:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._day: date | None = None
        self._count = 0
        self._last: float | None = None  # monotonic time of the last attempt
        self._blocked_until: float | None = None

    def reset(self) -> None:
        """Forget everything (restart; tests)."""
        with self._lock:
            self._day, self._count, self._last, self._blocked_until = None, 0, None, None

    def _roll(self, today: date) -> None:
        if self._day != today:
            self._day, self._count = today, 0

    def count(self, today: date) -> int:
        with self._lock:
            return self._count if self._day == today else 0

    def reserve(self, today: date, tz: str, cooldown_s: int, daily_max: int) -> Verdict:
        """Decide and, on `go`, count the attempt and start the cooldown."""
        with self._lock:
            now = self._clock()
            self._roll(today)
            if self._blocked_until is not None and now < self._blocked_until:
                return Verdict("blocked", math.ceil(self._blocked_until - now), self._count)
            if self._count >= daily_max:
                return Verdict("limit_reached", seconds_to_next_midnight(tz), self._count)
            if self._last is not None and now - self._last < cooldown_s:
                return Verdict("cooldown", math.ceil(cooldown_s - (now - self._last)), self._count)
            prev, self._last = self._last, now
            self._count += 1
            return Verdict("go", None, self._count, prev, self._day)

    def refund(self, verdict: Verdict) -> None:
        """The attempt never reached Garmin (another sync was running): give it back."""
        with self._lock:
            if self._day == verdict._day and self._count > 0:
                self._count -= 1
            self._last = verdict._prev_last

    def block(self, seconds: float | None) -> None:
        """Garmin answered 429: no manual sync until its Retry-After (15 min if unknown)."""
        wait = seconds if seconds and seconds > 0 else UNKNOWN_BLOCK_S
        with self._lock:
            self._blocked_until = self._clock() + wait


manual_sync_guard = ManualSyncGuard()
