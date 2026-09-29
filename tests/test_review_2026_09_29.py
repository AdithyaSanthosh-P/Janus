"""Regression tests for the 2026-09-29 ultrareview findings (all verified
against the code before fixing; see reviews/index.md)."""

from __future__ import annotations

import asyncio
import json

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.kernel import replies
from prism_rt.model.types import ActionType, GoalRecord, GoalStatus, JobKind, TaskState
from prism_rt.observability.xray import TracedQueue
from prism_rt.profiles import apply_overrides, fdb_v3_config
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

HOMES = {"name": "search_homes", "mutability": "read_only", "parameters": {"type": "object", "properties": {
    "city": {"type": "string"}}, "required": ["city"]}}
COMMUTE = {"name": "get_commute", "mutability": "read_only", "parameters": {"type": "object", "properties": {
    "origin": {"type": "string"}, "destination": {"type": "string"}}, "required": ["origin", "destination"]}}
FAST = {JobKind.INTERPRET: 10_000, JobKind.COMPOSE: 10_000, JobKind.BIND: 10_000}
CHAIN = {"act": "new_goal", "intent": "search_homes", "slot_deltas": [], "actions": [
    {"tool": "search_homes", "args": {"city": "Austin"}},
    {"tool": "get_commute", "args": {"destination": "the office"},
     "refs": [{"param": "origin", "from": 0, "field": "address", "select": "the cheapest one"}]}]}


def test_bind_failures_do_not_carry_over_to_a_rebind_after_a_correction():
    """Finding 1: with max_interpret_retries=1, one BIND failure before the
    first success and one after the upstream re-ran must NOT add up to a
    give-up -- the second bind is a new grounding with its own budget."""
    outcomes = iter(["fail", "ok", "fail", "ok"])

    def bind(prompt):
        if next(outcomes) == "fail":
            raise RuntimeError("transient BIND failure")
        return {"args": {"origin": "H1"}}

    provider = ScriptedProvider()
    provider.register("interpret", "actually Dallas", {"act": "slot_update", "slot_deltas": [
        {"name": "city", "scope": "goal", "op": "set", "value": "Dallas"}]})
    provider.register("interpret", "Austin", CHAIN)
    provider.register("bind", "get_commute", bind)
    provider.register("compose", "a1", {"text": "About 20 minutes.", "claims": []})
    tools = {"search_homes": {"latency_ms": 200, "response": {"results": [{"id": "H1"}]}},
             "get_commute": {"latency_ms": 1_000, "response": {"minutes": 20}}}
    h = SimHarness(Config(action_plans_enabled=True, multi_action_enabled=True, max_interpret_retries=1),
                   seed=1, provider=provider, tools=tools, worker_latency_us=FAST)
    h.send(0, [manifest_event([HOMES, COMMUTE])])
    h.send(100_000, [chunk_event("homes in Austin and the commute from the cheapest one")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 700_000, stop_on_final=False)  # first bind: fail, then ok; get_commute in flight
    actions += [er.action for er in h.send(750_000, [chunk_event("actually Dallas")]).emit_report.emitted]
    actions += [er.action for er in h.send(800_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 5_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1 and finals[0].body.task_completed is True
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_status_describes_the_last_finished_task_not_the_last_created():
    """Finding 2: a later-created but still-suspended goal must not be
    reported as "your last request"."""
    provider = ScriptedProvider()
    provider.register("interpret", "Austin", {"act": "new_goal", "intent": "search_homes", "slot_deltas": [],
                                              "actions": [{"tool": "search_homes", "args": {"city": "Austin"}}]})
    provider.register("compose", "a0", {"text": "Found one.", "claims": []})
    h = SimHarness(Config(action_plans_enabled=True, multi_action_enabled=True), seed=1, provider=provider,
                   tools={"search_homes": {"latency_ms": 200, "response": {"results": [{"id": "H1"}]}}}, worker_latency_us=FAST)
    h.send(0, [manifest_event([HOMES])])
    h.send(100_000, [chunk_event("homes in Austin")])
    h.send(150_000, [eot_event()])
    drain(h, 2_000_000)
    finished = h.store.goals.all()[0].goal_id
    # A goal created later but parked (suspended), never finished.
    def park_late_goal(txn, now_us, step_no):
        txn.store.goals.create(GoalRecord(goal_id="g-late", status=GoalStatus.SUSPENDED, created_turn="t-x",
                                          created_step=10_000, task_state=TaskState.EXECUTING))

    h.send(h.clock.now_us() + 10_000, inject=park_late_goal)
    assert replies.last_finished_goal(h.store).goal_id == finished
    assert "search homes (Austin)" in replies.status_text(h.store)


def test_traced_queue_records_put_nowait(tmp_path):
    """Finding 3: the kernel writes actions with put_nowait()."""
    path = tmp_path / "room.wire.jsonl"

    async def run():
        q = TracedQueue(path, "out")
        q.put_nowait({"action_type": "speak", "body": {"text": "hi"}})
        await q.put({"action_type": "final", "body": {"text": "bye"}})

    asyncio.run(run())
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["action_type"] for r in lines] == ["speak", "final"]


def test_set_override_for_a_tuple_field():
    """Finding 4: --set read_only_tools=a,b must become a tuple."""
    config = apply_overrides(fdb_v3_config(), ["read_only_tools=search_flights, track_order"])
    assert config.read_only_tools == ("search_flights", "track_order")
