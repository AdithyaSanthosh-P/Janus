"""Day 2 (docs/fdb_v3_implementation_plan.md §5.2): G4 (commit_intent)
exemption for tools with undeclared mutability.

Found live (2026-09-25 session, T3 text-replay harness against FDB-v3):
every FDB-v3 tool declares no mutability, so `ToolCatalog`'s safe
default (STATE_CHANGING, W5) applies to all twelve of them -- including
plain reads like `search_flights`. G4 then requires an explicit
Interpreter-set `commit_intent=true` for *every* call, and when a live
model doesn't set it (which happens inconsistently), the call sits
`PROPOSED` forever with total silence: no clarify, no retry. Reproduced
directly (travel_21: `slot_deltas` extracted correctly, `commit_intent`
never set, `search_flights` never admitted).

`Config.g4_exempt_undeclared_mutability` (default False) lets G4 pass
for a call whose tool has no *declared* mutability, without touching a
tool that does declare one -- G3 (floor closed) and G10/G11 (settle,
when enabled) still gate it exactly as before."""

from __future__ import annotations

from conftest import FAST_WORKER_LATENCY, chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, GoalStatus
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

# No "mutability" key at all -- exactly what fdb_manifest.py produces for
# every FDB tool (mutability is never declared, per its own docstring).
UNDECLARED_SEARCH_FLIGHTS_TOOL = {
    "name": "search_flights",
    "parameters": {
        "type": "object",
        "properties": {"destination": {"type": "string"}, "date": {"type": "string"}},
        "required": ["destination"],
    },
}


def new_harness(config, tools=None, provider=None, **kwargs):
    kwargs.setdefault("worker_latency_us", FAST_WORKER_LATENCY)
    return SimHarness(config, seed=1, tools=tools, provider=provider, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def _provider_no_commit_intent() -> ScriptedProvider:
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Delhi",
        # Deliberately no "commit_intent" key at all -- the exact live
        # failure mode (gemini-3.6-flash thinking off didn't set it for
        # travel_21's search_flights request).
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}]},
    )
    provider.register(
        "plan",
        "search_flights",
        {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]},
    )
    provider.register("compose", "s1", {"text": "Found a flight to Delhi.", "claims": ["result:s1"]})
    return provider


def test_flag_off_stalls_silently_without_commit_intent(config):
    """Baseline: reproduces the live failure exactly -- the call never
    gets admitted, the goal never completes, no clarify either."""
    assert config.g4_exempt_undeclared_mutability is False
    provider = _provider_no_commit_intent()
    h = new_harness(config, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([UNDECLARED_SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Delhi")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 2_000_000, stop_on_final=False)
    assert ActionType.TOOL_CALL not in [a.action_type for a in actions]
    assert ActionType.FINAL not in [a.action_type for a in actions]
    assert h.store.goals.all()[0].status == GoalStatus.ACTIVE  # stuck, not completed, not failed
    assert_clean(h)


def test_flag_on_completes_without_explicit_commit_intent(config):
    """The fix: the same scenario, same provider (still no commit_intent),
    flag on -> the call is admitted and the goal completes normally."""
    on_config = Config(**{**config.__dict__, "g4_exempt_undeclared_mutability": True})
    provider = _provider_no_commit_intent()
    h = new_harness(on_config, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([UNDECLARED_SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Delhi")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 2_000_000)
    types = [a.action_type for a in actions]
    assert ActionType.TOOL_CALL in types
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.text == "Found a flight to Delhi."
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED
    assert_clean(h)


def test_flag_on_does_not_exempt_a_declared_write_tool(config):
    """A tool that *does* declare mutability=state_changing must still
    require explicit commit_intent, even with the flag on -- this never
    weakens a real write, only tools nobody ever classified."""
    declared_write_tool = {
        "name": "book_flight",
        "mutability": "state_changing",
        "parameters": {"type": "object", "properties": {"flight_id": {"type": "string"}}, "required": ["flight_id"]},
    }
    on_config = Config(**{**config.__dict__, "g4_exempt_undeclared_mutability": True})
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book flight AI-1",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}]},
    )
    provider.register(
        "plan",
        "book_flight",
        {"steps": [{"local_id": "s1", "tool": "book_flight", "kind": "write", "bindings": {"flight_id": {"type": "fact", "key": "slot.$G.flight_id"}}, "after": []}]},
    )
    h = new_harness(on_config, tools={"book_flight": {"latency_ms": 100, "response": {"status": "booked"}}}, provider=provider)
    h.send(0, [manifest_event([declared_write_tool])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 2_000_000, stop_on_final=False)
    assert ActionType.TOOL_CALL not in [a.action_type for a in actions]
    assert_clean(h)
