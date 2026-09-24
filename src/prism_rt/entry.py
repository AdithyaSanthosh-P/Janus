"""Runtime setup + run_scenario(): the composition root for running against
a *real* harness transport, wiring the same components `sim/harness.py`
wires for tests.

Phase A (`docs/post_v4_implementation_plan.md`) rewrite. `run_scenario` was
previously a plain synchronous function wired to `AsyncWorkerRunner`, which
dispatches jobs via `asyncio.ensure_future(...)` — that schedules a
coroutine but never runs it unless something drives the event loop. Since
the old loop never `await`ed anything, no worker job ever executed:
`poll_results()` returned empty forever, and the agent would ingest events
and emit nothing at all, silently, until the watchdog fired with no
salvage (fixed separately in `kernel/reducers.py._apply_watchdog`).
Confirmed by grep before this rewrite: nothing in `src/`, `tests/`, or
`demo/` had ever called `run_scenario` or `setup()` — this had never been
executed once.

`run_scenario` is now genuinely `async def`, consuming from an
`asyncio.Queue` and pushing to another — the literal contract
`guidelines/Theme_5_Guide.md` §3 states: *"Participants implement an agent
communicating over two asynchronous queues (timestamped events input,
actions output)."* `run_scenario_io` is a thin, synchronous-callback
adapter over the same async path, for any harness that hands us
`read_event()`/`write_action()` instead of raw queues.

Per-event decode and per-step kernel execution are wrapped in try/except
(P-02): one malformed or unexpected event, or one bug the codec's
tolerance didn't anticipate, must never take down the whole scenario —
`adapters/codec.py.decode` is already tolerant of malformed *input*, this
is the second line of defense for anything that gets past it anyway.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Protocol

from prism_rt.adapters.clock import ClockPort, CoupledClock, SteppedClock
from prism_rt.adapters.codec import HarnessCodec
from prism_rt.adapters.output_writer import OutputWriter, WriteResult
from prism_rt.config import DEFAULT_CONFIG, Config
from prism_rt.ids import IdGenerator
from prism_rt.kernel.step import Kernel, StepReport
from prism_rt.model.actions import Action
from prism_rt.observability.decision_log import DecisionLogger
from prism_rt.observability.watchdog import ScenarioWatchdog
from prism_rt.store.session import SessionStore
from prism_rt.workers.gateway import ModelGateway, Provider
from prism_rt.workers.runner import AsyncWorkerRunner

# How often the main loop re-checks for completed worker jobs and the
# watchdog even when no new event has arrived on the input queue. Small
# enough that a worker completing mid-turn is picked up promptly; not so
# small it busy-spins.
_POLL_INTERVAL_S = 0.05


class HarnessIO(Protocol):
    def read_event(self) -> dict | None:
        """Return the next raw wire event, or None when the scenario ends."""
        ...

    def write_action(self, encoded: dict) -> None: ...


class _QueueWriter:
    """Adapts an `asyncio.Queue` into the synchronous `OutputWriter`
    protocol `EmissionGate` calls — emission happens synchronously inside
    `Kernel.step()` (K3: no buffer between decision and write), so this
    can't `await`; `put_nowait` on an unbounded queue never blocks."""

    def __init__(self, actions: "asyncio.Queue[dict]", codec: HarnessCodec) -> None:
        self._actions = actions
        self._codec = codec

    def write(self, action: Action) -> WriteResult:
        try:
            encoded = self._codec.encode(action)
        except Exception as exc:  # noqa: BLE001 - never crash the loop on encode failure
            return WriteResult(ok=False, error=str(exc))
        self._actions.put_nowait(encoded)
        return WriteResult(ok=True)


@dataclass
class RunSummary:
    step_count: int = 0
    reports: list[StepReport] = field(default_factory=list)
    watchdog_fired: bool = False


class Codec(Protocol):
    """What `run_scenario` needs from a wire codec: `HarnessCodec` (Janus's
    own dialect) or `adapters/kit_codec.py.KitCodec` (the evaluation kit's)."""

    def timestamp_us(self, raw: dict) -> int | None: ...
    def decode(self, raw: dict, *, seq: int) -> list: ...
    def encode(self, action: Action) -> dict: ...


@dataclass
class Runtime:
    config: Config
    provider: Provider
    codec: Codec | None = None  # None -> HarnessCodec (unchanged default)

    async def run_scenario(
        self,
        events: "asyncio.Queue[dict | None]",
        actions: "asyncio.Queue[dict]",
        meta: dict | None = None,
    ) -> RunSummary:
        """The real, async entry point. Consumes raw event dicts from
        `events` until `None` (or any falsy sentinel) is received or the
        queue is otherwise exhausted; pushes encoded action dicts onto
        `actions` as the kernel emits them. Returns once the scenario ends.
        """
        meta = meta or {}
        session = SessionStore.new(self.config, IdGenerator(meta.get("seed", 0)))
        clock: ClockPort = CoupledClock() if self.config.clock_model == "A" else SteppedClock()
        # Two roles: `codec` speaks the harness's wire format (external
        # events in, actions out); `internal` decodes the events this loop
        # synthesizes itself (worker_result, timer_fired, watchdog), which
        # are always in Janus's own dialect whatever the wire format is.
        internal = HarnessCodec()
        codec = self.codec if self.codec is not None else internal
        writer = _QueueWriter(actions, codec)
        log = DecisionLogger(meta.get("log_path")) if self.config.log_decisions else None
        runner = AsyncWorkerRunner(ModelGateway(self.provider))
        kernel = Kernel(self.config, session, clock, writer, runner=runner, log=log)

        summary = RunSummary()
        seq = 0
        wall_start = time.monotonic()
        watchdog = ScenarioWatchdog(self.config.watchdog_timeout_ms / 1000, callback=lambda: None)
        watchdog.start()
        # P0.1 (C9c, docs/original_design_audit.md): the earliest time a
        # driver-visible timer (TimerWheel, e.g. G10's settle wake in
        # kernel/commit.py) needs the kernel to step again, per the most
        # recent StepReport. Without honouring this, a write blocked behind
        # settle_ms never gets re-evaluated once the harness stops sending
        # events, and stalls until the watchdog salvages it (D1).
        next_wake_us: int | None = None

        while True:
            if watchdog.check():
                summary.watchdog_fired = True
                seq += 1
                wall_elapsed_s = time.monotonic() - wall_start
                self._step_safely(
                    kernel, internal, {"ts_us": clock.now_us(), "type": "watchdog", "payload": {"wall_elapsed_s": wall_elapsed_s}}, seq, summary
                )
                break

            # Drain any worker results that completed since the last
            # iteration — these arrive asynchronously from our own worker
            # tasks, not from the external `events` queue, so they need
            # their own polling point every time around this loop.
            for result in runner.poll_results(clock.now_us()):
                seq += 1
                raw = {
                    "ts_us": clock.now_us(),
                    "type": "worker_result",
                    "payload": {"job_id": result.job_id, "kind": result.kind, "status": result.status, "proposal": result.proposal},
                }
                report = self._step_safely(kernel, internal, raw, seq, summary)
                if report is not None:
                    next_wake_us = report.next_wake_us

            if next_wake_us is not None:
                # Model B never advances on its own — jump the virtual clock
                # straight to the due timer (there is nothing to wait for).
                # Model A's CoupledClock already tracks real elapsed wall
                # time on its own, so this only ever fires once that much
                # real time has genuinely passed.
                if clock.model == "B" and next_wake_us > clock.now_us():
                    clock.advance_to(next_wake_us)
                if next_wake_us <= clock.now_us():
                    seq += 1
                    raw = {
                        "ts_us": clock.now_us(),
                        "type": "timer_fired",
                        "payload": {"timer_id": "liveness", "timer_kind": "liveness", "liveness_fire": True},
                    }
                    report = self._step_safely(kernel, internal, raw, seq, summary)
                    next_wake_us = report.next_wake_us if report is not None else None
                    # Yield before looping: this loop shares the harness's
                    # event loop (docs/PROTOCOL.md §5), so this branch must
                    # never be able to spin without letting it run.
                    await asyncio.sleep(0)
                    continue  # re-check watchdog + workers before waiting on events again

            try:
                raw = await asyncio.wait_for(events.get(), timeout=_POLL_INTERVAL_S)
            except asyncio.TimeoutError:
                continue  # nothing new; loop back to re-check watchdog + poll workers + liveness

            if not raw:
                break
            seq += 1
            self._advance_clock(clock, codec, raw)
            report = self._step_safely(kernel, codec, raw, seq, summary)
            if report is not None:
                next_wake_us = report.next_wake_us

        return summary

    def _advance_clock(self, clock: ClockPort, codec: Codec, raw: dict) -> None:
        # The codec owns which wire field carries the timestamp (`ts_us`/
        # `ts_ms`/... for HarnessCodec, `timestamp_ms` for KitCodec).
        try:
            ts_us = codec.timestamp_us(raw) if isinstance(raw, dict) else None
            if ts_us is not None:
                clock.advance_to(ts_us)
        except (TypeError, ValueError):
            pass  # unparseable or backward timestamp -> clock just doesn't advance; decode below still degrades gracefully

    def _step_safely(self, kernel: Kernel, codec: Codec, raw: dict, seq: int, summary: RunSummary) -> StepReport | None:
        """P-02: one bad event, or one bug the codec's own tolerance
        didn't anticipate, must never end the scenario. `codec.decode`
        already returns `[]` for anything it can't make sense of rather
        than raising; this is the second line of defense for whatever
        gets past that anyway (a reducer bug, an unexpected exception deep
        in a worker's proposal handling, etc.). Returns the StepReport so
        the caller can read `next_wake_us` (P0.1 liveness); None on either
        failure path above, or if the event decoded to nothing."""
        try:
            envelopes = codec.decode(raw, seq=seq)
        except Exception:  # noqa: BLE001 - decode is documented tolerant; this is pure insurance
            return None
        try:
            report = kernel.step(envelopes)
        except Exception:  # noqa: BLE001 - a single step's failure must not end the scenario
            return None
        summary.reports.append(report)
        summary.step_count += 1
        return report

    async def run_scenario_io(self, io: HarnessIO, meta: dict | None = None) -> RunSummary:
        """Synchronous-callback adapter over `run_scenario`, for a harness
        that hands us `read_event()`/`write_action()` instead of raw
        `asyncio.Queue` objects. Bridges by running the blocking calls in
        a thread so they never stall the event loop `run_scenario` needs
        to actually drive worker tasks."""
        events: "asyncio.Queue[dict | None]" = asyncio.Queue()
        actions: "asyncio.Queue[dict]" = asyncio.Queue()
        stop = asyncio.Event()

        async def pump_in() -> None:
            while True:
                raw = await asyncio.to_thread(io.read_event)
                await events.put(raw)
                if not raw:
                    return

        async def pump_out() -> None:
            while not stop.is_set():
                get_task = asyncio.ensure_future(actions.get())
                stop_task = asyncio.ensure_future(stop.wait())
                done, pending = await asyncio.wait({get_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                if get_task in done:
                    io.write_action(get_task.result())

        in_task = asyncio.ensure_future(pump_in())
        out_task = asyncio.ensure_future(pump_out())
        try:
            summary = await self.run_scenario(events, actions, meta)
        finally:
            stop.set()
            in_task.cancel()
            await asyncio.gather(in_task, out_task, return_exceptions=True)
        return summary


def setup(config: Config | None = None, *, provider: Provider | None = None, codec: Codec | None = None) -> Runtime:
    if provider is None:
        from prism_rt.workers.gateway import ScriptedProvider

        provider = ScriptedProvider()
    return Runtime(config=config or DEFAULT_CONFIG, provider=provider, codec=codec)
