import sys
sys.path.insert(0, "src")
sys.path.insert(0, ".")

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, FactStatus
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider
from prism_rt.model.actions import SpeakBody
from reviews.sonnet.pass2_semantic import drain

SEARCH_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"destination": {"type": "string"}},
        "required": ["destination"],
    },
}

BOOK_TOOL = {
    "name": "book_flight",
    "mutability": "state_changing",
    "parameters": {
        "type": "object",
        "properties": {"flight_id": {"type": "string"}},
        "required": ["flight_id"],
    },
}

def run_test():
    provider = ScriptedProvider()
    provider.register(
        "interpret", "book flight to Pune",
        {
            "act": "NEW_GOAL",
            "intent": "book_flight",
            "slot_deltas": [{"op": "set", "name": "destination", "value": "Pune"}]
        }
    )
    provider.register(
        "interpret", "nevermind book flight to Mumbai",
        {
            "act": "NEW_GOAL",
            "intent": "book_flight",
            "slot_deltas": [{"op": "set", "name": "destination", "value": "Mumbai"}]
        }
    )
    provider.register(
        "plan", "book_flight",
        {
            "steps": [
                {"local_id": "s1", "tool": "search_flights", "kind": "read", "after": [], "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}},
                {"local_id": "s2", "tool": "book_flight", "kind": "write", "after": ["s1"], "bindings": {"flight_id": {"type": "result", "local_id": "s1", "path": "id"}}}
            ]
        }
    )

    config = Config(
        transitive_invalidation=True,
        settle_barrier_enabled=False,
    )
    h = SimHarness(config, seed=1, provider=provider)

    h.send(10_000, [{"type": "manifest", "payload": {"tools": [SEARCH_TOOL, BOOK_TOOL]}}])
    h.send(20_000, [
        {"type": "text_chunk", "payload": {"text": "book flight to Pune"}, "ts_us": 20_000},
        {"type": "end_of_turn", "payload": {}, "ts_us": 21_000}
    ])

    drain(h, 200_000, stop_on_final=False)

    print("Goals:")
    for g in h.store.goals.all():
        print(f"  {g.goal_id}: {g.status} {g.task_state}")
    
    print("Facts:")
    for f in h.store.facts.all():
        print(f"  {f.key}: {f.value}")

    in_flight = [c for c in h.store.call_ledger.all() if c.status == CallStatus.IN_FLIGHT]
    print("In flight calls:", [c.call_id for c in in_flight])
    assert len(in_flight) == 1
    call1 = in_flight[0]
    print(f"Call 1 ID: {call1.call_id}, Status: {call1.status}")

    # Suspend the goal by issuing a new one
    h.send(210_000, [
        {"type": "text_chunk", "payload": {"text": "nevermind book flight to Mumbai"}, "ts_us": 210_000},
        {"type": "end_of_turn", "payload": {}, "ts_us": 211_000}
    ])
    drain(h, 300_000, stop_on_final=False)

    print(f"Call 1 Status after new goal: {h.store.call_ledger.get(call1.call_id).status}")

    # Now deliver the tool result for call1
    h.send(310_000, [{"type": "tool_result", "payload": {"call_id": call1.call_id, "status": "ok", "result": {"id": "f1"}}, "ts_us": 310_000}])
    drain(h, 400_000, stop_on_final=False)

    final_call1 = h.store.call_ledger.get(call1.call_id)
    print(f"Call 1 Final Status: {final_call1.status}")

run_test()
