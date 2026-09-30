"""Day 2 WP2 (docs/fdb_v3_day2_plan.md): multi-action requests.

Found live (gemini-3.5-flash-lite, a real 3-action FDB-v3 recording):
the Interpreter only ever populated `intent`/`slot_deltas` for the
*first* action a turn asked for. `Config.multi_action_enabled` (default
False) adds `requested_actions` (Interpreter -> goal.<gid>.actions ->
Planner prompt) so the Planner emits one step per action instead of
one step total.
"""

from __future__ import annotations

from conftest import FAST_WORKER_LATENCY, chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, GoalStatus
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

SEARCH_FLIGHTS_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"destination": {"type": "string"}}, "required": ["destination"]},
}
TRACK_ORDER_TOOL = {
    "name": "track_order",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
}


def new_harness(config, tools=None, provider=None, **kwargs):
    kwargs.setdefault("worker_latency_us", FAST_WORKER_LATENCY)
    return SimHarness(config, seed=1, tools=tools, provider=provider, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def test_two_action_turn_gets_both_tool_calls_when_enabled(config):
    on_config = Config(**{**config.__dict__, "multi_action_enabled": True})
    provider = ScriptedProvider()
    provider.register(
        "interpret", "find flights to Paris and track order 42",
        {
            "act": "new_goal", "intent": "search_flights",
            "requested_actions": ["search_flights", "track_order"],
            "slot_deltas": [
                {"name": "destination", "scope": "goal", "op": "set", "value": "Paris"},
                {"name": "order_id", "scope": "goal", "op": "set", "value": "42"},
            ],
        },
    )
    provider.register(
        "plan", "search_flights",
        {
            "steps": [
                {"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
                {"local_id": "s2", "tool": "track_order", "kind": "read", "bindings": {"order_id": {"type": "fact", "key": "slot.$G.order_id"}}, "after": []},
            ]
        },
    )
    provider.register("compose", "", {"text": "Found your flight and checked the order.", "claims": []})
    h = new_harness(
        on_config,
        tools={
            "search_flights": {"latency_ms": 50, "response": {"flight_id": "FL1"}},
            "track_order": {"latency_ms": 50, "response": {"status": "out for delivery"}},
        },
        provider=provider,
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, TRACK_ORDER_TOOL])])
    h.send(100_000, [chunk_event("find flights to Paris and track order 42")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 3_000_000)
    calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert sorted(c.body.tool_name for c in calls) == ["search_flights", "track_order"]
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED
    assert_clean(h)


def test_requested_actions_ignored_when_flag_off(config):
    """Same scripted responses, flag off: `requested_actions` in the raw
    interpretation is simply never stored/read -- the Planner's prompt
    never even mentions a second action, and (per this test's own
    single-step plan response) exactly one call is made. Proves the
    flag is a real gate, not a no-op wrapper."""
    assert config.multi_action_enabled is False
    provider = ScriptedProvider()
    provider.register(
        "interpret", "find flights to Paris",
        {
            "act": "new_goal", "intent": "search_flights",
            "requested_actions": ["search_flights", "track_order"],  # present but must be ignored
            "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Paris"}],
        },
    )
    provider.register(
        "plan", "search_flights",
        {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]},
    )
    provider.register("compose", "", {"text": "Found your flight.", "claims": []})
    h = new_harness(config, tools={"search_flights": {"latency_ms": 50, "response": {"flight_id": "FL1"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, TRACK_ORDER_TOOL])])
    h.send(100_000, [chunk_event("find flights to Paris")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 3_000_000)
    calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert [c.body.tool_name for c in calls] == ["search_flights"]
    assert_clean(h)
