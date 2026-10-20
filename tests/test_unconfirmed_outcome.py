"""WP2: Config.unconfirmed_outcomes_enabled -- the tool-result status "unknown".

A device command that the cloud accepted but whose state change could not be
read back is "sent, not confirmed": the call is consumed (the plan moves on),
the effect is UNKNOWN (never FAILED, never CONFIRMED), and nothing the agent
says or reports may claim it is done.
"""

from __future__ import annotations

import re

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, EffectStatus, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

LOCK = {"name": "st_lock", "mutability": "state_changing",
        "parameters": {"type": "object", "properties": {"device": {"type": "string"}}, "required": ["device"]}}
FAST = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000, JobKind.EXTRACT: 10_000, JobKind.BIND: 10_000}
UNCONFIRMED = {"outcome": "unconfirmed", "sent": True, "device": "front door"}
GOAL = {"act": "new_goal", "intent": "st_lock", "slot_deltas": [], "commit_intent": True,
        "actions": [{"tool": "st_lock", "args": {"device": "front door"}}]}


def config(**overrides) -> Config:
    base = dict(action_plans_enabled=True, multi_action_enabled=True, conversational_replies_enabled=True,
                unconfirmed_outcomes_enabled=True, settle_barrier_enabled=True, settle_ms=500)
    base.update(overrides)
    return Config(**base)


def run(cfg, response=UNCONFIRMED, utterances=("lock the front door",)):
    provider = ScriptedProvider()
    provider.register("interpret", "lock the front", GOAL)
    # What a careless composer would say; the kernel must not let it through.
    provider.register("compose", "a0", {"text": "The front door is locked.", "claims": []})
    h = SimHarness(cfg, seed=1, provider=provider, tools={"st_lock": {"latency_ms": 200, "response": response}}, worker_latency_us=FAST)
    h.send(0, [manifest_event([LOCK])])
    actions = []
    ts = 100_000
    for text in utterances:
        h.send(ts, [chunk_event(text)])
        actions += [er.action for er in h.send(ts + 50_000, [eot_event()]).emit_report.emitted]
        actions += drain(h, ts + 4_000_000, stop_on_final=False)
        ts += 5_000_000
    return h, actions


def finals(actions):
    return [a for a in actions if a.action_type == ActionType.FINAL]


def calls(actions):
    return [a.body.tool_name for a in actions if a.action_type == ActionType.TOOL_CALL]


def assert_clean(h):
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_unknown_result_never_reads_as_done():
    h, actions = run(config())
    (final,) = finals(actions)
    assert final.body.task_completed is False
    assert not re.search(r"\b(is locked|locked\.|done|completed)\b", final.body.text.lower())
    assert "not confirm" in final.body.text.lower() or "couldn't confirm" in final.body.text.lower()
    assert_clean(h)


def test_unknown_result_marks_the_effect_unknown_and_the_call_consumed():
    h, actions = run(config())
    (call,) = [c for c in h.store.call_ledger.all() if c.tool == "st_lock"]
    assert call.status == CallStatus.CONSUMED
    assert [e.status for e in h.store.effect_ledger.all()] == [EffectStatus.UNKNOWN]


def test_unknown_claim_grade_is_not_effect_done():
    h, actions = run(config(claim_grades_enabled=True))
    (final,) = finals(actions)
    assert final.body.claim_grade is not None and final.body.claim_grade.name != "EFFECT_DONE"


def test_identical_request_is_not_resent_after_an_unknown_outcome():
    h, actions = run(config(), utterances=("lock the front door", "lock the front door again"))
    assert calls(actions) == ["st_lock"]
    assert_clean(h)


def test_confirmed_path_is_unchanged():
    h, actions = run(config(), response={"done": True, "state": "locked"})
    (final,) = finals(actions)
    assert final.body.task_completed is True
    assert [e.status for e in h.store.effect_ledger.all()] == [EffectStatus.CONFIRMED]


def test_flag_off_treats_unknown_as_malformed():
    h, actions = run(config(unconfirmed_outcomes_enabled=False))
    assert [e.status for e in h.store.effect_ledger.all()] != [EffectStatus.CONFIRMED]


def test_profiles():
    from prism_rt.profiles import demo_config, fdb_v3_config

    assert fdb_v3_config().unconfirmed_outcomes_enabled is False
    assert demo_config().unconfirmed_outcomes_enabled is True


def test_composer_prompt_tells_the_model_an_unknown_effect_is_not_done():
    from prism_rt.workers.composer import build_prompt

    prompt = build_prompt({"facts": {}, "effects": {"a0": "unknown"}, "open_questions": []})
    assert "unknown" in prompt and "not confirmed" in prompt


def test_final_still_reports_the_writes_that_did_confirm():
    two = {"act": "new_goal", "intent": "st_lock", "slot_deltas": [], "commit_intent": True, "actions": [
        {"tool": "st_lock", "args": {"device": "front door"}}, {"tool": "st_ok", "args": {"device": "ac"}}]}
    ok = {"name": "st_ok", "mutability": "state_changing",
          "parameters": {"type": "object", "properties": {"device": {"type": "string"}}, "required": ["device"]}}
    provider = ScriptedProvider()
    provider.register("interpret", "lock the front", two)
    provider.register("compose", "a0", {"text": "All done.", "claims": []})
    tools = {"st_lock": {"latency_ms": 200, "response": UNCONFIRMED}, "st_ok": {"latency_ms": 200, "response": {"done": True}}}
    h = SimHarness(config(), seed=1, provider=provider, tools=tools, worker_latency_us=FAST)
    h.send(0, [manifest_event([LOCK, ok])])
    h.send(100_000, [chunk_event("lock the front door")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted] + drain(h, 4_000_000, stop_on_final=False)
    (final,) = finals(actions)
    assert "ok (ac)" in final.body.text and "couldn't confirm" in final.body.text
    assert final.body.task_completed is False
