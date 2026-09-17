"""SimHarness: deterministic event replay against the kernel.

Time never runs on its own — `send`/`advance` explicitly move the
`SteppedClock` forward, and any tool result whose mock latency has elapsed
by that timestamp is delivered in the same step as everything else due at
that time. Nothing here reads the wall clock.

V0 scenarios are built directly in Python (`send(...)`, `inject=...`)
rather than from the YAML scenario format in
`docs/sonnet_implementation_plan.md` §6.2 — that format's `scripted_responses`
section only makes sense once `workers/gateway.py` (V1) exists to consume
it. `load_yaml` is a placeholder for that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from prism_rt.adapters.clock import SteppedClock
from prism_rt.adapters.codec import HarnessCodec
from prism_rt.adapters.output_writer import BufferOutputWriter
from prism_rt.config import Config
from prism_rt.ids import IdGenerator
from prism_rt.kernel.step import Kernel, StepReport
from prism_rt.model.actions import Action, ToolCallBody
from prism_rt.model.events import Envelope
from prism_rt.model.types import ActionType, JobKind
from prism_rt.observability.decision_log import DecisionLogger
from prism_rt.store.session import SessionStore
from prism_rt.workers.gateway import ModelGateway, ScriptedProvider
from prism_rt.workers.runner import ScriptedRunner


class MockToolRegistry:
    """Schedules tool results at `call ts + configured latency`, keyed by
    call_id. Cancelling a call does not un-schedule its result — tests rely
    on late results still arriving so ResultRouter branch 4
    (completed_after_cancel) is exercised end-to-end."""

    def __init__(self, tools: dict | None = None) -> None:
        self._tools = tools or {}
        self._scheduled: dict[str, tuple[int, str, object, dict | None]] = {}
        self._cancelled: set[str] = set()

    def on_call(self, call_id: str, tool_name: str, args: dict, ts_us: int) -> None:
        cfg = self._tools.get(tool_name, {})
        latency_us = int(cfg.get("latency_ms", 0)) * 1000
        due_us = ts_us + latency_us
        error = cfg.get("error")
        status = "error" if error else "ok"
        response = cfg.get("response")
        self._scheduled[call_id] = (due_us, status, response, error)

    def on_cancel(self, call_id: str) -> None:
        self._cancelled.add(call_id)

    def due_results(self, now_us: int) -> list[tuple[str, str, object, dict | None]]:
        due = [
            (cid, due_ts, status, response, error)
            for cid, (due_ts, status, response, error) in self._scheduled.items()
            if due_ts <= now_us
        ]
        due.sort(key=lambda item: (item[1], item[0]))  # deterministic: due_ts, then call_id
        for cid, *_ in due:
            del self._scheduled[cid]
        return [(cid, status, response, error) for cid, _due_ts, status, response, error in due]


@dataclass
class RunLog:
    reports: list[StepReport] = field(default_factory=list)

    @property
    def emitted_actions(self) -> list[Action]:
        return [er.action for report in self.reports for er in report.emit_report.emitted]


class SimHarness:
    def __init__(
        self,
        config: Config,
        *,
        seed: int = 0,
        tools: dict | None = None,
        provider: ScriptedProvider | None = None,
        worker_latency_us: dict[JobKind, int] | None = None,
    ) -> None:
        self.config = config
        self.clock = SteppedClock()
        self.store = SessionStore.new(config, IdGenerator(seed))
        self.codec = HarnessCodec()
        self.writer = BufferOutputWriter(self.codec)
        self.log = DecisionLogger()
        self.provider = provider if provider is not None else ScriptedProvider()
        self.gateway = ModelGateway(self.provider)
        default_latency = {JobKind.INTERPRET: 50_000, JobKind.PLAN: 50_000, JobKind.COMPOSE: 50_000}
        self.runner = ScriptedRunner(self.gateway, latency_us_by_kind=worker_latency_us or default_latency)
        self.kernel = Kernel(config, self.store, self.clock, self.writer, runner=self.runner, log=self.log)
        self.mock_tools = MockToolRegistry(tools)
        self._seq = 0
        self.run_log = RunLog()

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def send(self, ts_us: int, raw_events: list[dict] | None = None, *, inject=None) -> StepReport:
        self.clock.advance_to(ts_us)
        envelopes: list[Envelope] = []
        for raw in raw_events or []:
            if "ts_us" not in raw and "ts_ms" not in raw:
                raw = {**raw, "ts_us": ts_us}
            envelopes.extend(self.codec.decode(raw, seq=self._next_seq()))
        envelopes.extend(self._due_tool_result_envelopes(ts_us))
        envelopes.extend(self._due_worker_result_envelopes(ts_us))

        report = self.kernel.step(envelopes, inject=inject)
        self._register_new_calls(report)
        self.run_log.reports.append(report)
        return report

    def advance(self, ts_us: int) -> StepReport:
        """Move time forward with no new external events, delivering
        whatever mock tool results are due by then."""
        return self.send(ts_us)

    def _due_tool_result_envelopes(self, now_us: int) -> list[Envelope]:
        envelopes: list[Envelope] = []
        for call_id, status, response, error in self.mock_tools.due_results(now_us):
            raw = {
                "ts_us": now_us,
                "type": "tool_result",
                "payload": {"call_id": call_id, "status": status, "result": response, "error": error},
            }
            envelopes.extend(self.codec.decode(raw, seq=self._next_seq()))
        return envelopes

    def _due_worker_result_envelopes(self, now_us: int) -> list[Envelope]:
        envelopes: list[Envelope] = []
        for result in self.runner.poll_results(now_us):
            raw = {
                "ts_us": now_us,
                "type": "worker_result",
                "payload": {
                    "job_id": result.job_id,
                    "kind": result.kind,
                    "status": result.status,
                    "proposal": result.proposal,
                },
            }
            envelopes.extend(self.codec.decode(raw, seq=self._next_seq()))
        return envelopes

    def _register_new_calls(self, report: StepReport) -> None:
        for er in report.emit_report.emitted:
            action = er.action
            if action.action_type == ActionType.TOOL_CALL:
                body = action.body
                assert isinstance(body, ToolCallBody)
                self.mock_tools.on_call(body.call_id, body.tool_name, body.arguments, report.now_us)
            elif action.action_type == ActionType.CANCEL:
                self.mock_tools.on_cancel(action.body.target_call_id)
