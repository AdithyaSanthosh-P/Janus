"""Day 1 (docs/fdb_v3_implementation_plan.md), found live: a real PLAN
response from gemini-3.6-flash with `thinkingBudget: 0` returned a bare
fact-binding key (`"key": "query"`) instead of the fully-qualified
`"slot.$G.query"` its own schema's example shows. `kernel/proposals.py.
_normalize_fact_key` now treats an unprefixed key as shorthand for
`slot.$G.<name>` -- every legitimate fact-key scheme in this codebase is
always dotted, so this is unambiguous, not a guess.

Reproduced against the real FDB-v3 text-replay harness before this fix
(search_products asked to clarify "what should query be?" even though
the Interpreter had already committed `slot.<gid>.query` -- see the
2026-09-25 session for the live PLAN response that motivated this)."""

from __future__ import annotations

from conftest import FAST_WORKER_LATENCY, SEARCH_FLIGHTS_TOOL, chunk_event, drain, eot_event, manifest_event

from prism_rt.model.types import ActionType, GoalStatus
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider


def new_harness(config, tools=None, provider=None, **kwargs):
    kwargs.setdefault("worker_latency_us", FAST_WORKER_LATENCY)
    return SimHarness(config, seed=1, tools=tools, provider=provider, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def test_bare_fact_key_in_plan_binding_still_binds(config):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Delhi",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}]},
    )
    # The live-model failure mode: no "slot.$G." prefix at all.
    provider.register(
        "plan",
        "search_flights",
        {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "destination"}}, "after": []}]},
    )
    provider.register("compose", "s1", {"text": "Found a flight to Delhi.", "claims": ["result:s1"]})

    h = new_harness(config, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Delhi")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 2_000_000)
    types = [a.action_type for a in actions]
    assert ActionType.CLARIFY not in types, "a bare fact key must not stall the step on a spurious clarify"
    assert ActionType.TOOL_CALL in types
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.text == "Found a flight to Delhi."
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED
    assert_clean(h)


def test_already_qualified_fact_key_is_unaffected(config):
    """The normalization must be a no-op for the common, correct case."""
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Delhi",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}]},
    )
    provider.register(
        "plan",
        "search_flights",
        {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]},
    )
    provider.register("compose", "s1", {"text": "Found a flight to Delhi.", "claims": ["result:s1"]})

    h = new_harness(config, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Delhi")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 2_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED
    assert_clean(h)


def test_normalize_fact_key_unit():
    from prism_rt.kernel.proposals import _normalize_fact_key

    assert _normalize_fact_key("query") == "slot.$G.query"
    assert _normalize_fact_key("order_id") == "slot.$G.order_id"
    assert _normalize_fact_key("slot.$G.destination") == "slot.$G.destination"
    assert _normalize_fact_key("derived.$G.selected_flight") == "derived.$G.selected_flight"
    assert _normalize_fact_key("claim.$Q.top_half_color") == "claim.$Q.top_half_color"
    assert _normalize_fact_key(None) is None
