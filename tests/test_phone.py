"""Phone actions (demo): an alarm or a timer the companion app sets on the
phone. The app's answer is the tool result: done is said word for word, a
refusal is a failure, no answer is "sent, not confirmed"."""

from __future__ import annotations

import asyncio

import pytest
from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.model.types import ActionType, JobKind
from prism_rt.phone import MANIFEST, PhoneRefused, PhoneToolset, PhoneUnavailable, parse_time, spoken_minutes, spoken_time
from prism_rt.profiles import demo_config
from prism_rt.recap import MANIFEST as RECAP_MANIFEST, RecapToolset
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

FAST = {kind: 10_000 for kind in JobKind}


def _intent(tool, args):
    return {"act": "new_goal", "intent": tool, "slot_deltas": [], "commit_intent": True,
            "actions": [{"tool": tool, "args": args}]}


def setup(answer="ok"):
    """answer: "ok", "refuse", "silent" (never answers) or "none" (no phone)."""
    phone = PhoneToolset(timeout_s=0.01)
    sent: list[dict] = []

    async def publish(message):
        sent.append(message)
        if answer == "ok":
            phone.on_reply({"type": "phone_result", "id": message["id"], "ok": True})
        elif answer == "refuse":
            phone.on_reply({"type": "phone_result", "id": message["id"], "ok": False, "error": "no clock app"})

    if answer != "none":
        phone.publish = publish
    execute = phone.make_executor()
    holder: dict = {}
    recap = RecapToolset(lambda: holder["h"].store).make_executor()
    provider = ScriptedProvider()
    provider.register("interpret", "alarm for 6:30", _intent("phone_set_alarm", {"time": "06:30"}))
    provider.register("interpret", "five minute timer", _intent("phone_set_timer", {"minutes": 5}))
    provider.register("interpret", "what did you do", _intent("session_recap", {}))
    provider.register("compose", "", {"text": "Something the composer made up.", "claims": []})
    tools = {m["name"]: {"latency_ms": 200, "handler": (lambda args, n=m["name"]: asyncio.run(execute(n, args)))}
             for m in MANIFEST}
    tools["session_recap"] = {"latency_ms": 100, "handler": lambda args: asyncio.run(recap("session_recap", args))}
    h = SimHarness(demo_config(), seed=1, provider=provider, tools=tools, worker_latency_us=FAST)
    holder["h"] = h
    h.send(0, [manifest_event([*MANIFEST, *RECAP_MANIFEST])])
    return h, sent


def say(h, ts, text, until):
    h.send(ts, [chunk_event(text)])
    return [er.action for er in h.send(ts + 50_000, [eot_event()]).emit_report.emitted] + drain(h, until, stop_on_final=False)


def finals(actions):
    return [a.body.text for a in actions if a.action_type == ActionType.FINAL]


def test_an_alarm_is_set_on_the_phone_and_said_back():
    h, sent = setup()
    assert finals(say(h, 100_000, "set an alarm for 6:30", 5_000_000)) == ["Your alarm is set for 6:30 AM."]
    assert [(m["action"], m["hour"], m["minute"]) for m in sent] == [("set_alarm", 6, 30)]
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_a_timer_is_started():
    h, sent = setup()
    assert finals(say(h, 100_000, "start a five minute timer", 5_000_000)) == ["Your timer for 5 minutes has started."]
    assert [(m["action"], m["seconds"]) for m in sent] == [("set_timer", 300)]


def test_the_same_alarm_asked_again_is_not_set_twice():
    h, sent = setup()
    say(h, 100_000, "set an alarm for 6:30", 5_000_000)
    say(h, 5_100_000, "set an alarm for 6:30", 10_000_000)
    assert len(sent) == 1


def _execute(answer, tool="phone_set_alarm", args=None):
    phone = PhoneToolset(timeout_s=0.01)

    async def publish(message):
        if answer == "refuse":
            phone.on_reply({"type": "phone_result", "id": message["id"], "ok": False, "error": "no clock app"})

    if answer != "none":
        phone.publish = publish
    return asyncio.run(phone.make_executor()(tool, args or {"time": "06:30"}))


def test_a_refusal_fails_the_call():
    # The voice bridge turns the exception into an error result (status "error").
    with pytest.raises(PhoneRefused, match="no clock app"):
        _execute("refuse")


def test_without_a_phone_the_call_fails_at_once():
    with pytest.raises(PhoneUnavailable):
        _execute("none")


def test_a_bad_time_fails_before_anything_is_sent():
    with pytest.raises(ValueError):
        _execute("ok", args={"time": "25:00"})


def test_no_answer_is_sent_but_not_confirmed():
    assert _execute("silent")["outcome"] == "unconfirmed"
    h, sent = setup("silent")
    said = finals(say(h, 100_000, "set an alarm for 6:30", 8_000_000))
    assert len(sent) == 1 and said and not any("is set" in t for t in said)
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_the_recap_lists_the_alarm():
    h, _ = setup()
    say(h, 100_000, "set an alarm for 6:30", 5_000_000)
    assert finals(say(h, 5_100_000, "what did you do", 9_000_000)) == ["Done: set an alarm for 6:30 AM."]


@pytest.mark.parametrize("value,expected", [("06:30", (6, 30)), ("6:30", (6, 30)), ("0630", (6, 30)),
                                            ("19:00", (19, 0)), ("7", (7, 0)), ("7.15", (7, 15))])
def test_times(value, expected):
    assert parse_time(value) == expected


@pytest.mark.parametrize("value", ["25:00", "6:75", "six", ""])
def test_not_times(value):
    with pytest.raises(ValueError):
        parse_time(value)


def test_said_back():
    assert [spoken_time(6, 30), spoken_time(19, 0), spoken_time(0, 5), spoken_time(12, 15)] == [
        "6:30 AM", "7 PM", "12:05 AM", "12:15 PM"]
    assert [spoken_minutes(5), spoken_minutes(1), spoken_minutes(0.5), spoken_minutes(1.5)] == [
        "5 minutes", "1 minute", "30 seconds", "1 minute 30 seconds"]
