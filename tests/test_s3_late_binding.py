"""S3 Increment II (`docs-personal/private-docs/s3_plan_2026-09-28.md`):
late binding of chained arguments (`kernel/binder.py`, `JobKind.BIND`).

A chained argument ("add the one you find", "the commute from the cheapest
one") is chosen from the upstream step's *real* result once it exists:
through a deterministic fast path when the named field is unambiguous,
otherwise through a BIND job. Tools are READ-declared so corrections can
re-run steps without the separate duplicate-write guard getting involved.
"""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, FactStatus, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

FIND_TOOL = {
    "name": "find_items",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}
ADD_TOOL = {
    "name": "add_item",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"item_id": {"type": "string"}, "quantity": {"type": "integer"}},
        "required": ["item_id"],
    },
}
HOMES_TOOL = {
    "name": "search_homes",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
}
COMMUTE_TOOL = {
    "name": "get_commute",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"origin": {"type": "string"}, "destination": {"type": "string"}},
        "required": ["origin", "destination"],
    },
}

FAST = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000, JobKind.EXTRACT: 10_000, JobKind.BIND: 10_000}
TOOLS = {
    "find_items": {"latency_ms": 200, "response": {"items": [{"item_id": "P1", "price": 40}]}},
    "add_item": {"latency_ms": 200, "response": {"cart_total": 80}},
    "search_homes": {"latency_ms": 200, "response": {"results": [{"id": "H1", "price": 1700}, {"id": "H2", "price": 1900}]}},
    "get_commute": {"latency_ms": 200, "response": {"minutes": 20}},
}


def config(**overrides) -> Config:
    return Config(action_plans_enabled=True, multi_action_enabled=True, **overrides)


def harness(provider: ScriptedProvider, tools=None, worker=None, **overrides) -> SimHarness:
    return SimHarness(config(**overrides), seed=1, provider=provider, tools=tools or TOOLS, worker_latency_us=worker or FAST)


def assert_clean(h: SimHarness) -> None:
    violations = TraceChecker().check(h.run_log.reports, store=h.store)
    assert violations == [], violations


def send(h: SimHarness, ts_us: int, events: list[dict]) -> list:
    return [er.action for er in h.send(ts_us, events).emit_report.emitted]


def tool_calls(actions) -> list[tuple[str, dict]]:
    return [(a.body.tool_name, dict(a.body.arguments)) for a in actions if a.action_type == ActionType.TOOL_CALL]


def jobs(h: SimHarness, kind: JobKind) -> list:
    return [j for j in h.store.jobs.all() if j.kind == kind]


def new_goal(actions: list[dict]) -> dict:
    return {"act": "new_goal", "intent": actions[0]["tool"], "slot_deltas": [], "actions": actions}


FIND_THEN_ADD = new_goal([
    {"tool": "find_items", "args": {"query": "coffee maker"}},
    {"tool": "add_item", "args": {"quantity": 2}, "refs": [{"param": "item_id", "from": 0, "field": "item_id", "select": "the one found"}]},
])
HOMES_THEN_COMMUTE = new_goal([
    {"tool": "search_homes", "args": {"city": "Austin"}},
    {"tool": "get_commute", "args": {"origin": "there", "destination": "the office"},
     "refs": [{"param": "origin", "from": 0, "field": "address", "select": "the cheapest one"}]},
])


def run_to_final(h: SimHarness, transcript: str) -> list:
    h.send(100_000, [chunk_event(transcript)])
    actions = send(h, 150_000, [eot_event()])
    return actions + drain(h, 3_000_000)


def test_chained_ref_binds_through_the_fast_path_without_a_model_call():
    provider = ScriptedProvider()
    provider.register("interpret", "coffee", FIND_THEN_ADD)
    provider.register("compose", "a1", {"text": "Added two to your cart.", "claims": []})
    h = harness(provider)
    h.send(0, [manifest_event([FIND_TOOL, ADD_TOOL])])
    actions = run_to_final(h, "find a coffee maker and add two of the one you find")
    assert tool_calls(actions) == [("find_items", {"query": "coffee maker"}), ("add_item", {"item_id": "P1", "quantity": 2})]
    assert jobs(h, JobKind.BIND) == [] and jobs(h, JobKind.PLAN) == []
    assert [a for a in actions if a.action_type == ActionType.FINAL]
    assert_clean(h)


def test_chained_ref_binds_through_a_bind_job_when_the_field_is_missing():
    """The result has no `address` at all (the FDB mock's own shape) -- the
    BIND job picks the chosen item's id from what actually came back; the
    literal placeholder "there" is never sent."""
    provider = ScriptedProvider()
    provider.register("interpret", "Austin", HOMES_THEN_COMMUTE)
    provider.register("bind", "get_commute", {"args": {"origin": "H1"}})
    provider.register("compose", "a1", {"text": "About 20 minutes.", "claims": []})
    h = harness(provider)
    h.send(0, [manifest_event([HOMES_TOOL, COMMUTE_TOOL])])
    actions = run_to_final(h, "search homes in Austin and check the commute from the cheapest one to the office")
    assert tool_calls(actions) == [("search_homes", {"city": "Austin"}), ("get_commute", {"origin": "H1", "destination": "the office"})]
    assert len(jobs(h, JobKind.BIND)) == 1 and jobs(h, JobKind.PLAN) == []
    assert [a for a in actions if a.action_type == ActionType.CLARIFY] == []
    assert_clean(h)


def test_bind_gives_up_honestly_after_repeated_failures():
    provider = ScriptedProvider()
    provider.register("interpret", "Austin", HOMES_THEN_COMMUTE)
    # No "bind" rule: every BIND attempt raises LookupError -> a failed job.
    h = harness(provider, max_interpret_retries=1)
    h.send(0, [manifest_event([HOMES_TOOL, COMMUTE_TOOL])])
    actions = run_to_final(h, "search homes in Austin and check the commute from the cheapest one to the office")
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1 and finals[0].body.task_completed is False
    assert tool_calls(actions) == [("search_homes", {"city": "Austin"})]
    assert len(jobs(h, JobKind.BIND)) == 2  # max_interpret_retries + 1 attempts, then honest give-up
    assert_clean(h)


def test_upstream_rerun_rebinds_without_livelock():
    """a1's chained value was bound from a0's result; the user then corrects
    a0's query while a1 is in flight. a0 re-runs, a1's bind is re-grounded
    (same value -- the mock returns the same item) and a1 re-runs once --
    no re-dispatch loop, one FINAL."""
    provider = ScriptedProvider()
    provider.register("interpret", "espresso", {"act": "slot_update", "slot_deltas": [
        {"name": "query", "scope": "goal", "op": "set", "value": "espresso machine"}]})
    provider.register("interpret", "coffee", FIND_THEN_ADD)
    provider.register("compose", "a1", {"text": "Added two to your cart.", "claims": []})
    tools = {**TOOLS, "add_item": {"latency_ms": 1_000, "response": {"cart_total": 80}}}
    h = harness(provider, tools=tools)
    h.send(0, [manifest_event([FIND_TOOL, ADD_TOOL])])
    h.send(100_000, [chunk_event("find a coffee maker and add two of the one you find")])
    actions = send(h, 150_000, [eot_event()])
    actions += drain(h, 590_000, stop_on_final=False)
    assert tool_calls(actions)[-1] == ("add_item", {"item_id": "P1", "quantity": 2})
    actions += send(h, 600_000, [chunk_event("actually make it an espresso machine")])
    actions += send(h, 650_000, [eot_event()])
    actions += drain(h, 4_000_000)
    calls = tool_calls(actions)
    assert calls.count(("find_items", {"query": "espresso machine"})) == 1
    assert calls[-1] == ("add_item", {"item_id": "P1", "quantity": 2})
    assert calls.count(("add_item", {"item_id": "P1", "quantity": 2})) == 2  # the cancelled one, then the re-bound one
    assert len([a for a in actions if a.action_type == ActionType.FINAL]) == 1
    assert_clean(h)


def test_stale_bind_result_is_dropped_not_counted_as_a_failure():
    """The BIND job is slow; the upstream step re-runs while it is in
    flight. Its result is grounded in the superseded result, so it is
    dropped (K4) -- and not counted toward the give-up budget."""
    provider = ScriptedProvider()
    provider.register("interpret", "Dallas", {"act": "slot_update", "slot_deltas": [
        {"name": "city", "scope": "goal", "op": "set", "value": "Dallas"}]})
    provider.register("interpret", "Austin", HOMES_THEN_COMMUTE)
    provider.register("bind", "get_commute", {"args": {"origin": "H1"}})
    provider.register("compose", "a1", {"text": "About 20 minutes.", "claims": []})
    worker = {**FAST, JobKind.BIND: 400_000}
    h = harness(provider, worker=worker)
    h.send(0, [manifest_event([HOMES_TOOL, COMMUTE_TOOL])])
    h.send(100_000, [chunk_event("search homes in Austin and check the commute from the cheapest one to the office")])
    actions = send(h, 150_000, [eot_event()])
    actions += drain(h, 440_000, stop_on_final=False)  # a0 consumed at ~370ms; BIND in flight until ~770ms
    assert len(jobs(h, JobKind.BIND)) == 1
    actions += send(h, 450_000, [chunk_event("actually Dallas")])
    actions += send(h, 500_000, [eot_event()])
    actions += drain(h, 4_000_000)
    assert tool_calls(actions)[-1] == ("get_commute", {"origin": "H1", "destination": "the office"})
    assert ("search_homes", {"city": "Dallas"}) in tool_calls(actions)
    assert len(jobs(h, JobKind.BIND)) == 2
    gid = h.store.goals.all()[0].goal_id
    failures = h.store.facts.get(f"bindfail.{gid}.a1")
    assert failures is None or failures.status == FactStatus.RETRACTED
    assert_clean(h)


def test_invalid_ref_falls_back_to_plan():
    provider = ScriptedProvider()
    provider.register("interpret", "coffee", new_goal([
        {"tool": "find_items", "args": {"query": "coffee maker"}, "refs": [{"param": "query", "from": 1}]},
        {"tool": "add_item", "args": {"item_id": "P9"}},
    ]))
    provider.register("plan", "find_items", {"steps": [{"local_id": "s1", "tool": "find_items", "kind": "read",
        "bindings": {"query": {"type": "fact", "key": "slot.$G.query"}}, "after": []}]})
    provider.register("compose", "s1", {"text": "Found one.", "claims": []})
    h = harness(provider)
    h.send(0, [manifest_event([FIND_TOOL, ADD_TOOL])])
    run_to_final(h, "find a coffee maker")
    assert len(jobs(h, JobKind.PLAN)) == 1
    assert h.store.plans.current(h.store.goals.all()[0].goal_id).origin == "planner"


def test_late_stale_check_fires_on_a_tampered_binding(monkeypatch):
    """Fault injection for sim/checker.py's LATE-STALE: the executor is made
    to send a value other than the bind fact it read. The checker must say
    so -- a clean trace here would mean the check can never fire."""
    import dataclasses

    from prism_rt.kernel import executor

    real = executor.valid_bind_fact

    def tampered(store, key):
        fact = real(store, key)
        return None if fact is None else dataclasses.replace(fact, value="TAMPERED")

    monkeypatch.setattr(executor, "valid_bind_fact", tampered)
    provider = ScriptedProvider()
    provider.register("interpret", "Austin", HOMES_THEN_COMMUTE)
    provider.register("bind", "get_commute", {"args": {"origin": "H1"}})
    provider.register("compose", "a1", {"text": "About 20 minutes.", "claims": []})
    h = harness(provider)
    h.send(0, [manifest_event([HOMES_TOOL, COMMUTE_TOOL])])
    actions = run_to_final(h, "search homes in Austin and check the commute from the cheapest one to the office")
    assert ("get_commute", {"origin": "TAMPERED", "destination": "the office"}) in tool_calls(actions)
    violations = TraceChecker().check(h.run_log.reports, store=h.store)
    assert [v for v in violations if v.invariant == "LATE-STALE"], violations


def test_late_binding_replay_identity():
    def run_once() -> list:
        provider = ScriptedProvider()
        provider.register("interpret", "Austin", HOMES_THEN_COMMUTE)
        provider.register("bind", "get_commute", {"args": {"origin": "H1"}})
        provider.register("compose", "a1", {"text": "About 20 minutes.", "claims": []})
        h = harness(provider)
        h.send(0, [manifest_event([HOMES_TOOL, COMMUTE_TOOL])])
        run_to_final(h, "search homes in Austin and check the commute from the cheapest one to the office")
        assert_clean(h)
        return [
            {"step_no": r.step_no, "emitted": [(er.action.action_type.value, getattr(er.action.body, "text", None)) for er in r.emit_report.emitted]}
            for r in h.run_log.reports
        ]

    assert run_once() == run_once()
