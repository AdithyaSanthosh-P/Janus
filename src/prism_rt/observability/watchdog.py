"""ScenarioWatchdog: the single permitted wall-clock decision (§13.5 of
`docs/prompt 2.txt`).

Exists only to force a truthful final response before the harness's
120-second hard cap if real-world wall time runs out — independent of
whatever virtual/harness time says. Every other timing decision in the
kernel goes through `ClockPort`; only this module and `sim/` are allowed
to read the real clock.
"""

from __future__ import annotations

import time
from typing import Callable


class ScenarioWatchdog:
    def __init__(self, budget_s: float, callback: Callable[[], None]) -> None:
        self._budget_s = budget_s
        self._callback = callback
        self._start: float | None = None
        self._fired = False

    def start(self) -> None:
        self._start = time.monotonic()
        self._fired = False

    def check(self) -> bool:
        """Returns True (and fires the callback, once) if the wall-clock
        budget has elapsed since `start()`. No-op if never started."""
        if self._start is None or self._fired:
            return self._fired
        if time.monotonic() - self._start >= self._budget_s:
            self._fired = True
            self._callback()
        return self._fired
