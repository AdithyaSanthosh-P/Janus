"""Config.conditional_actions_enabled: "if X, do A; otherwise B" runs only
the branch the earlier action's real result supports.

Found in the 3 Oct voice run (ecommerce_20): every action mentioned was
run, so an item was added to the cart under "only if it's under $50" when
the only one found cost more. The Interpreter now marks each conditional
action with `only_if` (the earlier action that decides it and the test in
words); kernel/binder.py checks the test against that action's result in a
BIND job before the step may run, and the answer says what was not done.
Tools are READ-declared, as in test_s3_late_binding.py.
"""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.kernel.proposals import _parse_actions
from prism_rt.model.types import ActionType, JobKind
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
TRACK_TOOL = {
    "name": "track_parcel",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"parcel_id": {"type": "string"}}, "required": ["parcel_id"]},
}

FAST = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000, JobKind.EXTRACT: 10_000, JobKind.BIND: 10_000}
TOOLS = {
    "find_items": {"latency_ms": 200, "response": {"items": [{"item_id": "P1", "price": 40}]}},
    "add_item": {"latency_ms": 200, "response": {"cart_total": 80}},
    "track_parcel": {"latency_ms": 200, "response": {"status": "in transit"}},
}

FIND_ADD_OR_TRACK = {
    "act": "new_goal",
    "intent": "find_items",
    "slot_deltas": [],
    "actions": [
        {"tool": "find_items", "args": {"query": "lamp"}},
        {
            "tool": "add_item",
            "args": {"quantity": 2},
            "refs": [{"param": "item_id", "from": 0, "field": "item_id", "select": "the one found"}],
            "only_if": {"from": 0, "test": "a lamp under $30 was found"},
        },
        {"tool": "track_parcel", "args": {"parcel_id": "Z9"}, "only_if": {"from": 0, "test": "every lamp found is $30 or more"}},
    ],
}
REQUEST = "find a lamp; if one is under 30 add two, otherwise just track parcel Z9"


def harness(provider: ScriptedProvider, **overrides) -> SimHarness:
    overrides = {"conditional_actions_enabled": True, **overrides}
    config = Config(action_plans_enabled=True, multi_action_enabled=True, **overrides)
    h = SimHarness(config, seed=1, provider=provider, tools=TOOLS, worker_latency_us=FAST)
    h.send(0, [manifest_event([FIND_TOOL, ADD_TOOL, TRACK_TOOL])])
    return h


def run_to_final(h: SimHarness) -> list:
    h.send(100_000, [chunk_event(REQUEST)])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted]
    return actions + drain(h, 3_000_000)


def tool_calls(actions) -> list[tuple[str, dict]]:
    return [(a.body.tool_name, dict(a.body.arguments)) for a in actions if a.action_type == ActionType.TOOL_CALL]


def finals(actions) -> list:
    return [a for a in actions if a.action_type == ActionType.FINAL]


def assert_clean(h: SimHarness) -> None:
    violations = TraceChecker().check(h.run_log.reports, store=h.store)
    assert violations == [], violations


def test_only_the_branch_the_result_supports_runs():
    """The only lamp costs $40: nothing is added, the parcel is tracked, and
    the answer is told which action was skipped and why."""
    provider = ScriptedProvider()
    provider.register("interpret", "lamp", FIND_ADD_OR_TRACK)
    provider.register("bind", "add_item", {"proceed": False, "args": {}})
    provider.register("bind", "track_parcel", {"proceed": True, "args": {}})
    provider.register("compose", "deliberately NOT run", {"text": "The lamp is $40, so I didn't add it; parcel Z9 is in transit.", "claims": []})
    h = harness(provider)
    actions = run_to_final(h)
    assert tool_calls(actions) == [("find_items", {"query": "lamp"}), ("track_parcel", {"parcel_id": "Z9"})]
    final = finals(actions)
    assert len(final) == 1 and final[0].body.task_completed is True
    assert "didn't add it" in final[0].body.text
    assert_clean(h)


def test_the_other_branch_runs_when_the_condition_holds():
    provider = ScriptedProvider()
    provider.register("interpret", "lamp", FIND_ADD_OR_TRACK)
    provider.register("bind", "add_item", {"proceed": True, "args": {"item_id": "P1"}})
    provider.register("bind", "track_parcel", {"proceed": False, "args": {}})
    provider.register("compose", "deliberately NOT run", {"text": "Added two lamps.", "claims": []})
    h = harness(provider)
    actions = run_to_final(h)
    assert tool_calls(actions) == [("find_items", {"query": "lamp"}), ("add_item", {"item_id": "P1", "quantity": 2})]
    assert len(finals(actions)) == 1 and finals(actions)[0].body.task_completed is True
    assert_clean(h)


def test_an_unanswered_condition_never_runs_the_action():
    """A BIND reply without a verdict counts as a failed check: the action is
    not run on a guess, and the goal gives up honestly after the retry budget."""
    provider = ScriptedProvider()
    provider.register("interpret", "lamp", FIND_ADD_OR_TRACK)
    provider.register("bind", "add_item", {"args": {"item_id": "P1"}})
    provider.register("compose", "a0", {"text": "Found a lamp.", "claims": []})
    h = harness(provider, max_interpret_retries=1)
    actions = run_to_final(h)
    assert ("add_item", {"item_id": "P1", "quantity": 2}) not in tool_calls(actions)
    assert len(finals(actions)) == 1 and finals(actions)[0].body.task_completed is False
    assert_clean(h)


def test_flag_off_runs_every_action_as_before():
    provider = ScriptedProvider()
    provider.register("interpret", "lamp", FIND_ADD_OR_TRACK)
    provider.register("compose", "a2", {"text": "Done.", "claims": []})
    h = harness(provider, conditional_actions_enabled=False)
    actions = run_to_final(h)
    assert [name for name, _ in tool_calls(actions)] == ["find_items", "add_item", "track_parcel"]
    assert [j for j in h.store.jobs.all() if j.kind == JobKind.BIND] == []  # fast path, no condition check
    assert_clean(h)


def test_only_if_parsing():
    parsed = _parse_actions(FIND_ADD_OR_TRACK["actions"])
    assert parsed[0].condition is None
    assert parsed[1].condition.source == 0 and parsed[1].condition.test == "a lamp under $30 was found"
    # A condition with no test is malformed: the whole list falls back to the PLAN path.
    assert _parse_actions([{"tool": "find_items"}, {"tool": "add_item", "only_if": {"from": 0}}]) == ()
    assert _parse_actions([{"tool": "find_items"}, {"tool": "add_item", "only_if": None}])[1].condition is None


def test_replay_identity():
    def run_once() -> list:
        provider = ScriptedProvider()
        provider.register("interpret", "lamp", FIND_ADD_OR_TRACK)
        provider.register("bind", "add_item", {"proceed": False, "args": {}})
        provider.register("bind", "track_parcel", {"proceed": True, "args": {}})
        provider.register("compose", "deliberately NOT run", {"text": "Tracked.", "claims": []})
        h = harness(provider)
        run_to_final(h)
        return [
            [(er.action.action_type.value, getattr(er.action.body, "text", None)) for er in r.emit_report.emitted]
            for r in h.run_log.reports
        ]

    assert run_once() == run_once()
