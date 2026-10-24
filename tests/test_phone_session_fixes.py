"""Fixes from a real-phone session (23 Oct, demo profile): a second booking
went out on a misheard turn without a yes; the speech-to-text prompt came back
as the user's words; a correction just after a device command went through
ended the request; a misheard question got the last task's status; an
unsupported request listed tool names."""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event
from test_devicecare import _booking, _diagnosed_toolset, calls, harness
from test_recap import finals, say, setup, spoken

from prism_rt.adapters.voice_settings import echoes_prompt
from prism_rt.model.types import ActionType
from prism_rt.sim.checker import TraceChecker
from prism_rt.workers.gateway import ScriptedProvider

PROMPT = ("The speaker is talking in English (possibly with an accent) to a voice assistant. Transcribe exactly what "
          "they say, in English, using the Latin alphabet. The assistant is called Janus. It controls a smart home: the "
          "thermostat, the AC, the garage, the front door lock, the light switch, and a Samsung washer; it can book a "
          "technician visit, and set alarms and timers on the phone.")


def _turn(h, ts, text, until):
    h.send(ts, [chunk_event(text)])
    return [er.action for er in h.send(ts + 50_000, [eot_event()]).emit_report.emitted] + drain(h, until, stop_on_final=False)


def test_a_second_booking_waits_for_a_yes_even_when_a_later_turn_changes_it():
    provider = ScriptedProvider()
    provider.register("interpret", "yes please", {"act": "confirm", "slot_deltas": []})
    provider.register("interpret", "since morning", {
        "act": "slot_update", "commit_intent": True,
        "slot_deltas": [{"name": "time_slot", "scope": "goal", "op": "set", "value": "morning"}]})
    provider.register("interpret", "evening instead", _booking("Friday", "evening"))
    provider.register("interpret", "Friday afternoon", _booking("Friday", "afternoon"))
    provider.register("compose", "book_technician", {"text": "Booked.", "claims": []})
    tk = _diagnosed_toolset()
    h = harness(provider, tk)
    _turn(h, 100_000, "book a technician for Friday afternoon", 6_000_000)
    asked = _turn(h, 6_100_000, "Friday evening instead", 12_000_000)
    assert any("as a second one" in getattr(a.body, "text", "") for a in asked)

    actions = _turn(h, 12_100_000, "what do we need to do since morning", 18_000_000)
    assert calls(actions) == [] and len(tk.bookings) == 1
    assert any("Friday morning anyway, as a second one?" in getattr(a.body, "text", "") for a in actions)

    _turn(h, 18_100_000, "yes please", 24_000_000)
    assert [(b["date"], b["time_slot"]) for b in tk.bookings] == [("Friday", "afternoon"), ("Friday", "morning")]
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_a_correction_just_after_a_device_command_went_through_sends_the_new_value():
    h, mock = setup()
    h.provider._rules.insert(0, ("interpret", "make it 23", {
        "act": "slot_update", "commit_intent": True,
        "slot_deltas": [{"name": "celsius", "scope": "goal", "op": "set", "value": 23}]}))
    h.provider._rules.insert(0, ("interpret", "thermostat to 22", {
        "act": "new_goal", "intent": "st_set_cooling_setpoint", "slot_deltas": [], "commit_intent": True,
        "actions": [{"tool": "st_set_cooling_setpoint", "args": {"device": "thermostat", "celsius": 22}}]}))
    h.send(100_000, [chunk_event("set the thermostat to 22")])
    h.send(150_000, [eot_event()])
    # The user starts the correction while 22 is on its way; it goes through
    # before they finish, so the request is still open when the turn ends.
    for t in range(200_000, 6_000_000, 20_000):
        h.send(t, [])
        if mock.commands:
            break
    h.send(t + 20_000, [chunk_event("no, actually, make it 23")])
    drain(h, t + 600_000, stop_on_final=False)
    assert h.store.effect_ledger.all() and [c[3] for c in mock.commands] == [[22]]
    lines = spoken([er.action for er in h.send(t + 650_000, [eot_event()]).emit_report.emitted]
                   + drain(h, t + 8_000_000, stop_on_final=False))
    assert [c[3] for c in mock.commands] == [[22], [23]]
    assert not any("already went through before" in x for x in lines)
    assert "The thermostat is now 23 degrees." in lines
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_a_misheard_question_after_a_task_is_asked_again_not_answered_with_status():
    h, _ = setup()
    h.provider._rules.insert(0, ("interpret", "camera stopped", {"act": "unclear", "slot_deltas": []}))
    h.provider._rules.insert(0, ("interpret", "has it been", {"act": "unclear", "slot_deltas": []}))
    say(h, 100_000, "lock the front door", 5_000_000)
    lines = spoken(say(h, 5_100_000, "my camera stopped with this error, what does it mean?", 10_000_000))
    assert not any(x.startswith("For your last request") for x in lines)
    lines = spoken(say(h, 10_100_000, "has it been locked yet?", 14_000_000))
    assert any(x.startswith("For your last request") for x in lines)


def test_an_unsupported_request_says_what_janus_is_for_not_its_tool_names():
    h, _ = setup()
    h.provider._rules.insert(0, ("interpret", "cancel the booking", {
        "act": "unclear", "slot_deltas": [], "unsupported": "cancel a booking"}))
    lines = spoken(say(h, 100_000, "cancel the booking", 4_000_000))
    assert lines == ["Sorry — \"cancel a booking\" isn't something I can do from here. I can control your smart-home "
                     "devices, look into a problem with your washer, book a technician, and set alarms and timers."]


def test_the_prompt_echoed_back_is_recognised():
    assert echoes_prompt("The speaker is talking in English (possibly with an accent) to a voice assistant.", PROMPT)
    assert echoes_prompt("set alarms and timers on the phone", PROMPT)
    assert not echoes_prompt("Set the thermostat to twenty-two.", PROMPT)
    assert not echoes_prompt("Lock the front door.", PROMPT)
    assert not echoes_prompt("Book a technician visit for Friday.", PROMPT)


def test_finals_helper_is_shared():
    assert finals([]) == [] and ActionType.FINAL


def test_known_mishearings_are_fixed_in_demo_transcripts():
    from prism_rt.adapters.voice_settings import fix_misheard_words
    assert fix_misheard_words("Set the Tomos Tractor to 22.") == "Set the thermostat to 22."
    assert fix_misheard_words("Hello Janice, set the homostat to 21") == "Hello Janus, set the thermostat to 21"
    assert fix_misheard_words("Set the thermostat to 22.") == "Set the thermostat to 22."
    assert fix_misheard_words("Now.") == "No." and fix_misheard_words("now") == "No"
    assert fix_misheard_words("Now make it 21.") == "Now make it 21."
