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


def session(tools_override=None, config=None, mock=None, extra=()):
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
    h = SimHarness(config or demo_config(), seed=1, provider=provider, tools=tools, worker_latency_us=FAST)
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
