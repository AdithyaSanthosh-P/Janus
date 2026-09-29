"""Fixes for the 28 Sep live voice demo findings.

- Config.read_only_tools / settle_reads_enabled: correcting a search after it
  ran re-runs it (it used to fail honestly -- the duplicate-write guard
  treated an undeclared-mutability search as a booking); reads still wait out
  the settle barrier.
- Config.conversational_replies_enabled: honest "can't do that", status
  summaries, a polite reply to thanks, and a turn-stall salvage that says
  what it has done and never fires while waiting on the user's answer.
"""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind, ToolMutability
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

# No mutability declared -- the FDB shape.
SEARCH = {"name": "search_flights", "parameters": {"type": "object", "properties": {
    "destination": {"type": "string"}, "date": {"type": "string"}}, "required": ["destination", "date"]}}
BOOK = {"name": "book_flight", "parameters": {"type": "object", "properties": {
    "passenger_name": {"type": "string"}}, "required": ["passenger_name"]}}

FAST = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000, JobKind.EXTRACT: 10_000, JobKind.BIND: 10_000}


def config(**overrides) -> Config:
    base = dict(action_plans_enabled=True, multi_action_enabled=True, g4_exempt_undeclared_mutability=True,
                read_only_tools=("search_flights",), settle_reads_enabled=True, conversational_replies_enabled=True)
    base.update(overrides)
    return Config(**base)


def harness(provider, cfg, tools=None) -> SimHarness:
    tools = tools or {"search_flights": {"latency_ms": 200, "response": {"flight_id": "FL1"}},
                      "book_flight": {"latency_ms": 200, "response": {"booking_ref": "B1"}}}
    return SimHarness(cfg, seed=1, provider=provider, tools=tools, worker_latency_us=FAST)


def send(h, ts, events) -> list:
    return [er.action for er in h.send(ts, events).emit_report.emitted]


def speaks(actions) -> list[str]:
    return [a.body.text for a in actions if a.action_type in (ActionType.SPEAK, ActionType.FINAL, ActionType.CLARIFY)]


def tool_calls(actions) -> list:
    return [(a.body.tool_name, dict(a.body.arguments)) for a in actions if a.action_type == ActionType.TOOL_CALL]


def assert_clean(h) -> None:
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


CHENNAI_TOMORROW = {"act": "new_goal", "intent": "search_flights", "slot_deltas": [], "actions": [
    {"tool": "search_flights", "args": {"destination": "Chennai", "date": "tomorrow"}}]}
DAY_AFTER = {"act": "slot_update", "slot_deltas": [{"name": "date", "scope": "goal", "op": "set", "value": "day after tomorrow"}]}


def _correct_after_search(cfg) -> list:
    provider = ScriptedProvider()
    provider.register("interpret", "day after", DAY_AFTER)
    provider.register("interpret", "Chennai", CHENNAI_TOMORROW)
    provider.register("compose", "a0", {"text": "Found a flight.", "claims": []})
    tools = {"search_flights": {"latency_ms": 1_500, "response": {"flight_id": "FL1"}}}
    h = harness(provider, cfg, tools)
    h.send(0, [manifest_event([SEARCH])])
    h.send(100_000, [chunk_event("flights to Chennai tomorrow")])
    actions = send(h, 150_000, [eot_event()])
    actions += drain(h, 1_400_000, stop_on_final=False)  # search emitted at ~1.15 s (settle), in flight
    actions += send(h, 1_500_000, [chunk_event("no make it day after tomorrow")])
    actions += send(h, 1_550_000, [eot_event()])
    actions += drain(h, 6_000_000)
    return actions, h


def test_catalog_marks_listed_tools_read_only():
    h = harness(ScriptedProvider(), config())
    h.send(0, [manifest_event([SEARCH, BOOK])])
    assert h.store.catalog.get("search_flights").mutability == ToolMutability.READ_ONLY
    assert h.store.catalog.get("book_flight").mutability == ToolMutability.STATE_CHANGING


def test_correcting_a_search_reruns_it():
    actions, h = _correct_after_search(config())
    assert tool_calls(actions)[-1] == ("search_flights", {"destination": "Chennai", "date": "day after tomorrow"})
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1 and finals[0].body.task_completed is True
    assert_clean(h)


def test_without_read_only_the_correction_fails_honestly():
    """Negative control -- the live demo's exact failure."""
    actions, _ = _correct_after_search(config(read_only_tools=()))
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1 and finals[0].body.task_completed is False


def test_reads_still_wait_out_the_settle_barrier():
    provider = ScriptedProvider()
    provider.register("interpret", "Chennai", CHENNAI_TOMORROW)
    provider.register("compose", "a0", {"text": "Found a flight.", "claims": []})
    h = harness(provider, config(settle_barrier_enabled=True, settle_ms=1000))
    h.send(0, [manifest_event([SEARCH])])
    h.send(100_000, [chunk_event("flights to Chennai tomorrow")])
    actions = send(h, 150_000, [eot_event()])
    actions += drain(h, 1_100_000, stop_on_final=False)
    assert tool_calls(actions) == []
    actions += drain(h, 2_000_000)
    assert tool_calls(actions) == [("search_flights", {"destination": "Chennai", "date": "tomorrow"})]


def test_unsupported_request_is_declined_honestly_not_mapped_to_a_tool():
    provider = ScriptedProvider()
    provider.register("interpret", "hotel", {"act": "new_goal", "intent": None, "slot_deltas": [], "actions": [],
                                             "unsupported": "book a hotel"})
    h = harness(provider, config())
    h.send(0, [manifest_event([SEARCH, BOOK])])
    h.send(100_000, [chunk_event("book me a hotel in Chennai")])
    actions = send(h, 150_000, [eot_event()]) + drain(h, 1_000_000, stop_on_final=False)
    said = speaks(actions)
    assert len(said) == 1 and said[0].startswith('Sorry — "book a hotel" isn\'t something I can do here.')
    assert "search flights" in said[0]
    assert tool_calls(actions) == [] and h.store.goals.all() == []


def test_unsupported_part_is_named_in_the_ack():
    provider = ScriptedProvider()
    provider.register("interpret", "Chennai", {**CHENNAI_TOMORROW, "unsupported": "book a hotel",
                                               "ack_phrase": "I'll search flights to Chennai for tomorrow."})
    provider.register("compose", "a0", {"text": "Found a flight.", "claims": []})
    h = harness(provider, config(echo_ack_enabled=True))
    h.send(0, [manifest_event([SEARCH])])
    h.send(100_000, [chunk_event("flights to Chennai tomorrow and a hotel there")])
    actions = send(h, 150_000, [eot_event()]) + drain(h, 3_000_000)
    assert "I'll search flights to Chennai for tomorrow. I can't do \"book a hotel\" here, though." in speaks(actions)


def test_status_question_after_a_finished_task_is_answered_from_what_ran():
    provider = ScriptedProvider()
    provider.register("interpret", "was my search finished", {"act": "unclear", "slot_deltas": [], "status_question": True})
    provider.register("interpret", "Chennai", CHENNAI_TOMORROW)
    provider.register("compose", "a0", {"text": "Found a flight.", "claims": []})
    h = harness(provider, config())
    h.send(0, [manifest_event([SEARCH])])
    h.send(100_000, [chunk_event("flights to Chennai tomorrow")])
    send(h, 150_000, [eot_event()])
    drain(h, 3_000_000)
    h.send(3_100_000, [chunk_event("was my search finished")])
    actions = send(h, 3_150_000, [eot_event()]) + drain(h, 4_000_000, stop_on_final=False)
    assert speaks(actions) == ["For your last request, I ran search flights (Chennai, tomorrow). Nothing else was done."]


def test_thanks_gets_a_polite_reply():
    provider = ScriptedProvider()
    provider.register("interpret", "thank", {"act": "smalltalk", "slot_deltas": []})
    h = harness(provider, config())
    h.send(0, [manifest_event([SEARCH])])
    h.send(100_000, [chunk_event("thank you")])
    actions = send(h, 150_000, [eot_event()]) + drain(h, 1_000_000, stop_on_final=False)
    assert speaks(actions) == ["Happy to help — what would you like me to do?"]


def test_salvage_waits_while_the_user_is_answering_a_question():
    provider = ScriptedProvider()
    provider.register("interpret", "Chennai", {"act": "new_goal", "intent": "search_flights", "slot_deltas": [],
                                               "actions": [{"tool": "search_flights", "args": {"destination": "Chennai"}}]})
    h = harness(provider, config(turn_stall_salvage_ms=2_000))
    h.send(0, [manifest_event([SEARCH])])
    h.send(100_000, [chunk_event("flights to Chennai")])
    actions = send(h, 150_000, [eot_event()]) + drain(h, 6_000_000, stop_on_final=False)
    assert [a for a in actions if a.action_type == ActionType.CLARIFY]
    assert [a for a in actions if a.action_type == ActionType.FINAL] == []


def test_salvage_says_what_it_has_done():
    provider = ScriptedProvider()
    provider.register("interpret", "Chennai", CHENNAI_TOMORROW)
    tools = {"search_flights": {"latency_ms": 20_000, "response": {"flight_id": "FL1"}}}
    h = harness(provider, config(turn_stall_salvage_ms=3_000), tools)
    h.send(0, [manifest_event([SEARCH])])
    h.send(100_000, [chunk_event("flights to Chennai tomorrow")])
    actions = send(h, 150_000, [eot_event()]) + drain(h, 5_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1 and finals[0].body.text == "I couldn't finish this in time. Nothing has been done yet."
    assert finals[0].body.task_completed is False


def test_unclear_turn_after_a_finished_task_gets_its_status():
    """The status_question flag is unreliable on a small model (found live):
    an unclear turn right after a finished task still gets the status."""
    provider = ScriptedProvider()
    provider.register("interpret", "booked or not", {"act": "unclear", "slot_deltas": []})
    provider.register("interpret", "Chennai", CHENNAI_TOMORROW)
    provider.register("compose", "a0", {"text": "Found a flight.", "claims": []})
    h = harness(provider, config())
    h.send(0, [manifest_event([SEARCH])])
    h.send(100_000, [chunk_event("flights to Chennai tomorrow")])
    send(h, 150_000, [eot_event()])
    drain(h, 3_000_000)
    h.send(3_100_000, [chunk_event("booked or not")])
    actions = send(h, 3_150_000, [eot_event()]) + drain(h, 4_000_000, stop_on_final=False)
    assert speaks(actions) == ["For your last request, I ran search flights (Chennai, tomorrow). Nothing else was done. Anything else?"]


def test_invented_act_with_unsupported_is_not_dropped():
    from prism_rt.kernel.proposals import parse_interpretation
    from prism_rt.model.types import InterpretAct

    interp = parse_interpretation({"act": "unsupported", "unsupported": "book a hotel", "actions": []}, turn_id="t", input_digest="")
    assert interp.act == InterpretAct.UNCLEAR and interp.unsupported == "book a hotel"
    try:
        parse_interpretation({"act": "nonsense"}, turn_id="t", input_digest="")
        raise AssertionError("an invented act with nothing to recover must still be rejected")
    except ValueError:
        pass


ORDER = {"name": "track_order", "parameters": {"type": "object", "properties": {
    "order_id": {"type": "string"}}, "required": ["order_id"]}}


def test_new_request_while_a_task_runs_is_appended_not_parked():
    """Found live: "book a flight ... and then track my order" arrived as a
    second NEW_GOAL and silently suspended the first task."""
    provider = ScriptedProvider()
    provider.register("interpret", "parcel A12", {"act": "new_goal", "intent": "track_order", "slot_deltas": [],
                                                   "actions": [{"tool": "track_order", "args": {"order_id": "A12"}}]})
    provider.register("interpret", "Chennai", CHENNAI_TOMORROW)
    provider.register("compose", "a1", {"text": "Done both.", "claims": []})
    tools = {"search_flights": {"latency_ms": 1_500, "response": {"flight_id": "FL1"}},
             "track_order": {"latency_ms": 200, "response": {"status": "shipped"}}}
    h = harness(provider, config(read_only_tools=("search_flights", "track_order")), tools)
    h.send(0, [manifest_event([SEARCH, ORDER])])
    h.send(100_000, [chunk_event("flights to Chennai tomorrow")])
    actions = send(h, 150_000, [eot_event()]) + drain(h, 1_300_000, stop_on_final=False)
    actions += send(h, 1_400_000, [chunk_event("and then check on parcel A12")])
    actions += send(h, 1_450_000, [eot_event()]) + drain(h, 6_000_000)
    assert tool_calls(actions) == [("search_flights", {"destination": "Chennai", "date": "tomorrow"}), ("track_order", {"order_id": "A12"})]
    assert len(h.store.goals.all()) == 1
    assert [a.body.task_completed for a in actions if a.action_type == ActionType.FINAL] == [True]
    assert_clean(h)


def test_correction_after_a_finished_task_reruns_it_with_the_change():
    provider = ScriptedProvider()
    provider.register("interpret", "make it Delhi", {"act": "slot_update", "slot_deltas": [
        {"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}]})
    provider.register("interpret", "Chennai", CHENNAI_TOMORROW)
    provider.register("compose", "a0", {"text": "Found a flight.", "claims": []})
    h = harness(provider, config())
    h.send(0, [manifest_event([SEARCH])])
    h.send(100_000, [chunk_event("flights to Chennai tomorrow")])
    actions = send(h, 150_000, [eot_event()]) + drain(h, 3_000_000)
    assert [a for a in actions if a.action_type == ActionType.FINAL]
    h.send(3_100_000, [chunk_event("no make it Delhi")])
    actions = send(h, 3_150_000, [eot_event()]) + drain(h, 6_000_000)
    assert tool_calls(actions) == [("search_flights", {"destination": "Delhi", "date": "tomorrow"})]
    assert [a for a in actions if a.action_type == ActionType.FINAL]
    assert_clean(h)


def test_zero_watchdog_budget_means_no_session_timer():
    import asyncio

    from prism_rt.entry import setup

    provider = ScriptedProvider()
    runtime = setup(config=Config(watchdog_timeout_ms=0), provider=provider)

    async def run():
        events, out = asyncio.Queue(), asyncio.Queue()
        await events.put({"type": "manifest", "ts_us": 1, "payload": {"tools": [SEARCH]}})
        await events.put(None)
        return await runtime.run_scenario(events, out, meta={"seed": 1})

    assert asyncio.run(run()).watchdog_fired is False


BOOK_DECLARED = {"name": "book_technician", "mutability": "state_changing", "parameters": {"type": "object", "properties": {
    "slot": {"type": "string"}}, "required": ["slot"]}}


def _unconfirmed_booking(cfg):
    provider = ScriptedProvider()
    provider.register("interpret", "yes go ahead", {"act": "confirm", "slot_deltas": []})
    provider.register("interpret", "technician", {"act": "new_goal", "intent": "book_technician", "slot_deltas": [],
                                                  "commit_intent": False,
                                                  "actions": [{"tool": "book_technician", "args": {"slot": "Friday 10am"}}]})
    provider.register("compose", "a0", {"text": "Booked for Friday 10am.", "claims": []})
    h = harness(provider, cfg, {"book_technician": {"latency_ms": 200, "response": {"booking_id": "T1"}}})
    h.send(0, [manifest_event([BOOK_DECLARED])])
    h.send(100_000, [chunk_event("maybe a technician on Friday 10am")])
    actions = send(h, 150_000, [eot_event()]) + drain(h, 2_500_000, stop_on_final=False)
    return h, actions


def test_unconfirmed_write_is_asked_about_then_runs_on_yes():
    h, actions = _unconfirmed_booking(config())
    assert "Shall I go ahead and book technician (Friday 10am)?" in speaks(actions)
    assert tool_calls(actions) == []
    h.send(2_600_000, [chunk_event("yes go ahead")])
    actions = send(h, 2_650_000, [eot_event()]) + drain(h, 6_000_000)
    assert tool_calls(actions) == [("book_technician", {"slot": "Friday 10am"})]
    assert [a.body.task_completed for a in actions if a.action_type == ActionType.FINAL] == [True]
    assert_clean(h)


def test_unconfirmed_write_stays_silent_with_the_flag_off():
    """Control: the pre-fix behaviour -- nothing is said, nothing runs."""
    _, actions = _unconfirmed_booking(config(conversational_replies_enabled=False))
    assert not any("Shall I go ahead" in t for t in speaks(actions)) and tool_calls(actions) == []
