"""Config.merge_split_turns_enabled: a request split by a mid-sentence pause
("find flights to Pune ... and track order A12") is interpreted once, over
the combined text, instead of twice in a row (found live, 28 Sep: the
second, redundant INTERPRET added ~5 s before the agent spoke).
"""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

SEARCH = {"name": "search_flights", "mutability": "read_only", "parameters": {"type": "object", "properties": {
    "destination": {"type": "string"}}, "required": ["destination"]}}
ORDER = {"name": "track_order", "mutability": "read_only", "parameters": {"type": "object", "properties": {
    "order_id": {"type": "string"}}, "required": ["order_id"]}}
TOOLS = {"search_flights": {"latency_ms": 200, "response": {"flight_id": "FL1"}},
         "track_order": {"latency_ms": 200, "response": {"status": "shipped"}}}
SLOW_INTERPRET = {JobKind.INTERPRET: 300_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000}

BOTH = {"act": "new_goal", "intent": "search_flights", "slot_deltas": [], "actions": [
    {"tool": "search_flights", "args": {"destination": "Pune"}}, {"tool": "track_order", "args": {"order_id": "A12"}}]}
FIRST_HALF = {"act": "new_goal", "intent": "search_flights", "slot_deltas": [], "actions": [
    {"tool": "search_flights", "args": {"destination": "Pune"}}]}
SECOND_HALF = {"act": "addition", "slot_deltas": [], "actions": [
    {"tool": "search_flights", "args": {"destination": "Pune"}}, {"tool": "track_order", "args": {"order_id": "A12"}}]}


def run(config: Config) -> tuple[SimHarness, list, list[str]]:
    prompts: list[str] = []

    def interpret(prompt: str) -> dict:
        prompts.append(prompt)
        if "flights to Pune" in prompt and "order A12" in prompt and "Current actions" not in prompt:
            return BOTH
        return SECOND_HALF if "order A12" in prompt else FIRST_HALF

    provider = ScriptedProvider()
    provider.register("interpret", "", interpret)
    provider.register("compose", "a1", {"text": "Done both.", "claims": []})
    h = SimHarness(config, seed=1, provider=provider, tools=TOOLS, worker_latency_us=SLOW_INTERPRET)
    h.send(0, [manifest_event([SEARCH, ORDER])])
    actions = []
    for ts, event in ((100_000, chunk_event("find flights to Pune")), (150_000, eot_event()),
                      (250_000, chunk_event("and track order A12")), (300_000, eot_event())):
        actions += [er.action for er in h.send(ts, [event]).emit_report.emitted]
    actions += drain(h, 3_000_000)
    return h, actions, prompts


def tool_calls(actions) -> list:
    return [(a.body.tool_name, dict(a.body.arguments)) for a in actions if a.action_type == ActionType.TOOL_CALL]


def config(**overrides) -> Config:
    return Config(**{"action_plans_enabled": True, "multi_action_enabled": True, "merge_split_turns_enabled": True, **overrides})


def test_split_request_is_interpreted_once_over_both_halves():
    h, actions, prompts = run(config())
    applied = [j for j in h.store.jobs.all() if j.kind == JobKind.INTERPRET]
    assert tool_calls(actions) == [("search_flights", {"destination": "Pune"}), ("track_order", {"order_id": "A12"})]
    assert len(h.store.goals.all()) == 1
    # The first half's job was dispatched, but the one that counted saw both halves.
    assert any("flights to Pune" in p and "order A12" in p for p in prompts)
    assert len(applied) == 2
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_merge_also_works_with_speculative_interpretation():
    h, actions, prompts = run(config(speculative_interpretation_enabled=True))
    assert tool_calls(actions) == [("search_flights", {"destination": "Pune"}), ("track_order", {"order_id": "A12"})]
    assert len(h.store.goals.all()) == 1
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_flag_off_interprets_the_halves_one_after_the_other():
    h, actions, prompts = run(config(merge_split_turns_enabled=False))
    assert not any("flights to Pune" in p and "order A12" in p and "Current actions" not in p for p in prompts)
    assert tool_calls(actions)[0] == ("search_flights", {"destination": "Pune"})


def test_merge_replay_identity():
    def once():
        h, actions, _ = run(config(speculative_interpretation_enabled=True))
        return [(a.action_type.value, getattr(a.body, "text", None)) for a in actions]

    assert once() == once()
