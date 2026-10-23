"""The `session_recap` demo tool (survey H4): "What did you do?" is answered
from the kernel's ledgers, word for word, including what Janus did NOT do."""

from __future__ import annotations

import asyncio

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.canonical import world_target_key
from prism_rt.model.types import ActionType, JobKind
from prism_rt.profiles import demo_config
from prism_rt.recap import MANIFEST as RECAP_MANIFEST, RecapToolset
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.smartthings.mock import MockSmartThings
from prism_rt.smartthings.tools import MANIFEST, SmartThingsToolset
from prism_rt.workers.gateway import ScriptedProvider

FAST = {kind: 10_000 for kind in JobKind}
LOCK = {"act": "new_goal", "intent": "st_lock", "slot_deltas": [], "commit_intent": True,
        "actions": [{"tool": "st_lock", "args": {"device": "front door"}}]}
RECAP = {"act": "new_goal", "intent": "session_recap", "slot_deltas": [], "commit_intent": True,
         "actions": [{"tool": "session_recap", "args": {}}]}
OFFER = {"offer_id": "o-1", "text": "The garage is open. Shall I close it?", "tool": "st_close_garage", "args": {"device": "garage"}}


async def _no_sleep(_s):
    return None


def run(coro):
    return asyncio.run(coro)


def setup(stuck=()):
    mock = MockSmartThings(stuck=stuck)
    run(mock.command("garage-1", "garageDoorControl", "open"))
    mock.commands.clear()
    st = SmartThingsToolset(mock, sleep=_no_sleep).make_executor()
    holder: dict = {}
    recap = RecapToolset(lambda: holder["h"].store).make_executor()
    provider = ScriptedProvider()
    provider.register("interpret", "this morning", {"act": "smalltalk", "slot_deltas": []})
    provider.register("interpret", "what didn't you do", {"act": "smalltalk", "slot_deltas": [], "status_question": True})
    provider.register("interpret", "hello there", {"act": "smalltalk", "slot_deltas": []})
    provider.register("interpret", "what did you do", RECAP)
    provider.register("interpret", "lock the front", LOCK)
    provider.register("interpret", "switch on", {"act": "new_goal", "intent": "st_switch", "slot_deltas": [], "commit_intent": True,
                                                 "actions": [{"tool": "st_switch", "args": {"device": "switch", "state": "on"}}]})
    provider.register("compose", "", {"text": "Something the composer made up.", "claims": []})
    tools = {m["name"]: {"latency_ms": 200, "handler": (lambda args, n=m["name"]: run(st(n, args)))} for m in MANIFEST}
    tools["session_recap"] = {"latency_ms": 100, "handler": lambda args: run(recap("session_recap", args))}
    h = SimHarness(demo_config(), seed=1, provider=provider, tools=tools, worker_latency_us=FAST)
    holder["h"] = h
    h.send(0, [manifest_event([*MANIFEST, *RECAP_MANIFEST])])
    return h, mock


def say(h, ts, text, until):
    h.send(ts, [chunk_event(text)])
    return [er.action for er in h.send(ts + 50_000, [eot_event()]).emit_report.emitted] + drain(h, until, stop_on_final=False)


def push(h, ts, events, until):
    return [er.action for er in h.send(ts, events).emit_report.emitted] + drain(h, until, stop_on_final=False)


def finals(actions):
    return [a.body.text for a in actions if a.action_type == ActionType.FINAL]


def test_nothing_yet():
    h, _ = setup()
    assert finals(say(h, 100_000, "what did you do", 4_000_000)) == [
        "Nothing yet: I haven't sent or held back any action in this conversation."]


def test_the_recap_lists_what_was_done_and_what_was_never_sent_word_for_word():
    h, mock = setup()
    say(h, 100_000, "lock the front door", 5_000_000)
    key = world_target_key("garage")
    push(h, 5_500_000, [{"type": "offer", "payload": OFFER}], 7_000_000)
    push(h, 7_100_000, [{"type": "world_fact", "payload": {"key": key, "value": "open"}}], 7_300_000)
    push(h, 7_600_000, [{"type": "world_fact", "payload": {"key": key, "value": "closed"}}], 8_500_000)
    actions = say(h, 9_000_000, "what did you do", 13_000_000)
    assert finals(actions) == ["Done: lock the front door. Never sent: close the garage."]
    assert [c[2] for c in mock.commands] == ["lock"]  # the recap itself sends nothing
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_an_unconfirmed_command_is_never_listed_as_done():
    h, _ = setup(stuck={"switch"})
    say(h, 100_000, "switch on", 20_000_000)
    actions = say(h, 20_100_000, "what did you do", 24_000_000)
    assert finals(actions) == ["Sent but not confirmed: turn the switch on."]


def spoken(actions):
    return [a.body.text for a in actions if a.action_type in (ActionType.SPEAK, ActionType.FINAL) and getattr(a.body, "text", None)]


def test_the_question_filed_as_small_talk_or_as_a_status_question_still_gets_the_recap():
    h, _ = setup()
    say(h, 100_000, "lock the front door", 5_000_000)
    first = spoken(say(h, 5_100_000, "what did you do this morning", 8_000_000))
    second = spoken(say(h, 8_100_000, "and what didn't you do", 11_000_000))
    assert first == ["Done: lock the front door."] and second == ["Done: lock the front door."]


def test_explain_that_again_repeats_the_last_answer():
    # Found on a phone, 23 Oct: "Oh wait, can you explain that again?" (read as
    # unclear) got the status summary "For your last request, I ran ...".
    h, _ = setup()
    h.provider.register("interpret", "explain that again", {"act": "unclear", "slot_deltas": []})
    answer = finals(say(h, 100_000, "lock the front door", 5_000_000))
    assert len(answer) == 1
    lines = spoken(say(h, 5_100_000, "oh wait, can you explain that again?", 8_000_000))
    assert lines == [f"Sure. {answer[0]}"]
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_a_greeting_stays_small_talk():
    h, _ = setup()
    lines = spoken(say(h, 100_000, "hello there", 3_000_000))
    assert not any(t.startswith(("Done", "Nothing yet")) for t in lines)
