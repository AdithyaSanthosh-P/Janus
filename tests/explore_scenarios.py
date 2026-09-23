"""Scenarios for `sim/explorer.py` (M4 / C17).

Each one is a shape already proven by a hand-written acceptance test
elsewhere in this suite, re-expressed as a timed script so the explorer
can perturb it. The race each exercises is named in its docstring.
"""

from __future__ import annotations

from conftest import BOOK_FLIGHT_TOOL, GET_SEAT_MAP_TOOL, SEARCH_FLIGHTS_TOOL

from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.sim.explorer import Scenario, TimedEvent
from prism_rt.workers.gateway import ScriptedProvider

WORKERS = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000}


def _manifest(tools):
    return {"type": "manifest", "payload": {"tools": tools}}


def _chunk(text):
    return {"type": "text_chunk", "payload": {"text": text}}


def _eot():
    return {"type": "end_of_turn", "payload": {}}


def _interrupt():
    return {"type": "interruption", "payload": {}}


def _read_plan(tool="search_flights", param="destination"):
    return {"steps": [{"local_id": "s1", "tool": tool, "kind": "read", "bindings": {param: {"type": "fact", "key": f"slot.$G.{param}"}}, "after": []}]}


def _write_plan():
    return {"steps": [{"local_id": "s1", "tool": "book_flight", "kind": "write", "bindings": {"flight_id": {"type": "fact", "key": "slot.$G.flight_id"}}, "after": []}]}


def _finals_after(h, ts_us):
    return [
        er.action
        for r in h.run_log.reports
        for er in r.emit_report.emitted
        if er.action.action_type == ActionType.FINAL and er.action.ts_us >= ts_us
    ]


# --- 1. correction vs. in-flight read result (I-03, L1) ----------------------


def _search_correction_provider():
    p = ScriptedProvider()
    p.register("interpret", "transcript: 'Find flights to Pune'", {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]})
    p.register("interpret", "transcript: 'actually Mumbai'", {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]})
    p.register("plan", "goal_intent: search_flights", _read_plan())
    p.register("compose", "s1", {"text": "Flights found.", "claims": ["result:s1"]})
    return p


def _no_stale_final_after_correction(h, run):
    """Once the correction has started (interruption), no successful FINAL
    may still describe Pune."""
    interruption_ts = run.schedule.event_ts[3]
    return [
        f"stale-final: FINAL at {a.ts_us} still says destination={a.snapshot.slots.get('destination')!r} after the correction began at {interruption_ts}"
        for a in _finals_after(h, interruption_ts)
        if a.body.task_completed and a.snapshot and a.snapshot.slots.get("destination") == "Pune"
    ]


SEARCH_CORRECTION = Scenario(
    name="search_correction",
    config=Config(),
    tools={"search_flights": {"latency_ms": 400, "response": {"flight_id": "AI-1"}}},
    provider=_search_correction_provider,
    events=(
        TimedEvent(0, _manifest([SEARCH_FLIGHTS_TOOL])),
        TimedEvent(100_000, _chunk("Find flights to Pune")),
        TimedEvent(150_000, _eot()),
        TimedEvent(400_000, _interrupt()),
        TimedEvent(410_000, _chunk("actually Mumbai")),
        TimedEvent(420_000, _eot()),
    ),
    worker_latency_us=WORKERS,
    check=_no_stale_final_after_correction,
)


# --- 2. write vs. correction (I-14, L5) -------------------------------------


def _write_correction_provider():
    p = ScriptedProvider()
    p.register("interpret", "transcript: 'Book the 6pm flight'", {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "F6"}], "commit_intent": True})
    p.register("interpret", "transcript: 'no the 8pm one'", {"act": "slot_update", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "F8"}]})
    p.register("plan", "goal_intent: book_flight", _write_plan())
    p.register("compose", "s1", {"text": "Booked.", "claims": ["effect:s1"]})
    return p


def _no_superseded_write(h, run):
    """A write emitted after the user began correcting it is exactly the
    double-booking race L5 names: the correction was already underway."""
    interruption_ts = run.schedule.event_ts[3]
    out = []
    for r in h.run_log.reports:
        for er in r.emit_report.emitted:
            a = er.action
            if a.action_type == ActionType.TOOL_CALL and a.body.tool_name == "book_flight" and a.body.arguments.get("flight_id") == "F6" and a.ts_us >= interruption_ts:
                out.append(f"superseded-write: book_flight(F6) emitted at {a.ts_us}, after the correction began at {interruption_ts}")
    return out


WRITE_CORRECTION = Scenario(
    name="write_correction",
    config=Config(),
    tools={"book_flight": {"latency_ms": 300, "response": {"confirmation": "OK"}}},
    provider=_write_correction_provider,
    events=(
        TimedEvent(0, _manifest([BOOK_FLIGHT_TOOL])),
        TimedEvent(100_000, _chunk("Book the 6pm flight")),
        TimedEvent(150_000, _eot()),
        TimedEvent(400_000, _interrupt()),
        TimedEvent(410_000, _chunk("no the 8pm one")),
        TimedEvent(420_000, _eot()),
    ),
    worker_latency_us=WORKERS,
    check=_no_superseded_write,
)


# --- 3. cancel vs. write result (I-15, W4) ----------------------------------


def _write_cancel_provider():
    p = ScriptedProvider()
    p.register("interpret", "transcript: 'Book the 6pm flight'", {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "F6"}], "commit_intent": True})
    p.register("interpret", "transcript: 'wait cancel that'", {"act": "abort", "slot_deltas": []})
    p.register("plan", "goal_intent: book_flight", _write_plan())
    p.register("compose", "s1", {"text": "Booked.", "claims": ["effect:s1"]})
    return p


WRITE_CANCEL = Scenario(
    name="write_cancel",
    config=Config(),
    tools={"book_flight": {"latency_ms": 800, "response": {"confirmation": "OK"}}},
    provider=_write_cancel_provider,
    events=(
        TimedEvent(0, _manifest([BOOK_FLIGHT_TOOL])),
        TimedEvent(100_000, _chunk("Book the 6pm flight")),
        TimedEvent(150_000, _eot()),
        TimedEvent(600_000, _interrupt()),
        TimedEvent(610_000, _chunk("wait cancel that")),
        TimedEvent(620_000, _eot()),
    ),
    worker_latency_us=WORKERS,
)


# --- 4. correction through a chained plan (I-04, L1 transitive) --------------


def _chain_provider():
    p = ScriptedProvider()
    p.register("interpret", "transcript: 'Find flights to Pune'", {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]})
    p.register("interpret", "transcript: 'actually Mumbai'", {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]})
    p.register(
        "plan",
        "goal_intent: search_flights",
        {
            "steps": [
                {"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": [], "output_map": {"flight_id": "derived.$G.selected_flight"}},
                {"local_id": "s2", "tool": "get_seat_map", "kind": "read", "bindings": {"flight_id": {"type": "step_output", "step_key": "s1", "path": "flight_id"}}, "after": ["s1"]},
            ]
        },
    )
    p.register("compose", "s", {"text": "Seat map ready.", "claims": ["result:s2"]})
    return p


CHAIN_CORRECTION = Scenario(
    name="chain_correction",
    config=Config(),
    tools={
        "search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-505"}},
        "get_seat_map": {"latency_ms": 800, "response": {"seats": ["1A"]}},
    },
    provider=_chain_provider,
    events=(
        TimedEvent(0, _manifest([SEARCH_FLIGHTS_TOOL, GET_SEAT_MAP_TOOL])),
        TimedEvent(100_000, _chunk("Find flights to Pune")),
        TimedEvent(150_000, _eot()),
        TimedEvent(500_000, _interrupt()),
        TimedEvent(510_000, _chunk("actually Mumbai")),
        TimedEvent(520_000, _eot()),
    ),
    worker_latency_us=WORKERS,
    check=_no_stale_final_after_correction,
)


# --- 5. slow tool while the user starts talking again (CS-28) ---------------


def _overlap_provider():
    p = _search_correction_provider()
    p.register("interpret", "transcript: 'what time is it'", {"act": "addition", "slot_deltas": []})
    return p


SLOW_TOOL_OVERLAP = Scenario(
    name="slow_tool_overlap",
    config=Config(),
    tools={"search_flights": {"latency_ms": 800, "response": {"flight_id": "AI-1"}}},
    provider=_overlap_provider,
    events=(
        TimedEvent(0, _manifest([SEARCH_FLIGHTS_TOOL])),
        TimedEvent(100_000, _chunk("Find flights to Pune")),
        TimedEvent(150_000, _eot()),
        TimedEvent(300_000, _chunk("what time is it")),
        TimedEvent(1_100_000, _eot()),
    ),
    worker_latency_us=WORKERS,
)


# --- 6. backchannel during work (I-11): must NOT hold anything --------------


def _backchannel_provider():
    # When the goal has already finished (fast tool), "mm-hmm" is a new
    # utterance and legitimately goes to the Interpreter -- a live model
    # would call it a backchannel, so script that.
    p = _search_correction_provider()
    p.register("interpret", "transcript: 'mm-hmm'", {"act": "backchannel", "slot_deltas": []})
    return p


def _backchannel_still_completes(h, run):
    finals = _finals_after(h, 0)
    return [] if finals else ["backchannel: a pure backchannel suppressed the goal's FINAL entirely"]


BACKCHANNEL = Scenario(
    name="backchannel",
    config=Config(),
    tools={"search_flights": {"latency_ms": 400, "response": {"flight_id": "AI-1"}}},
    provider=_backchannel_provider,
    events=(
        TimedEvent(0, _manifest([SEARCH_FLIGHTS_TOOL])),
        TimedEvent(100_000, _chunk("Find flights to Pune")),
        TimedEvent(150_000, _eot()),
        TimedEvent(300_000, _interrupt()),
        TimedEvent(305_000, _chunk("mm-hmm")),
        TimedEvent(310_000, _eot()),
    ),
    worker_latency_us=WORKERS,
    check=_backchannel_still_completes,
)


# --- 7. retry vs. late success -------------------------------------------------


RETRY_CORRECTION = Scenario(
    name="retry_correction",
    config=Config(),
    tools={"search_flights": {"latency_ms": 200, "response": {"flight_id": "AI-1"}, "fail_first": 1}},
    provider=_search_correction_provider,
    events=SEARCH_CORRECTION.events,
    worker_latency_us=WORKERS,
    check=_no_stale_final_after_correction,
)


# --- 8. manifest update mid-plan (S-08) -----------------------------------------


MANIFEST_UPDATE = Scenario(
    name="manifest_update",
    config=Config(),
    tools={"search_flights": {"latency_ms": 400, "response": {"flight_id": "AI-1"}}},
    provider=_search_correction_provider,
    events=(
        TimedEvent(0, _manifest([SEARCH_FLIGHTS_TOOL])),
        TimedEvent(100_000, _chunk("Find flights to Pune")),
        TimedEvent(150_000, _eot()),
        TimedEvent(300_000, _manifest([SEARCH_FLIGHTS_TOOL, GET_SEAT_MAP_TOOL])),
    ),
    worker_latency_us=WORKERS,
)


# --- 9. speculative interpretation + correction (Phase 6) ----------------------


SPECULATIVE_CORRECTION = Scenario(
    name="speculative_correction",
    config=Config(speculative_interpretation_enabled=True),
    tools={"search_flights": {"latency_ms": 400, "response": {"flight_id": "AI-1"}}},
    provider=_search_correction_provider,
    events=SEARCH_CORRECTION.events,
    worker_latency_us=WORKERS,
    check=_no_stale_final_after_correction,
)


# --- 10. C1 inert-tail promotion + a later correction --------------------------


def _inert_tail_provider():
    # When timing prevents promotion, the whole turn is interpreted -- a
    # live model reads "... please" the same way, so script that too.
    p = _search_correction_provider()
    p.register("interpret", "transcript: 'Find flights to Pune please'", {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]})
    return p


SPECULATIVE_INERT_TAIL = Scenario(
    name="speculative_inert_tail",
    config=Config(speculative_interpretation_enabled=True),
    tools={"search_flights": {"latency_ms": 400, "response": {"flight_id": "AI-1"}}},
    provider=_inert_tail_provider,
    events=(
        TimedEvent(0, _manifest([SEARCH_FLIGHTS_TOOL])),
        TimedEvent(100_000, _chunk("Find flights to Pune")),
        TimedEvent(140_000, _chunk("please")),
        TimedEvent(150_000, _eot()),
        TimedEvent(400_000, _interrupt()),
        TimedEvent(410_000, _chunk("actually Mumbai")),
        TimedEvent(420_000, _eot()),
    ),
    worker_latency_us=WORKERS,
    check=lambda h, run: [
        f"stale-final: FINAL at {a.ts_us} says Pune after the correction began at {run.schedule.event_ts[4]}"
        for a in _finals_after(h, run.schedule.event_ts[4])
        if a.body.task_completed and a.snapshot and a.snapshot.slots.get("destination") == "Pune"
    ],
)


ALL_SCENARIOS = (
    SEARCH_CORRECTION,
    WRITE_CORRECTION,
    WRITE_CANCEL,
    CHAIN_CORRECTION,
    SLOW_TOOL_OVERLAP,
    BACKCHANNEL,
    RETRY_CORRECTION,
    MANIFEST_UPDATE,
    SPECULATIVE_CORRECTION,
    SPECULATIVE_INERT_TAIL,
)
