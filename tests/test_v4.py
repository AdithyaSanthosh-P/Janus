"""V4 hardening tests: targeted timing sweeps (I-03, I-14, R-05) per
`docs/prototype_version_plan.md`'s V4 scope, plus C10 (reference-bound
write identifiers, `docs/theme05_implementation_blueprint.md` §5.5).

The sweeps don't test new behavior — they re-run an existing V1/V2
scenario at several nearby event timings (+-10ms around the original,
5ms increments) to prove the same invariants hold regardless of small
timing perturbation, rather than only working for one hand-picked delay.
"""

from __future__ import annotations

import pytest
from conftest import (
    BOOK_FLIGHT_TOOL,
    FAST_WORKER_LATENCY,
    SEARCH_FLIGHTS_TOOL,
    chunk_event,
    drain,
    eot_event,
    interruption_event,
    manifest_event,
)

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, GoalStatus, TaskState
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider


def v4_config(**overrides) -> Config:
    return Config(
        transitive_invalidation=overrides.pop("transitive_invalidation", False),
        settle_barrier_enabled=overrides.pop("settle_barrier_enabled", False),
        absence_read_sets=overrides.pop("absence_read_sets", False),
        claim_grades_enabled=overrides.pop("claim_grades_enabled", False),
        rebinder_enabled=overrides.pop("rebinder_enabled", False),
        vision_enabled=overrides.pop("vision_enabled", False),
        reference_bound_identifiers=overrides.pop("reference_bound_identifiers", False),
        **overrides,
    )


def new_harness(config, tools=None, provider=None, **kwargs):
    kwargs.setdefault("worker_latency_us", FAST_WORKER_LATENCY)
    return SimHarness(config, seed=1, tools=tools, provider=provider, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def flat_plan(tool, *, kind="read", param="destination"):
    return {"steps": [{"local_id": "s1", "tool": tool, "kind": kind, "bindings": {param: {"type": "fact", "key": f"slot.$G.{param}"}}, "after": []}]}


# --- I-03 sweep: interrupt-during-execution, +-10ms around the original ----


@pytest.mark.parametrize("interrupt_delay_us", [0, 5_000, 10_000, 15_000, 20_000])
def test_i03_sweep_interrupt_timing(interrupt_delay_us):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register(
        "interpret",
        "actually Mumbai",
        {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]},
    )
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Here are the Mumbai flights.", "claims": ["result:s1"]})

    h = new_harness(v4_config(), tools={"search_flights": {"latency_ms": 800, "response": {"flight_id": "AI-9"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    drain(h, 220_000, stop_on_final=False)
    pune_calls = [a for a in h.run_log.emitted_actions if a.action_type == ActionType.TOOL_CALL]
    assert len(pune_calls) == 1
    pune_call_id = pune_calls[0].body.call_id
    assert h.store.call_ledger.get(pune_call_id).status == CallStatus.IN_FLIGHT

    h.send(h.clock.now_us() + interrupt_delay_us, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("actually Mumbai")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])
    drain(h, h.clock.now_us() + 1_500_000)

    all_actions = h.run_log.emitted_actions
    cancels = [a for a in all_actions if a.action_type == ActionType.CANCEL]
    assert len(cancels) == 1
    assert cancels[0].body.target_call_id == pune_call_id

    mumbai_calls = [a for a in all_actions if a.action_type == ActionType.TOOL_CALL and a.body.call_id != pune_call_id]
    assert len(mumbai_calls) == 1
    assert mumbai_calls[0].body.arguments["destination"] == "Mumbai"

    finals = [a for a in all_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert "Pune" not in finals[0].body.text
    assert_clean(h)


# --- I-14 sweep: correction within the settle window, +-10ms around 150ms --


@pytest.mark.parametrize("correction_delay_us", [140_000, 145_000, 150_000, 155_000, 160_000])
def test_i14_sweep_settle_correction_timing(correction_delay_us):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book flight AI-1",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True},
    )
    provider.register(
        "interpret",
        "wait the other one",
        {"act": "slot_update", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-2"}], "commit_intent": True},
    )
    provider.register("plan", "book_flight", flat_plan("book_flight", kind="write", param="flight_id"))
    provider.register("compose", "confirmation", {"text": "Booked AI-2.", "claims": ["effect:s1"]})

    config = v4_config(settle_barrier_enabled=True, settle_ms=300)
    h = new_harness(config, tools={"book_flight": {"latency_ms": 50, "response": {"confirmation": "XYZ"}}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])

    drain(h, 250_000, stop_on_final=False)
    ai1_calls = [c for c in h.store.call_ledger.all() if c.args.get("flight_id") == "AI-1"]
    assert len(ai1_calls) == 1
    assert ai1_calls[0].status == CallStatus.PROPOSED  # blocked behind G10, never emitted

    h.send(h.clock.now_us() + correction_delay_us, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("wait the other one")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])
    drain(h, h.clock.now_us() + 3_000_000)

    all_actions = h.run_log.emitted_actions
    ai1_emitted = [a for a in all_actions if a.action_type == ActionType.TOOL_CALL and a.body.arguments.get("flight_id") == "AI-1"]
    assert ai1_emitted == []
    assert h.store.call_ledger.get(ai1_calls[0].call_id).status == CallStatus.DISCARDED

    ai2_calls = [c for c in h.store.call_ledger.all() if c.args.get("flight_id") == "AI-2" and c.status == CallStatus.CONSUMED]
    assert len(ai2_calls) == 1
    finals = [a for a in all_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert_clean(h)


# --- R-05 sweep: completion/cancellation race, +-10ms around the original --


@pytest.mark.parametrize("delta_us", [-10_000, -5_000, 0, 5_000, 10_000])
def test_r05_sweep_completion_cancellation_race(delta_us):
    """R-05's own invariant isn't "the correction always wins" — at these
    flag settings (no settle barrier, no rebinder) a correction landing at
    almost exactly the same instant as the original search's completion
    can legitimately lose that race; nothing in V0-V1 reopens an
    already-composed goal. What must hold regardless of exact timing is
    what the original test checks: the outcome is deterministic (replay-
    identical) and structurally clean (TraceChecker), not corrupted by the
    race. This sweep re-checks that at several nearby offsets, not just
    the one originally hand-picked delay."""

    def build_provider() -> ScriptedProvider:
        provider = ScriptedProvider()
        provider.register(
            "interpret",
            "Find flights to Pune",
            {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
        )
        provider.register(
            "interpret",
            "actually Mumbai",
            {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]},
        )
        provider.register("plan", "goal_intent: search_flights", flat_plan("search_flights"))
        provider.register("compose", "s1", {"text": "Mumbai flights found.", "claims": ["result:s1"]})
        return provider

    def run(record_only: bool):
        h = new_harness(v4_config(), tools={"search_flights": {"latency_ms": 500, "response": {"flight_id": "AI-1"}}}, provider=build_provider())
        h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
        h.send(100_000, [chunk_event("Find flights to Pune")])
        h.send(150_000, [eot_event()])
        drain(h, 250_000, stop_on_final=False)
        call = next(c for c in h.store.call_ledger.all() if c.tool == "search_flights")
        due_us = call.emitted_ts_us + 500_000
        h.send(due_us - 20_000 + delta_us, [interruption_event()])
        h.send(due_us - 15_000 + delta_us, [chunk_event("actually Mumbai")])
        h.send(due_us, [eot_event()])  # tool result auto-delivered the same batch
        drain(h, h.clock.now_us() + 2_000_000)
        return h.log.records if record_only else h

    # Structural cleanliness + exactly-one-FINAL at this offset.
    h = run(record_only=False)
    finals = [a for a in h.run_log.emitted_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert_clean(h)

    # Determinism: the race resolves the same way every time at this offset.
    violations = TraceChecker().check_replay(lambda: run(record_only=True), times=5)
    assert violations == []


# --- C10: reference-bound write identifiers ---------------------------------


def test_c10_model_literal_identifier_refused():
    provider = ScriptedProvider()
    provider.register("interpret", "Book a flight", {"act": "new_goal", "intent": "book_flight", "commit_intent": True})
    # The Planner invents a flight_id the user never said — must be refused,
    # not booked, per docs/theme05_implementation_blueprint.md §5.5.
    provider.register(
        "plan",
        "book_flight",
        {"steps": [{"local_id": "s1", "tool": "book_flight", "kind": "write", "bindings": {"flight_id": {"type": "literal", "value": "AI-999"}}, "after": []}]},
    )

    config = v4_config(reference_bound_identifiers=True)
    h = new_harness(config, tools={"book_flight": {"latency_ms": 50, "response": {"confirmation": "OK"}}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book a flight")])
    h.send(150_000, [eot_event()])
    drain(h, 1_000_000, stop_on_final=False)

    all_actions = h.run_log.emitted_actions
    assert ActionType.TOOL_CALL not in [a.action_type for a in all_actions]
    assert ActionType.CLARIFY in [a.action_type for a in all_actions]
    assert h.store.goals.all()[0].task_state == TaskState.CLARIFYING
    assert_clean(h)


def test_c10_user_verbatim_identifier_accepted():
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book flight AI-1",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True},
    )
    provider.register("plan", "book_flight", flat_plan("book_flight", kind="write", param="flight_id"))
    provider.register("compose", "confirmation", {"text": "Booked AI-1.", "claims": ["effect:s1"]})

    config = v4_config(reference_bound_identifiers=True)
    h = new_harness(config, tools={"book_flight": {"latency_ms": 50, "response": {"confirmation": "OK"}}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])
    drain(h, 1_000_000)

    finals = [a for a in h.run_log.emitted_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED
    assert_clean(h)


def test_c10_step_output_identifier_accepted():
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book me a flight to Pune",
        {"act": "new_goal", "intent": "search_and_book", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}], "commit_intent": True},
    )
    provider.register(
        "plan",
        "search_and_book",
        {
            "steps": [
                {"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
                {
                    "local_id": "s2",
                    "tool": "book_flight",
                    "kind": "write",
                    "bindings": {"flight_id": {"type": "step_output", "step_key": "s1", "path": "flights.0.flight_id"}},
                    "after": ["s1"],
                },
            ]
        },
    )
    provider.register("compose", "confirmation", {"text": "Booked it.", "claims": ["effect:s2"]})

    config = v4_config(reference_bound_identifiers=True)
    h = new_harness(
        config,
        tools={
            "search_flights": {"latency_ms": 50, "response": {"flights": [{"flight_id": "AI-7"}]}},
            "book_flight": {"latency_ms": 50, "response": {"confirmation": "OK"}},
        },
        provider=provider,
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book me a flight to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 1_500_000)

    book_calls = [a for a in h.run_log.emitted_actions if a.action_type == ActionType.TOOL_CALL and a.body.tool_name == "book_flight"]
    assert len(book_calls) == 1
    assert book_calls[0].body.arguments["flight_id"] == "AI-7"
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED
    assert_clean(h)
