"""Targeted investigation: Does a stale compose text survive a correction
and get used for a FINAL about the wrong thing?

Scenario:
1. User says "Find flights to Pune"
2. Interpretation -> goal, plan, search_flights(Pune) dispatched
3. search_flights(Pune) completes -> CONSUMED
4. COMPOSE job dispatched (reads result, not slots)
5. User says "actually Mumbai" DURING compose
6. Correction applies slot change -> task_state back to PLANNING
7. COMPOSE result arrives -> writes compose.<gid>.text = "Pune flights"
8. The question: is this stale text used for FINAL?
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, FactStatus, GoalStatus, JobKind, TaskState
from prism_rt.sim.harness import SimHarness
from prism_rt.sim.checker import TraceChecker
from prism_rt.workers.gateway import ScriptedProvider

SEARCH_FLIGHTS_ENUM_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {
            "destination": {"type": "string", "enum": ["Pune", "Mumbai", "Goa"]},
        },
        "required": ["destination"],
    },
}

def manifest_event(tools):
    return {"type": "manifest", "payload": {"tools": tools}}

def chunk_event(text):
    return {"type": "text_chunk", "payload": {"text": text}}

def eot_event():
    return {"type": "end_of_turn", "payload": {}}


def test_stale_compose():
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,  # keep it simple
    )
    provider = ScriptedProvider()
    provider.register(
        "interpret", "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights",
         "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register(
        "interpret", "actually Mumbai",
        {"act": "slot_update",
         "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]},
    )
    provider.register("plan", "search_flights", {
        "steps": [
            {"local_id": "s1", "tool": "search_flights", "kind": "read",
             "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
        ]
    })
    provider.register("compose", "s1", {"text": "Here are the PUNE flights.", "claims": ["result:s1"]})

    # COMPOSE takes 300ms, search takes 50ms
    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 50, "response": {"flights": [{"id": "AI-1"}]}}},
                   provider=provider,
                   worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 300_000})

    h.send(0, [manifest_event([SEARCH_FLIGHTS_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    # Advance until the Pune search completes and COMPOSE starts
    all_actions = []
    for step_us in range(160_000, 450_000, 10_000):
        report = h.advance(step_us)
        for er in report.emit_report.emitted:
            all_actions.append(er.action)
            desc = getattr(er.action.body, 'text', None) or getattr(er.action.body, 'tool_name', None)
            print(f"  t={step_us}: EMITTED {er.action.action_type.value}: {desc}")

    gid = h.store.goals.all()[0].goal_id
    goal = h.store.goals.get(gid)
    print(f"\nGoal state before correction: {goal.task_state}")
    compose_text = h.store.facts.get(f"compose.{gid}.text")
    print(f"compose text before correction: {compose_text}")

    # Send correction: "actually Mumbai"
    print("\n--- Correction at t=450_000 ---")
    h.send(450_000, [chunk_event("actually Mumbai")])
    h.send(460_000, [eot_event()])

    goal = h.store.goals.get(gid)
    print(f"Goal state after correction: {goal.task_state}")
    compose_text = h.store.facts.get(f"compose.{gid}.text")
    print(f"compose text after correction: {compose_text}")

    # Now let COMPOSE result arrive (dispatched at ~350_000, 300ms latency = ~650_000)
    for step_us in range(470_000, 1_000_000, 10_000):
        report = h.advance(step_us)
        for er in report.emit_report.emitted:
            all_actions.append(er.action)
            desc = getattr(er.action.body, 'text', None) or getattr(er.action.body, 'tool_name', None)
            print(f"  t={step_us}: EMITTED {er.action.action_type.value}: {desc}")

    goal = h.store.goals.get(gid)
    print(f"\nGoal state after compose resolves: {goal.task_state}")
    compose_text = h.store.facts.get(f"compose.{gid}.text")
    print(f"compose text after compose resolves: {compose_text.value if compose_text and compose_text.status != FactStatus.RETRACTED else None}")

    # Let the whole thing finish
    for step_us in range(1_000_000, 3_000_000, 10_000):
        report = h.advance(step_us)
        for er in report.emit_report.emitted:
            all_actions.append(er.action)
            desc = getattr(er.action.body, 'text', None) or getattr(er.action.body, 'tool_name', None)
            print(f"  t={step_us}: EMITTED {er.action.action_type.value}: {desc}")

    # Check: what text did the FINAL say?
    finals = [a for a in all_actions if a.action_type == ActionType.FINAL]
    if finals:
        for f in finals:
            print(f"\n*** FINAL text: \"{f.body.text}\"")
            if "PUNE" in f.body.text.upper():
                print("*** BUG CONFIRMED: FINAL says PUNE after correction to Mumbai!")
            elif "MUMBAI" in f.body.text.upper():
                print("*** OK: FINAL correctly says Mumbai")
            else:
                print(f"*** FINAL text doesn't mention either city")
    else:
        print("\n*** No FINAL emitted!")
        # Check goal state
        goal = h.store.goals.get(gid)
        print(f"Final goal state: {goal.status}, {goal.task_state}")
        active = h.store.facts.get("goal.active")
        print(f"Active goal: {active.value if active else None}")

    # TraceChecker
    violations = TraceChecker().check(h.run_log.reports, store=h.store)
    print(f"TraceChecker violations: {violations}")


if __name__ == "__main__":
    test_stale_compose()
