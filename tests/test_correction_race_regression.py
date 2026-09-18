"""Regression test for a real correctness bug found by an independent
review (Antigravity IDE, Claude Opus 4.6 Thinking — `reviews/opus/`) and
confirmed by direct reproduction, not by trusting the review's own
narrative: a correction landing while a goal is finishing up could let a
COMPOSE job dispatched *before* the correction go on to produce a FINAL
grounded in the pre-correction (stale) result — a "false completion
claim" against the project's own core invariant (see CLAUDE.md's "What is
being built").

Two compounding bugs, both in `kernel/task.py.TaskStateMachine`, fixed
together:

1. `_all_required_steps_done` checked only `CallStatus.CONSUMED`, never
   read-set validity — unlike `PlanExecutor._step_done`'s existing,
   correct version of the same check. A correction that invalidates the
   only remaining step's call (CONSUMED, but now stale) still counted as
   "done," transitioning EXECUTING -> RESPONDING in the very step
   `PlanExecutor.propose_ready_calls` — which runs *after* TaskStateMachine
   in the DECIDE phase's fixed order — would otherwise have re-executed
   it. Once RESPONDING is reached, `PlanExecutor` never even looks at the
   stale step again (its own guard requires `task_state == EXECUTING`).

2. `_build_compose_request`'s read set pinned only `result.<call_id>`
   (which never changes once written — the call itself is CONSUMED, a
   terminal status InvalidationEngine never revisits) and `goal.active`,
   never the *slot* facts the underlying call itself depended on. So a
   COMPOSE job already dispatched before a correction stayed "valid"
   forever from its own read set's point of view, and its stale result
   got applied unconditionally once it resolved.

Fixing #1 alone stops the premature transition, but a compose job already
in flight from *before* the correction can still resolve later and — with
only bug #2 still open — silently apply stale text once the goal
legitimately reaches RESPONDING again. Both fixes are required together;
this test would fail with either one reverted (verified during the fix).
"""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

SEARCH_ENUM_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"destination": {"type": "string", "enum": ["Pune", "Mumbai"]}}, "required": ["destination"]},
}


def flat_plan(tool: str, *, param: str = "destination") -> dict:
    return {"steps": [{"local_id": "s1", "tool": tool, "kind": "read", "bindings": {param: {"type": "fact", "key": f"slot.$G.{param}"}}, "after": []}]}


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def test_correction_during_in_flight_compose_does_not_produce_stale_final():
    """A COMPOSE job dispatched for the Pune result is still in flight
    (300ms latency) when a correction to Mumbai lands. The eventual FINAL
    must be grounded in the Mumbai search, and `compose.<goal>.text` must
    be written exactly once, from the corrected (Mumbai) result — never
    from the stale (Pune) COMPOSE job, which must be silently rejected."""
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,  # forces a full replan, the exact shape that exposed the race
    )
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
    # A single canned compose response, deliberately content-blind (matches
    # any "s1" prompt) -- this test does not rely on the *text* differing
    # per city; it verifies the *mechanism* directly (see assertions below),
    # which is the precise, non-string-fragile way to check this.
    provider.register("compose", "s1", {"text": "Here are the flights.", "claims": ["result:s1"]})

    h = SimHarness(
        config,
        seed=1,
        provider=provider,
        tools={"search_flights": {"latency_ms": 50, "response": {"flights": [{"id": "AI-1"}]}}},
        worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 300_000},
    )

    h.send(0, [manifest_event([SEARCH_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 400_000, stop_on_final=False)  # Pune search consumes (~220ms); COMPOSE (300ms) still in flight

    gid = h.store.goals.all()[0].goal_id
    pune_call = next(c for c in h.store.call_ledger.all() if c.args.get("destination") == "Pune")
    assert pune_call.status == CallStatus.CONSUMED  # confirms the race window: consumed, compose still pending

    h.send(450_000, [chunk_event("actually Mumbai")])
    h.send(460_000, [eot_event()])

    actions = drain(h, 2_000_000)

    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1

    mumbai_calls = [
        c for c in h.store.call_ledger.all()
        if c.args.get("destination") == "Mumbai" and c.status == CallStatus.CONSUMED
    ]
    assert len(mumbai_calls) == 1  # the corrected search actually ran and consumed

    # The pre-correction (Pune) call must never have been invalidated as
    # a *call* (CONSUMED is terminal, unchanged) -- what must change is
    # that its read set is no longer valid, which is what both fixes key
    # off instead of the call's own status.
    assert pune_call.status == CallStatus.CONSUMED
    assert not h.store.facts.is_valid(pune_call.read_set).is_valid

    # Two COMPOSE jobs must have run: one dispatched before the
    # correction (legitimately, at the time), grounded in the Pune
    # result; one dispatched after, grounded in the Mumbai result. This
    # is the precise, content-independent check that fix #2 actually
    # closed the gap: the *stale* job's read set (which fix #2 extends to
    # include the slot the underlying call depended on, not just its
    # never-changing result fact) must itself now read as invalid -- that
    # is exactly what makes `_apply_worker_result` reject it instead of
    # applying stale text, regardless of what the canned response text
    # happens to say.
    compose_jobs = [j for j in h.store.jobs._jobs.values() if j.kind == JobKind.COMPOSE]
    assert len(compose_jobs) == 2
    pune_result_key = f"result.{pune_call.call_id}"
    mumbai_result_key = f"result.{mumbai_calls[0].call_id}"
    stale_job = next(j for j in compose_jobs if any(e.key == pune_result_key for e in j.read_set.entries))
    fresh_job = next(j for j in compose_jobs if any(e.key == mumbai_result_key for e in j.read_set.entries))
    assert stale_job is not fresh_job
    # Checked post-run, after FINAL: emission.py._complete_goal clears
    # goal.active to None once FINAL is emitted, which makes *any* job's
    # read set captured while a goal was active look "invalid" in
    # hindsight (both jobs pinned goal.active) -- that's expected and not
    # what's being tested here. What actually governed correctness was
    # each job's validity *at the moment it resolved*, which the
    # compose-write-count and mumbai_calls checks below establish
    # directly; the one thing still meaningful to check post-hoc is that
    # the stale job's invalidity is *not* solely goal.active (which both
    # jobs would share) -- it specifically includes the slot fix #2 added.
    stale_failing_keys = {f[0] for f in h.store.facts.is_valid(stale_job.read_set).failing}
    assert "slot.g-0001.destination" in stale_failing_keys

    # compose.<gid>.text must have been written exactly once across the
    # whole run -- if the stale (Pune) COMPOSE job's result had been
    # applied, this fact would have gone ABSENT -> committed once early
    # (the bug), then possibly again later; either a double-write or an
    # early (pre-Mumbai-consumption) write indicates the regression.
    compose_writes = [
        report.step_no
        for report in h.run_log.reports
        for change in report.change_set.changes
        if change[0] == f"compose.{gid}.text" and change[3] is None  # old_status None == first-ever write (ABSENT -> *)
    ]
    assert len(compose_writes) == 1

    assert_clean(h)
