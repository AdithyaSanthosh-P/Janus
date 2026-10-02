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


# --- 1 Oct audit R03/R03b: a write already confirmed this session --------

CART = {"name": "add_to_cart", "parameters": {"type": "object", "properties": {
    "product_id": {"type": "string"}, "quantity": {"type": "integer", "default": 1}}, "required": ["product_id"]}}


def _event_driven(h, until_us: int) -> list:
    actions = []
    while True:
        due = [h.store.timers.next_due_us()]
        due += [d for d, _k, _v in h.runner._pending.values()]
        due += [entry[0] for entry in h.mock_tools._scheduled.values()]
        due = [t for t in due if t is not None]
        if not due or min(due) > until_us:
            return actions
        actions += [er.action for er in h.send(max(min(due), h.clock.now_us())).emit_report.emitted]


def _repeat_write_run(second_args: dict):
    """ecommerce_13 (voice): "could you add item K2" ran add_to_cart{K2}; the
    rest of the sentence, "to my cart, just one of them", asked again with the
    schema default spelled out."""
    provider = ScriptedProvider()
    provider.register("interpret", "just one of them", {"act": "new_goal", "intent": "add_to_cart", "slot_deltas": [],
                      "commit_intent": True, "actions": [{"tool": "add_to_cart", "args": second_args}]})
    provider.register("interpret", "add item K2", {"act": "new_goal", "intent": "add_to_cart", "slot_deltas": [],
                      "commit_intent": True, "actions": [{"tool": "add_to_cart", "args": {"product_id": "K2"}}]})
    provider.register("compose", "", {"text": "Done.", "claims": []})
    h = SimHarness(fdb_v3_config(), seed=1, provider=provider,
                   tools={"add_to_cart": {"latency_ms": 50, "response": {"status": "success"}}},
                   worker_latency_us={k: 10_000 for k in JobKind})
    h.send(0, [manifest_event([CART])])
    h.send(100_000, [chunk_event("could you add item K2")])
    actions = [er.action for er in h.send(200_000, [eot_event()]).emit_report.emitted] + _event_driven(h, 5_000_000)
    h.send(5_000_000, [chunk_event("to my cart just one of them")])
    actions += [er.action for er in h.send(5_100_000, [eot_event()]).emit_report.emitted] + _event_driven(h, 40_000_000)
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []
    return h, actions


@pytest.mark.parametrize("second_args", [{"product_id": "K2"}, {"product_id": "K2", "quantity": 1}])
def test_a_write_already_confirmed_is_reported_done_not_repeated(second_args):
    h, actions = _repeat_write_run(second_args)
    assert [a.body.tool_name for a in actions if a.action_type == ActionType.TOOL_CALL] == ["add_to_cart"]
    later = [a for a in actions if a.ts_us >= 5_000_000]
    spoken = [(a.action_type, a.body.text) for a in later if a.action_type in (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL)]
    assert len(spoken) == 1 and spoken[0][0] == ActionType.FINAL  # no "working on it", no salvage
    assert "already" in spoken[0][1] and "K2" in spoken[0][1]
    assert all(g.status.value == "completed" for g in h.store.goals.all())


# --- 1 Oct audit R06b: one acknowledgement per goal ----------------------------

def test_a_replan_does_not_repeat_the_same_acknowledgement():
    """ecommerce_13 (voice): a missing value was re-extracted, the goal went
    back to planning with a new PLAN job, and the identical ACK was spoken a
    second time 4 s after the first."""
    provider = ScriptedProvider()
    provider.register("interpret", "", {"act": "new_goal", "intent": "add_to_cart", "commit_intent": True,
                      "slot_deltas": [{"name": "quantity", "op": "set", "value": 1}],
                      "ack_phrase": "I'll add one of those items to your cart."})
    provider.register("plan", "", {"steps": [{"local_id": "s1", "tool": "add_to_cart", "kind": "write", "after": [],
                      "bindings": {"product_id": {"type": "fact", "key": "slot.$G.product_id"},
                                   "quantity": {"type": "fact", "key": "slot.$G.quantity"}}}]})
    provider.register("extract", "", {"value": "K2"})
    provider.register("compose", "", {"text": "Added K2.", "claims": []})
    latency = {k: 10_000 for k in JobKind}
    latency.update({JobKind.PLAN: 1_000_000, JobKind.EXTRACT: 800_000})
    h = SimHarness(fdb_v3_config(), seed=1, provider=provider,
                   tools={"add_to_cart": {"latency_ms": 50, "response": {"status": "success"}}}, worker_latency_us=latency)
    h.send(0, [manifest_event([CART])])
    h.send(100_000, [chunk_event("could you add item K2 to my cart, just one of them")])
    actions = [er.action for er in h.send(1_600_000, [eot_event()]).emit_report.emitted] + _event_driven(h, 30_000_000)
    acks = [a.body.text for a in actions if a.action_type == ActionType.SPEAK and a.body.kind == "ack"]
    assert acks == ["I'll add one of those items to your cart."]
    assert [a.body.tool_name for a in actions if a.action_type == ActionType.TOOL_CALL] == ["add_to_cart"]
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


# --- 1 Oct audit R09: independent actions do not wait on a blocked sibling --

TRACK2 = {"name": "track_order", "parameters": {"type": "object", "properties": {
    "order_id": {"type": "string"}}, "required": ["order_id"]}}
PRODUCTS = {"name": "search_products", "parameters": {"type": "object", "properties": {
    "query": {"type": "string"}}, "required": ["query"]}}
FILTER2 = {"name": "update_search_filter", "parameters": {"type": "object", "properties": {
    "filter_name": {"type": "string"}, "value": {"type": "string"}}, "required": ["filter_name", "value"]}}
APT2 = {"name": "search_apartments", "parameters": {"type": "object", "properties": {
    "city": {"type": "string"}}, "required": ["city"]}}


def _multi_action_run(actions_spec, tools, manifest, **overrides):
    provider = ScriptedProvider()
    provider.register("interpret", "", {"act": "new_goal", "intent": actions_spec[0]["tool"], "slot_deltas": [],
                      "actions": actions_spec})
    provider.register("compose", "", {"text": "Done.", "claims": []})
    provider.register("extract", "", {"value": None})
    h = SimHarness(fdb_v3_config(**overrides), seed=1, provider=provider, tools=tools,
                   worker_latency_us={k: 10_000 for k in JobKind})
    h.send(0, [manifest_event(manifest)])
    h.send(100_000, [chunk_event("do these things")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted] + _event_driven(h, 30_000_000)
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []
    return h, actions


def _calls(actions):
    return [(a.ts_us, a.body.tool_name, dict(a.body.arguments)) for a in actions if a.action_type == ActionType.TOOL_CALL]


BLOCKED_THEN_INDEPENDENT = [{"tool": "track_order", "args": {}},  # no order id said: asks, never answered
                            {"tool": "search_products", "args": {"query": "mechanical keyboard"}}]
TOOLS_R09 = {"track_order": {"latency_ms": 50, "response": {"status": "shipped"}},
             "search_products": {"latency_ms": 50, "response": {"results": [{"id": "K2"}]}}}


def test_an_independent_action_runs_while_its_sibling_waits_for_an_answer():
    _h, actions = _multi_action_run(BLOCKED_THEN_INDEPENDENT, TOOLS_R09, [TRACK2, PRODUCTS])
    assert [c[1:] for c in _calls(actions)] == [("search_products", {"query": "mechanical keyboard"})]


def test_without_the_flag_the_blocked_sibling_starves_it():
    """Negative control: the live failure (class K)."""
    _h, actions = _multi_action_run(BLOCKED_THEN_INDEPENDENT, TOOLS_R09, [TRACK2, PRODUCTS],
                                    parallel_independent_actions=False)
    assert _calls(actions) == []


def test_a_read_after_a_write_still_waits_for_it():
    """"Update my filter, then search": the search reads state the write changes."""
    spec = [{"tool": "update_search_filter", "args": {"filter_name": "pets_allowed", "value": "true"}},
            {"tool": "search_apartments", "args": {"city": "Dallas"}}]
    tools = {"update_search_filter": {"latency_ms": 400, "response": {"status": "success"}},
             "search_apartments": {"latency_ms": 50, "response": {"results": []}}}
    _h, actions = _multi_action_run(spec, tools, [FILTER2, APT2])
    calls = _calls(actions)
    assert [c[1] for c in calls] == ["update_search_filter", "search_apartments"]
    assert calls[1][0] >= calls[0][0] + 400_000  # after the update's result (400 ms tool latency)


def test_two_calls_to_the_same_tool_keep_their_order():
    """FDB pairs same-name calls first-in-first-out: "check orders 12 and 40"."""
    spec = [{"tool": "track_order", "args": {"order_id": "12"}}, {"tool": "track_order", "args": {"order_id": "40"}}]
    tools = {"track_order": {"latency_ms": 300, "response": {"status": "shipped"}}}
    _h, actions = _multi_action_run(spec, tools, [TRACK2])
    calls = _calls(actions)
    assert [c[2]["order_id"] for c in calls] == ["12", "40"]
    assert calls[1][0] >= calls[0][0] + 300_000  # the second waited for the first


def test_an_action_that_reads_the_blocked_one_still_waits():
    """"Find a keyboard and add the cheapest to my cart" with no product said:
    the cart step reads the search's result, so it cannot run without it."""
    spec = [{"tool": "search_products", "args": {}},
            {"tool": "add_to_cart", "args": {}, "refs": [{"param": "product_id", "from": 0, "field": "id", "select": "cheapest"}]}]
    tools = {"search_products": {"latency_ms": 50, "response": {"results": [{"id": "K2"}]}},
             "add_to_cart": {"latency_ms": 50, "response": {"status": "success"}}}
    _h, actions = _multi_action_run(spec, tools, [PRODUCTS, CART])
    assert _calls(actions) == []


# --- 1 Oct audit R08: a follow-up can use the finished task's results -------

SEARCH_P = {"name": "search_products", "parameters": {"type": "object", "properties": {
    "query": {"type": "string"}}, "required": ["query"]}}


def test_a_follow_up_can_act_on_the_results_of_the_task_that_just_finished():
    """ecommerce_19 (voice): goal 1 searched and answered before "and add one
    of them to the cart" arrived; the follow-up could not see the results and
    asked for a product id nobody would give. The model below can only name
    the product if the prompt actually shows it."""
    provider = ScriptedProvider()

    def interpret(prompt: str) -> dict:
        if "add one of them" in prompt:
            args = {"product_id": "PROD1", "quantity": 1} if "PROD1" in prompt else {"quantity": 1}
            return {"act": "new_goal", "intent": "add_to_cart", "slot_deltas": [], "commit_intent": True,
                    "actions": [{"tool": "add_to_cart", "args": args}]}
        return {"act": "new_goal", "intent": "search_products", "slot_deltas": [],
                "actions": [{"tool": "search_products", "args": {"query": "tablet"}}]}

    provider.register("interpret", "", interpret)
    provider.register("extract", "", {"value": None})
    provider.register("compose", "", {"text": "Done.", "claims": []})
    h = SimHarness(fdb_v3_config(), seed=1, provider=provider, worker_latency_us={k: 10_000 for k in JobKind},
                   tools={"search_products": {"latency_ms": 50, "response": {"products": [{"product_id": "PROD1", "price": 99.99}]}},
                          "add_to_cart": {"latency_ms": 50, "response": {"status": "success"}}})
    h.send(0, [manifest_event([SEARCH_P, CART])])
    h.send(100_000, [chunk_event("search for a tablet")])
    actions = [er.action for er in h.send(200_000, [eot_event()]).emit_report.emitted] + _event_driven(h, 6_000_000)
    h.send(6_000_000, [chunk_event("and add one of them to the cart")])
    actions += [er.action for er in h.send(6_100_000, [eot_event()]).emit_report.emitted] + _event_driven(h, 40_000_000)
    assert [c[1:] for c in _calls(actions)] == [
        ("search_products", {"query": "tablet"}), ("add_to_cart", {"product_id": "PROD1", "quantity": 1})]
    assert not [a for a in actions if a.action_type == ActionType.CLARIFY]
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


# --- 1 Oct audit (R04b remainder): one failed step does not drop its siblings --

def _failing_first_run(spec, **overrides):
    tools = {"track_order": {"latency_ms": 50, "error": {"code": "not_found", "message": "order not found"}},
             "search_products": {"latency_ms": 50, "response": {"results": [{"id": "K2"}]}},
             "add_to_cart": {"latency_ms": 50, "response": {"status": "success"}}}
    provider = ScriptedProvider()
    provider.register("interpret", "", {"act": "new_goal", "intent": spec[0]["tool"], "slot_deltas": [], "actions": spec})
    provider.register("compose", "", {"text": "Composed.", "claims": []})
    h = SimHarness(fdb_v3_config(**overrides), seed=1, provider=provider, tools=tools,
                   worker_latency_us={k: 10_000 for k in JobKind})
    h.send(0, [manifest_event([TRACK2, PRODUCTS, CART])])
    h.send(100_000, [chunk_event("do these things")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted] + _event_driven(h, 30_000_000)
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []
    return h, actions


def test_an_independent_action_still_runs_after_a_sibling_fails():
    spec = [{"tool": "track_order", "args": {"order_id": "ZZ9"}},
            {"tool": "search_products", "args": {"query": "keyboard"}}]
    _h, actions = _failing_first_run(spec)
    assert [c[1] for c in _calls(actions)] == ["track_order", "search_products"]
    finals = [a.body for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1 and finals[0].task_completed is False  # honest: one action failed


def test_without_the_flag_one_failure_ends_the_goal():
    """Negative control: the live failure (housing_24: 'Missing tools')."""
    spec = [{"tool": "track_order", "args": {"order_id": "ZZ9"}},
            {"tool": "search_products", "args": {"query": "keyboard"}}]
    _h, actions = _failing_first_run(spec, partial_failure_continues=False)
    assert [c[1] for c in _calls(actions)] == ["track_order"]


def test_an_action_that_needs_the_failed_ones_result_fails_with_it():
    spec = [{"tool": "track_order", "args": {"order_id": "ZZ9"}},
            {"tool": "search_products", "args": {"query": "keyboard"}},
            {"tool": "add_to_cart", "args": {}, "refs": [{"param": "product_id", "from": 0, "field": "id", "select": "it"}]}]
    _h, actions = _failing_first_run(spec)
    assert [c[1] for c in _calls(actions)] == ["track_order", "search_products"]
    finals = [a.body for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1 and finals[0].task_completed is False


# --- 1 Oct audit: an empty required argument is not a filled one -----------

COMMUTE = {"name": "calculate_commute", "parameters": {"type": "object", "properties": {
    "origin_address": {"type": "string"}, "destination_address": {"type": "string"},
    "mode": {"type": "string", "default": "driving"}}, "required": ["origin_address", "destination_address"]}}
APT3 = {"name": "search_apartments", "parameters": {"type": "object", "properties": {
    "city": {"type": "string"}}, "required": ["city"]}}


def test_a_blank_value_picked_for_a_required_argument_is_not_sent():
    """housing_17 (voice, both runs): calculate_commute went out with
    origin_address="" -- a required string bound to "" counted as filled."""
    spec = [{"tool": "search_apartments", "args": {"city": "Austin"}},
            {"tool": "calculate_commute", "args": {"destination_address": "the university"},
             "refs": [{"param": "origin_address", "from": 0, "field": "address", "select": "first"}]}]
    provider = ScriptedProvider()
    provider.register("interpret", "", {"act": "new_goal", "intent": "search_apartments", "slot_deltas": [], "actions": spec})
    provider.register("compose", "", {"text": "Composed.", "claims": []})
    provider.register("bind", "", {"values": {"origin_address": ""}})
    h = SimHarness(fdb_v3_config(), seed=1, provider=provider, worker_latency_us={k: 10_000 for k in JobKind},
                   tools={"search_apartments": {"latency_ms": 50, "response": {"results": [{"id": "A1", "address": ""}]}},
                          "calculate_commute": {"latency_ms": 50, "response": {"minutes": 12}}})
    h.send(0, [manifest_event([APT3, COMMUTE])])
    h.send(100_000, [chunk_event("find a place in Austin and the commute from it to the university")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted] + _event_driven(h, 30_000_000)
    calls = _calls(actions)
    assert [c[1] for c in calls] == ["search_apartments"]
    finals = [a.body for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1 and finals[0].task_completed is False
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_validate_args_rejects_a_blank_required_string():
    provider = ScriptedProvider()
    h = SimHarness(fdb_v3_config(), seed=1, provider=provider)
    h.send(0, [manifest_event([COMMUTE])])
    result = h.store.catalog.validate_args("calculate_commute", {"origin_address": "  ", "destination_address": "x"})
    assert not result.valid and "blank_required:origin_address" in result.errors
    assert h.store.catalog.validate_args("calculate_commute", {"origin_address": "home", "destination_address": "x"}).valid
