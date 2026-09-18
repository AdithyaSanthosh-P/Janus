"""Investigation: what does apply_interpretation do when the goal is already
COMPLETED/RESPONDING and a SLOT_UPDATE comes in?

From reading interpret_apply.py:
- Line 254: if goal.status != GoalStatus.ACTIVE -> return gid (does nothing)
- Line 278: if changed and goal.task_state not in (COMPLETED, FAILED) -> transition to PLANNING

So: a SLOT_UPDATE on a COMPLETED goal does NOTHING. The slot IS written
(line 257 runs before the status check), but no re-planning or re-execution
happens. This is the I-06 gap documented in currentStatus.md.

BUT: Investigation 2 is more concerning. The correction arrives during
COMPOSE (task_state=RESPONDING), the goal is ACTIVE. The slot change
should invalidate the compose job. Let's see what happens...

Actually wait — let me re-read. The compose result resolves at t=~600_000.
The correction arrives at t=410_000. Let me check: does the slot change
trigger invalidation of the COMPOSE job?

The COMPOSE job's read set includes "goal.active" — does it include the
slot fact? Let's check _build_compose_request in task.py.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, FactStatus, GoalStatus, JobKind, JobStatus, TaskState
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider


def manifest_event(tools):
    return {"type": "manifest", "payload": {"tools": tools}}

def chunk_event(text):
    return {"type": "text_chunk", "payload": {"text": text}}

def eot_event():
    return {"type": "end_of_turn", "payload": {}}


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


def investigation_3():
    """Does the COMPOSE job's read set include the slot? If not, changing
    the slot won't invalidate the compose job, and a stale COMPOSE result
    will be used for FINAL."""
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

    # COMPOSE takes 200ms -> correction can arrive during compose
    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 50, "response": {"flights": [{"id": "AI-1"}]}}},
                   provider=provider,
                   worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 200_000})

    h.send(0, [manifest_event([SEARCH_FLIGHTS_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    # Let compose job start
    for step_us in range(160_000, 400_000, 10_000):
        report = h.advance(step_us)
        if report.emit_report.emitted:
            for er in report.emit_report.emitted:
                body = er.action.body
                desc = getattr(body, 'text', None) or getattr(body, 'tool_name', None)
                print(f"  t={step_us}: EMITTED {er.action.action_type.value}: {desc}")

    gid = h.store.goals.all()[0].goal_id
    goal = h.store.goals.get(gid)
    print(f"\nGoal state: {goal.status}, task_state: {goal.task_state}")

    # Check compose job's read set
    compose_jobs = [j for j in h.store.jobs.all() if j.kind == JobKind.COMPOSE]
    for j in compose_jobs:
        print(f"COMPOSE job {j.job_id}: status={j.status}")
        print(f"  Read set keys: {[e.key for e in j.read_set.entries]}")
        # Check if slot fact is in read set
        slot_key = f"slot.{gid}.destination"
        has_slot = any(e.key == slot_key for e in j.read_set.entries)
        print(f"  Has slot.{gid}.destination in read set? {has_slot}")

    # Send correction during COMPOSE
    print("\n--- Sending correction during COMPOSE ---")
    h.send(410_000, [chunk_event("actually Mumbai")])
    h.send(420_000, [eot_event()])

    # Check compose job status after correction
    for j in compose_jobs:
        updated = h.store.jobs.get(j.job_id)
        print(f"COMPOSE job {j.job_id} after correction: status={updated.status}")
        # Check read set validity
        validity = h.store.facts.is_valid(j.read_set)
        print(f"  Read set still valid? {validity.is_valid}")
        if not validity.is_valid:
            print(f"  Failing entries: {validity.failing}")

    # Watch what happens when compose resolves
    for step_us in range(430_000, 1_500_000, 10_000):
        report = h.advance(step_us)
        if report.emit_report.emitted:
            for er in report.emit_report.emitted:
                body = er.action.body
                desc = getattr(body, 'text', None) or getattr(body, 'tool_name', None)
                print(f"  t={step_us}: EMITTED {er.action.action_type.value}: {desc}")

    # Final check
    goal = h.store.goals.get(gid)
    print(f"\nFinal goal state: {goal.status}, task_state: {goal.task_state}")

    # Did it compose with Pune or Mumbai?
    compose_fact = h.store.facts.get(f"compose.{gid}.text")
    print(f"compose.{gid}.text: {compose_fact}")

    # What about the slot?
    slot_fact = h.store.facts.get(f"slot.{gid}.destination")
    print(f"slot.{gid}.destination: {slot_fact.value if slot_fact else None}")


if __name__ == "__main__":
    investigation_3()
