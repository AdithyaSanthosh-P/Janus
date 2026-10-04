"""4 Oct voice run (housing_15), kernel side: a barge-in over the agent's
ACK opens a turn (TurnManager.on_interruption) and CommitGate holds every
call while it is open ("floor_open"). The bridge now closes a wordless
barge-in with an end_of_turn (tests/test_voice_bridge.py); this checks that
closing the empty turn releases the held call and interprets nothing."""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, interruption_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

FIND_TOOL = {
    "name": "find_items",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}
FAST = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000, JobKind.EXTRACT: 10_000, JobKind.BIND: 10_000}
TOOLS = {"find_items": {"latency_ms": 200, "response": {"items": [{"item_id": "P1"}]}}}
FIND = {"act": "new_goal", "intent": "find_items", "slot_deltas": [], "actions": [{"tool": "find_items", "args": {"query": "lamp"}}]}


def _calls(actions):
    return [a for a in actions if a.action_type == ActionType.TOOL_CALL]


def test_a_closed_wordless_barge_in_releases_the_held_call():
    provider = ScriptedProvider()
    provider.register("interpret", "lamp", FIND)
    provider.register("compose", "a0", {"text": "Found a lamp.", "claims": []})
    config = Config(action_plans_enabled=True, multi_action_enabled=True, settle_barrier_enabled=True, settle_reads_enabled=True, settle_ms=1000)
    h = SimHarness(config, seed=1, provider=provider, tools=TOOLS, worker_latency_us=FAST)
    h.send(0, [manifest_event([FIND_TOOL])])
    h.send(100_000, [chunk_event("find me a lamp")])
    out = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted]
    out += drain(h, 400_000, stop_on_final=False)
    assert _calls(out) == []  # still inside the settle window
    out += [er.action for er in h.send(450_000, [interruption_event()]).emit_report.emitted]
    held = drain(h, 3_000_000, stop_on_final=False)
    assert _calls(held) == []  # the barge-in's open turn holds the call
    out += held
    out += [er.action for er in h.send(3_100_000, [eot_event()]).emit_report.emitted]
    out += drain(h, 6_000_000)
    assert [c.body.tool_name for c in _calls(out)] == ["find_items"]
    assert [a for a in out if a.action_type == ActionType.FINAL]
    assert len([j for j in h.store.jobs.all() if j.kind == JobKind.INTERPRET]) == 1  # the empty turn is not interpreted
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []
