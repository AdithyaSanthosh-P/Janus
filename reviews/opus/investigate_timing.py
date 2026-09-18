"""Deep investigation of the _all_required_steps_done / _step_done
consistency gap. The question: when a correction arrives AFTER a goal
has already transitioned to RESPONDING and then COMPLETED (FINAL emitted),
does the system properly handle the late correction, or does the completed
goal silently eat the correction?

This is NOT necessarily a bug — the architecture spec says I-06 (correction
after FINAL) is deliberately not built. But let's check:
1. Is the FINAL emitted with stale data?
2. Or was the FINAL correct at the time, and the correction is just too late?

Also: what about a correction that arrives BETWEEN the step CONSUMED and
FINAL emission — during RESPONDING? This is the more interesting case.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, FactStatus, GoalStatus, JobKind, TaskState
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

FAST_WORKER_LATENCY = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000}

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

def manifest_event(tools):
    return {"type": "manifest", "payload": {"tools": tools}}

def chunk_event(text):
    return {"type": "text_chunk", "payload": {"text": text}}

def eot_event():
    return {"type": "end_of_turn", "payload": {}}

def drain(harness, max_us, *, step_us=10_000, stop_on_final=True):
    actions = []
    t = harness.clock.now_us()
    while t < max_us:
        t += step_us
        report = harness.advance(t)
        for er in report.emit_report.emitted:
            actions.append(er.action)
            if stop_on_final and er.action.action_type.value == "final":
                return actions
    return actions


def investigate_timing():
    """Trace through exactly what happens step by step when a correction
    arrives after the goal completes but before the user expects it to."""
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=True,
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
    provider.register("compose", "s1", {"text": "Here are the Pune flights.", "claims": ["result:s1"]})

    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 50, "response": {"flights": [{"id": "AI-1"}]}}},
                   provider=provider, worker_latency_us=FAST_WORKER_LATENCY)

    h.send(0, [manifest_event([SEARCH_FLIGHTS_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    print("After EOT, stepping through:")
    for step_us in range(160_000, 600_000, 10_000):
        report = h.advance(step_us)
        if report.emit_report.emitted:
            for er in report.emit_report.emitted:
                print(f"  t={step_us}: EMITTED {er.action.action_type.value}: {getattr(er.action.body, 'text', None) or getattr(er.action.body, 'tool_name', None) or getattr(er.action.body, 'target_call_id', None)}")

    gid = h.store.goals.all()[0].goal_id
    goal = h.store.goals.get(gid)
    print(f"\nGoal state: {goal.status}, task_state: {goal.task_state}")
    print(f"Active goal: {h.store.facts.get('goal.active')}")

    # Now send correction
    print("\n--- Sending correction ---")
    h.send(600_000, [chunk_event("actually Mumbai")])
    h.send(650_000, [eot_event()])

    # Watch what happens
    for step_us in range(660_000, 1_200_000, 10_000):
        report = h.advance(step_us)
        if report.emit_report.emitted:
            for er in report.emit_report.emitted:
                print(f"  t={step_us}: EMITTED {er.action.action_type.value}: {getattr(er.action.body, 'text', None) or getattr(er.action.body, 'tool_name', None) or getattr(er.action.body, 'target_call_id', None)}")

    goal = h.store.goals.get(gid)
    print(f"\nFinal goal state: {goal.status}, task_state: {goal.task_state}")
    active = h.store.facts.get("goal.active")
    print(f"Active goal fact: {active.value if active else None} (status: {active.status if active else None})")

    # Check: was a second goal created for the correction?
    all_goals = h.store.goals.all()
    print(f"Total goals: {len(all_goals)}")
    for g in all_goals:
        print(f"  {g.goal_id}: status={g.status}, task_state={g.task_state}")


def investigate_correction_during_compose():
    """The more interesting case: what happens when a correction arrives
    WHILE the COMPOSE job is in flight (after the search completed but
    before FINAL is emitted)?"""
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=True,
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
    provider.register("compose", "s1", {"text": "Here are the Pune flights.", "claims": ["result:s1"]})

    # Key: COMPOSE takes 200ms, search takes 50ms -> correction can arrive
    # while COMPOSE is in flight
    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 50, "response": {"flights": [{"id": "AI-1"}]}}},
                   provider=provider,
                   worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 200_000})

    h.send(0, [manifest_event([SEARCH_FLIGHTS_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    # Let the search complete and COMPOSE start
    drain(h, 400_000, stop_on_final=False)

    gid = h.store.goals.all()[0].goal_id
    goal = h.store.goals.get(gid)
    print(f"Goal state while compose is in flight: {goal.task_state}")
    compose_jobs = h.store.jobs.running_by_kind_goal(JobKind.COMPOSE, gid)
    print(f"Running COMPOSE jobs: {len(compose_jobs)}")

    # Send correction during COMPOSE
    h.send(410_000, [chunk_event("actually Mumbai")])
    h.send(420_000, [eot_event()])

    # What happens?
    for step_us in range(430_000, 1_500_000, 10_000):
        report = h.advance(step_us)
        if report.emit_report.emitted:
            for er in report.emit_report.emitted:
                body = er.action.body
                desc = getattr(body, 'text', None) or getattr(body, 'tool_name', None)
                print(f"  t={step_us}: EMITTED {er.action.action_type.value}: {desc}")

    goal = h.store.goals.get(gid)
    print(f"\nFinal goal state: {goal.status}, task_state: {goal.task_state}")

    # Did it search for Mumbai?
    all_calls = h.store.call_ledger.all()
    for c in all_calls:
        print(f"  call {c.call_id}: tool={c.tool}, args={c.args}, status={c.status}")


if __name__ == "__main__":
    print("=" * 60)
    print("INVESTIGATION 1: Correction after FINAL")
    print("=" * 60)
    investigate_timing()

    print("\n\n" + "=" * 60)
    print("INVESTIGATION 2: Correction during COMPOSE")
    print("=" * 60)
    investigate_correction_during_compose()
