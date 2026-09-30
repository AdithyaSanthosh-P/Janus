"""30 Sep voice runs (housing_09, housing_14): a search went out with an
assumed budget (max_price=10000) in the pause before the user said "around two
thousand", then ran again with 2000 -- an extra call FDB counts as a failure.
With assumed_value_settle_enabled (FDB profile) a call carrying an assumed
value waits the longer settle window."""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.model.types import ActionType, JobKind
from prism_rt.profiles import fdb_v3_config
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

APT = {"name": "search_apartments", "parameters": {"type": "object", "properties": {
    "city": {"type": "string"}, "bedrooms": {"type": "integer"}, "max_price": {"type": "number"}},
    "required": ["city", "bedrooms", "max_price"]}}
LATENCY = {k: 10_000 for k in (JobKind.INTERPRET, JobKind.PLAN, JobKind.COMPOSE, JobKind.EXTRACT, JobKind.BIND)}


def _run(**overrides) -> list:
    provider = ScriptedProvider()
    provider.register("interpret", "max price around", {
        "act": "slot_update", "slot_deltas": [{"name": "a0.max_price", "scope": "goal", "op": "set", "value": 2000}]})
    provider.register("interpret", "two-bedroom in Chicago", {
        "act": "new_goal", "intent": "search_apartments", "slot_deltas": [],
        "actions": [{"tool": "search_apartments", "args": {"city": "Chicago", "bedrooms": 2, "max_price": 10000},
                     "assumed": ["max_price"]}]})
    provider.register("compose", "a0", {"text": "Found one.", "claims": []})
    h = SimHarness(fdb_v3_config(**overrides), seed=1, provider=provider,
                   tools={"search_apartments": {"latency_ms": 300, "response": {"results": [{"id": "APT1"}]}}},
                   worker_latency_us=LATENCY)
    h.send(0, [manifest_event([APT])])
    h.send(100_000, [chunk_event("I'm interested in a two-bedroom in Chicago")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 2_150_000, stop_on_final=False)  # a 2 s pause, then the rest of the sentence
    h.send(2_200_000, [chunk_event("and keep the max price around two thousand a month")])
    actions += [er.action for er in h.send(2_250_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 8_000_000)
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []
    return [dict(a.body.arguments) for a in actions if a.action_type == ActionType.TOOL_CALL]


def test_a_call_with_an_assumed_value_waits_for_the_rest_of_the_sentence():
    assert _run() == [{"city": "Chicago", "bedrooms": 2, "max_price": 2000}]


def test_without_the_hold_the_assumed_value_goes_out_first():
    """Negative control: the live failure."""
    calls = _run(assumed_value_settle_enabled=False)
    assert [c["max_price"] for c in calls] == [10000, 2000]
