"""1 Oct audit: the kernel only learned the user was speaking from transcripts,
which land ~1.1 s after the words. A genuine pause long enough to close the
turn, then more speech, let a call (and speech) go out while the user was
audibly talking -- 29 calls in the 72 current-code voice recordings.
Config.vad_floor_enabled: `user_speech` events from the voice host hold tool
calls and SPEAK/CLARIFY/FINAL while the user is speaking or their words are
still being transcribed."""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.model.types import ActionType, JobKind
from prism_rt.profiles import fdb_v3_config
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

APT = {"name": "search_apartments", "parameters": {"type": "object", "properties": {
    "city": {"type": "string"}, "bedrooms": {"type": "integer"}, "max_price": {"type": "number"}},
    "required": ["city", "bedrooms", "max_price"]}}
LATENCY = {k: 10_000 for k in (JobKind.INTERPRET, JobKind.PLAN, JobKind.COMPOSE, JobKind.EXTRACT, JobKind.BIND)}


def speech_event(active: bool) -> dict:
    return {"type": "user_speech", "payload": {"active": active}}


def _harness(**overrides) -> SimHarness:
    provider = ScriptedProvider()
    provider.register("interpret", "max price around", {
        "act": "slot_update", "slot_deltas": [{"name": "a0.max_price", "scope": "goal", "op": "set", "value": 2000}]})
    provider.register("interpret", "two-bedroom in Chicago", {
        "act": "new_goal", "intent": "search_apartments", "slot_deltas": [],
        "actions": [{"tool": "search_apartments", "args": {"city": "Chicago", "bedrooms": 2, "max_price": 3000}}]})
    provider.register("compose", "a0", {"text": "Found one.", "claims": []})
    h = SimHarness(fdb_v3_config(**overrides), seed=1, provider=provider,
                   tools={"search_apartments": {"latency_ms": 300, "response": {"results": [{"id": "APT1"}]}}},
                   worker_latency_us=LATENCY)
    h.send(0, [manifest_event([APT])])
    return h


def _calls(actions) -> list[dict]:
    return [dict(a.body.arguments) for a in actions if a.action_type == ActionType.TOOL_CALL]


def _event_driven(h, until_us: int) -> list:
    """Step only when production would: at a due timer or worker/tool result."""
    actions = []
    while True:
        due = [h.store.timers.next_due_us()]
        due += [due_us for due_us, _kind, _view in h.runner._pending.values()]
        due += [entry[0] for entry in h.mock_tools._scheduled.values()]
        due = [t for t in due if t is not None]
        if not due or min(due) > until_us:
            return actions
        report = h.send(max(min(due), h.clock.now_us()))
        actions += [er.action for er in report.emit_report.emitted]


def _pause_then_correction(**overrides) -> list:
    """Turn 1 closes on a real pause; the user starts speaking again before the
    settle window ends; the words of that second part arrive ~1.3 s later."""
    h = _harness(**overrides)
    h.send(100_000, [speech_event(True), chunk_event("I'm interested in a two-bedroom in Chicago")])
    actions = [er.action for er in h.send(150_000, [speech_event(False), eot_event()]).emit_report.emitted]
    actions += drain(h, 900_000, stop_on_final=False)
    h.send(900_000, [speech_event(True)])  # speaking again: no text yet
    actions += drain(h, 2_200_000, stop_on_final=False)  # settle (1 s) ends at 1.15 s, mid-speech
    h.send(2_200_000, [chunk_event("and keep the max price around two thousand a month")])
    h.send(2_210_000, [speech_event(False)])
    actions += [er.action for er in h.send(3_700_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 9_000_000)
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []
    return actions


def test_no_call_goes_out_while_the_user_is_audibly_speaking():
    assert _calls(_pause_then_correction(vad_floor_enabled=True)) == [{"city": "Chicago", "bedrooms": 2, "max_price": 2000}]


def test_the_fdb_profile_holds_the_floor_on_user_speech():
    assert fdb_v3_config().vad_floor_enabled


def test_without_the_signal_the_first_call_goes_out_mid_speech():
    """Negative control: the live failure."""
    calls = _calls(_pause_then_correction(vad_floor_enabled=False))
    assert [c["max_price"] for c in calls] == [3000, 2000]


def test_a_stale_speaking_report_stops_holding_the_floor():
    """A lost "stopped" report must not hold the floor forever: the call is
    released once the report is older than vad_floor_max_ms, with no further
    events (the cap timer wakes the kernel)."""
    h = _harness(vad_floor_enabled=True, vad_floor_max_ms=3_000)
    h.send(100_000, [chunk_event("I'm interested in a two-bedroom in Chicago")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted]
    h.send(200_000, [speech_event(True)])  # never followed by speech_event(False)
    actions += _event_driven(h, 3_100_000)
    assert _calls(actions) == []
    actions += _event_driven(h, 6_000_000)
    assert _calls(actions) == [{"city": "Chicago", "bedrooms": 2, "max_price": 3000}]


def test_the_agent_does_not_start_speaking_over_the_user():
    h = _harness(vad_floor_enabled=True)
    h.send(100_000, [chunk_event("I'm interested in a two-bedroom in Chicago")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 1_300_000, stop_on_final=False)  # settle ends at 1.15 s
    assert _calls(actions)  # the search is out; its result lands at ~1.45 s
    h.send(1_300_000, [speech_event(True)])  # the user starts saying something
    held = drain(h, 4_000_000, stop_on_final=False)
    assert not [a for a in held if a.action_type in (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL)]
    released = [er.action for er in h.send(4_000_000, [speech_event(False)]).emit_report.emitted]
    released += drain(h, 6_000_000)
    assert [a for a in released if a.action_type == ActionType.FINAL]


def test_flag_off_ignores_user_speech_events():
    h = _harness(vad_floor_enabled=False)
    h.send(100_000, [speech_event(True)])
    assert h.store.facts.get("session.user_active") is None
