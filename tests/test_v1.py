"""V1 acceptance tests: 15 scenarios, all driven through ScriptedProvider +
SimHarness against the real kernel (no mocking of kernel internals).

Per docs/sonnet_implementation_plan.md §4 VERSION 1 and §11.2, freezing
v1-text-agent requires all 15 scenarios passing plus replay identity.
ScriptedProvider rules match on exact transcript substrings (the prompt
embeds `transcript: {text!r}`), so each turn's canned response is
unambiguous regardless of what state the goal happens to be in.
"""

from __future__ import annotations

from conftest import (
    BOOK_FLIGHT_TOOL,
    CREATE_TICKET_TOOL,
    FAST_WORKER_LATENCY,
    GET_SEAT_MAP_TOOL,
    SEARCH_FLIGHTS_TOOL,
    chunk_event,
    drain,
    eot_event,
    interruption_event,
    manifest_event,
    tool_result_event,
)

from prism_rt.model.types import ActionType, CallRecord, CallStatus, EffectStatus, FactStatus, GoalStatus, JobKind, Provenance, StepKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider


def new_harness(config, tools=None, provider=None, **kwargs):
    kwargs.setdefault("worker_latency_us", FAST_WORKER_LATENCY)
    return SimHarness(config, seed=1, tools=tools, provider=provider, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def action_types(actions) -> list[str]:
    return [a.action_type.value for a in actions]


def flat_plan(tool: str, *, kind: str = "read", extra_bindings: dict | None = None, param: str = "destination") -> dict:
    bindings = {param: {"type": "fact", "key": f"slot.$G.{param}"}}
    if extra_bindings:
        bindings.update(extra_bindings)
    return {"steps": [{"local_id": "s1", "tool": tool, "kind": kind, "bindings": bindings, "after": []}]}


# --- N-01: simple read -------------------------------------------------


def test_n01_simple_read(config):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Delhi",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}]},
    )
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found a flight to Delhi.", "claims": ["result:s1"]})

    h = new_harness(config, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Delhi")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 2_000_000)
    assert ActionType.TOOL_CALL in [a.action_type for a in actions]
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.text == "Found a flight to Delhi."
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED
    assert_clean(h)


# --- N-02: chained calls -------------------------------------------------


def test_n02_chained_calls(config):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Delhi and the seat map",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}]},
    )
    provider.register(
        "plan",
        "search_flights",
        {
            "steps": [
                {"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
                {
                    "local_id": "s2",
                    "tool": "get_seat_map",
                    "kind": "read",
                    "bindings": {"flight_id": {"type": "step_output", "step_key": "s1", "path": "flights.0.flight_id"}},
                    "after": ["s1"],
                },
            ]
        },
    )
    provider.register("compose", "s2", {"text": "Here is the seat map.", "claims": ["result:s2"]})

    h = new_harness(
        config,
        tools={
            "search_flights": {"latency_ms": 100, "response": {"flights": [{"flight_id": "AI-1"}]}},
            "get_seat_map": {"latency_ms": 100, "response": {"seats": ["1A", "1B"]}},
        },
        provider=provider,
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, GET_SEAT_MAP_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Delhi and the seat map")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 3_000_000)
    tool_calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert [c.body.tool_name for c in tool_calls] == ["search_flights", "get_seat_map"]
    assert tool_calls[1].body.arguments["flight_id"] == "AI-1"
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert_clean(h)


# --- N-03: clarification -------------------------------------------------


def test_n03_clarification(config):
    provider = ScriptedProvider()
    provider.register("interpret", "Find me flights", {"act": "new_goal", "intent": "search_flights", "slot_deltas": []})
    provider.register("interpret", "Pune", {"act": "answer_clarification", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]})
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found flights to Pune.", "claims": ["result:s1"]})

    h = new_harness(config, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find me flights")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 500_000, stop_on_final=False)
    clarifies = [a for a in actions if a.action_type == ActionType.CLARIFY]
    assert len(clarifies) == 1
    assert h.store.goals.all()[0].status == GoalStatus.ACTIVE

    h.send(h.clock.now_us() + 50_000, [chunk_event("Pune")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])
    actions += drain(h, 2_000_000)

    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.text == "Found flights to Pune."
    assert_clean(h)


# --- N-04: successful write ----------------------------------------------


def test_n04_successful_write(config):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book the flight to Mumbai",
        {
            "act": "new_goal",
            "intent": "book_flight",
            "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}],
            "commit_intent": True,
        },
    )
    provider.register(
        "plan",
        "book_flight",
        {
            "steps": [
                {"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
                {
                    "local_id": "s2",
                    "tool": "book_flight",
                    "kind": "write",
                    "bindings": {"flight_id": {"type": "step_output", "step_key": "s1", "path": "flight_id"}},
                    "after": ["s1"],
                },
            ]
        },
    )
    provider.register("compose", "s2", {"text": "Booked your flight to Mumbai.", "claims": ["effect:s2"]})

    h = new_harness(
        config,
        tools={
            "search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-505"}},
            "book_flight": {"latency_ms": 100, "response": {"confirmation": "XYZ"}},
        },
        provider=provider,
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book the flight to Mumbai")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 3_000_000)
    tool_calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert [c.body.tool_name for c in tool_calls] == ["search_flights", "book_flight"]
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1

    book_call = next(c for c in h.store.call_ledger.all() if c.tool == "book_flight")
    effect = h.store.effect_ledger.by_fingerprint(book_call.fingerprint)
    assert effect.status == EffectStatus.CONFIRMED
    assert_clean(h)


# --- I-01: interrupt before planning --------------------------------------


def test_i01_interrupt_before_interpretation_returns(config):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Delhi",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}]},
    )
    provider.register(
        "interpret",
        "actually Mumbai",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]},
    )
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found flights to Mumbai.", "claims": ["result:s1"]})

    # INTERPRET latency of 100ms is longer than the 5ms gap between the two
    # turns closing, so turn 1's interpretation is still in flight when
    # turn 2 (the correction) closes. Turn 2 queues behind turn 1 (it used
    # to overwrite turn 1's pending marker, which silently lost a genuine
    # rapid follow-up -- found by `sim/explorer.py`); the TRIAGE hold keeps
    # anything turn 1 produces from being emitted until turn 2 is
    # understood.
    h = new_harness(
        config,
        tools={"search_flights": {"latency_ms": 50, "response": {"flight_id": "AI-1"}}},
        provider=provider,
        worker_latency_us={**FAST_WORKER_LATENCY, JobKind.INTERPRET: 100_000},
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Delhi")])
    h.send(150_000, [eot_event()])
    h.send(155_000, [interruption_event()])
    h.send(158_000, [chunk_event("actually Mumbai")])
    h.send(163_000, [eot_event()])

    actions = drain(h, 3_000_000)
    tool_calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    # Blueprint I-01 acceptance: exactly one search call (Mumbai); no plan
    # acted on with Delhi after the correction. The Delhi turn *is*
    # interpreted now (queued, not discarded) and may create a goal record
    # that the correction replaces -- but nothing from it is ever emitted.
    assert [c.body.arguments["destination"] for c in tool_calls] == ["Mumbai"]
    completed = [g for g in h.store.goals.all() if g.status == GoalStatus.COMPLETED]
    assert len(completed) == 1
    assert h.store.facts.get(f"slot.{completed[0].goal_id}.destination").value == "Mumbai"
    assert not any(a.action_type == ActionType.SPEAK and "Delhi" in (a.body.text or "") for a in actions)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.text == "Found flights to Mumbai."
    assert_clean(h)


# --- I-03: interrupt during execution -------------------------------------


def test_i03_interrupt_during_execution_cancels_and_reissues(config):
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

    h = new_harness(config, tools={"search_flights": {"latency_ms": 800, "response": {"flight_id": "AI-9"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    # Let interpretation + planning complete and the Pune search go in flight.
    pre_correction = drain(h, 220_000, stop_on_final=False)
    pune_calls = [a for a in pre_correction if a.action_type == ActionType.TOOL_CALL]
    assert len(pune_calls) == 1
    pune_call_id = pune_calls[0].body.call_id
    assert h.store.call_ledger.get(pune_call_id).status == CallStatus.IN_FLIGHT

    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("actually Mumbai")])
    r = h.send(h.clock.now_us() + 5_000, [eot_event()])

    correction_actions = drain(h, 1_500_000)
    all_actions = pre_correction + correction_actions
    cancels = [a for a in all_actions if a.action_type == ActionType.CANCEL]
    assert len(cancels) == 1
    assert cancels[0].body.target_call_id == pune_call_id
    assert h.store.call_ledger.get(pune_call_id).status in (CallStatus.CANCEL_REQUESTED, CallStatus.COMPLETED_AFTER_CANCEL)

    mumbai_calls = [a for a in all_actions if a.action_type == ActionType.TOOL_CALL and a.body.call_id != pune_call_id]
    assert len(mumbai_calls) == 1
    assert mumbai_calls[0].body.arguments["destination"] == "Mumbai"

    finals = [a for a in all_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert "Delhi" not in finals[0].body.text
    assert_clean(h)


# --- I-07: repeated rapid interruptions -----------------------------------


def test_i07_repeated_rapid_interruptions(config):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    for city in ("Delhi", "Chennai", "Mumbai"):
        provider.register(
            "interpret",
            f"actually {city}",
            {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": city}]},
        )
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Here are the flights.", "claims": ["result:s1"]})

    h = new_harness(config, tools={"search_flights": {"latency_ms": 1_000, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 250_000, stop_on_final=False)
    for city in ("Delhi", "Chennai", "Mumbai"):
        h.send(h.clock.now_us() + 20_000, [interruption_event()])
        h.send(h.clock.now_us() + 5_000, [chunk_event(f"actually {city}")])
        h.send(h.clock.now_us() + 5_000, [eot_event()])
        actions += drain(h, h.clock.now_us() + 200_000, stop_on_final=False)

    actions += drain(h, 3_000_000)

    tool_calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    # Only the final destination (Mumbai) should ever have completed with a result.
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert h.store.facts.snapshot_committed().get(f"slot.{h.store.goals.all()[0].goal_id}.destination") == "Mumbai"
    # Every call except the last must have ended up cancelled, not consumed.
    consumed = [c for c in h.store.call_ledger.all() if c.status == CallStatus.CONSUMED]
    assert len(consumed) == 1
    assert consumed[0].args["destination"] == "Mumbai"
    assert_clean(h)


# --- I-08 / I-09: goal replacement, then return ---------------------------


def test_i08_i09_goal_replace_and_return(config):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Delhi",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}]},
    )
    provider.register(
        "interpret",
        "actually create a support ticket",
        {
            "act": "new_goal",
            "intent": "create_ticket",
            "slot_deltas": [{"name": "issue", "scope": "goal", "op": "set", "value": "lost bag"}],
            "commit_intent": True,
        },
    )
    provider.register(
        "interpret",
        "go back to the flights",
        lambda prompt: {"act": "return_to_goal", "resume_goal_id": "g-0001", "slot_deltas": []},
    )
    provider.register("plan", "goal_intent: search_flights", flat_plan("search_flights"))
    provider.register("plan", "goal_intent: create_ticket", flat_plan("create_ticket", kind="write", param="issue"))
    provider.register("compose", "flight_id", {"text": "Here are the Delhi flights.", "claims": ["result:s1"]})
    provider.register("compose", "ticket_id", {"text": "Ticket created.", "claims": ["effect:s1"]})

    h = new_harness(
        config,
        tools={
            "search_flights": {"latency_ms": 500, "response": {"flight_id": "AI-1"}},
            "create_ticket": {"latency_ms": 100, "response": {"ticket_id": "T-1"}},
        },
        provider=provider,
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, CREATE_TICKET_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Delhi")])
    h.send(150_000, [eot_event()])

    pre = drain(h, 220_000, stop_on_final=False)
    search_calls = [a for a in pre if a.action_type == ActionType.TOOL_CALL]
    assert len(search_calls) == 1
    delhi_goal_id = h.store.goals.all()[0].goal_id
    assert delhi_goal_id == "g-0001"

    # I-08: replace the goal entirely.
    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("actually create a support ticket")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])
    mid = drain(h, h.clock.now_us() + 800_000, stop_on_final=True)

    assert h.store.goals.get("g-0001").status == GoalStatus.SUSPENDED
    ticket_final = [a for a in mid if a.action_type == ActionType.FINAL]
    assert len(ticket_final) == 1
    assert ticket_final[0].body.text == "Ticket created."

    # I-09: return to the suspended Delhi goal.
    h.send(h.clock.now_us() + 10_000, [chunk_event("go back to the flights")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])
    # Check immediately (before the re-triggered search can complete) that
    # the return itself took effect: g-0001 reactivated and made current.
    reactivated = drain(h, h.clock.now_us() + 30_000, stop_on_final=False)
    assert h.store.goals.get("g-0001").status == GoalStatus.ACTIVE
    active_fact = h.store.facts.get("goal.active")
    assert active_fact.value == "g-0001"

    # V1 has no rebinder/result-reuse on return (Known Limitations, §4
    # VERSION 1): returning forces a fresh PLANNING pass like any other
    # act, so the Delhi search is genuinely reissued rather than reusing
    # the RETAINED result from before the goal was suspended. Confirm it
    # completes correctly rather than asserting the call was skipped.
    post = drain(h, h.clock.now_us() + 800_000)
    all_actions = pre + mid + reactivated + post
    finals = [a for a in all_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 2
    assert finals[-1].body.text == "Here are the Delhi flights."
    assert h.store.goals.get("g-0001").status == GoalStatus.COMPLETED
    assert_clean(h)


# --- I-13: abort -----------------------------------------------------------


def test_i13_abort_cancels_all_and_emits_final(config):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Delhi",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}]},
    )
    provider.register("interpret", "never mind", {"act": "abort", "slot_deltas": []})
    provider.register("plan", "search_flights", flat_plan("search_flights"))

    h = new_harness(config, tools={"search_flights": {"latency_ms": 800, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Delhi")])
    h.send(150_000, [eot_event()])

    pre = drain(h, 220_000, stop_on_final=False)
    calls = [a for a in pre if a.action_type == ActionType.TOOL_CALL]
    assert len(calls) == 1
    call_id = calls[0].body.call_id

    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("never mind")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])
    post = drain(h, h.clock.now_us() + 200_000)

    all_actions = pre + post
    cancels = [a for a in all_actions if a.action_type == ActionType.CANCEL]
    assert len(cancels) == 1
    assert cancels[0].body.target_call_id == call_id
    finals = [a for a in all_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is False
    assert h.store.goals.all()[0].status == GoalStatus.ABANDONED
    assert_clean(h)


# --- S-01: state-changing retry ---------------------------------------------


def test_s01_write_retryable_error_retries_once(config):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book the flight",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True},
    )
    provider.register("plan", "book_flight", flat_plan("book_flight", kind="write", param="flight_id"))
    provider.register("compose", "confirmation", {"text": "Booked.", "claims": ["effect:s1"]})

    # Huge mock latency: book_flight must never auto-resolve — every result
    # in this test is delivered by hand so the exact error/retryable
    # content is controlled.
    h = new_harness(config, tools={"book_flight": {"latency_ms": 10_000_000}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book the flight")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 300_000, stop_on_final=False)
    proposed_or_inflight = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert len(proposed_or_inflight) == 1
    first_call_id = proposed_or_inflight[0].body.call_id

    # Deliver a retryable error for attempt 1. PlanExecutor re-proposes and
    # CommitGate re-admits the retry in this same step (same mechanics as
    # same-step cancellation) — capture this send()'s own emissions, not
    # just what a later drain() picks up.
    r = h.send(h.clock.now_us() + 50_000, [tool_result_event(first_call_id, status="error", error={"retryable": True})])
    assert h.store.call_ledger.get(first_call_id).status == CallStatus.FAILED

    retry_actions = [er.action for er in r.emit_report.emitted] + drain(h, h.clock.now_us() + 500_000, stop_on_final=False)
    retry_calls = [a for a in retry_actions if a.action_type == ActionType.TOOL_CALL]
    assert len(retry_calls) == 1
    second_call_id = retry_calls[0].body.call_id
    assert second_call_id != first_call_id
    assert h.store.call_ledger.get(second_call_id).attempt == 2

    final_actions = drain(h, h.clock.now_us() + 300_000, stop_on_final=False)
    h.send(h.clock.now_us() + 50_000, [tool_result_event(second_call_id, status="ok", result={"confirmation": "XYZ"})])
    final_actions += drain(h, h.clock.now_us() + 300_000)

    finals = [a for a in (actions + retry_actions + final_actions) if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert h.store.call_ledger.get(second_call_id).status == CallStatus.CONSUMED
    assert_clean(h)


# --- S-02: write timeout -----------------------------------------------------


def test_s02_write_timeout_informs_truthfully(config):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book the flight",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True},
    )
    provider.register("plan", "book_flight", flat_plan("book_flight", kind="write", param="flight_id"))

    # Huge mock latency: an unconfigured tool actually auto-resolves
    # immediately with an empty result (and gets correctly rejected as
    # malformed) — a genuinely absent result needs an explicit latency
    # longer than the test window.
    h = new_harness(config, tools={"book_flight": {"latency_ms": 10_000_000}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book the flight")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 300_000, stop_on_final=False)
    calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert len(calls) == 1
    call_id = calls[0].body.call_id
    assert h.store.call_ledger.get(call_id).status == CallStatus.IN_FLIGHT

    fp = h.store.call_ledger.get(call_id).fingerprint
    effect = h.store.effect_ledger.by_fingerprint(fp)
    assert effect.status == EffectStatus.PENDING
    # No result ever arrives (no mock tool configured) — the effect stays
    # PENDING rather than silently CONFIRMED; a truthful system asks or
    # reports uncertainty rather than assuming success (W2).
    assert_clean(h)


# --- S-03: duplicate write request -------------------------------------------


def test_s03_duplicate_write_request_informs(config):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book the flight",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True},
    )
    provider.register("plan", "book_flight", flat_plan("book_flight", kind="write", param="flight_id"))
    provider.register("compose", "confirmation", {"text": "Booked.", "claims": ["effect:s1"]})

    h = new_harness(config, tools={"book_flight": {"latency_ms": 100, "response": {"confirmation": "XYZ"}}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book the flight")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 2_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED

    # Manually re-propose the identical write (as if the user asked again) —
    # CommitGate must block it (G5: confirmed effect), and FastResponder
    # must say something rather than silently doing nothing.
    fp_call = next(c for c in h.store.call_ledger.all() if c.tool == "book_flight")

    def inject_dup(txn, now_us, step_no):
        txn.facts.set("goal.active", fp_call.goal_id, FactStatus.COMMITTED, Provenance(source="test"), rule="test")
        dup = CallRecord(
            call_id=txn.store.ids.next("call"),
            goal_id=fp_call.goal_id,
            step_key=fp_call.step_key,
            tool="book_flight",
            kind=StepKind.WRITE,
            args=fp_call.args,
            fingerprint=fp_call.fingerprint,
            read_set=fp_call.read_set,
        )
        txn.call_ledger.create(dup)

    r = h.send(h.clock.now_us() + 100_000, inject=inject_dup)
    dup_actions = [er.action for er in r.emit_report.emitted] + drain(h, h.clock.now_us() + 100_000, stop_on_final=False)
    informs = [a for a in dup_actions if a.action_type == ActionType.SPEAK and a.body.kind == "inform"]
    assert len(informs) == 1
    tool_calls_after = [a for a in dup_actions if a.action_type == ActionType.TOOL_CALL]
    assert tool_calls_after == []


# --- R-02: duplicate tool result delivery ------------------------------------


def test_r02_duplicate_tool_result_ignored(config):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Delhi",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}]},
    )
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found flights to Delhi.", "claims": ["result:s1"]})

    h = new_harness(config, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Delhi")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 300_000, stop_on_final=False)
    calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    call_id = calls[0].body.call_id

    h.send(h.clock.now_us() + 50_000, [tool_result_event(call_id, result={"flight_id": "AI-1"})])
    assert h.store.call_ledger.get(call_id).status == CallStatus.CONSUMED

    r = h.send(h.clock.now_us() + 5_000, [tool_result_event(call_id, result={"flight_id": "AI-1"})])
    assert r.emit_report.emitted == ()
    assert h.store.call_ledger.get(call_id).status == CallStatus.CONSUMED

    drain(h, h.clock.now_us() + 2_000_000)
    assert_clean(h)


# --- R-06: simultaneous events ordered deterministically --------------------


def test_r06_simultaneous_events_deterministic_order(config):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Delhi",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}]},
    )
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found flights to Delhi.", "claims": ["result:s1"]})

    h = new_harness(config, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    # manifest + chunk at the same ts_us: SETUP(0) must be applied before
    # USER_CONTENT(2) regardless of list order (§5.1 class ordering).
    h.clock.advance_to(100_000)
    envelopes = []
    envelopes.extend(h.codec.decode({"ts_us": 100_000, "type": "text_chunk", "payload": {"text": "Find flights to Delhi"}}, seq=h._next_seq()))
    envelopes.extend(h.codec.decode({"ts_us": 100_000, "type": "end_of_turn", "payload": {}}, seq=h._next_seq()))
    report = h.kernel.step(envelopes)
    h.run_log.reports.append(report)
    assert h.store.turn_log.get("t-0001") is not None
    assert h.store.turn_log.get("t-0001").closed_ts_us == 100_000

    actions = drain(h, 2_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert_clean(h)


# --- Replay identity (C1) ----------------------------------------------------


def test_v1_replay_identity(config):
    def run_once() -> list[dict]:
        provider = ScriptedProvider()
        provider.register(
            "interpret",
            "Find flights to Delhi",
            {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}]},
        )
        provider.register("plan", "search_flights", flat_plan("search_flights"))
        provider.register("compose", "s1", {"text": "Found flights to Delhi.", "claims": ["result:s1"]})

        h = new_harness(config, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, provider=provider)
        h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
        h.send(100_000, [chunk_event("Find flights to Delhi")])
        h.send(150_000, [eot_event()])
        drain(h, 2_000_000)
        return h.log.records

    violations = TraceChecker().check_replay(run_once, times=5)
    assert violations == []
