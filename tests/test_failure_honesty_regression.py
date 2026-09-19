"""Regression tests for two real correctness bugs found by an independent
review (a fresh Sonnet session, given full repo/terminal access and asked
to keep reviewing after the correction-race fix — see `reviews/sonnet/`).
Both are "false completion claim" / silent-hang failures against this
project's own core stated invariant (CLAUDE.md's "What is being built":
"...without duplicate side effects or false completion claims").

1. **False completion claim on retry exhaustion** (more severe, found via
   this session's own follow-up investigation into F-SONNET-2, not
   Sonnet's own report directly): `kernel/executor.py.PlanExecutor.
   _fail_goal` — called whenever a step's tool call fails past
   `max_read_retries`/`max_write_retries`, a completely ordinary scenario
   (a flaky tool, a rate limit) — set `task_state=RESPONDING` and wrote a
   FINAL whose own text says "I couldn't complete this... kept failing,"
   but never touched `goal.status`, leaving it `ACTIVE`. `FastResponder.
   _final`'s `task_completed = goal.status != GoalStatus.ABANDONED`
   check then read `ACTIVE != ABANDONED` as `True` — the FINAL claimed
   `task_completed=True` while its own text said the opposite. Fixed by
   setting `goal.status = GoalStatus.ABANDONED` in `_fail_goal`, exactly
   the signal `_final`'s existing check was already looking for.

2. **S-07 silent livelock** (F-SONNET-2, `docs/theme05_implementation_
   blueprint.md`'s S-07 scenario: a plan step referencing a tool not in
   the current catalog): `PlanExecutor.propose_ready_calls` created a
   `CallRecord` for the step regardless, which `CommitGate.scan_and_admit`
   then left `PROPOSED` forever (G1 — "tool exists and is usable" — can
   never pass for a tool that was simply never declared, and nothing
   short of a later manifest update naming it would ever change that).
   The goal never completed, never asked for clarification, and never
   failed — it would sit silently in `EXECUTING` until the scenario-wide
   watchdog (105s default) eventually fired and salvaged it, 100+ seconds
   later. Fixed by checking `store.catalog.get(step.tool)` before binding
   and calling `_fail_goal` immediately (reusing the same honest-failure
   path retry-exhaustion already uses) instead of creating a doomed call.
"""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, GoalStatus, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

SEARCH_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"destination": {"type": "string"}}, "required": ["destination"]},
}


def flat_plan(tool: str, *, param: str = "destination") -> dict:
    return {"steps": [{"local_id": "s1", "tool": tool, "kind": "read", "bindings": {param: {"type": "fact", "key": f"slot.$G.{param}"}}, "after": []}]}


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def _config(**overrides) -> Config:
    overrides.setdefault("transitive_invalidation", False)
    overrides.setdefault("settle_barrier_enabled", False)
    overrides.setdefault("absence_read_sets", False)
    overrides.setdefault("claim_grades_enabled", False)
    overrides.setdefault("rebinder_enabled", False)
    return Config(**overrides)


def test_retry_exhaustion_final_does_not_claim_completion():
    """A tool that always errors, past max_read_retries, must produce a
    FINAL with task_completed=False and goal.status=ABANDONED -- not a
    FINAL whose text says the task failed while claiming success."""
    config = _config(max_read_retries=1)
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Should never get here.", "claims": []})

    h = SimHarness(
        config,
        seed=1,
        provider=provider,
        tools={"search_flights": {"latency_ms": 10, "error": {"message": "boom", "retryable": True}}},
        worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000},
    )
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 1_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert "couldn't complete" in finals[0].body.text.lower()
    assert finals[0].body.task_completed is False

    gid = h.store.goals.all()[0].goal_id
    assert h.store.goals.get(gid).status == GoalStatus.ABANDONED
    assert_clean(h)


def test_unknown_tool_in_plan_fails_immediately_instead_of_livelocking():
    """A plan step referencing a tool absent from the current catalog
    (S-07) must produce an honest FINAL in the same step it's discovered
    -- not sit PROPOSED, blocked by CommitGate's G1, forever."""
    config = _config()
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register("plan", "search_flights", {"steps": [{"local_id": "s1", "tool": "nonexistent_magic_tool", "kind": "read", "bindings": {}, "after": []}]})
    provider.register("compose", "s1", {"text": "Should never get here.", "claims": []})

    h = SimHarness(config, seed=1, provider=provider, tools={}, worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000})
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    # A real livelock would still show nothing after a very long drain;
    # the fix resolves it within a handful of steps.
    actions = drain(h, 300_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is False

    gid = h.store.goals.all()[0].goal_id
    goal = h.store.goals.get(gid)
    assert goal.status == GoalStatus.ABANDONED
    # No call was ever left dangling in PROPOSED.
    assert all(c.status != CallStatus.PROPOSED for c in h.store.call_ledger.all())
    assert_clean(h)


def test_phase_failure_honesty_replay_identity():
    def run_once() -> list[dict]:
        config = _config(max_read_retries=1)
        provider = ScriptedProvider()
        provider.register(
            "interpret",
            "Find flights to Pune",
            {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
        )
        provider.register("plan", "search_flights", flat_plan("search_flights"))
        provider.register("compose", "s1", {"text": "Should never get here.", "claims": []})
        h = SimHarness(
            config,
            seed=1,
            provider=provider,
            tools={"search_flights": {"latency_ms": 10, "error": {"message": "boom", "retryable": True}}},
            worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000},
        )
        h.send(0, [manifest_event([SEARCH_TOOL])])
        h.send(100_000, [chunk_event("Find flights to Pune")])
        h.send(150_000, [eot_event()])
        drain(h, 1_000_000)
        return h.log.records

    violations = TraceChecker().check_replay(run_once, times=5)
    assert violations == []
