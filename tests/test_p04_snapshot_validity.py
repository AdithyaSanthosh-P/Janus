"""P-04 (blueprint line 2528): "Snapshot validity | Every scenario |
Snapshot keys ⊆ parameter names; types match schema; revisions monotonic |
CS-17, 27". Revisions were already covered by CS-27; this closes the rest.

`TraceChecker._check_p04` now runs against every test in the suite (it
rebuilds the catalog from the trace's own manifest events, so a later
manifest dropping a tool -- S-08 -- can't produce false positives).
Building it surfaced two real kernel gaps, both reproduced before fixing:
1. `SnapshotProjector` reported every committed `slot.<goal>.*` fact, so a
   slot a model extracted that no tool takes reached the harness -- a
   FINAL's snapshot carried {'destination': 'Pune', 'mood': 'excited'}.
2. A wrongly-typed slot value (a live model returning destination=42 for a
   string param) produced a call G8 could never admit: it sat PROPOSED,
   silently, for 20+ simulated seconds -- the same livelock class as S-07.
   `PlanExecutor` now checks args before creating the call and asks the
   user for a bad user slot (or fails honestly for anything else).
"""

from __future__ import annotations

from types import SimpleNamespace

from conftest import FAST_WORKER_LATENCY, SEARCH_FLIGHTS_TOOL, chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.kernel.emission import EmittedRecord, EmitReport
from prism_rt.model.actions import Action, SpeakBody
from prism_rt.model.types import EMPTY_READ_SET, ActionType, Snapshot
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider


def _action(action_id: str, slots: dict) -> Action:
    return Action(
        action_type=ActionType.FINAL,
        action_id=action_id,
        ts_us=0,
        body=SpeakBody(text="ok", kind="final"),
        read_set=EMPTY_READ_SET,
        snapshot=Snapshot(intent="search_flights", slots=slots, revision=1, digest="d"),
    )


def _report(step_no: int, actions: list, batch=()) -> SimpleNamespace:
    return SimpleNamespace(
        step_no=step_no,
        batch=batch,
        invalidation=SimpleNamespace(invalidated_call_ids=()),
        emit_report=EmitReport(emitted=tuple(EmittedRecord(action=a) for a in actions), rejected=()),
    )


def _manifest(tools: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(payload_type="manifest", payload=SimpleNamespace(tools=tools))


def _p04(reports) -> list:
    return [v for v in TraceChecker().check(reports) if v.invariant == "P-04"]


def test_p04_check_passes_valid_snapshots():
    reports = [_report(1, [], batch=(_manifest([SEARCH_FLIGHTS_TOOL]),)), _report(2, [_action("a1", {"destination": "Pune"})])]
    assert _p04(reports) == []


def test_p04_check_catches_unknown_key_and_wrong_type():
    reports = [
        _report(1, [], batch=(_manifest([SEARCH_FLIGHTS_TOOL]),)),
        _report(2, [_action("a1", {"destination": "Pune", "mood": "excited"})]),
        _report(3, [_action("a2", {"destination": 42})]),
    ]
    messages = [v.message for v in _p04(reports)]
    assert len(messages) == 2
    assert "'mood'" in messages[0] and "not a parameter" in messages[0]
    assert "schema type 'string'" in messages[1]


def test_p04_check_uses_catalog_as_of_each_step_not_final_state():
    """A manifest that later drops a tool mustn't retroactively invalidate
    snapshots that were correct when emitted (S-08's shape)."""
    seat = {"name": "get_seat_map", "parameters": {"type": "object", "properties": {"flight_id": {"type": "string"}}}}
    reports = [
        _report(1, [], batch=(_manifest([SEARCH_FLIGHTS_TOOL, seat]),)),
        _report(2, [_action("a1", {"flight_id": "AI-1"})]),
        _report(3, [], batch=(_manifest([SEARCH_FLIGHTS_TOOL]),)),
    ]
    assert _p04(reports) == []


def _harness(first_deltas: list[dict]) -> SimHarness:
    provider = ScriptedProvider()
    # Rules keyed on the transcript line -- every INTERPRET prompt also
    # carries the tool list, so a bare "flights" rule would match turn 2 too.
    provider.register("interpret", "transcript: 'flights please'", {"act": "new_goal", "intent": "search_flights", "slot_deltas": first_deltas})
    provider.register(
        "interpret",
        "transcript: 'Pune'",
        {"act": "answer_clarification", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register(
        "plan",
        "goal_intent: search_flights",
        {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]},
    )
    provider.register("compose", "s1", {"text": "Found flights to Pune.", "claims": ["result:s1"]})
    h = SimHarness(Config(), seed=1, provider=provider, worker_latency_us=FAST_WORKER_LATENCY, tools={"search_flights": {"latency_ms": 50, "response": {"f": 1}}})
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("flights please")])
    h.send(150_000, [eot_event()])
    return h


def test_extra_non_parameter_slot_is_not_reported_in_the_snapshot():
    h = _harness(
        [
            {"name": "destination", "scope": "goal", "op": "set", "value": "Pune"},
            {"name": "mood", "scope": "goal", "op": "set", "value": "excited"},
        ]
    )
    actions = drain(h, 2_000_000)
    final = next(a for a in actions if a.action_type == ActionType.FINAL)
    assert final.snapshot.slots == {"destination": "Pune"}
    assert h.store.facts.get("slot.g-0001.mood") is not None  # kept in state, just not reported
    assert _p04(h.run_log.reports) == []


def test_wrongly_typed_slot_asks_instead_of_livelocking():
    h = _harness([{"name": "destination", "scope": "goal", "op": "set", "value": 42}])
    first = drain(h, 1_000_000, stop_on_final=False)
    assert [a.action_type for a in first if a.action_type != ActionType.SPEAK] == [ActionType.CLARIFY]
    assert h.store.call_ledger.all() == []  # no doomed call left PROPOSED
    h.send(1_100_000, [chunk_event("Pune")])
    h.send(1_150_000, [eot_event()])
    later = drain(h, 3_000_000)
    assert [a.body.tool_name for a in later if a.action_type == ActionType.TOOL_CALL] == ["search_flights"]
    finals = [a for a in later if a.action_type == ActionType.FINAL]
    assert len(finals) == 1 and finals[0].snapshot.slots == {"destination": "Pune"}
    assert [v for v in TraceChecker().check(h.run_log.reports, store=h.store)] == []
