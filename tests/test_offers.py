"""Proactive offers (Config.proactive_offers_enabled): Janus speaks first --
"The garage is open. Shall I close it?" -- and the offer behaves like any
write held for a yes: "yes" sends it once, "no" withdraws it, and if someone
else closes the garage while Janus waits, the offer withdraws itself
(kernel/evidence.py)."""

from __future__ import annotations

import asyncio
import dataclasses

from conftest import chunk_event, drain, eot_event, manifest_event

from config.templates import EVIDENCE_WITHDRAWN
from prism_rt.adapters.offers import build_offer
from prism_rt.canonical import world_target_key
from prism_rt.model.types import ActionType, JobKind
from prism_rt.profiles import demo_config, fdb_v3_config
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.smartthings.mock import MockSmartThings
from prism_rt.smartthings.tools import MANIFEST, SmartThingsToolset
from prism_rt.workers.gateway import ScriptedProvider

FAST = {kind: 10_000 for kind in JobKind}
OFFER = {"offer_id": "o-1", "text": "The garage is open. Shall I close it?",
         "tool": "st_close_garage", "args": {"device": "garage"}}


async def _no_sleep(_s):
    return None


def run(coro):
    return asyncio.run(coro)


def setup(config=None):
    mock = MockSmartThings()
    run(mock.command("garage-1", "garageDoorControl", "open"))
    mock.commands.clear()
    execute = SmartThingsToolset(mock, sleep=_no_sleep).make_executor()
    provider = ScriptedProvider()
    provider.register("interpret", "no thanks", {"act": "deny", "slot_deltas": []})
    provider.register("interpret", "yes please", {"act": "confirm", "slot_deltas": []})
    provider.register("interpret", "lock the front", {"act": "new_goal", "intent": "st_lock", "slot_deltas": [],
                                                      "commit_intent": True,
                                                      "actions": [{"tool": "st_lock", "args": {"device": "front door"}}]})
    provider.register("compose", "", {"text": "The garage is closed.", "claims": []})
    tools = {m["name"]: {"latency_ms": 200, "handler": (lambda args, n=m["name"]: run(execute(n, args)))} for m in MANIFEST}
    h = SimHarness(config or demo_config(), seed=1, provider=provider, tools=tools, worker_latency_us=FAST)
    h.send(0, [manifest_event(MANIFEST)])
    return h, mock


def push(h, ts, events, until):
    return [er.action for er in h.send(ts, events).emit_report.emitted] + drain(h, until, stop_on_final=False)


def say(h, ts, text, until):
    h.send(ts, [chunk_event(text)])
    return [er.action for er in h.send(ts + 50_000, [eot_event()]).emit_report.emitted] + drain(h, until, stop_on_final=False)


def offer_event(**overrides):
    return {"type": "offer", "payload": {**OFFER, **overrides}}


def said(actions):
    return [a.body.text for a in actions if a.action_type in (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL)
            and getattr(a.body, "text", None)]


def tool_calls(actions):
    return [a.body.tool_name for a in actions if a.action_type == ActionType.TOOL_CALL]


def clean(h):
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def offered(h):
    actions = push(h, 500_000, [offer_event()], 2_500_000)
    assert said(actions) == [OFFER["text"]]  # the offer itself, no "got it" first
    assert tool_calls(actions) == []
    return actions


def test_yes_closes_the_garage_once():
    h, mock = setup()
    offered(h)
    actions = say(h, 3_000_000, "yes please", 7_000_000)
    assert tool_calls(actions) == ["st_close_garage"]
    assert [c[2] for c in mock.commands] == ["close"]
    assert any(a.action_type == ActionType.FINAL for a in actions)
    clean(h)


def test_no_withdraws_the_offer():
    h, mock = setup()
    offered(h)
    actions = say(h, 3_000_000, "no thanks", 6_000_000)
    assert tool_calls(actions) == [] and mock.commands == []
    assert "Okay, nothing was sent." in said(actions)
    clean(h)


def test_someone_else_closes_it_and_the_offer_withdraws_itself():
    h, mock = setup()
    offered(h)
    key = world_target_key("garage")
    push(h, 2_600_000, [{"type": "world_fact", "payload": {"key": key, "value": "open"}}], 2_800_000)  # baseline
    actions = push(h, 3_500_000, [{"type": "world_fact", "payload": {"key": key, "value": "closed"}}], 4_500_000)
    assert EVIDENCE_WITHDRAWN.format(device="the garage", value="closed") in said(actions)
    later = say(h, 5_000_000, "yes please", 8_000_000)
    assert tool_calls(actions + later) == [] and mock.commands == []
    clean(h)


def test_no_offer_while_a_request_is_running():
    h, _ = setup()
    h.send(100_000, [chunk_event("lock the front door")])
    h.send(150_000, [eot_event()])
    actions = push(h, 200_000, [offer_event()], 3_000_000)
    assert OFFER["text"] not in said(actions)
    clean(h)


def test_no_offer_while_the_user_is_speaking():
    h, _ = setup()
    h.send(100_000, [{"type": "user_speech", "payload": {"active": True}}])
    actions = push(h, 200_000, [offer_event()], 1_500_000)
    assert said(actions) == []
    clean(h)


def test_an_offer_for_an_unknown_or_read_only_tool_is_ignored():
    h, _ = setup()
    actions = push(h, 500_000, [offer_event(tool="st_get_status")], 1_500_000)
    actions += push(h, 1_600_000, [offer_event(offer_id="o-2", tool="no_such_tool")], 2_500_000)
    assert said(actions) == []
    clean(h)


def test_offers_are_demo_only():
    assert demo_config().proactive_offers_enabled is True
    assert fdb_v3_config().proactive_offers_enabled is False
    off = dataclasses.replace(demo_config(), proactive_offers_enabled=False)
    h, _ = setup(off)
    actions = push(h, 500_000, [offer_event()], 2_000_000)
    assert said(actions) == []


# ---- the host builds offers from what the devices say ------------------------------

def test_build_offer_only_when_there_is_something_to_offer():
    async def read_open(tool, spoken):
        return "open" if tool == "st_close_garage" else "locked"

    async def read_closed(tool, spoken):
        return "closed" if tool == "st_close_garage" else "unlocked"

    garage = run(build_offer("garage_open", read_open))
    assert garage["tool"] == "st_close_garage" and garage["text"] == "The garage is open. Shall I close it?"
    assert run(build_offer("garage_open", read_closed)) is None
    door = run(build_offer("door_unlocked", read_closed))
    assert door["tool"] == "st_lock" and door["text"] == "The front door is unlocked. Shall I lock it?"
    assert run(build_offer("door_unlocked", read_open)) is None
    assert run(build_offer("nonsense", read_open)) is None


def test_an_offer_long_after_the_users_last_words_is_not_salvaged_at_once():
    """Found live: the stall salvage measured from the user's last end of
    turn, so an offer made 20 s later ended at once with "I couldn't finish
    this in time"."""
    h, mock = setup()
    say(h, 100_000, "lock the front door", 4_000_000)  # a request, finished
    actions = push(h, 25_000_000, [offer_event()], 27_000_000)
    assert said(actions) == [OFFER["text"]]
    later = say(h, 30_000_000, "yes please", 34_000_000)
    assert tool_calls(later) == ["st_close_garage"]
    clean(h)


# ---- survey B1: the washer's fault, offered as a technician visit ---------------

from prism_rt.devicecare.tools import MANIFEST as CARE_MANIFEST, DeviceCareToolset  # noqa: E402


def washer_setup(extra=()):
    care = DeviceCareToolset()
    execute = care.make_executor()
    provider = ScriptedProvider()
    for key, reply in extra:
        provider.register("interpret", key, reply)
    provider.register("interpret", "no thanks", {"act": "deny", "slot_deltas": []})
    provider.register("interpret", "yes please", {"act": "confirm", "slot_deltas": []})
    provider.register("compose", "", {"text": "Your technician is booked.", "claims": []})
    tools = {m["name"]: {"latency_ms": 200, "handler": (lambda args, n=m["name"]: run(execute(n, args)))} for m in CARE_MANIFEST}
    h = SimHarness(demo_config(), seed=1, provider=provider, tools=tools, worker_latency_us=FAST)
    h.send(0, [manifest_event(CARE_MANIFEST)])
    payload = run(build_offer("washer_error", None, lambda device: care._get_device_status({"device_type": device})))
    return h, care, payload


def washer_offered(h, payload):
    actions = push(h, 500_000, [{"type": "offer", "payload": payload}], 2_500_000)
    assert said(actions) == [payload["text"]]
    assert tool_calls(actions) == []


def test_washer_offer_names_the_code_and_the_visit():
    _, _, payload = washer_setup()
    assert payload["tool"] == "book_technician" and payload["args"] == {"date": "tomorrow", "time_slot": "morning"}
    assert payload["text"] == ("Your washer has stopped with code 4C: water is not being supplied to the drum. "
                               "Shall I book a technician for tomorrow morning?")


def test_washer_offer_yes_books_exactly_one_visit():
    h, care, payload = washer_setup()
    washer_offered(h, payload)
    actions = say(h, 3_000_000, "yes please", 7_000_000)
    assert tool_calls(actions) == ["book_technician"]
    assert [(b["issue_id"], b["date"], b["time_slot"]) for b in care.bookings] == [("washer_no_water", "tomorrow", "morning")]
    clean(h)


def test_washer_offer_no_books_nothing():
    h, care, payload = washer_setup()
    washer_offered(h, payload)
    actions = say(h, 3_000_000, "no thanks", 6_000_000)
    assert tool_calls(actions) == [] and care.bookings == []
    assert "Okay, nothing was sent." in said(actions)
    clean(h)


def test_washer_offer_corrected_books_the_new_slot_only():
    fix = {"act": "slot_update", "slot_deltas": [{"name": "date", "scope": "goal", "op": "set", "value": "Friday"},
                                                 {"name": "time_slot", "scope": "goal", "op": "set", "value": "afternoon"}]}
    h, care, payload = washer_setup(extra=[("friday afternoon", fix)])
    washer_offered(h, payload)
    first = say(h, 3_000_000, "make it friday afternoon", 7_000_000)
    # The offer's own sentence named tomorrow morning: the question is asked
    # again about what is now pending, and nothing is booked before the yes.
    assert said(first) == ["Okay — shall I book a technician for Friday afternoon?"]
    assert care.bookings == []
    later = say(h, 7_100_000, "yes please", 11_000_000)
    assert [(b["date"], b["time_slot"]) for b in care.bookings] == [("Friday", "afternoon")]
    assert tool_calls(first + later) == ["book_technician"]
    clean(h)


def test_no_washer_offer_without_a_fault():
    care = DeviceCareToolset()
    care.status["washer"]["error_code"] = None
    assert run(build_offer("washer_error", None, lambda device: care._get_device_status({"device_type": device}))) is None
    assert run(build_offer("washer_error", None)) is None  # no appliance reader: nothing to offer
