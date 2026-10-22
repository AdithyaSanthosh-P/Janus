"""Demo speech polish (Config.demo_speech_polish_enabled), from the 21 Oct live
runs: failures said in the tool's own words, actions said from the manifest's
labels, and a plain answer to a "yes" with nothing waiting."""

from __future__ import annotations

import asyncio
import dataclasses

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.kernel.replies import NOTHING_PENDING_YES, action_phrase
from prism_rt.model.types import ActionType, JobKind
from prism_rt.profiles import demo_config, fdb_v3_config
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.smartthings.mock import MockSmartThings
from prism_rt.smartthings.tools import MANIFEST, SmartThingsToolset
from prism_rt.workers.gateway import ScriptedProvider

FAST = {kind: 10_000 for kind in JobKind}
LOCK = {"act": "new_goal", "intent": "st_lock", "slot_deltas": [], "commit_intent": True,
        "actions": [{"tool": "st_lock", "args": {"device": "the front door"}}]}


async def _no_sleep(_s):
    return None


def session(tools_override=None, config=None, mock=None, extra=(), latency=None):
    mock = mock or MockSmartThings()
    execute = SmartThingsToolset(mock, sleep=_no_sleep).make_executor()
    provider = ScriptedProvider()
    for key, reply in extra:
        provider.register("interpret", key, reply)
    provider.register("interpret", "lock the front", LOCK)
    provider.register("compose", "", {"text": "Done.", "claims": []})
    tools = {m["name"]: {"latency_ms": 200, "handler": (lambda args, n=m["name"]: asyncio.run(execute(n, args)))}
             for m in MANIFEST}
    tools.update(tools_override or {})
    h = SimHarness(config or demo_config(), seed=1, provider=provider, tools=tools, worker_latency_us=latency or FAST)
    h.send(0, [manifest_event(MANIFEST)])
    return h, mock


def say(h, ts, text, until):
    h.send(ts, [chunk_event(text)])
    return [er.action for er in h.send(ts + 50_000, [eot_event()]).emit_report.emitted] + drain(h, until, stop_on_final=False)


def spoken(actions):
    return [a.body.text for a in actions if a.action_type in (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL)
            and getattr(a.body, "text", None)]


UNREACHABLE = {"st_lock": {"latency_ms": 200, "error": {"message": "I couldn't reach SmartThings.", "retryable": False}}}


def test_a_failed_write_is_said_in_the_tools_own_words():
    h, _ = session(UNREACHABLE)
    actions = say(h, 100_000, "lock the front door", 5_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert finals[-1].body.text == "I couldn't reach SmartThings. So I couldn't lock the front door."
    assert finals[-1].body.task_completed is False
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_without_the_polish_the_generic_failure_text_stays():
    off = dataclasses.replace(demo_config(), demo_speech_polish_enabled=False)
    h, _ = session(UNREACHABLE, config=off)
    actions = say(h, 100_000, "lock the front door", 5_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert finals[-1].body.text == "I couldn't complete this — st lock failed and can't be safely retried."


def test_an_unconfirmed_write_is_said_from_its_label():
    h, _ = session(mock=MockSmartThings(stuck={"lock"}))
    actions = say(h, 100_000, "lock the front door", 6_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert finals[-1].body.text == ("I sent the command to lock the front door, but I couldn't confirm it went "
                                    "through — please check the device before relying on it.")


def test_a_yes_with_nothing_waiting_gets_a_plain_answer():
    h, _ = session(extra=[("yes", {"act": "confirm", "slot_deltas": []})])
    actions = say(h, 100_000, "yes", 4_000_000)
    assert NOTHING_PENDING_YES in spoken(actions)
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_action_phrase_drops_a_doubled_article():
    h, _ = session()
    assert action_phrase(h.store, "st_lock", {"device": "the front door"}) == "lock the front door"
    assert action_phrase(h.store, "st_switch", {"device": "My Light", "state": "off"}) == "turn the Light off"
    assert action_phrase(h.store, "st_list_devices", {}) is None


def test_the_polish_is_demo_only():
    assert demo_config().demo_speech_polish_enabled is True
    assert fdb_v3_config().demo_speech_polish_enabled is False


def test_a_status_read_says_which_device_the_user_named():
    mock = MockSmartThings()
    out = asyncio.run(SmartThingsToolset(mock, sleep=_no_sleep).make_executor()("st_get_status", {"device": "front door"}))
    assert out["device"] == "Janus Lock" and "front door" in out["note"]


def _final_for(compose_text, *, actions, utterance, mock=None):
    mock = mock or MockSmartThings()
    execute = SmartThingsToolset(mock, sleep=_no_sleep).make_executor()
    provider = ScriptedProvider()
    provider.register("interpret", utterance[:12], {"act": "new_goal", "intent": actions[0]["tool"], "slot_deltas": [],
                                                    "commit_intent": True, "actions": actions})
    provider.register("compose", "", {"text": compose_text, "claims": []})
    tools = {m["name"]: {"latency_ms": 200, "handler": (lambda args, n=m["name"]: asyncio.run(execute(n, args)))}
             for m in MANIFEST}
    h = SimHarness(demo_config(), seed=1, provider=provider, tools=tools, worker_latency_us=FAST)
    h.send(0, [manifest_event(MANIFEST)])
    actions_out = say(h, 100_000, utterance, 6_000_000)
    finals = [a for a in actions_out if a.action_type == ActionType.FINAL]
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []
    return finals[-1].body


def test_a_confirmed_device_command_is_said_from_its_result():
    body = _final_for("I attempted to lock it, but it failed.",
                      actions=[{"tool": "st_lock", "args": {"device": "front door"}},
                               {"tool": "st_set_cooling_setpoint", "args": {"device": "thermostat", "celsius": 21}}],
                      utterance="lock the front door and set the thermostat to 21")
    assert body.text == "The front door is now locked; the thermostat is now 21 degrees."
    assert body.task_completed is True


def test_a_device_already_in_place_is_said_so():
    mock = MockSmartThings()
    asyncio.run(mock.command("lock-1", "lock", "lock"))
    body = _final_for("Done.", actions=[{"tool": "st_lock", "args": {"device": "front door"}}],
                      utterance="lock the front door", mock=mock)
    assert body.text == "The front door was already locked, so I didn't send anything."


def test_a_read_keeps_the_composers_answer():
    body = _final_for("The front door is unlocked.", actions=[{"tool": "st_get_status", "args": {"device": "front door"}}],
                      utterance="is the front door locked")
    assert body.text == "The front door is unlocked."


def test_asking_again_for_what_janus_already_did_says_it_was_not_sent_twice():
    """Survey A5: "Lock the front door." ... "Lock the front door." The second
    request finds the door locked by our own first command: said as a repeat,
    and only one lock command reached the device."""
    h, mock = session()
    first = say(h, 100_000, "lock the front door", 6_000_000)
    assert spoken([a for a in first if a.action_type == ActionType.FINAL]) == ["The front door is now locked."]
    second = say(h, 6_100_000, "lock the front door", 12_000_000)
    finals = [a for a in second if a.action_type == ActionType.FINAL]
    assert spoken(finals) == ["The front door was already locked: I did that earlier, so I didn't send it again."]
    assert len([c for c in mock.commands if c[2] == "lock"]) == 1
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_an_acknowledgement_held_over_a_correction_names_the_corrected_value():
    """Found live (22 Oct): "Set the thermostat to twenty-two." -- the user
    keeps talking -- "Actually, make it twenty-one." The acknowledgement for
    22 was held while the user spoke and went out after the 22 call had been
    withdrawn, still saying 22. It now names what is pending (21); 22 is
    never said, never sent."""
    to22 = {"act": "new_goal", "intent": "st_set_cooling_setpoint", "slot_deltas": [], "commit_intent": True,
            "ack_phrase": "I'll set the thermostat to 22 degrees.",
            "actions": [{"tool": "st_set_cooling_setpoint", "args": {"device": "thermostat", "celsius": 22}}]}
    to21 = {"act": "slot_update", "slot_deltas": [{"name": "celsius", "scope": "goal", "op": "set", "value": 21}]}
    h, mock = session(extra=[("make it twenty-one", to21), ("twenty-two", to22)],
                      latency={**FAST, JobKind.INTERPRET: 600_000})  # live: the model answers while the user talks on
    out = [er.action for er in h.send(100_000, [chunk_event("set the thermostat to twenty-two")]).emit_report.emitted]
    out += [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted]
    out += [er.action for er in h.send(160_000, [{"type": "user_speech", "payload": {"active": True}}]).emit_report.emitted]
    out += drain(h, 1_500_000, stop_on_final=False)  # 22 is interpreted and planned while the user is still talking
    out += [er.action for er in h.send(1_600_000, [chunk_event("actually make it twenty-one")]).emit_report.emitted]
    out += [er.action for er in h.send(1_700_000, [{"type": "user_speech", "payload": {"active": False}}]).emit_report.emitted]
    out += [er.action for er in h.send(1_750_000, [eot_event()]).emit_report.emitted]
    out += drain(h, 8_000_000, stop_on_final=False)
    lines = spoken(out)
    assert not any("to 22" in t for t in lines), lines  # 22 is only ever named as what it is not
    assert "Okay, 21, not 22: I'll set the thermostat to 21 degrees." in lines, lines
    assert [c[3] for c in mock.commands if c[2] == "setCoolingSetpoint"] == [[21]]
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []
