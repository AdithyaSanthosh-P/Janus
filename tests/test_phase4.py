"""Phase 4 (`docs/post_v4_implementation_plan.md`) interruption-recovery
depth tests: the CHUNK cancellation anchor (`docs/prompt 2.txt` §8.2).

Every prior version's cancellation was EOT-anchored: a correction only
took effect once the turn closed and a fresh interpretation committed a
changed slot fact. Phase 4 adds an earlier anchor — `kernel/detector.py`'s
`QuickDetector.detect_values` extracts a HIGH-confidence slot value
straight from a chunk, and `kernel/turns.py`'s `TurnManager._detect_chunk_
anchor` cancels any in-flight call already bound to that slot in the same
step the chunk arrives, via `StoreTxn.mark_chunk_anchor` feeding the slot's
own key into the ordinary `InvalidationEngine` pipeline — no new
cancellation machinery, same mechanism a real fact change uses.

`SEARCH_FLIGHTS_ENUM_TOOL` (not the shared `SEARCH_FLIGHTS_TOOL` fixture,
which has no `enum`) gives `destination` a closed vocabulary so
`detect_values`'s enum extractor has something to match — this mirrors the
`docs/prompt 2.txt` §8.7 worked example (Pune -> Mumbai) directly.
"""

from __future__ import annotations

from conftest import (
    FAST_WORKER_LATENCY,
    chunk_event,
    drain,
    eot_event,
    manifest_event,
)

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, FactStatus, GoalStatus, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

SEARCH_FLIGHTS_ENUM_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {
            "destination": {"type": "string", "enum": ["Pune", "Mumbai", "Goa"]},
            "date": {"type": "string"},
        },
        "required": ["destination"],
    },
}


def flat_plan(tool: str, *, param: str = "destination") -> dict:
    return {"steps": [{"local_id": "s1", "tool": tool, "kind": "read", "bindings": {param: {"type": "fact", "key": f"slot.$G.{param}"}}, "after": []}]}


def new_harness(config, tools=None, provider=None, **kwargs):
    kwargs.setdefault("worker_latency_us", FAST_WORKER_LATENCY)
    return SimHarness(config, seed=1, tools=tools, provider=provider, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def _base_config(**overrides) -> Config:
    overrides.setdefault("transitive_invalidation", False)
    overrides.setdefault("settle_barrier_enabled", False)
    overrides.setdefault("absence_read_sets", False)
    overrides.setdefault("claim_grades_enabled", False)
    overrides.setdefault("rebinder_enabled", False)
    return Config(**overrides)


def _search_pune_provider() -> ScriptedProvider:
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register(
        "interpret",
        "actually make it Mumbai",
        {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]},
    )
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Here are the Mumbai flights.", "claims": ["result:s1"]})
    return provider


# --- chunk-anchor cancels before EOT ---------------------------------------


def test_chunk_anchor_cancels_in_flight_call_in_the_chunks_own_step():
    config = _base_config()
    provider = _search_pune_provider()
    h = new_harness(config, tools={"search_flights": {"latency_ms": 2000, "response": {"flight_id": "AI-9"}}}, provider=provider)

    h.send(0, [manifest_event([SEARCH_FLIGHTS_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    pre_correction = drain(h, 400_000, stop_on_final=False)
    pune_calls = [a for a in pre_correction if a.action_type == ActionType.TOOL_CALL]
    assert len(pune_calls) == 1
    pune_call_id = pune_calls[0].body.call_id
    assert h.store.call_ledger.get(pune_call_id).status == CallStatus.IN_FLIGHT

    # The correction chunk lands while the turn is still OPEN (no EOT, no
    # explicit interruption event) — this is the point being tested: the
    # cancel must not need to wait for either.
    r = h.send(h.clock.now_us() + 10_000, [chunk_event("actually make it Mumbai")])
    cancels = [er.action for er in r.emit_report.emitted if er.action.action_type == ActionType.CANCEL]
    assert len(cancels) == 1
    assert cancels[0].body.target_call_id == pune_call_id
    assert h.store.call_ledger.get(pune_call_id).status == CallStatus.CANCEL_REQUESTED

    # And the hypothesis is on record, never touching the committed slot.
    hyp_key = f"hyp.{h.store.turn_log.open_turn_id()}.destination"
    hyp_fact = h.store.facts.get(hyp_key)
    assert hyp_fact is not None and hyp_fact.status == FactStatus.HYPOTHESIS and hyp_fact.value == "Mumbai"
    slot_key = f"slot.{h.store.goals.all()[0].goal_id}.destination"
    assert h.store.facts.get(slot_key).value == "Pune"  # still the committed value — chunk-anchor never mutates it

    # Same step: the plan's step_key is now CANCEL_REQUESTED, which
    # PlanExecutor retries immediately (an existing V1 fix, unrelated to
    # Phase 4 — see currentStatus.md's V1 bug list) rather than waiting.
    # Since the *committed* slot digest hasn't changed yet (only the
    # hypothesis has — chunk-anchor cancellation is deliberately cheaper
    # than a real interpretation round-trip), the immediate retry rebinds
    # to the still-Pune value and gets re-emitted as a second read. This is
    # a real, documented interaction, not a bug: it's a wasted (but
    # harmless — a READ, never a WRITE) extra call, not a correctness
    # problem, and it disappears once the correction turn's EOT actually
    # commits Mumbai, which invalidates *that* call through the ordinary
    # (non-chunk-anchor) ARC of the invalidation engine.
    retry_calls = [er.action for er in r.emit_report.emitted if er.action.action_type == ActionType.TOOL_CALL]
    assert len(retry_calls) == 1
    assert retry_calls[0].body.arguments["destination"] == "Pune"
    assert retry_calls[0].body.call_id != pune_call_id

    # Close the turn: interpretation now actually commits Mumbai and the
    # rest of the goal proceeds normally to a Mumbai-grounded FINAL.
    h.send(h.clock.now_us() + 5_000, [eot_event()])
    rest = drain(h, 4_000_000)  # search_flights' own 2000ms mock latency dominates this budget
    all_actions = pre_correction + [er.action for er in r.emit_report.emitted] + rest
    mumbai_calls = [
        a for a in all_actions if a.action_type == ActionType.TOOL_CALL and a.body.arguments.get("destination") == "Mumbai"
    ]
    assert len(mumbai_calls) == 1
    finals = [a for a in all_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED
    assert_clean(h)


def test_chunk_anchor_does_not_fire_when_value_matches_current_slot():
    """Re-stating the same value must not spuriously cancel work (D4-style
    revert safety, applied to the chunk anchor instead of a real fact
    change)."""
    config = _base_config()
    provider = _search_pune_provider()
    h = new_harness(config, tools={"search_flights": {"latency_ms": 2000, "response": {"flight_id": "AI-9"}}}, provider=provider)

    h.send(0, [manifest_event([SEARCH_FLIGHTS_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 400_000, stop_on_final=False)

    in_flight = [c for c in h.store.call_ledger.all() if c.status == CallStatus.IN_FLIGHT]
    assert len(in_flight) == 1
    pune_call_id = in_flight[0].call_id

    r = h.send(h.clock.now_us() + 10_000, [chunk_event("yes, Pune please")])
    cancels = [er.action for er in r.emit_report.emitted if er.action.action_type == ActionType.CANCEL]
    assert cancels == []
    assert h.store.call_ledger.get(pune_call_id).status == CallStatus.IN_FLIGHT
    assert_clean(h)


def test_chunk_anchor_requires_no_active_goal_to_be_inert():
    """A HIGH enum value spoken before any goal exists (first turn) must
    not attempt any invalidation — there is nothing to cancel, and the
    hypothesis fact alone is a harmless no-op."""
    config = _base_config()
    provider = _search_pune_provider()
    h = new_harness(config, tools={"search_flights": {"latency_ms": 2000, "response": {"flight_id": "AI-9"}}}, provider=provider)

    h.send(0, [manifest_event([SEARCH_FLIGHTS_ENUM_TOOL])])
    r = h.send(100_000, [chunk_event("Find flights to Pune")])
    cancels = [er.action for er in r.emit_report.emitted if er.action.action_type == ActionType.CANCEL]
    assert cancels == []
    hyp_key = f"hyp.{h.store.turn_log.open_turn_id()}.destination"
    assert h.store.facts.get(hyp_key).value == "Pune"
    assert_clean(h)


# --- I-02: interrupt during planning ---------------------------------------


def test_i02_interrupt_during_planning_rejects_stale_plan():
    config = _base_config()
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

    # Planner latency long enough that the correction's own (fast)
    # interpretation commits well before the first PLAN job resolves.
    h = new_harness(
        config,
        tools={"search_flights": {"latency_ms": 50, "response": {"flight_id": "AI-9"}}},
        provider=provider,
        worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 800_000, JobKind.COMPOSE: 10_000},
    )

    h.send(0, [manifest_event([SEARCH_FLIGHTS_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    # Correction turn closes at +400ms of harness time, well before the
    # first (Pune) PLAN job's 800ms resolves. The stale Pune PLAN job is
    # rejected (invalid read set) only once it actually resolves, and only
    # then does a fresh (Mumbai) PLAN job get dispatched — so the total
    # budget needs room for two back-to-back 800ms PLAN latencies, not one.
    h.send(h.clock.now_us() + 400_000, [chunk_event("actually Mumbai")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])

    actions = drain(h, 3_000_000)
    tool_calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert len(tool_calls) == 1
    assert tool_calls[0].body.arguments["destination"] == "Mumbai"

    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert_clean(h)


def test_phase4_replay_identity():
    def run_once() -> list[dict]:
        config = _base_config()
        provider = _search_pune_provider()
        h = new_harness(config, tools={"search_flights": {"latency_ms": 2000, "response": {"flight_id": "AI-9"}}}, provider=provider)
        h.send(0, [manifest_event([SEARCH_FLIGHTS_ENUM_TOOL])])
        h.send(100_000, [chunk_event("Find flights to Pune")])
        h.send(150_000, [eot_event()])
        drain(h, 400_000, stop_on_final=False)
        h.send(h.clock.now_us() + 10_000, [chunk_event("actually make it Mumbai")])
        h.send(h.clock.now_us() + 5_000, [eot_event()])
        drain(h, 1_500_000)
        return h.log.records

    violations = TraceChecker().check_replay(run_once, times=5)
    assert violations == []
