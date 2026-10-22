"""The read-set X-ray on the companion app's cards (app/INTEGRATION.md,
`based_on` / `changed`): what an action was built on, and what changed to
withdraw it -- the user's correction, or the world while Janus waited."""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event
import test_demo_speech_polish as polish
import test_offers as offers

from prism_rt.adapters.event_feed import EventFeed, why
from prism_rt.devicecare.tools import MANIFEST as CARE_MANIFEST
from prism_rt.model.types import JobKind

TO22 = {"act": "new_goal", "intent": "st_set_cooling_setpoint", "slot_deltas": [], "commit_intent": True,
        "actions": [{"tool": "st_set_cooling_setpoint", "args": {"device": "thermostat", "celsius": 22}}]}
TO21 = {"act": "slot_update", "slot_deltas": [{"name": "celsius", "scope": "goal", "op": "set", "value": 21}]}


def corrected():
    h, mock = polish.session(extra=[("make it twenty-one", TO21), ("twenty-two", TO22)],
                             latency={**polish.FAST, JobKind.INTERPRET: 600_000})
    h.send(100_000, [chunk_event("set the thermostat to twenty-two")])
    h.send(150_000, [eot_event()])
    h.send(160_000, [{"type": "user_speech", "payload": {"active": True}}])
    drain(h, 1_500_000, stop_on_final=False)
    h.send(1_600_000, [chunk_event("actually make it twenty-one")])
    h.send(1_700_000, [{"type": "user_speech", "payload": {"active": False}}])
    h.send(1_750_000, [eot_event()])
    drain(h, 8_000_000, stop_on_final=False)
    return h


def test_a_corrected_value_is_what_withdrew_the_old_call():
    h = corrected()
    old, new = h.store.call_ledger.all()
    assert why(old, h.store) == {
        "based_on": [{"what": "celsius", "value": "22", "source": "you"}, {"what": "device", "value": "thermostat", "source": "you"}],
        "changed": [{"what": "celsius", "was": "22", "now": "21", "source": "you"}],
    }
    assert why(new, h.store)["changed"] == []
    assert {"what": "celsius", "value": "21", "source": "you"} in why(new, h.store)["based_on"]


def test_the_washer_reading_is_what_withdrew_the_offered_visit():
    h, care, payload = offers.washer_setup()
    events = []
    feed = EventFeed(events.append, backend="mock", packs=["device_care"], manifest=CARE_MANIFEST, clock_ms=lambda: 0)
    h.run_log.reports = polish_hook(h, feed)
    offers.washer_offered(h, payload)
    offers.push(h, 2_600_000, [offers.washer_fact("4C")], 2_800_000)
    offers.push(h, 3_500_000, [offers.washer_fact("clear")], 4_500_000)
    (cancelled,) = [e for e in events if e["type"] == "call_cancelled"]
    assert cancelled["reason"] == "Washer error code cleared while waiting"
    assert {"what": "date", "value": "tomorrow", "source": "offer"} in cancelled["based_on"]
    assert {"what": "washer", "value": "4C", "source": "world"} in cancelled["based_on"]
    assert cancelled["changed"] == [{"what": "washer", "was": "4C", "now": "clear", "source": "world"}]
    (planned,) = [e for e in events if e["type"] == "call_planned"]
    assert {"what": "time slot", "value": "morning", "source": "offer"} in planned["based_on"]


def polish_hook(h, feed):
    class Hooked(list):
        def append(self, report):
            super().append(report)
            feed.on_step(report, h.store)

    hooked = Hooked()
    hooked.extend(h.run_log.reports)
    return hooked
