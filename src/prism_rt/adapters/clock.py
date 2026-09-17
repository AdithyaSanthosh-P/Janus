"""ClockPort: the only permitted source of time inside the kernel.

`docs/prompt 2.txt` §13 bans wall-clock reads from kernel/store/tool code;
every timestamp comes from here. V0 implements Model B only (stepped/harness
time) — see `docs/sonnet_implementation_plan.md` §1.2 ("Multiple clock
models — CUT: build Model B only").
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ClockPort(ABC):
    @abstractmethod
    def now_us(self) -> int: ...

    @abstractmethod
    def advance_to(self, ts_us: int) -> None: ...

    @abstractmethod
    def busy(self, flag: bool) -> None: ...


class SteppedClock(ClockPort):
    """Model B: time only moves when explicitly advanced — no wall waiting,
    no timer fires while virtual time stands still."""

    def __init__(self, start_us: int = 0) -> None:
        self._now_us = start_us
        self._busy = False

    def now_us(self) -> int:
        return self._now_us

    def advance_to(self, ts_us: int) -> None:
        if ts_us < self._now_us:
            raise ValueError(f"clock cannot move backward: {ts_us} < {self._now_us}")
        self._now_us = ts_us

    def busy(self, flag: bool) -> None:
        self._busy = bool(flag)

    @property
    def is_busy(self) -> bool:
        return self._busy
