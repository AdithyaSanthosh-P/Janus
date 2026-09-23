"""ClockPort: the only permitted source of time inside the kernel.

`docs/prompt 2.txt` §13 bans wall-clock reads from kernel/store/tool code;
every timestamp comes from here. `docs/prompt 2.txt` §13.2 names three
clock models (A coupled, B stepped, C agent-stamped); V0 built Model B only
(`docs/sonnet_implementation_plan.md` §1.2, "CUT: build Model B only").

Post-V4 (`docs/original_design_audit.md` P0.1 / C9c): Model A (`CoupledClock`)
is now built too, for the real production driver (`entry.py`). Model B's
`SteppedClock` never advances on its own between explicit `advance_to`
calls -- correct for a deterministic simulator that scripts every timestamp,
wrong for a real driver, where a step-scheduled timer (e.g. the
settle-barrier wake, `kernel/commit.py`) must still get a chance to be
honoured even when the harness sends nothing more for a while (D1). Model A
fixes that by deriving `now_us()` from real elapsed wall time on top of the
last harness-provided timestamp -- "the only permitted derivation" per
§13.2 -- so `entry.py`'s idle loop can simply keep re-stepping the kernel
and have `now_us()` already reflect genuine elapsed time.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Callable


class ClockPort(ABC):
    #: Which of `docs/prompt 2.txt` §13.2's clock models this is ("A" or
    #: "B") -- `Config.clock_model` selects between them; nothing else in
    #: the kernel/store reads this, since no correctness decision may ever
    #: depend on which model is active (only `now_us()` and `advance_to`
    #: may).
    model: str

    @abstractmethod
    def now_us(self) -> int: ...

    @abstractmethod
    def advance_to(self, ts_us: int) -> None: ...

    @abstractmethod
    def busy(self, flag: bool) -> None: ...


class SteppedClock(ClockPort):
    """Model B: time only moves when explicitly advanced — no wall waiting,
    no timer fires while virtual time stands still. Used by `sim/harness.py`
    (every test) and optionally by `entry.py` (`Config.clock_model = "B"`)
    for a driver that wants exact, deterministic virtual timestamps instead
    of real elapsed wall time."""

    model = "B"

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


class CoupledClock(ClockPort):
    """Model A (`docs/prompt 2.txt` §13.2): `now_us()` is the last
    harness-provided timestamp (set via `advance_to`, which every real
    inbound event and worker-result stamp already calls) plus real
    monotonic elapsed time since that timestamp was set. Reading the wall
    clock is confined to this one adapter, exactly as §13.4 requires
    everywhere else.

    This is what lets the production driver (`entry.py`) release a call
    blocked behind a step-scheduled timer (e.g. the settle barrier's G10 in
    `kernel/commit.py`) without needing an explicit external event: simply
    re-running `Kernel.step()` periodically while idle is enough, because
    `now_us()` has already moved forward on its own by the time it's read
    (D1, `docs/original_design_audit.md`).

    `busy(flag)` is a no-op for Model A per §13.2's own table -- `now_us()`
    always derives from real elapsed time regardless of it; the flag is
    still recorded (`is_busy`) for parity with `SteppedClock` and any
    future consumer, but nothing here reads it back.
    """

    model = "A"

    def __init__(self, start_us: int = 0, *, wall_clock: Callable[[], float] = time.monotonic) -> None:
        self._floor_us = start_us
        self._wall_clock = wall_clock
        self._anchor_wall = wall_clock()
        self._busy = False

    def now_us(self) -> int:
        elapsed_us = round((self._wall_clock() - self._anchor_wall) * 1_000_000)
        return self._floor_us + max(0, elapsed_us)

    def advance_to(self, ts_us: int) -> None:
        current = self.now_us()
        if ts_us < current:
            raise ValueError(f"clock cannot move backward: {ts_us} < {current}")
        self._floor_us = ts_us
        self._anchor_wall = self._wall_clock()

    def busy(self, flag: bool) -> None:
        self._busy = bool(flag)

    @property
    def is_busy(self) -> bool:
        return self._busy
