"""How every Garmin caller waits: one `Sleeper` type (tests pass a recorder instead of
`time.sleep`) and the `Pacer`s that space requests out.

* `FixedPause`: the same pause before every request (python-garminconnect, workout push).
* `MinInterval`: at least `interval_s` between request starts, sleeping only the remainder
  (personal API client).

Retry / rate-limit backoffs are not pacing: callers sleep those through `Pacer.sleep` or a plain
`Sleeper`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

Sleeper = Callable[[float], None]
Clock = Callable[[], float]


class Pacer(Protocol):
    sleep: Sleeper

    def wait(self) -> None:
        """Block until the next request may go out (and count it as sent now)."""


class FixedPause:
    def __init__(self, interval_s: float, sleep: Sleeper = time.sleep) -> None:
        self.interval_s = interval_s
        self.sleep = sleep

    def wait(self) -> None:
        self.sleep(self.interval_s)


class MinInterval:
    def __init__(
        self, interval_s: float, sleep: Sleeper = time.sleep, clock: Clock = time.monotonic
    ) -> None:
        self.interval_s = interval_s
        self.sleep = sleep
        self.clock = clock
        self._last = 0.0  # clock() of the previous request start

    def wait(self) -> None:
        remaining = self._last + self.interval_s - self.clock()
        if remaining > 0:
            self.sleep(remaining)
        self._last = self.clock()
