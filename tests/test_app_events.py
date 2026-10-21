"""WP6: the companion-app event feed (app/INTEGRATION.md, topic janus.events, v1).

`EventFeed` turns kernel step reports (plus the host's filler and device
reads) into the contract's JSON events. Driven here from a deterministic
sim run; every event is checked against the contract's field table, which is
itself cross-checked against the recorded sample file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.adapters.event_feed import EVENT_FIELDS, EventFeed
from prism_rt.config import Config
from prism_rt.model.types import JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.smartthings.mock import MockSmartThings
from prism_rt.smartthings.tools import MANIFEST, SmartThingsToolset
from prism_rt.workers.gateway import ScriptedProvider

import asyncio

FAST = {k: 10_000 for k in JobKind}
ROOT = Path(__file__).resolve().parents[1]


class Hooked(list):
    """RunLog.reports that calls the feed after every step, with the store as
    it is right after that step."""

    def __init__(self, on_append):
        super().__init__()
        self._on_append = on_append

    def append(self, report):
        super().append(report)
        self._on_append(report)


def demo(**kw):
    base = dict(action_plans_enabled=True, multi_action_enabled=True, conversational_replies_enabled=True,
                confirmation_policy_enabled=True, unconfirmed_outcomes_enabled=True,
                settle_barrier_enabled=True, settle_ms=500)
    base.update(kw)
    return Config(**base)


def session(interpret, mock=None, utterances=("lock the door",), cfg=None):
    mock = mock or MockSmartThings()

    async def no_sleep(_s):
        return None

    toolset = SmartThingsToolset(mock, sleep=no_sleep)
    execute = toolset.make_executor()
    events: list[dict] = []
    t = {"ms": 0}
    feed = EventFeed(events.append, backend="mock", packs=["smartthings"], manifest=MANIFEST, clock_ms=lambda: t["ms"])
    provider = ScriptedProvider()
    for key, reply in interpret:
        provider.register("interpret", key, reply)
    provider.register("compose", "a0", {"text": "Done.", "claims": []})
    tools = {m["name"]: {"latency_ms": 200, "handler": (lambda a, n=m["name"]: asyncio.run(execute(n, a)))} for m in MANIFEST}
    h = SimHarness(cfg or demo(), seed=1, provider=provider, tools=tools, worker_latency_us=FAST)
    h.run_log.reports = Hooked(lambda r: (t.__setitem__("ms", r.now_us // 1000), feed.on_step(r, h.store)))
    feed.session_started()
    h.send(0, [manifest_event(MANIFEST)])
    ts = 100_000
    for text in utterances:
        h.send(ts, [chunk_event(text)])
        h.send(ts + 50_000, [eot_event()])
        drain(h, ts + 4_000_000, stop_on_final=False)
        ts += 5_000_000
    return h, events, feed


def of(events, type_, **match):
    return [e for e in events if e["type"] == type_ and all(e.get(k) == v for k, v in match.items())]


LOCK_AC = {"act": "new_goal", "intent": "st_lock", "slot_deltas": [], "commit_intent": True, "actions": [
    {"tool": "st_lock", "args": {"device": "front door"}},
    {"tool": "st_set_cooling_setpoint", "args": {"device": "ac", "celsius": 24}}]}
FIX_AC = {"act": "slot_update", "slot_deltas": [{"name": "a1.celsius", "scope": "goal", "op": "set", "value": 26}]}
UNLOCK = {"act": "new_goal", "intent": "st_unlock", "slot_deltas": [], "commit_intent": True,
          "actions": [{"tool": "st_unlock", "args": {"device": "front door"}}]}


# ---- contract -----------------------------------------------------------------

needs_app_files = pytest.mark.skipif(not (ROOT / "app" / "INTEGRATION.md").is_file(), reason="app/ is not in the test image")


@needs_app_files
def test_event_fields_table_matches_the_contract_document():
    doc = (ROOT / "app" / "INTEGRATION.md").read_text()
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", doc, re.M)) & set(EVENT_FIELDS)
    # every type in the feed's table appears in the document's table
    assert documented == set(EVENT_FIELDS)


@needs_app_files
def test_recorded_sample_uses_only_contract_fields():
    for line in (ROOT / "app" / "sample_events.jsonl").read_text().splitlines():
        ev = json.loads(line)
        assert set(EVENT_FIELDS[ev["type"]]) <= set(ev), ev


def check_schema(events):
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    times = [e["t_ms"] for e in events]
    assert times == sorted(times)
    for e in events:
        assert e["v"] == 1 and isinstance(e["t_ms"], int)
        assert set(EVENT_FIELDS[e["type"]]) <= set(e), e
        json.dumps(e)


# ---- a recorded-shape run -------------------------------------------------------

def test_lock_and_corrected_setpoint_tell_the_whole_story():
    h, events, _ = session([("lock the door", LOCK_AC)], utterances=("lock the door and set the ac to 24",))
    check_schema(events)
    assert events[0]["type"] == "session_started" and events[0]["backend"] == "mock"
    assert of(events, "heard")[0]["text"] == "lock the door and set the ac to 24"
    assert of(events, "turn_closed")
    planned = of(events, "call_planned")
    assert {p["tool"] for p in planned} == {"st_lock", "st_set_cooling_setpoint"}
    lock = next(p for p in planned if p["tool"] == "st_lock")
    assert lock["label"] == "Lock the front door" and lock["kind"] == "write" and lock["security"] is False
    sent = of(events, "call_sent")
    assert {e["call_id"] for e in sent} == {p["call_id"] for p in planned}
    results = {e["call_id"]: e for e in of(events, "call_result")}
    assert results[lock["call_id"]]["outcome"] == "confirmed"
    assert of(events, "goal_finished")[0]["completed"] is True
    summary = of(events, "ledger_summary")[-1]
    # The mock thermostat is already at 24: that call is "already", nothing was
    # sent, so only the lock is committed (it used to count as 2).
    assert results[next(p for p in planned if p["tool"] == "st_set_cooling_setpoint")["call_id"]]["outcome"] == "already"
    assert summary["committed"] == 1 and summary["unconfirmed"] == 0
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_a_correction_marks_the_old_call_never_sent_with_the_new_value():
    h, events, _ = session([("lock the door", LOCK_AC), ("make it 26", FIX_AC)],
                           utterances=("lock the door and set the ac to 24", "no wait, make it 26"))
    ac = [p for p in of(events, "call_planned") if p["tool"] == "st_set_cooling_setpoint"]
    cancelled = of(events, "call_cancelled")
    if len(ac) > 1:  # the 24 call was withdrawn before it was sent
        old = next(c for c in cancelled if c["call_id"] == ac[0]["call_id"])
        assert "26" in old["reason"]
        assert ac[0]["call_id"] not in {e["call_id"] for e in of(events, "call_sent")}
        assert ac[0]["call_id"] not in {e["call_id"] for e in of(events, "call_result")}
    check_schema(events)


def test_held_reason_is_reported_with_its_code_and_only_when_it_changes():
    h, events, _ = session([("lock the door", LOCK_AC)], utterances=("lock the door and set the ac to 24",))
    held = of(events, "call_held")
    assert held and {e["reason_code"] for e in held} <= {"user_speaking", "settling", "needs_go_ahead", "needs_confirmation",
                                                          "duplicate", "outcome_unknown", "other"}
    keyed = [(e["call_id"], e["reason_code"]) for e in held]
    assert len(keyed) == len(set(keyed))  # never repeated for the same reason


def test_security_call_asks_and_waits_then_runs_on_yes():
    yes = {"act": "confirm", "slot_deltas": []}
    h, events, _ = session([("yes please", yes), ("unlock the door", UNLOCK)],
                           utterances=("unlock the door", "yes please"))
    planned = of(events, "call_planned")[0]
    assert planned["tool"] == "st_unlock" and planned["security"] is True
    held = [e for e in of(events, "call_held", call_id=planned["call_id"])]
    assert any(e["reason_code"] == "needs_confirmation" and e["reason"] == "Security action: waiting for your yes" for e in held)
    asked = of(events, "confirmation_asked", call_id=planned["call_id"])
    assert len(asked) == 1 and "security action" in asked[0]["question"].lower()
    order = [e["type"] for e in events if e.get("call_id") == planned["call_id"]]
    assert order.index("confirmation_asked") < order.index("call_sent") < order.index("call_result")
    check_schema(events)


def test_unconfirmed_is_never_reported_as_done():
    h, events, _ = session([("lock the door", LOCK_AC)], mock=MockSmartThings(stuck={"lock"}),
                           utterances=("lock the door and set the ac to 24",))
    lock_id = next(p for p in of(events, "call_planned") if p["tool"] == "st_lock")["call_id"]
    (res,) = of(events, "call_result", call_id=lock_id)
    assert res["outcome"] == "unconfirmed"
    fin = of(events, "goal_finished")[0]
    assert fin["completed"] is False
    assert of(events, "ledger_summary")[-1]["unconfirmed"] == 1


def test_repeat_lock_is_reported_as_already():
    lock = {"act": "new_goal", "intent": "st_lock", "slot_deltas": [], "commit_intent": True,
            "actions": [{"tool": "st_lock", "args": {"device": "front door"}}]}
    h, events, _ = session([("lock the door", lock)], utterances=("lock the door", "lock the door again"))
    outcomes = [e["outcome"] for e in of(events, "call_result")]
    assert outcomes == ["confirmed", "already"]



def test_a_repeated_request_is_a_blocked_duplicate_not_a_second_commit():
    """Survey A5: "Lock the door." ... "Lock the door again." The second one
    sends nothing (the device is already locked, by us); the ledger counts one
    command committed and one duplicate blocked, not two commits."""
    lock = {"act": "new_goal", "intent": "st_lock", "slot_deltas": [], "commit_intent": True,
            "actions": [{"tool": "st_lock", "args": {"device": "front door"}}]}
    again = {"act": "new_goal", "intent": "st_lock", "slot_deltas": [], "commit_intent": True,
             "actions": [{"tool": "st_lock", "args": {"device": "the Front Door"}}]}
    h, events, _ = session([("lock it again", again), ("lock the door", lock)],
                           utterances=("lock the door", "lock it again", "lock it again"))
    assert [e["outcome"] for e in of(events, "call_result")] == ["confirmed", "already", "already"]
    summary = of(events, "ledger_summary")[-1]
    assert summary["committed"] == 1 and summary["duplicates_blocked"] == 2
    check_schema(events)


def test_already_in_place_by_someone_else_is_neither_committed_nor_a_duplicate():
    """The door was locked before Janus was asked: nothing was sent and nothing
    of ours was repeated."""
    mock = MockSmartThings()
    mock._devices["lock-1"][1]["lock"]["lock"] = "locked"
    lock = {"act": "new_goal", "intent": "st_lock", "slot_deltas": [], "commit_intent": True,
            "actions": [{"tool": "st_lock", "args": {"device": "front door"}}]}
    h, events, _ = session([("lock the door", lock)], mock=mock)
    assert [e["outcome"] for e in of(events, "call_result")] == ["already"]
    summary = of(events, "ledger_summary")[-1]
    assert summary["committed"] == 0 and summary["duplicates_blocked"] == 0

# ---- host-side events --------------------------------------------------------------

def make_feed():
    events = []
    clock = {"ms": 0}
    return events, clock, EventFeed(events.append, backend="real", packs=["smartthings"], manifest=MANIFEST, clock_ms=lambda: clock["ms"])


def test_filler_and_device_state_are_published_directly():
    events, clock, feed = make_feed()
    feed.session_started()
    feed.agent_said("Okay, one moment.", "filler")
    feed.device_state("dev-1", "Front door", "lock", "lock", "locked")
    check_schema(events)
    assert events[0]["backend"] == "real" and events[0]["packs"] == ["smartthings"]
    assert events[1] == {**events[1], "type": "agent_said", "kind": "filler"}
    assert events[2]["backend"] == "real" and events[2]["value"] == "locked"
    feed.device_state("dev-washer", "Washer", "washerOperatingState", "machineState", "pause", backend="mock")
    check_schema(events)
    assert events[3]["backend"] == "mock"  # a simulated device on a real session says so


def test_unknown_event_types_are_a_programming_error_not_silently_sent():
    _events, _clock, feed = make_feed()
    with pytest.raises(KeyError):
        feed._send("telepathy")


def test_publishing_failure_never_breaks_the_kernel():
    def boom(_event):
        raise RuntimeError("room closed")

    feed = EventFeed(boom, backend="mock", packs=[], manifest=[], clock_ms=lambda: 0)
    feed.session_started()  # swallowed


def test_events_carry_no_secrets(monkeypatch):
    monkeypatch.setenv("ST_PAT", "sekret-token-123")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "sekret-lk-456")
    h, events, _ = session([("lock the door", LOCK_AC)], utterances=("lock the door and set the ac to 24",))
    blob = json.dumps(events)
    assert "sekret" not in blob and "Bearer" not in blob


# ---- the entry hook ------------------------------------------------------------

def test_entry_calls_on_step_after_each_step_and_survives_a_failing_callback():
    from prism_rt.entry import setup

    seen = []

    def on_step(report):
        seen.append(report.step_no)
        raise RuntimeError("feed bug")  # must not end the scenario

    runtime = setup(config=Config(watchdog_timeout_ms=0), provider=ScriptedProvider())

    async def go():
        events, out = asyncio.Queue(), asyncio.Queue()
        await events.put({"type": "manifest", "ts_us": 1, "payload": {"tools": MANIFEST}})
        await events.put({"type": "text_chunk", "ts_us": 2, "payload": {"text": "hello"}})
        await events.put(None)
        return await runtime.run_scenario(events, out, meta={"seed": 1, "on_step": on_step})

    summary = asyncio.run(go())
    assert seen and seen == [r.step_no for r in summary.reports]
