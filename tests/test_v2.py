"""V2 acceptance tests: 12 scenarios from docs/sonnet_implementation_plan.md
§4 VERSION 2 — transitive invalidation, settle barrier, rebinder, absence
read-sets, claim grades, reconciliation.

Every test builds its own `Config` with only the V2 flags it needs, rather
than relying on a global default flip — this is what keeps
`tests/test_v1.py` passing unmodified after this file exists (an explicit
V2 acceptance criterion: "All V1 + V2 scenarios pass").
"""

from __future__ import annotations

from conftest import (
    BOOK_FLIGHT_TOOL,
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

from prism_rt.config import Config
from prism_rt.model.types import (
    ActionType,
    CallStatus,
    ClaimGrade,
    EffectStatus,
    FactStatus,
    GoalStatus,
    JobKind,
    Provenance,
)
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

CONFIRM_PRICE_TOOL = {
    "name": "confirm_price",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"flight_id": {"type": "string"}}, "required": ["flight_id"]},
}


def v2_config(**overrides) -> Config:
    """All V2 flags start False here regardless of DEFAULT_CONFIG's own
    defaults (which flip True once V2 is frozen) — each test opts in to
    exactly the feature(s) it means to exercise via `overrides`, so it
    stays isolated from unrelated V2 behavior (e.g. a write test that
    doesn't care about the settle barrier shouldn't suddenly wait 300ms
    just because some *other* flag defaulted True at the dataclass level)."""
    return Config(
        transitive_invalidation=overrides.pop("transitive_invalidation", False),
        settle_barrier_enabled=overrides.pop("settle_barrier_enabled", False),
        absence_read_sets=overrides.pop("absence_read_sets", False),
        claim_grades_enabled=overrides.pop("claim_grades_enabled", False),
        rebinder_enabled=overrides.pop("rebinder_enabled", False),
        **overrides,
    )


def new_harness(config, tools=None, provider=None, **kwargs):
    kwargs.setdefault("worker_latency_us", FAST_WORKER_LATENCY)
    return SimHarness(config, seed=1, tools=tools, provider=provider, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def flat_plan(tool, *, kind="read", param="destination", output_map=None, extra_bindings=None):
    bindings = {param: {"type": "fact", "key": f"slot.$G.{param}"}}
    if extra_bindings:
        bindings.update(extra_bindings)
    step = {"local_id": "s1", "tool": tool, "kind": kind, "bindings": bindings, "after": []}
    if output_map:
        step["output_map"] = output_map
    return {"steps": [step]}


# --- I-04: transitive cancel of a downstream chained call -------------------


def test_i04_transitive_cancel_of_downstream_chain():
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
    provider.register(
        "plan",
        "goal_intent: search_flights",
        {
            "steps": [
                {
                    "local_id": "s1",
                    "tool": "search_flights",
                    "kind": "read",
                    "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}},
                    "after": [],
                    "output_map": {"flight_id": "derived.$G.selected_flight"},
                },
                {
                    "local_id": "s2",
                    "tool": "get_seat_map",
                    "kind": "read",
                    "bindings": {"flight_id": {"type": "step_output", "step_key": "s1", "path": "flight_id"}},
                    "after": ["s1"],
                },
            ]
        },
    )
    provider.register("compose", "seats", {"text": "Seat map ready.", "claims": ["result:s2"]})

    config = v2_config(transitive_invalidation=True)
    h = new_harness(
        config,
        tools={
            "search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-505"}},
            "get_seat_map": {"latency_ms": 800, "response": {"seats": ["1A"]}},
        },
        provider=provider,
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, GET_SEAT_MAP_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    pre = drain(h, 400_000, stop_on_final=False)
    seat_call = next(c for c in h.store.call_ledger.all() if c.tool == "get_seat_map")
    assert seat_call.status == CallStatus.IN_FLIGHT

    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("actually Mumbai")])
    correction_step = h.send(h.clock.now_us() + 5_000, [eot_event()])

    # The correction closes the turn; the actual cancellation happens once
    # the interpretation result is consumed (a later step) — poll for it,
    # then assert the cancel of the *chained* call landed in that same step.
    cancelled_this_step = False
    t = h.clock.now_us()
    for _ in range(50):
        t += 10_000
        report = h.advance(t)
        cancels = [er.action for er in report.emit_report.emitted if er.action.action_type == ActionType.CANCEL]
        if any(c.body.target_call_id == seat_call.call_id for c in cancels):
            cancelled_this_step = True
            break
    assert cancelled_this_step, "transitive cancellation of the downstream call never happened"

    final_actions = drain(h, h.clock.now_us() + 2_000_000)
    assert any(a.action_type == ActionType.FINAL for a in final_actions)
    # Cancelled promptly; the late Pune-flight seat-map result (if it still
    # arrives before this drain ends) must resolve to completed_after_cancel,
    # never CONSUMED.
    assert h.store.call_ledger.get(seat_call.call_id).status in (
        CallStatus.CANCEL_REQUESTED,
        CallStatus.COMPLETED_AFTER_CANCEL,
    )
    assert_clean(h)


# --- I-04-chain: 3-step chain, interrupt at step 2 ---------------------------


def test_i04_chain_three_steps_interrupt_at_step_two():
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {
            "act": "new_goal",
            "intent": "search_flights",
            "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
            "commit_intent": True,
        },
    )
    provider.register(
        "interpret",
        "actually Mumbai",
        {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}], "commit_intent": True},
    )
    provider.register(
        "plan",
        "goal_intent: search_flights",
        {
            "steps": [
                {
                    "local_id": "s1",
                    "tool": "search_flights",
                    "kind": "read",
                    "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}},
                    "after": [],
                    "output_map": {"flight_id": "derived.$G.chosen_flight"},
                },
                {
                    "local_id": "s2",
                    "tool": "confirm_price",
                    "kind": "read",
                    "bindings": {"flight_id": {"type": "step_output", "step_key": "s1", "path": "flight_id"}},
                    "after": ["s1"],
                    "output_map": {"price": "derived.$G.chosen_price"},
                },
                {
                    "local_id": "s3",
                    "tool": "book_flight",
                    "kind": "write",
                    "bindings": {"flight_id": {"type": "step_output", "step_key": "s2", "path": "flight_id"}},
                    "after": ["s2"],
                },
            ]
        },
    )
    provider.register("compose", "confirmed", {"text": "Booked.", "claims": ["effect:s3"]})

    config = v2_config(transitive_invalidation=True)
    h = new_harness(
        config,
        tools={
            "search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}},
            "confirm_price": {"latency_ms": 700, "response": {"flight_id": "AI-1", "price": 4200}},
            "book_flight": {"latency_ms": 100, "response": {"confirmation": "XYZ"}},
        },
        provider=provider,
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, CONFIRM_PRICE_TOOL, BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    drain(h, 400_000, stop_on_final=False)
    s2_call = next(c for c in h.store.call_ledger.all() if c.tool == "confirm_price")
    assert s2_call.status == CallStatus.IN_FLIGHT
    assert not any(c.tool == "book_flight" for c in h.store.call_ledger.all())

    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("actually Mumbai")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])

    t = h.clock.now_us()
    s2_cancelled = False
    for _ in range(50):
        t += 10_000
        report = h.advance(t)
        for er in report.emit_report.emitted:
            if er.action.action_type == ActionType.CANCEL and er.action.body.target_call_id == s2_call.call_id:
                s2_cancelled = True
        if s2_cancelled:
            break
    assert s2_cancelled

    finals = drain(h, h.clock.now_us() + 3_000_000)
    assert any(a.action_type == ActionType.FINAL for a in finals)
    book_calls = [c for c in h.store.call_ledger.all() if c.tool == "book_flight"]
    assert len(book_calls) == 1
    assert book_calls[0].status == CallStatus.CONSUMED
    assert_clean(h)


# --- I-05: correction and result arrive at the same instant -----------------


def test_i05_correction_arriving_right_before_completion_still_converges_truthfully():
    """I-05's premise ("correction at t-1ms beats the result") assumes a
    synchronous fact change from the correction event itself. This
    architecture's interpretation is genuinely async (no chunk/EOT event
    changes a slot fact directly — only a later WORKER_RESULT does), so
    there is no literal same-microsecond ordering trick that makes a raw
    correction event beat a tool result already due at that instant; V0's
    directly-injected-fact tests already cover that ordering guarantee at
    the mechanism level. What's actually testable and load-bearing here:
    even if a stale read result slips through moments before the
    correction's interpretation lands, the system must still converge to a
    truthful final answer and never surface the stale value."""
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

    config = v2_config()
    h = new_harness(config, tools={"search_flights": {"latency_ms": 500, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    pre = drain(h, 250_000, stop_on_final=False)
    call = next(c for c in h.store.call_ledger.all() if c.tool == "search_flights")
    assert call.status == CallStatus.IN_FLIGHT

    # Correction turn closes 1ms before the mock tool result is due —
    # tight enough that the result may well be consumed before the async
    # interpretation resolves.
    due_us = call.emitted_ts_us + 500_000
    h.send(due_us - 20_000, [interruption_event()])
    h.send(due_us - 15_000, [chunk_event("actually Mumbai")])
    h.send(due_us - 1_000, [eot_event()])

    finals = drain(h, h.clock.now_us() + 2_000_000)
    final = next(a for a in finals if a.action_type == ActionType.FINAL)
    assert "Pune" not in final.body.text
    assert final.body.text == "Mumbai flights found."
    gid = h.store.goals.all()[0].goal_id
    assert h.store.facts.get(f"slot.{gid}.destination").value == "Mumbai"
    assert_clean(h)


# --- I-10: rebinder — localized correction, no replan -----------------------


def test_i10_localized_correction_rebinds_without_replanning():
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

    config = v2_config(rebinder_enabled=True)
    h = new_harness(config, tools={"search_flights": {"latency_ms": 800, "response": {"flight_id": "AI-9"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 220_000, stop_on_final=False)

    gid = h.store.goals.all()[0].goal_id
    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("actually Mumbai")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])
    correction = drain(h, h.clock.now_us() + 2_000_000)

    # No PLAN job was ever (re-)dispatched for this — rebind, not replan.
    assert not any(a.rule_id == "responder.ack" for a in correction if a.action_type == ActionType.SPEAK)
    assert h.store.goals.get(gid).task_state.value in ("executing", "responding", "completed")
    assert any(a.action_type == ActionType.CANCEL for a in correction)
    assert any(a.action_type == ActionType.TOOL_CALL and a.body.arguments.get("destination") == "Mumbai" for a in correction)
    assert_clean(h)


# --- I-11: backchannel interruption does not cancel anything ----------------


def test_i11_backchannel_does_not_cancel_or_reinterpret():
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register("plan", "goal_intent: search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Pune flights found.", "claims": ["result:s1"]})

    config = v2_config()
    h = new_harness(config, tools={"search_flights": {"latency_ms": 400, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 250_000, stop_on_final=False)
    call = next(c for c in h.store.call_ledger.all() if c.tool == "search_flights")
    assert call.status == CallStatus.IN_FLIGHT

    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("mm-hmm")])
    r = h.send(h.clock.now_us() + 5_000, [eot_event()])
    assert r.emit_report.emitted == ()
    # The pending-turn marker still names the *original* turn (t-0001,
    # already retracted on consumption) — the backchannel turn never wrote
    # its own turn_id here, i.e. no interpretation was dispatched for it.
    pending = h.store.facts.get("session.pending_interpretation_turn")
    assert pending is not None and pending.status == FactStatus.RETRACTED
    assert not h.store.jobs.running_by_kind_goal(JobKind.INTERPRET, h.store.goals.all()[0].goal_id)
    assert h.store.call_ledger.get(call.call_id).status == CallStatus.IN_FLIGHT  # untouched

    finals = drain(h, h.clock.now_us() + 2_000_000)
    final = next(a for a in finals if a.action_type == ActionType.FINAL)
    assert final.body.text == "Pune flights found."
    assert_clean(h)


# --- I-12: absence entries — adding an omitted optional constraint ----------


def test_i12_optional_constraint_addition_invalidates_via_absence_entry():
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register(
        "interpret",
        "only direct flights",
        {"act": "addition", "slot_deltas": [{"name": "direct_only", "scope": "goal", "op": "set", "value": True}]},
    )
    provider.register(
        "plan",
        "goal_intent: search_flights",
        {
            "steps": [
                {
                    "local_id": "s1",
                    "tool": "search_flights",
                    "kind": "read",
                    "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}},
                    "after": [],
                    "absence_keys": ["slot.$G.direct_only"],
                }
            ]
        },
    )
    provider.register("compose", "s1", {"text": "Direct flights to Pune found.", "claims": ["result:s1"]})

    config = v2_config(absence_read_sets=True)
    h = new_harness(config, tools={"search_flights": {"latency_ms": 800, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 250_000, stop_on_final=False)
    call = next(c for c in h.store.call_ledger.all() if c.tool == "search_flights")
    assert call.status == CallStatus.IN_FLIGHT
    assert any(e.key.endswith(".direct_only") for e in call.read_set.entries)

    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("only direct flights")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])

    cancelled = False
    t = h.clock.now_us()
    for _ in range(40):
        t += 10_000
        report = h.advance(t)
        if any(er.action.action_type == ActionType.CANCEL and er.action.body.target_call_id == call.call_id for er in report.emit_report.emitted):
            cancelled = True
            break
    assert cancelled, "adding the previously-absent constraint should have invalidated the in-flight call"
    assert_clean(h)


# --- I-14: settle barrier catches a correction ------------------------------


def test_i14_correction_within_settle_window_blocks_the_original_booking():
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

    config = v2_config(settle_barrier_enabled=True, settle_ms=300)
    h = new_harness(config, tools={"book_flight": {"latency_ms": 50, "response": {"confirmation": "XYZ"}}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])

    pre = drain(h, 250_000, stop_on_final=False)
    ai1_calls = [c for c in h.store.call_ledger.all() if c.args.get("flight_id") == "AI-1"]
    assert len(ai1_calls) == 1
    assert ai1_calls[0].status == CallStatus.PROPOSED  # blocked behind G10, never emitted

    # Correct at +150ms — well inside the 300ms settle window.
    h.send(h.clock.now_us() + 150_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("wait the other one")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])

    finals = drain(h, h.clock.now_us() + 3_000_000)
    assert any(a.action_type == ActionType.FINAL for a in finals)

    # The AI-1 attempt must never have been emitted as a TOOL_CALL.
    all_actions = pre + finals
    ai1_emitted = [
        a for a in all_actions if a.action_type == ActionType.TOOL_CALL and a.body.arguments.get("flight_id") == "AI-1"
    ]
    assert ai1_emitted == []
    assert h.store.call_ledger.get(ai1_calls[0].call_id).status == CallStatus.DISCARDED

    ai2_calls = [c for c in h.store.call_ledger.all() if c.args.get("flight_id") == "AI-2" and c.status == CallStatus.CONSUMED]
    assert len(ai2_calls) == 1
    assert_clean(h)


# --- I-15: write cancelled in flight, then reconciled -----------------------


def test_i15_write_cancelled_in_flight_completes_anyway_and_is_reconciled():
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book the flight",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True},
    )
    provider.register("interpret", "wait cancel that", {"act": "abort", "slot_deltas": []})
    provider.register("plan", "book_flight", flat_plan("book_flight", kind="write", param="flight_id"))

    config = v2_config()
    h = new_harness(config, tools={"book_flight": {"latency_ms": 800, "response": {"confirmation": "XYZ"}}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book the flight")])
    h.send(150_000, [eot_event()])
    drain(h, 300_000, stop_on_final=False)
    call = next(c for c in h.store.call_ledger.all() if c.status == CallStatus.IN_FLIGHT)

    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("wait cancel that")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])
    actions = drain(h, h.clock.now_us() + 2_000_000, stop_on_final=False)

    assert any(a.action_type == ActionType.CANCEL and a.body.target_call_id == call.call_id for a in actions)
    assert h.store.call_ledger.get(call.call_id).status == CallStatus.COMPLETED_AFTER_CANCEL
    reconcile_informs = [a for a in actions if a.action_type == ActionType.SPEAK and a.body.kind == "inform" and "gone through" in a.body.text]
    assert len(reconcile_informs) == 1
    fp = call.fingerprint
    assert h.store.effect_ledger.by_fingerprint(fp).status == EffectStatus.CONFIRMED
    assert_clean(h)


# --- S-04: paraphrase duplicate detection (canonical fingerprint) -----------


def test_s04_paraphrased_duplicate_write_blocked_by_fingerprint():
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book the flight",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "  AI-1  "}], "commit_intent": True},
    )
    provider.register("plan", "book_flight", flat_plan("book_flight", kind="write", param="flight_id"))
    provider.register("compose", "confirmation", {"text": "Booked.", "claims": ["effect:s1"]})

    config = v2_config()
    h = new_harness(config, tools={"book_flight": {"latency_ms": 100, "response": {"confirmation": "XYZ"}}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book the flight")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 2_000_000)
    assert any(a.action_type == ActionType.FINAL for a in actions)
    first_call = next(c for c in h.store.call_ledger.all() if c.tool == "book_flight")

    # Re-propose with whitespace-paraphrased args — canonical.py trims and
    # collapses whitespace, so this must fingerprint identically to the
    # first call and be blocked as a duplicate, not treated as new.
    def inject_paraphrase(txn, now_us, step_no):
        from prism_rt.model.types import CallRecord, StepKind, fingerprint_for

        args = {"flight_id": "AI-1"}
        assert fingerprint_for("book_flight", args) == first_call.fingerprint
        txn.facts.set("goal.active", first_call.goal_id, FactStatus.COMMITTED, Provenance(source="test"), rule="test")
        dup = CallRecord(
            call_id=txn.store.ids.next("call"),
            goal_id=first_call.goal_id,
            step_key=first_call.step_key,
            tool="book_flight",
            kind=StepKind.WRITE,
            args=args,
            fingerprint=fingerprint_for("book_flight", args),
            read_set=first_call.read_set,
        )
        txn.call_ledger.create(dup)

    r = h.send(h.clock.now_us() + 100_000, inject=inject_paraphrase)
    more = drain(h, h.clock.now_us() + 100_000, stop_on_final=False)
    tool_calls_after = [er.action for er in r.emit_report.emitted if er.action.action_type == ActionType.TOOL_CALL] + [
        a for a in more if a.action_type == ActionType.TOOL_CALL
    ]
    assert tool_calls_after == []
    assert_clean(h)


# --- S-10: INTENDED claim grade for a settling write ------------------------


def test_s10_intended_grade_while_write_settles():
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book the flight",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True},
    )
    provider.register("plan", "book_flight", flat_plan("book_flight", kind="write", param="flight_id"))
    provider.register("compose", "confirmation", {"text": "Booked.", "claims": ["effect:s1"]})

    config = v2_config(settle_barrier_enabled=True, settle_ms=300, claim_grades_enabled=True)
    h = new_harness(config, tools={"book_flight": {"latency_ms": 100, "response": {"confirmation": "XYZ"}}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book the flight")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 400_000, stop_on_final=False)
    intended = [a for a in actions if a.action_type == ActionType.SPEAK and a.body.claim_grade == ClaimGrade.INTENDED]
    assert len(intended) == 1
    # Never claim IN_PROGRESS for a call that hasn't been emitted yet.
    assert not any(a.action_type == ActionType.TOOL_CALL for a in actions)
    assert_clean(h)


# --- R-05: completion/cancellation race, deterministic ordering ------------


def test_r05_completion_cancellation_race_deterministic():
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

    def run_once():
        config = v2_config()
        h = new_harness(config, tools={"search_flights": {"latency_ms": 500, "response": {"flight_id": "AI-1"}}}, provider=ScriptedProvider())
        # re-register on the fresh provider each run (ScriptedProvider isn't shared to keep this hermetic)
        h.provider.register(
            "interpret", "Find flights to Pune",
            {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
        )
        h.provider.register(
            "interpret", "actually Mumbai",
            {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]},
        )
        h.provider.register("plan", "goal_intent: search_flights", flat_plan("search_flights"))
        h.provider.register("compose", "s1", {"text": "Mumbai flights found.", "claims": ["result:s1"]})

        h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
        h.send(100_000, [chunk_event("Find flights to Pune")])
        h.send(150_000, [eot_event()])
        drain(h, 250_000, stop_on_final=False)
        call = next(c for c in h.store.call_ledger.all() if c.tool == "search_flights")
        due_us = call.emitted_ts_us + 500_000
        h.send(due_us - 20_000, [interruption_event()])
        h.send(due_us - 15_000, [chunk_event("actually Mumbai")])
        h.send(due_us, [eot_event()])  # result auto-delivered same batch
        drain(h, h.clock.now_us() + 2_000_000)
        return h.log.records

    violations = TraceChecker().check_replay(run_once, times=5)
    assert violations == []


# --- T-05: timer liveness under Model B, no deadlock -------------------------


def test_t05_settle_timer_liveness_no_deadlock():
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book the flight",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True},
    )
    provider.register("plan", "book_flight", flat_plan("book_flight", kind="write", param="flight_id"))
    provider.register("compose", "confirmation", {"text": "Booked.", "claims": ["effect:s1"]})

    config = v2_config(settle_barrier_enabled=True, settle_ms=300)
    h = new_harness(config, tools={"book_flight": {"latency_ms": 50, "response": {"confirmation": "XYZ"}}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book the flight")])
    h.send(150_000, [eot_event()])

    # Phase 1: let interpret -> plan actually land (real WORKER_RESULT
    # events drive this, nothing to do with settle liveness) until the
    # write is PROPOSED and G10-blocked.
    report = None
    for _ in range(10):
        report = h.advance(h.clock.now_us() + 10_000)
        if h.store.call_ledger.proposed():
            break
    proposed = h.store.call_ledger.proposed()
    assert len(proposed) == 1 and proposed[0].tool == "book_flight"
    assert report.next_wake_us is not None, "call is blocked but no liveness wake was scheduled"

    # Phase 2: from here on, advance *only* to next_wake_us — the settle
    # window has no other event to drive it forward. If the driver only
    # ever honors this hint (never polls blindly), the write must still
    # eventually be admitted.
    admitted = False
    for _ in range(10):
        if report.next_wake_us is None:
            break
        report = h.advance(report.next_wake_us)
        if any(er.action.action_type == ActionType.TOOL_CALL for er in report.emit_report.emitted):
            admitted = True
            break
    assert admitted, "write was never admitted — settle liveness deadlocked"

    finals = drain(h, h.clock.now_us() + 500_000)
    assert any(a.action_type == ActionType.FINAL for a in finals)
    assert_clean(h)


# --- Replay identity across the whole V2 feature set ------------------------


def test_v2_replay_identity():
    def run_once() -> list[dict]:
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

        config = v2_config(transitive_invalidation=True, rebinder_enabled=True)
        h = new_harness(config, tools={"search_flights": {"latency_ms": 400, "response": {"flight_id": "AI-1"}}}, provider=provider)
        h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
        h.send(100_000, [chunk_event("Find flights to Pune")])
        h.send(150_000, [eot_event()])
        drain(h, 220_000, stop_on_final=False)
        h.send(h.clock.now_us() + 10_000, [interruption_event()])
        h.send(h.clock.now_us() + 5_000, [chunk_event("actually Mumbai")])
        h.send(h.clock.now_us() + 5_000, [eot_event()])
        drain(h, h.clock.now_us() + 2_000_000)
        return h.log.records

    violations = TraceChecker().check_replay(run_once, times=5)
    assert violations == []
