"""WP1: Config.confirmation_policy_enabled + CommitGate G12.

A tool declared `requires_confirmation` (a lock, a garage door) is never sent
on the strength of the model's commit_intent alone: the kernel asks, and only
an explicit "yes" to that question releases that call id.
"""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

UNLOCK = {"name": "st_unlock", "mutability": "state_changing", "requires_confirmation": True, "repeatable": True,
          "parameters": {"type": "object", "properties": {"device": {"type": "string"}}, "required": ["device"]}}
LOCK = {"name": "st_lock", "mutability": "state_changing", "repeatable": True,
        "parameters": {"type": "object", "properties": {"device": {"type": "string"}}, "required": ["device"]}}

FAST = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000, JobKind.EXTRACT: 10_000, JobKind.BIND: 10_000}


def config(**overrides) -> Config:
    base = dict(action_plans_enabled=True, multi_action_enabled=True, conversational_replies_enabled=True,
                confirmation_policy_enabled=True, settle_barrier_enabled=True, settle_ms=500)
    base.update(overrides)
    return Config(**base)


def new_goal(tool: str, device: str) -> dict:
    return {"act": "new_goal", "intent": tool, "slot_deltas": [], "commit_intent": True,
            "actions": [{"tool": tool, "args": {"device": device}}]}


def start(cfg, utterance_reply, extra=None):
    provider = ScriptedProvider()
    # Most specific first: the interpret prompt carries the earlier turns, so
    # the opening request's key would otherwise match every later turn too.
    provider.register("interpret", "cancel that", {"act": "deny", "slot_deltas": []})
    provider.register("interpret", "yes please", {"act": "confirm", "slot_deltas": []})
    for key, reply in (extra or {}).items():
        provider.register("interpret", key, reply)
    provider.register("interpret", "unlock the front", utterance_reply)
    provider.register("compose", "a0", {"text": "Done.", "claims": []})
    tools = {"st_unlock": {"latency_ms": 200, "response": {"done": True}},
             "st_lock": {"latency_ms": 200, "response": {"done": True}}}
    h = SimHarness(cfg, seed=1, provider=provider, tools=tools, worker_latency_us=FAST)
    h.send(0, [manifest_event([UNLOCK, LOCK])])
    h.send(100_000, [chunk_event("unlock the front door")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 3_000_000, stop_on_final=False)
    return h, actions


def say(h, ts, text, until):
    h.send(ts, [chunk_event(text)])
    actions = [er.action for er in h.send(ts + 50_000, [eot_event()]).emit_report.emitted]
    return actions + drain(h, until)


def speaks(actions):
    return [a.body.text for a in actions if a.action_type in (ActionType.SPEAK, ActionType.FINAL, ActionType.CLARIFY)]


def calls(actions):
    return [(a.body.tool_name, dict(a.body.arguments)) for a in actions if a.action_type == ActionType.TOOL_CALL]


def finals(actions):
    return [a for a in actions if a.action_type == ActionType.FINAL]


def assert_clean(h):
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_asks_before_unlock_even_with_commit_intent():
    h, actions = start(config(), new_goal("st_unlock", "front door"))
    assert calls(actions) == []
    assert any("security action" in t and "go ahead and unlock (front door)" in t for t in speaks(actions))
    assert_clean(h)


def test_yes_releases_exactly_one_call():
    h, actions = start(config(), new_goal("st_unlock", "front door"))
    actions = say(h, 3_100_000, "yes please", 8_000_000)
    assert calls(actions) == [("st_unlock", {"device": "front door"})]
    assert [f.body.task_completed for f in finals(actions)] == [True]
    assert_clean(h)


def test_no_sends_nothing_and_finishes_honestly():
    h, actions = start(config(), new_goal("st_unlock", "front door"))
    actions = say(h, 3_100_000, "cancel that", 8_000_000)
    assert calls(actions) == []
    fin = finals(actions)
    assert len(fin) == 1 and fin[0].body.task_completed is False
    assert "nothing was sent" in fin[0].body.text.lower()
    assert_clean(h)


def test_tool_without_the_field_is_not_asked():
    provider = ScriptedProvider()
    provider.register("interpret", "lock the", new_goal("st_lock", "front door"))
    provider.register("compose", "a0", {"text": "Done.", "claims": []})
    h = SimHarness(config(), seed=1, provider=provider, tools={"st_lock": {"latency_ms": 200, "response": {"done": True}}}, worker_latency_us=FAST)
    h.send(0, [manifest_event([UNLOCK, LOCK])])
    h.send(100_000, [chunk_event("lock the front door")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted] + drain(h, 3_000_000, stop_on_final=False)
    assert calls(actions) == [("st_lock", {"device": "front door"})]
    assert not any("security action" in t for t in speaks(actions))
    assert_clean(h)


def test_flag_off_means_no_question_and_old_behaviour():
    h, actions = start(config(confirmation_policy_enabled=False), new_goal("st_unlock", "front door"))
    assert calls(actions) == [("st_unlock", {"device": "front door"})]
    assert not any("security action" in t for t in speaks(actions))


def test_correcting_the_argument_after_the_question_asks_again():
    fix = {"act": "slot_update", "slot_deltas": [{"name": "device", "scope": "goal", "op": "set", "value": "back door"}]}
    h, actions = start(config(), new_goal("st_unlock", "front door"), extra={"back door": fix})
    actions = say(h, 3_100_000, "no the back door", 6_000_000)
    assert calls(actions) == []
    assert any("back door" in t and "security action" in t for t in speaks(actions))
    actions = say(h, 6_100_000, "yes please", 10_000_000)
    assert calls(actions) == [("st_unlock", {"device": "back door"})]
    assert_clean(h)


def test_fdb_profile_leaves_the_policy_off():
    from prism_rt.profiles import demo_config, fdb_v3_config

    assert fdb_v3_config().confirmation_policy_enabled is False
    assert demo_config().confirmation_policy_enabled is True


def test_a_yes_that_changes_the_argument_is_not_a_yes_to_the_old_one():
    """H2: "Yes... the back door, I mean." The yes and the change arrive in one
    turn. The grant names the call that was asked about (front door), which the
    change withdraws unsent; the back door is a new call and is asked again."""
    both = {"act": "confirm", "slot_deltas": [{"name": "device", "scope": "goal", "op": "set", "value": "back door"}]}
    h, actions = start(config(), new_goal("st_unlock", "front door"), extra={"i mean": both})
    actions = say(h, 3_100_000, "yes the back door i mean", 6_000_000)
    assert calls(actions) == []
    assert any("back door" in t and "security action" in t for t in speaks(actions))
    actions = say(h, 6_100_000, "yes please", 10_000_000)
    assert calls(actions) == [("st_unlock", {"device": "back door"})]
    assert_clean(h)


def test_a_yes_for_the_same_argument_restated_still_counts():
    """The yes restating the same value ("yes, the front door") is no change:
    it releases the call that was asked about, once."""
    same = {"act": "confirm", "slot_deltas": [{"name": "device", "scope": "goal", "op": "set", "value": "front door"}]}
    h, actions = start(config(), new_goal("st_unlock", "front door"), extra={"yes the front": same})
    actions = say(h, 3_100_000, "yes the front door", 8_000_000)
    assert calls(actions) == [("st_unlock", {"device": "front door"})]
    assert_clean(h)
