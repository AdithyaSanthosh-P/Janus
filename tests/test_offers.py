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
    assert payload["tool"] == "book_technician" and payload["args"] == {"date": "tomorrow", "time_slot": "morning", "device": "washer"}
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


def washer_fact(code):
    return {"type": "world_fact", "payload": {"key": world_target_key("washer"), "value": code}}


def test_washer_display_clears_while_janus_waits_and_nothing_is_booked():
    # The camera's reading of the display is the fact the offer rested on: the
    # code is gone before the yes, so the visit is withdrawn unbooked.
    h, care, payload = washer_setup()
    washer_offered(h, payload)
    push(h, 2_600_000, [washer_fact("4C")], 2_800_000)  # baseline when the question was asked
    actions = push(h, 3_500_000, [washer_fact("clear")], 4_500_000)
    assert ("Never mind: the washer's error code is gone. It cleared while I was waiting, so I didn't book anyone."
            in said(actions))
    reasons = [f.value for k, f in h.store.facts.by_prefix("withdrawn.").items() if k.endswith(".reason")]
    assert reasons == ["washer error code cleared while waiting"]
    later = say(h, 5_000_000, "yes please", 8_000_000)
    assert tool_calls(actions + later) == [] and care.bookings == []
    clean(h)


def test_a_late_yes_to_the_withdrawn_visit_books_nothing_even_if_the_model_takes_it_as_a_request():
    # Found live: after "never mind, it cleared", "Yes, please." came back
    # from the model as a fresh booking built from the conversation.
    rebook = {"act": "new_goal", "intent": "book_technician", "slot_deltas": [], "commit_intent": True,
              "actions": [{"tool": "book_technician", "args": {"date": "tomorrow", "time_slot": "morning"}}]}
    h, care, payload = washer_setup(extra=[("yes please", rebook), ("book a technician", rebook)])
    washer_offered(h, payload)
    push(h, 2_600_000, [washer_fact("4C")], 2_800_000)
    push(h, 3_500_000, [washer_fact("clear")], 4_500_000)
    later = say(h, 5_000_000, "yes please", 9_000_000)
    assert tool_calls(later) == [] and care.bookings == []
    assert ("Nothing is waiting for a yes right now: I didn't book a technician for tomorrow morning, because it changed "
            "while I was waiting. If you still want it, just ask.") in said(later)
    # Asking for it again, in words, books it.
    again = say(h, 10_000_000, "book a technician for tomorrow morning", 14_000_000)
    assert tool_calls(again) == ["book_technician"] and len(care.bookings) == 1
    clean(h)


def test_washer_display_shows_another_code_and_janus_asks_again():
    h, care, payload = washer_setup()
    washer_offered(h, payload)
    push(h, 2_600_000, [washer_fact("4C")], 2_800_000)
    push(h, 3_000_000, [washer_fact("4C")], 3_200_000)  # the same reading again: no news
    actions = push(h, 3_500_000, [washer_fact("5C")], 4_500_000)
    lines = said(actions)
    assert any(t.startswith("Heads up: The washer changed from 4C to 5C") and "Shall I still book a technician for tomorrow morning?" in t
               for t in lines), lines
    assert care.bookings == []
    later = say(h, 5_000_000, "yes please", 9_000_000)
    assert tool_calls(later) == ["book_technician"] and len(care.bookings) == 1
    clean(h)


def test_no_washer_offer_without_a_fault():
    care = DeviceCareToolset()
    care.status["washer"]["error_code"] = None
    assert run(build_offer("washer_error", None, lambda device: care._get_device_status({"device_type": device}))) is None
    assert run(build_offer("washer_error", None)) is None  # no appliance reader: nothing to offer


def test_no_washer_offer_once_a_visit_is_booked_for_it():
    """Found live (21 Oct): a second offer after the visit was booked was
    refused at once as a repeat; there is nothing left to offer."""
    care = DeviceCareToolset()
    status = care._get_device_status({"device_type": "washer"})
    assert run(build_offer("washer_error", None, lambda device: {**status, "visit_booked": True})) is None


# ---- the AC offer: someone else changes the thermostat while Janus waits -------

def ac_setup(setpoint=27):
    mock = MockSmartThings()
    run(mock.command("thermo-1", "thermostatCoolingSetpoint", "setCoolingSetpoint", [setpoint]))
    mock.commands.clear()
    toolset = SmartThingsToolset(mock, sleep=_no_sleep)
    execute = toolset.make_executor()
    provider = ScriptedProvider()
    provider.register("interpret", "no leave it", {"act": "deny", "slot_deltas": []})
    provider.register("interpret", "yes please", {"act": "confirm", "slot_deltas": []})
    provider.register("compose", "", {"text": "Done.", "claims": []})
    tools = {m["name"]: {"latency_ms": 200, "handler": (lambda args, n=m["name"]: run(execute(n, args)))} for m in MANIFEST}
    h = SimHarness(demo_config(), seed=1, provider=provider, tools=tools, worker_latency_us=FAST)
    h.send(0, [manifest_event(MANIFEST)])
    payload = run(build_offer("ac_hot", toolset.read_target, None, toolset.read_temperature))
    return h, mock, payload


def thermostat_fact(value):
    return {"type": "world_fact", "payload": {"key": world_target_key("thermostat"), "value": value}}


def test_ac_offer_names_the_room_and_the_target():
    _, _, payload = ac_setup()
    assert payload["text"] == "It's 29 degrees inside. Shall I set the AC to 24?"
    assert payload["tool"] == "st_set_cooling_setpoint" and payload["args"] == {"device": "thermostat", "celsius": 24}
    _, _, none = ac_setup(setpoint=24)
    assert none is None  # already at 24: nothing to offer


def test_ac_offer_someone_else_sets_26_and_janus_asks_again():
    h, mock, payload = ac_setup()
    actions = push(h, 500_000, [{"type": "offer", "payload": payload}], 2_500_000)
    assert said(actions) == [payload["text"]]
    push(h, 2_600_000, [thermostat_fact(27)], 2_800_000)  # baseline when the question was asked
    run(mock.command("thermo-1", "thermostatCoolingSetpoint", "setCoolingSetpoint", [26]))  # someone else
    mock.commands.clear()
    actions = push(h, 3_500_000, [thermostat_fact(26)], 4_500_000)
    lines = said(actions)
    assert any(t.startswith("Heads up: The thermostat changed from 27 to 26") and "Shall I still set the thermostat to 24 degrees?" in t
               for t in lines), lines
    assert tool_calls(actions) == []
    later = say(h, 5_000_000, "no leave it", 8_000_000)
    assert tool_calls(later) == [] and mock.commands == []
    assert "Okay, nothing was sent." in said(later)
    clean(h)


def test_ac_offer_someone_else_sets_24_and_the_offer_withdraws_itself():
    h, mock, payload = ac_setup()
    push(h, 500_000, [{"type": "offer", "payload": payload}], 2_500_000)
    push(h, 2_600_000, [thermostat_fact(27)], 2_800_000)
    actions = push(h, 3_500_000, [thermostat_fact(24)], 4_500_000)
    assert EVIDENCE_WITHDRAWN.format(device="the thermostat", value=24) in said(actions)
    later = say(h, 5_000_000, "yes please", 8_000_000)
    assert tool_calls(actions + later) == [] and mock.commands == []
    clean(h)


def test_nothing_to_offer_is_said_with_the_reason():
    from prism_rt.adapters.offers import no_offer_text

    care = DeviceCareToolset()
    care.bookings.append({"booking_id": "BK-0001", "issue_id": care.current_issue, "date": "Friday", "time_slot": "afternoon"})

    def read_appliance(device):
        status = care._get_device_status({"device_type": device})
        status["visit_booked"] = True
        return status

    async def read_target(tool, spoken):
        return "closed"

    assert run(no_offer_text("washer_error", read_target, read_appliance)) == (
        "Nothing to offer for the washer right now: a technician visit is already booked.")
    assert run(no_offer_text("garage_open", read_target)) == "The garage is already closed, so there's nothing to offer."
    assert run(no_offer_text("ac_hot", read_target)) == "The AC is already at 24 degrees or cooler, so there's nothing to offer."


def test_washer_offer_believes_the_camera_first():
    """With the camera on, the offer follows what it reads off the display; the
    washer's own report decides only with the camera off (phone, 24 Oct: the
    offer named 4C with the camera on a wall)."""
    care = DeviceCareToolset()

    def read_appliance(device):
        return care._get_device_status({"device_type": device})

    def look(seen, value):
        async def _look(device):
            return seen, value
        return _look

    on_4c = run(build_offer("washer_error", None, read_appliance, look=look(True, "4C")))
    assert on_4c["text"].startswith("Your washer has stopped with code 4C: water is not being supplied")
    wall = run(build_offer("washer_error", None, read_appliance, look=look(True, None)))
    assert wall == {"notice": "I can't make out the washer's display. Point the camera at it and ask me again."}
    clear = run(build_offer("washer_error", None, read_appliance, look=look(True, "clear")))
    assert clear == {"notice": "The washer's display shows no error right now, so there's nothing to offer."}
    camera_off = run(build_offer("washer_error", None, read_appliance, look=look(False, None)))
    assert camera_off["text"] == on_4c["text"]
    other = run(build_offer("washer_error", None, read_appliance, look=look(True, "5C")))
    assert other["text"] == "Your washer has stopped with code 5C. Shall I book a technician for tomorrow morning?"


def test_display_reader_look_says_whether_the_camera_saw_anything():
    from prism_rt.adapters.display_reader import DisplayReader

    def reader(frame, answer):
        return DisplayReader(lambda *a, **k: answer, lambda age: frame, lambda device: "4C")

    assert run(reader(None, {}).look("washer")) == (False, None)
    assert run(reader(None, {}).read("washer")) == "4C"  # the watch still falls back
    assert run(reader(object(), {"display_visible": False, "error_code": None}).look("washer")) == (True, None)
    assert run(reader(object(), {"display_visible": True, "error_code": None}).look("washer")) == (True, "clear")
    assert run(reader(object(), {"display_visible": True, "error_code": "4c"}).look("washer")) == (True, "4C")
