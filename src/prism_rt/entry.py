"""Runtime setup + run_scenario(): the composition root for running against
a *real* harness transport, wiring the same components `sim/harness.py`
wires for tests.

The evaluation kit's wire format and IO contract are unreleased
(`currentStatus.md` known risks); `HarnessIO` below is a minimal guess at
the shape (read one raw event at a time, write one encoded action at a
time) so this module has something concrete to depend on. Nothing in this
repository's test suite exercises `run_scenario` — every test drives the
kernel through `SimHarness` instead. Treat this as unverified until it
runs against the real kit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from prism_rt.adapters.clock import SteppedClock
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


class HarnessIO(Protocol):
    def read_event(self) -> dict | None:
        """Return the next raw wire event, or None when the scenario ends."""
        ...

    def write_action(self, encoded: dict) -> None: ...


class _IOWriter:
    """Adapts a HarnessIO into the OutputWriter protocol EmissionGate uses."""

    def __init__(self, io: HarnessIO, codec: HarnessCodec) -> None:
        self._io = io
        self._codec = codec

    def write(self, action: Action) -> WriteResult:
        try:
            encoded = self._codec.encode(action)
        except Exception as exc:  # noqa: BLE001 - never crash the loop on encode failure
            return WriteResult(ok=False, error=str(exc))
        self._io.write_action(encoded)
        return WriteResult(ok=True)


@dataclass
class RunSummary:
    step_count: int = 0
    reports: list[StepReport] = field(default_factory=list)
    watchdog_fired: bool = False


@dataclass
class Runtime:
    config: Config
    provider: Provider

    def run_scenario(self, io: HarnessIO, meta: dict | None = None) -> RunSummary:
        meta = meta or {}
        session = SessionStore.new(self.config, IdGenerator(meta.get("seed", 0)))
        clock = SteppedClock()
        codec = HarnessCodec()
        writer = _IOWriter(io, codec)
        log = DecisionLogger(meta.get("log_path")) if self.config.log_decisions else None
        runner = AsyncWorkerRunner(ModelGateway(self.provider))
        kernel = Kernel(self.config, session, clock, writer, runner=runner, log=log)

        summary = RunSummary()
        watchdog = ScenarioWatchdog(self.config.watchdog_timeout_ms / 1000, callback=lambda: None)
        watchdog.start()
        seq = 0

        while True:
            if watchdog.check():
                summary.watchdog_fired = True
                break
            raw = io.read_event()
            if raw is None:
                break
            seq += 1
            if "ts_us" in raw:
                clock.advance_to(int(raw["ts_us"]))
            elif "ts_ms" in raw:
                clock.advance_to(int(raw["ts_ms"]) * 1000)
            envelopes = codec.decode(raw, seq=seq)
            report = kernel.step(envelopes)
            summary.reports.append(report)
            summary.step_count += 1

        return summary


def setup(config: Config | None = None, *, provider: Provider | None = None) -> Runtime:
    if provider is None:
        from prism_rt.workers.gateway import ScriptedProvider

        provider = ScriptedProvider()
    return Runtime(config=config or DEFAULT_CONFIG, provider=provider)
