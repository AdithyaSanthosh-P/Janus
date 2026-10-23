"""The hedge check (demo): a request or correction said with "I think",
"maybe", "or so" is not sent on the interpretation alone. CommitGate G4 holds
the write and Janus asks; a yes sends it once, "no, make it 7" sends only 7.
A plain request never asks, and a hedged read just runs."""

from __future__ import annotations

import pytest
from test_phone import _intent, finals, say, setup

from prism_rt.kernel.interpret_apply import _HEDGE
from prism_rt.kernel.replies import spoken_clock_times
from prism_rt.model.types import ActionType
from prism_rt.sim.checker import TraceChecker

SEVEN = {"act": "slot_update", "commit_intent": True,
         "slot_deltas": [{"name": "time", "scope": "goal", "op": "set", "value": "07:00"}]}
YES = {"act": "confirm", "slot_deltas": []}


def hedged_setup():
    h, sent = setup()
    # Registered ahead of setup's own rules: the first rule that matches wins,
    # and a later turn's prompt also carries the earlier turns.
    rules = h.provider._rules
    rules.insert(0, ("interpret", "yes please", YES))
    rules.insert(0, ("interpret", "make it 7", SEVEN))
    rules.insert(0, ("interpret", "maybe 7", SEVEN))
    rules.insert(0, ("interpret", "whatever you did", _intent("session_recap", {})))
    return h, sent


def spoken(actions):
    return [a.body.text for a in actions if a.action_type in (ActionType.SPEAK, ActionType.FINAL) and getattr(a.body, "text", None)]


def test_a_hedged_alarm_is_asked_about_then_set_once_on_yes():
    h, sent = hedged_setup()
    lines = spoken(say(h, 100_000, "set an alarm for 6:30 I think", 4_000_000))
    assert "Just to check — set an alarm for 6:30 AM, right?" in lines
    assert sent == []
    actions = say(h, 4_100_000, "yes please", 9_000_000)
    assert finals(actions) == ["Your alarm is set for 6:30 AM."]
    assert [(m["hour"], m["minute"]) for m in sent] == [(6, 30)]
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_no_make_it_7_sends_only_7():
    h, sent = hedged_setup()
    say(h, 100_000, "set an alarm for 6:30 maybe", 4_000_000)
    actions = say(h, 4_100_000, "no, make it 7", 9_000_000)
    assert finals(actions) == ["Your alarm is set for 7 AM."]
    assert [(m["hour"], m["minute"]) for m in sent] == [(7, 0)]
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_a_hedged_correction_is_asked_about_too():
    h, sent = hedged_setup()
    say(h, 100_000, "set an alarm for 6:30", 150_000)  # still running when corrected
    lines = spoken(say(h, 200_000, "hmm, maybe 7", 4_000_000))
    assert lines == ["Just to check — set an alarm for 7 AM, right?"]
    assert sent == []
    assert finals(say(h, 4_100_000, "yes please", 9_000_000)) == ["Your alarm is set for 7 AM."]
    assert [(m["hour"], m["minute"]) for m in sent] == [(7, 0)]


def test_a_plain_request_never_asks():
    h, sent = hedged_setup()
    lines = spoken(say(h, 100_000, "set an alarm for 6:30", 5_000_000))
    assert not any(t.startswith("Just to check") for t in lines)
    assert len(sent) == 1


def test_a_hedged_read_just_runs():
    h, sent = hedged_setup()
    lines = spoken(say(h, 100_000, "maybe tell me whatever you did", 4_000_000))
    assert not any(t.startswith("Just to check") for t in lines)
    assert finals(say(h, 100_000 + 4_000_000, "set an alarm for 6:30", 9_000_000)) == ["Your alarm is set for 6:30 AM."]


@pytest.mark.parametrize("text", ["six thirty I think", "maybe 7", "probably 22", "7 or so", "I'm not sure, 21",
                                  "I guess 5 minutes", "not really sure"])
def test_hedges(text):
    assert _HEDGE.search(text)


@pytest.mark.parametrize("text", ["set an alarm for 6:30", "make it 7", "yes please", "thinking of 7", "mayberry"])
def test_not_hedges(text):
    assert not _HEDGE.search(text)


def test_the_same_alarm_again_is_named_as_the_action():
    h, sent = hedged_setup()
    say(h, 100_000, "set an alarm for 6:30", 5_000_000)
    assert finals(say(h, 5_100_000, "set an alarm for 6:30", 10_000_000)) == [
        "That's already done: you asked me to set an alarm for 6:30 AM earlier and it went through, so I won't repeat it."]
    assert len(sent) == 1


@pytest.mark.parametrize("text, said", [
    ("I'll set an alarm for 06:30 tomorrow morning.", "I'll set an alarm for 6:30 AM tomorrow morning."),
    ("I'll update your alarm time to 07:00.", "I'll update your alarm time to 7 AM."),
    ("Alarm at 19:15.", "Alarm at 7:15 PM."),
    ("Alarm at 6:30 tomorrow.", "Alarm at 6:30 tomorrow."),
    ("Alarm at 07:00 am.", "Alarm at 07:00 am."),
])
def test_a_model_sentence_says_times_as_a_person_would(text, said):
    assert spoken_clock_times(text) == said
