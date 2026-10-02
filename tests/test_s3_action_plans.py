"""S3 Increment I (`docs-personal/private-docs/s3_plan_2026-09-28.md`):
per-action interpretation compiled straight into a plan
(`kernel/action_plans.py`, `Config.action_plans_enabled`) plus the
assume-and-say policy (`Config.fill_unstated_required_enabled`).

Every flag defaults off -- the untouched 359-test suite passing unchanged
is the flag-off check; `test_flag_off_ignores_actions` is the explicit
negative control. Tools here are READ-declared unless a test is about
writes, so a correction can re-run a step without the (correct, separate)
duplicate-write guard getting in the way.
"""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.kernel.action_plans import coerce_to_schema
from prism_rt.kernel.proposals import parse_interpretation
from prism_rt.model.types import ActionType, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

SEARCH_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"destination": {"type": "string"}}, "required": ["destination"]},
}
WEATHER_TOOL = {
    "name": "get_weather",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"destination": {"type": "string"}}, "required": ["destination"]},
}
ORDER_TOOL = {
    "name": "check_order",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
}
HOMES_TOOL = {
    "name": "search_homes",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}, "bedrooms": {"type": "integer"}},
        "required": ["city", "bedrooms"],
    },
}
BOOK_TOOL = {
    "name": "book_flight",
    "mutability": "state_changing",
    "parameters": {"type": "object", "properties": {"destination": {"type": "string"}}, "required": ["destination"]},
}

FAST_LATENCY = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000, JobKind.EXTRACT: 10_000}
TOOLS = {
    "search_flights": {"latency_ms": 200, "response": {"flight_id": "AI-1"}},
    "get_weather": {"latency_ms": 200, "response": {"forecast": "sunny"}},
    "check_order": {"latency_ms": 200, "response": {"status": "shipped"}},
    "search_homes": {"latency_ms": 200, "response": {"results": [{"id": "H1"}]}},
    "book_flight": {"latency_ms": 200, "response": {"booking_ref": "B1"}},
}


def s3_config(**overrides) -> Config:
    return Config(action_plans_enabled=True, multi_action_enabled=True, **overrides)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def tool_calls(actions) -> list[tuple[str, dict]]:
    return [(a.body.tool_name, dict(a.body.arguments)) for a in actions if a.action_type == ActionType.TOOL_CALL]


def plan_jobs(h: SimHarness) -> list:
    return [j for j in h.store.jobs.all() if j.kind == JobKind.PLAN]


def new_goal(actions: list[dict], **extra) -> dict:
    return {"act": "new_goal", "intent": actions[0]["tool"], "slot_deltas": [], "actions": actions, **extra}


def harness(config: Config, provider: ScriptedProvider, tools=None) -> SimHarness:
    return SimHarness(config, seed=1, provider=provider, tools=tools or TOOLS, worker_latency_us=FAST_LATENCY)


def send(h: SimHarness, ts_us: int, events: list[dict]) -> list:
    """`SimHarness.send` runs exactly one kernel step -- whatever that step
    emits is only in its own report, never in a later `drain`."""
    return [er.action for er in h.send(ts_us, events).emit_report.emitted]


# ---------------------------------------------------------------------------
# Compile: no PLAN job, per-action slots, order, duplicates of one tool
# ---------------------------------------------------------------------------

def test_single_action_compiles_without_a_plan_job():
    provider = ScriptedProvider()
    provider.register("interpret", "Pune", new_goal([{"tool": "search_flights", "args": {"destination": "Pune"}}]))
    provider.register("compose", "a0", {"text": "Found flights to Pune.", "claims": ["result:a0"]})
    h = harness(s3_config(), provider)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 1_000_000)
    assert tool_calls(actions) == [("search_flights", {"destination": "Pune"})]
    assert [a for a in actions if a.action_type == ActionType.FINAL]
    assert plan_jobs(h) == []
    gid = h.store.goals.all()[0].goal_id
    plan = h.store.plans.current(gid)
    assert plan.origin == "compiled" and [s.step_key for s in plan.steps] == ["a0"]
    assert h.store.facts.get(f"slot.{gid}.a0.destination").value == "Pune"
    assert_clean(h)


def test_same_tool_twice_runs_in_order_with_its_own_args():
    provider = ScriptedProvider()
    provider.register("interpret", "orders", new_goal([
        {"tool": "check_order", "args": {"order_id": "12"}},
        {"tool": "check_order", "args": {"order_id": "40"}},
    ]))
    provider.register("compose", "a1", {"text": "Both orders shipped.", "claims": []})
    h = harness(s3_config(), provider)
    h.send(0, [manifest_event([ORDER_TOOL])])
    h.send(100_000, [chunk_event("check orders 12 and 40")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 1_500_000)
    assert tool_calls(actions) == [("check_order", {"order_id": "12"}), ("check_order", {"order_id": "40"})]
    assert plan_jobs(h) == []
    assert_clean(h)


def test_identical_actions_collapse_to_one_call():
    provider = ScriptedProvider()
    provider.register("interpret", "twice", new_goal([
        {"tool": "check_order", "args": {"order_id": "12"}},
        {"tool": "check_order", "args": {"order_id": "12"}},
    ]))
    provider.register("compose", "a0", {"text": "Order 12 shipped.", "claims": []})
    h = harness(s3_config(), provider)
    h.send(0, [manifest_event([ORDER_TOOL])])
    h.send(100_000, [chunk_event("check order 12, check it twice")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 1_000_000)
    assert tool_calls(actions) == [("check_order", {"order_id": "12"})]
    assert_clean(h)


# ---------------------------------------------------------------------------
# Corrections and clarify answers on a compiled goal
# ---------------------------------------------------------------------------

def test_correction_invalidates_only_its_own_action():
    """a0 (search) has consumed; a1 (order lookup) is in flight when the
    user corrects the order number. Only a1 re-runs -- a0's consumed call is
    untouched -- and no PLAN job is ever dispatched."""
    provider = ScriptedProvider()
    provider.register("interpret", "order 41", {"act": "slot_update", "slot_deltas": [
        {"name": "order_id", "scope": "goal", "op": "set", "value": "41"}]})
    provider.register("interpret", "Pune", new_goal([
        {"tool": "search_flights", "args": {"destination": "Pune"}},
        {"tool": "check_order", "args": {"order_id": "12"}},
    ]))
    provider.register("compose", "a1", {"text": "Done.", "claims": []})
    tools = {**TOOLS, "check_order": {"latency_ms": 1_000, "response": {"status": "shipped"}}}
    h = harness(s3_config(), provider, tools)
    h.send(0, [manifest_event([SEARCH_TOOL, ORDER_TOOL])])
    h.send(100_000, [chunk_event("find flights to Pune and check order 12")])
    actions = send(h, 150_000, [eot_event()])
    actions += drain(h, 590_000, stop_on_final=False)
    assert tool_calls(actions) == [("search_flights", {"destination": "Pune"}), ("check_order", {"order_id": "12"})]
    actions += send(h, 600_000, [chunk_event("actually make that order 41")])
    actions += send(h, 650_000, [eot_event()])
    actions += drain(h, 3_000_000)
    calls = tool_calls(actions)
    assert calls.count(("search_flights", {"destination": "Pune"})) == 1
    assert calls[-1] == ("check_order", {"order_id": "41"})
    assert [a for a in actions if a.action_type == ActionType.CANCEL]
    gid = h.store.goals.all()[0].goal_id
    assert h.store.facts.get(f"slot.{gid}.a1.order_id").value == "41"
    assert plan_jobs(h) == []
    assert_clean(h)


COMMUTE_TOOL = {
    "name": "get_commute",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"origin": {"type": "string"}, "mode": {"type": "string", "default": "driving"}},
        "required": ["origin"],
    },
}


def test_correction_stating_a_new_optional_param_reruns_with_it():
    """"make it walking" names an optional parameter the action never had.
    Without the recompile-in-place binding (action_plans.ensure_bindings)
    the call would re-run with its old arguments; without the per-action
    absence key (executor._schema_complete_absence_keys) the in-flight call
    would not even be invalidated."""
    provider = ScriptedProvider()
    provider.register("interpret", "make it walking", {"act": "slot_update", "slot_deltas": [
        {"name": "mode", "scope": "goal", "op": "set", "value": "walking"}]})
    provider.register("interpret", "commute", new_goal([{"tool": "get_commute", "args": {"origin": "home"}}]))
    provider.register("compose", "a0", {"text": "About 20 minutes.", "claims": []})
    tools = {**TOOLS, "get_commute": {"latency_ms": 1_000, "response": {"minutes": 20}}}
    h = harness(s3_config(), provider, tools)
    h.send(0, [manifest_event([COMMUTE_TOOL])])
    h.send(100_000, [chunk_event("how long is my commute from home")])
    actions = send(h, 150_000, [eot_event()])
    actions += drain(h, 390_000, stop_on_final=False)
    assert tool_calls(actions) == [("get_commute", {"origin": "home"})]
    actions += send(h, 400_000, [chunk_event("oh make it walking")])
    actions += send(h, 450_000, [eot_event()])
    actions += drain(h, 3_000_000)
    assert tool_calls(actions)[-1] == ("get_commute", {"origin": "home", "mode": "walking"})
    assert [a for a in actions if a.action_type == ActionType.CANCEL]
    assert plan_jobs(h) == []
    assert_clean(h)


def test_flat_clarify_answer_maps_to_the_asked_actions_slot():
    """Two actions both take `destination`; only the clarify target says
    which one the bare answer "Goa" belongs to."""
    provider = ScriptedProvider()
    provider.register("interpret", "Goa", {"act": "answer_clarification", "slot_deltas": [
        {"name": "destination", "scope": "goal", "op": "set", "value": "Goa"}]})
    provider.register("interpret", "weather", new_goal([
        {"tool": "search_flights", "args": {}},
        {"tool": "get_weather", "args": {"destination": "Rome"}},
    ]))
    provider.register("compose", "a1", {"text": "Done.", "claims": []})
    h = harness(s3_config(), provider)
    h.send(0, [manifest_event([SEARCH_TOOL, WEATHER_TOOL])])
    h.send(100_000, [chunk_event("book me a flight and get the weather in Rome")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 500_000, stop_on_final=False)
    assert [a for a in actions if a.action_type == ActionType.CLARIFY]
    h.send(600_000, [chunk_event("Goa")])
    h.send(650_000, [eot_event()])
    actions += drain(h, 2_000_000)
    assert tool_calls(actions) == [("search_flights", {"destination": "Goa"}), ("get_weather", {"destination": "Rome"})]
    assert plan_jobs(h) == []
    assert_clean(h)


def test_reextract_on_a_compiled_goal_rebinds_without_replanning():
    provider = ScriptedProvider()
    provider.register("interpret", "somewhere warm", new_goal([{"tool": "search_flights", "args": {}}]))
    provider.register("extract", "somewhere warm", {"value": "Goa"})
    provider.register("compose", "a0", {"text": "Found flights to Goa.", "claims": []})
    h = harness(s3_config(clarify_reextract_enabled=True), provider)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("I want to go somewhere warm")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 1_500_000)
    assert tool_calls(actions) == [("search_flights", {"destination": "Goa"})]
    assert [a for a in actions if a.action_type == ActionType.CLARIFY] == []
    assert plan_jobs(h) == []
    assert_clean(h)


def test_addition_appends_to_a_compiled_plan():
    provider = ScriptedProvider()
    provider.register("interpret", "also check", {"act": "addition", "slot_deltas": [], "actions": [
        {"tool": "search_flights", "args": {"destination": "Pune"}},
        {"tool": "check_order", "args": {"order_id": "12"}},
    ]})
    provider.register("interpret", "Pune", new_goal([{"tool": "search_flights", "args": {"destination": "Pune"}}]))
    provider.register("compose", "a1", {"text": "Done.", "claims": []})
    tools = {**TOOLS, "search_flights": {"latency_ms": 800, "response": {"flight_id": "AI-1"}}}
    h = harness(s3_config(), provider, tools)
    h.send(0, [manifest_event([SEARCH_TOOL, ORDER_TOOL])])
    h.send(100_000, [chunk_event("find flights to Pune")])
    actions = send(h, 150_000, [eot_event()])
    actions += drain(h, 390_000, stop_on_final=False)
    actions += send(h, 400_000, [chunk_event("oh and also check order 12")])
    actions += send(h, 450_000, [eot_event()])
    actions += drain(h, 3_000_000)
    assert tool_calls(actions) == [("search_flights", {"destination": "Pune"}), ("check_order", {"order_id": "12"})]
    assert plan_jobs(h) == []
    assert_clean(h)


# ---------------------------------------------------------------------------
# Fallback to the ordinary PLAN path
# ---------------------------------------------------------------------------

def test_unknown_tool_falls_back_to_plan_with_flat_slots():
    provider = ScriptedProvider()
    provider.register("interpret", "Pune", new_goal([
        {"tool": "search_flights", "args": {"destination": "Pune"}},
        {"tool": "teleport", "args": {}},
    ]))
    provider.register("plan", "search_flights", {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read",
        "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]})
    provider.register("compose", "s1", {"text": "Found flights.", "claims": []})
    h = harness(s3_config(), provider)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("find flights to Pune and teleport me")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 1_000_000)
    assert len(plan_jobs(h)) == 1
    assert tool_calls(actions) == [("search_flights", {"destination": "Pune"})]
    assert h.store.plans.current(h.store.goals.all()[0].goal_id).origin == "planner"
    assert_clean(h)


def test_flag_off_ignores_actions():
    provider = ScriptedProvider()
    provider.register("interpret", "Pune", new_goal(
        [{"tool": "search_flights", "args": {"destination": "Pune"}}],
        slot_deltas=[{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
    ))
    provider.register("plan", "search_flights", {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read",
        "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]})
    provider.register("compose", "s1", {"text": "Found flights.", "claims": []})
    h = harness(Config(action_plans_enabled=False), provider)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 1_000_000)
    assert len(plan_jobs(h)) == 1
    assert tool_calls(actions) == [("search_flights", {"destination": "Pune"})]
    assert_clean(h)


def test_malformed_actions_parse_to_empty_and_keep_the_rest():
    raw = {"act": "new_goal", "intent": "search_flights", "actions": [{"tool": "search_flights"}, {"args": {}}]}
    interp = parse_interpretation(raw, turn_id="t1", input_digest="")
    assert interp.actions == ()
    assert interp.intent == "search_flights"
    bad_ref = {"act": "new_goal", "actions": [{"tool": "x", "refs": [{"param": "p", "from": True}]}]}
    assert parse_interpretation(bad_ref, turn_id="t1", input_digest="").actions == ()
    good = {"act": "new_goal", "actions": [{"tool": "x", "args": {"p": 1}, "refs": [{"param": "q", "from": "0", "field": "id"}]}]}
    parsed = parse_interpretation(good, turn_id="t1", input_digest="").actions
    assert parsed[0].tool == "x" and parsed[0].refs[0].source == 0 and parsed[0].refs[0].field == "id"


# ---------------------------------------------------------------------------
# Assume-and-say (fill_unstated_required_enabled)
# ---------------------------------------------------------------------------

def test_assumed_numeric_param_is_filled_and_said_in_the_ack():
    provider = ScriptedProvider()
    provider.register("interpret", "Austin", new_goal(
        [{"tool": "search_homes", "args": {"city": "Austin", "bedrooms": 1}, "assumed": ["bedrooms"]}],
        ack_phrase="I'll search homes in Austin, assuming 1 bedroom.",
    ))
    provider.register("compose", "a0", {"text": "Found one home.", "claims": []})
    h = harness(s3_config(fill_unstated_required_enabled=True, echo_ack_enabled=True), provider)
    h.send(0, [manifest_event([HOMES_TOOL])])
    h.send(100_000, [chunk_event("search homes in Austin")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 1_000_000)
    assert tool_calls(actions) == [("search_homes", {"city": "Austin", "bedrooms": 1})]
    acks = [a.body.text for a in actions if a.action_type == ActionType.SPEAK and a.body.kind == "ack"]
    assert acks == ["I'll search homes in Austin, assuming 1 bedroom."]
    gid = h.store.goals.all()[0].goal_id
    assert h.store.facts.get(f"slot.{gid}.a0.bedrooms").provenance.source == "assumed"
    assert_clean(h)


def test_assumed_free_text_param_is_asked_instead():
    provider = ScriptedProvider()
    provider.register("interpret", "3 bedrooms", new_goal(
        [{"tool": "search_homes", "args": {"city": "Springfield", "bedrooms": 3}, "assumed": ["city"]}]))
    h = harness(s3_config(fill_unstated_required_enabled=True), provider)
    h.send(0, [manifest_event([HOMES_TOOL])])
    h.send(100_000, [chunk_event("find me 3 bedrooms")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 600_000, stop_on_final=False)
    clarifies = [a for a in actions if a.action_type == ActionType.CLARIFY]
    assert len(clarifies) == 1 and "city" in clarifies[0].body.text
    assert tool_calls(actions) == []


def test_assumed_values_are_dropped_when_the_policy_is_off():
    provider = ScriptedProvider()
    provider.register("interpret", "Austin", new_goal(
        [{"tool": "search_homes", "args": {"city": "Austin", "bedrooms": 1}, "assumed": ["bedrooms"]}]))
    h = harness(s3_config(fill_unstated_required_enabled=False), provider)
    h.send(0, [manifest_event([HOMES_TOOL])])
    h.send(100_000, [chunk_event("search homes in Austin")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 600_000, stop_on_final=False)
    clarifies = [a for a in actions if a.action_type == ActionType.CLARIFY]
    assert len(clarifies) == 1 and "bedrooms" in clarifies[0].body.text


# ---------------------------------------------------------------------------
# ACK, speculative promotion, coercion, replay identity
# ---------------------------------------------------------------------------

def test_compiled_plan_acks_once_with_no_second_on_it_notice():
    """A write held by the settle barrier would otherwise also get the S-10
    "On it — ..." notice in the same step as the compiled-plan ACK."""
    provider = ScriptedProvider()
    provider.register("interpret", "Pune", new_goal([{"tool": "book_flight", "args": {"destination": "Pune"}}],
                                                    commit_intent=True, ack_phrase="I'll book a flight to Pune."))
    provider.register("compose", "a0", {"text": "Booked.", "claims": []})
    h = harness(s3_config(echo_ack_enabled=True), provider)
    h.send(0, [manifest_event([BOOK_TOOL])])
    h.send(100_000, [chunk_event("book a flight to Pune")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 3_000_000)
    speaks = [a.body.text for a in actions if a.action_type == ActionType.SPEAK]
    assert speaks == ["I'll book a flight to Pune."]
    assert tool_calls(actions) == [("book_flight", {"destination": "Pune"})]
    assert_clean(h)


def test_speculative_promotion_compiles_the_plan():
    provider = ScriptedProvider()
    provider.register("interpret", "Pune", new_goal([{"tool": "search_flights", "args": {"destination": "Pune"}}]))
    provider.register("compose", "a0", {"text": "Found flights to Pune.", "claims": []})
    h = harness(s3_config(speculative_interpretation_enabled=True), provider)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    drain(h, 290_000, stop_on_final=False)  # the speculative INTERPRET (10ms) is long done by EOT
    actions = send(h, 300_000, [eot_event()])
    actions += drain(h, 1_200_000)
    assert tool_calls(actions) == [("search_flights", {"destination": "Pune"})]
    assert plan_jobs(h) == []
    assert len([j for j in h.store.jobs.all() if j.kind == JobKind.INTERPRET]) == 1
    assert_clean(h)


def test_coerce_to_schema():
    assert coerce_to_schema(2500, {"type": "string"}) == "2500"
    assert coerce_to_schema(2500.0, {"type": "string"}) == "2500"
    assert coerce_to_schema(True, {"type": "string"}) == "true"
    assert coerce_to_schema("2", {"type": "integer"}) == 2
    assert coerce_to_schema(2.0, {"type": "integer"}) == 2
    assert coerce_to_schema("$1,800", {"type": "number"}) == 1800
    assert coerce_to_schema("1800 a month", {"type": "number"}) == 1800
    assert coerce_to_schema("12.5", {"type": "number"}) == 12.5
    assert coerce_to_schema("yes", {"type": "boolean"}) is True
    assert coerce_to_schema("No", {"type": "boolean"}) is False
    assert coerce_to_schema("Passport", {"type": "string", "enum": ["passport", "id_card"]}) == "passport"
    assert coerce_to_schema("Pune", {"type": "string"}) == "Pune"
    assert coerce_to_schema("lots", {"type": "integer"}) == "lots"  # uncoercible: left for validation


def test_spoken_amount_for_a_string_parameter_is_the_bare_number():
    # 2 Oct voice run: a string-typed filter value was sent as "$1,800 a month".
    s = {"type": "string"}
    assert coerce_to_schema("$1,800 a month", s) == "1800"
    assert coerce_to_schema("1800 a month", s) == "1800"
    assert coerce_to_schema("2,500 dollars", s) == "2500"
    assert coerce_to_schema("$19.99", s) == "19.99"
    # No currency and no period: left exactly as said (could be an ID).
    assert coerce_to_schema("1800", s) == "1800"
    assert coerce_to_schema("0042", s) == "0042"
    assert coerce_to_schema("3 bedrooms", s) == "3 bedrooms"
    assert coerce_to_schema("Northside", s) == "Northside"


def test_action_plans_replay_identity():
    def run_once() -> list:
        provider = ScriptedProvider()
        provider.register("interpret", "orders", new_goal([
            {"tool": "search_flights", "args": {"destination": "Pune"}},
            {"tool": "check_order", "args": {"order_id": "12"}},
        ], ack_phrase="I'll search flights to Pune and check order 12."))
        provider.register("compose", "a1", {"text": "Done.", "claims": []})
        h = harness(s3_config(echo_ack_enabled=True), provider)
        h.send(0, [manifest_event([SEARCH_TOOL, ORDER_TOOL])])
        h.send(100_000, [chunk_event("flights to Pune and check orders 12")])
        h.send(150_000, [eot_event()])
        drain(h, 1_500_000)
        assert_clean(h)
        return [
            {"step_no": r.step_no, "emitted": [(er.action.action_type.value, er.action.body.text if hasattr(er.action.body, "text") else None) for er in r.emit_report.emitted]}
            for r in h.run_log.reports
        ]

    assert run_once() == run_once()
