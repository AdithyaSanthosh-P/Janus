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


# --- 1 Oct audit R04: an equivalent restatement is not a change -------------

FILTER = {"name": "update_search_filter", "parameters": {"type": "object", "properties": {
    "filter_name": {"type": "string"}, "value": {"type": "string"}}, "required": ["filter_name", "value"]}}
APT_DALLAS = {"name": "search_apartments", "parameters": {"type": "object", "properties": {
    "city": {"type": "string"}, "max_price": {"type": "number"}}, "required": ["city"]}}


def _restatement_run(restated: str):
    """housing_24 (voice): "update my max price to $1500 then search in Dallas",
    then, after the filter update went out, "because I'm on a tighter budget"
    read as a slot update restating the value in another form."""
    provider = ScriptedProvider()
    provider.register("interpret", "tighter budget", {"act": "slot_update", "slot_deltas": [
        {"name": "value", "op": "set", "value": restated}]})
    provider.register("interpret", "max price", {
        "act": "new_goal", "intent": "update_search_filter", "slot_deltas": [], "commit_intent": True,
        "actions": [{"tool": "update_search_filter", "args": {"filter_name": "max_price", "value": "$1500"}},
                    {"tool": "search_apartments", "args": {"city": "Dallas", "max_price": 1500}}]})
    provider.register("compose", "", {"text": "Done.", "claims": []})
    h = SimHarness(fdb_v3_config(), seed=1, provider=provider,
                   tools={"update_search_filter": {"latency_ms": 400, "response": {"status": "success"}},
                          "search_apartments": {"latency_ms": 100, "response": {"results": []}}},
                   worker_latency_us={k: 10_000 for k in JobKind})
    h.send(0, [manifest_event([FILTER, APT_DALLAS])])
    h.send(100_000, [chunk_event("first update my max price to $1500 then search for apartments in Dallas")])
    actions = [er.action for er in h.send(200_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 1_250_000, stop_on_final=False)
    h.send(1_250_000, [chunk_event("Because I'm on a tighter budget.")])
    actions += [er.action for er in h.send(1_350_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 8_000_000)
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []
    return actions


import pytest  # noqa: E402


@pytest.mark.parametrize("restated", ["1500", "$1,500", "1500 dollars"])
def test_restating_a_written_value_in_another_form_changes_nothing(restated):
    actions = _restatement_run(restated)
    calls = [a.body.tool_name for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert calls == ["update_search_filter", "search_apartments"]
    assert not [a for a in actions if a.action_type == ActionType.CANCEL]
    final = [a.body.text for a in actions if a.action_type == ActionType.FINAL]
    assert final == ["Done."]


@pytest.mark.parametrize("a, b", [
    ("$1500", "1500"), ("$1,500", 1500), (1500, 1500.0), ("1500 dollars", "$1500"), ("€20", "20 euros"),
    (True, "true"), ("Dallas", "dallas"), ("the gym ", "The gym."), ("BOB12", "bob12"),
])
def test_equivalent_surface_forms(a, b):
    from prism_rt.canonical import equivalent_values

    assert equivalent_values(a, b) and equivalent_values(b, a)


@pytest.mark.parametrize("a, b", [
    ("$1500", "15000"), ("1500", "1500.5"), (1, True), ("Pune", "Mumbai"), (False, "true"),
    ("1-bedroom", "2-bedroom"), ("FAST99", "FAST 99"), ({"x": 1}, {"x": 2}), ("10", "ten"),
])
def test_different_values_stay_different(a, b):
    from prism_rt.canonical import equivalent_values

    assert not equivalent_values(a, b) and not equivalent_values(b, a)
