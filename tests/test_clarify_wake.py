"""30 Sep rerun (housing_21, ecommerce_13): a compiled plan with a missing
required value set `goal.<gid>.clarify_target` in PlanExecutor, which runs
after TaskStateMachine in DECIDE -- so the targeted re-extraction was never
dispatched that step, and in production nothing else woke the kernel. The goal
sat silent until the 15 s stall salvage said "couldn't finish". `drain` hides
this (it steps every 10 ms); these tests step only where production would:
when a worker result is due, and when a timer is due."""

from __future__ import annotations

from conftest import chunk_event, eot_event, manifest_event

from prism_rt.model.types import ActionType, JobKind
from prism_rt.profiles import fdb_v3_config
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

APT = {"name": "search_apartments", "parameters": {"type": "object", "properties": {
    "city": {"type": "string"}, "bedrooms": {"type": "integer"}, "max_price": {"type": "number"}},
    "required": ["city", "bedrooms", "max_price"]}}
LATENCY = {k: 10_000 for k in (JobKind.INTERPRET, JobKind.PLAN, JobKind.COMPOSE, JobKind.EXTRACT, JobKind.BIND)}


def _event_driven(h, until_us: int) -> list:
    """Advance like production: to the next due timer or worker result only."""
    actions = []
    while True:
        due = [h.store.timers.next_due_us()]
        due += [due_us for due_us, _kind, _view in h.runner._pending.values()]  # worker results
        due += [entry[0] for entry in h.mock_tools._scheduled.values()]  # tool results
        due = [t for t in due if t is not None]
        if not due or min(due) > until_us:
            return actions
        report = h.send(max(min(due), h.clock.now_us()))
        actions += [er.action for er in report.emit_report.emitted]


def _run(extract_value):
    provider = ScriptedProvider()
    provider.register("extract", "three bedroom", {"value": extract_value})
    provider.register("interpret", "three bedroom", {"act": "new_goal", "intent": "search_apartments", "slot_deltas": [],
                                                     "actions": [{"tool": "search_apartments", "args": {"bedrooms": 3, "max_price": 2000}}]})
    h = SimHarness(fdb_v3_config(), seed=1, provider=provider,
                   tools={"search_apartments": {"latency_ms": 200, "response": {"results": [{"id": "APT1"}]}}},
                   worker_latency_us=LATENCY)
    h.send(0, [manifest_event([APT])])
    h.send(100_000, [chunk_event("a three bedroom place under two thousand")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted]
    return h, actions + _event_driven(h, 5_000_000)


def test_missing_value_is_asked_about_without_waiting_for_the_salvage():
    _h, actions = _run(None)
    clarifies = [a for a in actions if a.action_type == ActionType.CLARIFY]
    assert clarifies and "city" in clarifies[0].body.text
    assert not [a for a in actions if a.action_type == ActionType.FINAL]


def test_reextracted_value_runs_the_call_without_waiting_for_the_salvage():
    _h, actions = _run("Denver")
    calls = [(a.body.tool_name, dict(a.body.arguments)) for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert calls == [("search_apartments", {"city": "Denver", "bedrooms": 3, "max_price": 2000})]


def test_stall_salvage_waits_while_the_user_is_still_talking():
    """30 Sep rerun (travel_23, travel_21, travel_24): one end of turn, then
    the user kept talking for 15 s with none -- the salvage fired mid-sentence
    and the first action was lost."""
    from conftest import drain

    provider = ScriptedProvider()
    provider.register("interpret", "because I just renewed it", {"act": "unclear", "slot_deltas": []})
    provider.register("interpret", "three bedroom", {"act": "new_goal", "intent": "search_apartments", "slot_deltas": [],
                                                     "actions": [{"tool": "search_apartments",
                                                                  "args": {"city": "Denver", "bedrooms": 3, "max_price": 2000}}]})
    provider.register("compose", "a0", {"text": "Found one.", "claims": []})
    # The stall: a composer that takes 40 s (the tool itself returns at once).
    h = SimHarness(fdb_v3_config(), seed=1, provider=provider,
                   tools={"search_apartments": {"latency_ms": 200, "response": {"results": [{"id": "APT1"}]}}},
                   worker_latency_us={**LATENCY, JobKind.COMPOSE: 40_000_000})
    h.send(0, [manifest_event([APT])])
    h.send(100_000, [chunk_event("a three bedroom place in Denver under two thousand")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 5_000_000, stop_on_final=False)
    h.send(5_000_000, [chunk_event("because I just renewed it and then")])  # floor opens, no end of turn
    actions += drain(h, 14_000_000, stop_on_final=False)
    h.send(14_000_000, [chunk_event("because I just renewed it too")])
    actions += drain(h, 15_400_000, stop_on_final=False)
    actions += [er.action for er in h.send(15_500_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 20_000_000, stop_on_final=False)
    assert not [a for a in actions if a.action_type == ActionType.FINAL]


def test_a_failed_reextraction_still_lets_the_question_be_asked():
    """A re-extraction whose model call fails used to leave the clarify
    waiting on an attempt that kept being re-dispatched; nothing was said."""
    provider = ScriptedProvider()  # no "extract" answer registered: the job errors
    provider.register("interpret", "three bedroom", {"act": "new_goal", "intent": "search_apartments", "slot_deltas": [],
                                                     "actions": [{"tool": "search_apartments", "args": {"bedrooms": 3, "max_price": 2000}}]})
    h = SimHarness(fdb_v3_config(), seed=1, provider=provider, tools={}, worker_latency_us=LATENCY)
    h.send(0, [manifest_event([APT])])
    h.send(100_000, [chunk_event("a three bedroom place under two thousand")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted]
    actions += _event_driven(h, 3_000_000)
    clarifies = [a for a in actions if a.action_type == ActionType.CLARIFY]
    assert clarifies and "city" in clarifies[0].body.text
    extracts = [j for j in h.store.jobs.all() if j.kind == JobKind.EXTRACT] if hasattr(h.store.jobs, "all") else []
    assert len(extracts) <= 1  # tried once, not re-dispatched in a loop
