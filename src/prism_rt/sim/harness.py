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
        self._calls_per_tool: dict[str, int] = {}

    def on_call(self, call_id: str, tool_name: str, args: dict, ts_us: int) -> None:
        cfg = self._tools.get(tool_name, {})
        latency_us = int(cfg.get("latency_ms", 0)) * 1000
        due_us = ts_us + latency_us
        # `fail_first`: the tool's first N calls error, later ones succeed
        # (retry-vs-late-success races, `sim/explorer.py`).
        n = self._calls_per_tool.get(tool_name, 0)
        self._calls_per_tool[tool_name] = n + 1
        error = cfg.get("error")
        if error is None and n < int(cfg.get("fail_first", 0)):
            error = {"code": "transient", "message": "scripted transient failure"}
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
        blob_resolver=None,
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
        # blob_resolver (Phase 3): optional, live-use-only — resolves a
        # harness-given frame_id/clip_id into real bytes for VISION/ASR
        # jobs. None by default, so every existing SimHarness caller
        # (every test) is unaffected.
        self.runner = ScriptedRunner(self.gateway, latency_us_by_kind=worker_latency_us or default_latency, blob_resolver=blob_resolver)
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

    def fire_liveness_if_due(self) -> StepReport | None:
        """P0.1 (C9c, docs/original_design_audit.md): the deterministic
        simulator's analogue of `entry.py`'s real-time idle loop / Model A
        CoupledClock -- jumps straight to the earliest TimerWheel due time
        (`StepReport.next_wake_us`, e.g. G10's settle wake in
        `kernel/commit.py`) and steps with a `timer_fired`
        (`liveness_fire=True`) envelope, rather than blindly advancing by a
        fixed increment the way `advance`/`drain` do.

        Model B (`docs/prompt 2.txt` §13.2) never advances on its own, so a
        test driving the harness *only* through this method (no `send`/
        `advance` calls of its own) reproduces the exact "harness sends
        nothing more" shape D1 was found under -- proving a settle-blocked
        write is released by honouring `next_wake_us` alone, not by the
        test script happening to advance time regardless (which is what
        `drain`'s fixed-`step_us` loop does, and why it never caught D1).
        Returns None (no step taken) when no timer is currently scheduled.
        """
        due_us = self.store.timers.next_due_us()
        if due_us is None:
            return None
        self.clock.advance_to(max(due_us, self.clock.now_us()))
        now_us = self.clock.now_us()
        raw = {
            "ts_us": now_us,
            "type": "timer_fired",
            "payload": {"timer_id": "liveness", "timer_kind": "liveness", "liveness_fire": True},
        }
        envelopes = self.codec.decode(raw, seq=self._next_seq())
        envelopes.extend(self._due_tool_result_envelopes(now_us))
        envelopes.extend(self._due_worker_result_envelopes(now_us))
        report = self.kernel.step(envelopes)
        self._register_new_calls(report)
        self.run_log.reports.append(report)
        return report

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
