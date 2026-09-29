"""Fixes for the 29 Sep live voice session in which the agent stopped
responding after a clarifying question.

- A re-extracted value is coerced to the parameter's declared type ("two" for
  an integer `bedrooms` becomes 2), so the rebind doesn't fail validation and
  clarify the same slot again.
- Config.conversational_replies_enabled: while the same thing is still
  missing, each new user turn gets the question once more instead of silence.
- Config.conversational_replies_enabled: with no task running, the Interpreter
  is shown the task that just finished, so "make it X" can be read as a
  correction to it.
"""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

APARTMENTS = {"name": "search_apartments", "parameters": {"type": "object", "properties": {
    "city": {"type": "string"}, "bedrooms": {"type": "integer"}}, "required": ["city", "bedrooms"]}}
TOOLS = {"search_apartments": {"latency_ms": 200, "response": {"results": [{"id": "APT1"}]}}}
FAST = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000, JobKind.EXTRACT: 10_000, JobKind.BIND: 10_000}
PUNE_ONLY = {"act": "new_goal", "intent": "search_apartments", "slot_deltas": [], "actions": [
    {"tool": "search_apartments", "args": {"city": "Pune"}}]}
NOT_SURE = {"act": "unclear", "slot_deltas": []}


def config(**overrides) -> Config:
    base = dict(action_plans_enabled=True, multi_action_enabled=True, clarify_reextract_enabled=True,
                triage_hold_enabled=True, conversational_replies_enabled=True,
                read_only_tools=("search_apartments",), g4_exempt_undeclared_mutability=True)
    base.update(overrides)
    return Config(**base)


def harness(provider, cfg) -> SimHarness:
    h = SimHarness(cfg, seed=1, provider=provider, tools=TOOLS, worker_latency_us=FAST)
    h.send(0, [manifest_event([APARTMENTS])])
    return h


def send(h, ts, events) -> list:
    return [er.action for er in h.send(ts, events).emit_report.emitted]


def of_type(actions, kind) -> list:
    return [a for a in actions if a.action_type == kind]


def assert_clean(h) -> None:
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_reextracted_spoken_number_is_coerced_to_the_param_type():
    provider = ScriptedProvider()
    provider.register("extract", "flat in Pune", {"value": "two"})
    provider.register("interpret", "flat in Pune", PUNE_ONLY)
    provider.register("compose", "a0", {"text": "Found one.", "claims": []})
    h = harness(provider, config())
    h.send(100_000, [chunk_event("a two bedroom flat in Pune")])
    actions = send(h, 150_000, [eot_event()]) + drain(h, 3_000_000)
    calls = [(a.body.tool_name, dict(a.body.arguments)) for a in of_type(actions, ActionType.TOOL_CALL)]
    assert calls == [("search_apartments", {"city": "Pune", "bedrooms": 2})]
    assert of_type(actions, ActionType.CLARIFY) == []
    assert_clean(h)


def _ask_then_two_vague_turns(cfg) -> list:
    provider = ScriptedProvider()
    provider.register("interpret", "not sure", NOT_SURE)
    provider.register("interpret", "no idea", NOT_SURE)
    provider.register("extract", "flat in Pune", {"value": None})
    provider.register("interpret", "flat in Pune", PUNE_ONLY)
    h = harness(provider, cfg)
    h.send(100_000, [chunk_event("find me a flat in Pune")])
    actions = send(h, 150_000, [eot_event()]) + drain(h, 1_000_000, stop_on_final=False)
    h.send(1_100_000, [chunk_event("hmm not sure")])
    actions += send(h, 1_150_000, [eot_event()]) + drain(h, 2_000_000, stop_on_final=False)
    h.send(2_100_000, [chunk_event("no idea really")])
    actions += send(h, 2_150_000, [eot_event()]) + drain(h, 3_000_000, stop_on_final=False)
    assert_clean(h)
    return [a.body.text for a in of_type(actions, ActionType.CLARIFY)]


def test_still_missing_value_is_asked_again_once_per_new_turn():
    texts = _ask_then_two_vague_turns(config())
    assert len(texts) == 3
    assert "bedrooms" in texts[0]
    assert texts[1] == texts[2] == "Sorry, I still need the bedrooms before I can go on."


def test_still_missing_value_is_asked_only_once_with_the_flag_off():
    assert len(_ask_then_two_vague_turns(config(conversational_replies_enabled=False))) == 1


def test_finished_task_is_shown_to_the_interpreter_and_a_change_reruns_it():
    prompts: list[str] = []

    def make_it_three(prompt: str) -> dict:
        prompts.append(prompt)
        return {"act": "slot_update", "slot_deltas": [{"name": "bedrooms", "scope": "goal", "op": "set", "value": "three"}]}

    provider = ScriptedProvider()
    provider.register("interpret", "make it three", make_it_three)
    provider.register("interpret", "flat in Pune", {**PUNE_ONLY, "actions": [
        {"tool": "search_apartments", "args": {"city": "Pune", "bedrooms": 2}}]})
    provider.register("compose", "a0", {"text": "Found one.", "claims": []})
    h = harness(provider, config())
    h.send(100_000, [chunk_event("two bedroom flat in Pune")])
    send(h, 150_000, [eot_event()])
    assert of_type(drain(h, 3_000_000), ActionType.FINAL)
    h.send(3_100_000, [chunk_event("make it three")])
    actions = send(h, 3_150_000, [eot_event()]) + drain(h, 6_000_000)
    assert prompts and "Just finished: search_apartments {'city': 'Pune', 'bedrooms': 2}" in prompts[-1]
    calls = [(a.body.tool_name, dict(a.body.arguments)) for a in of_type(actions, ActionType.TOOL_CALL)]
    assert calls == [("search_apartments", {"city": "Pune", "bedrooms": 3})]
    assert_clean(h)


BOOK = {"name": "book_flight", "parameters": {"type": "object", "properties": {
    "passenger_name": {"type": "string"}}, "required": ["passenger_name"]}}
BOOK_ANA = {"act": "new_goal", "intent": "book_flight", "slot_deltas": [], "actions": [
    {"tool": "book_flight", "args": {"passenger_name": "Ana"}}]}


def test_repeat_booking_cancel_and_status_say_what_actually_ran():
    """The 29 Sep session: a repeated booking got "That's already been taken
    care of.", cancelling it got "Okay, cancelled.", and "was the flight
    booked?" was answered "nothing was done" although the first booking
    had gone through."""
    provider = ScriptedProvider()
    provider.register("interpret", "was it booked", {"act": "unclear", "slot_deltas": [], "status_question": True})
    provider.register("interpret", "cancel it", {"act": "abort", "slot_deltas": []})
    provider.register("interpret", "book Ana", BOOK_ANA)
    provider.register("compose", "a0", {"text": "Booked.", "claims": []})
    h = SimHarness(config(), seed=1, provider=provider,
                   tools={"book_flight": {"latency_ms": 200, "response": {"booking_ref": "B1"}}}, worker_latency_us=FAST)
    h.send(0, [manifest_event([BOOK])])
    h.send(100_000, [chunk_event("book Ana on a flight")])
    send(h, 150_000, [eot_event()])
    assert of_type(drain(h, 3_000_000), ActionType.FINAL)

    def turn(ts, text) -> list[str]:
        h.send(ts, [chunk_event(text)])
        actions = send(h, ts + 50_000, [eot_event()]) + drain(h, ts + 2_000_000, stop_on_final=False)
        return [a.body.text for a in actions if a.action_type in (ActionType.SPEAK, ActionType.FINAL)]

    assert "I've already done that — book flight (Ana) went through earlier, so I won't repeat it." in turn(3_100_000, "book Ana again")
    assert turn(5_200_000, "no cancel it") == ["Okay, I've dropped that request. Nothing new was done — your earlier request still stands."]
    assert turn(7_300_000, "was it booked") == [
        "Your last request was dropped before anything ran. Before that, I ran book flight (Ana). That still stands."]
    assert_clean(h)
