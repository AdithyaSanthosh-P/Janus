"""2 Oct independent review, verified findings.

F3 (as reproduced, not as described): a speculative INTERPRET result cached
while its turn was open was promoted at end of turn by comparing only the
transcript digest. The job's read set also covers the active goal and its
slots; when the previous turn's interpretation landed after the speculative
result was cached (a slow model call for turn 1, a fast one for turn 2), the
stale proposal -- computed with no active goal -- was applied. "Make it three
bedrooms" was lost and the agent asked for a city it already had."""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.model.types import ActionType, JobKind
from prism_rt.profiles import fdb_v3_config
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

APT = {"name": "search_apartments", "parameters": {"type": "object", "properties": {
    "city": {"type": "string"}, "bedrooms": {"type": "integer"}, "max_price": {"type": "number"}},
    "required": ["city"]}}


def _run(**overrides):
    provider = ScriptedProvider()
    # Turn 2 read with the goal in context: a correction of the running search.
    provider.register("interpret", "active_intent: search_apartments", {"act": "slot_update", "slot_deltas": [
        {"name": "a0.bedrooms", "scope": "goal", "op": "set", "value": 3}]})
    # Turn 2 read with no goal in context: a fresh, city-less search.
    provider.register("interpret", "make it three bedrooms", {
        "act": "new_goal", "intent": "search_apartments", "slot_deltas": [],
        "actions": [{"tool": "search_apartments", "args": {"bedrooms": 3}}]})
    provider.register("interpret", "apartments in Denver", {
        "act": "new_goal", "intent": "search_apartments", "slot_deltas": [],
        "actions": [{"tool": "search_apartments", "args": {"city": "Denver"}}]})
    provider.register("compose", "", {"text": "Found one.", "claims": []})
    provider.register("extract", "", {"value": None})
    h = SimHarness(fdb_v3_config(settle_ms=1500, **overrides), seed=1, provider=provider,
                   tools={"search_apartments": {"latency_ms": 100, "response": {"results": [{"id": "A1"}]}}},
                   worker_latency_us={k: 10_000 for k in JobKind})

    # Turn 1's interpretation is slow (900 ms), turn 2's speculative one fast (50 ms).
    submit = h.runner.submit

    def submit_with_latency(job_id, kind, view, *, dispatched_us):
        submit(job_id, kind, view, dispatched_us=dispatched_us)
        if kind == JobKind.INTERPRET:
            slow = "Denver" in view.get("transcript", "") and "three" not in view.get("transcript", "")
            _due, k, v = h.runner._pending[job_id]
            h.runner._pending[job_id] = (dispatched_us + (900_000 if slow else 50_000), k, v)

    h.runner.submit = submit_with_latency

    h.send(0, [manifest_event([APT])])
    h.send(100_000, [chunk_event("find apartments in Denver")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted]
    h.send(300_000, [chunk_event("and make it three bedrooms")])  # turn 2 opens: speculative job, cached at ~350 ms
    actions += drain(h, 1_200_000, stop_on_final=False)  # turn 1 applied at ~1.05 s: the cache is now stale
    actions += [er.action for er in h.send(1_250_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 6_000_000)
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []
    return actions


def test_a_speculative_result_stale_against_the_active_goal_is_not_promoted():
    actions = _run()
    calls = [(a.body.tool_name, dict(a.body.arguments)) for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert calls == [("search_apartments", {"city": "Denver", "bedrooms": 3})]
    assert not [a for a in actions if a.action_type == ActionType.CLARIFY]


def test_same_scenario_without_speculation_is_the_reference():
    actions = _run(speculative_interpretation_enabled=False)
    calls = [(a.body.tool_name, dict(a.body.arguments)) for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert calls == [("search_apartments", {"city": "Denver", "bedrooms": 3})]
