"""Day 2 WP1 (docs/fdb_v3_day2_plan.md): the FDB-v3 commit profile
(`prism_rt.profiles.fdb_v3_config`) and the incomplete-turn settle
extension (`Config.incomplete_turn_settle_enabled`, `kernel/commit.py`'s
`_turn_looks_incomplete`/`_effective_settle_us`).

Every scenario here uses a tool with *no* declared mutability (matching
`adapters/fdb_manifest.py`'s introspection of FDB's own tools, which
never declares one) so the catalog's safe default (STATE_CHANGING)
applies and every call goes through the full write gate stack (G3-G11),
exactly like a real FDB tool.
"""

from __future__ import annotations

import pytest

from conftest import FAST_WORKER_LATENCY, chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, GoalStatus
from prism_rt.profiles import fdb_v3_config
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

UNDECLARED_SEARCH_FLIGHTS_TOOL = {
    "name": "search_flights",
    "parameters": {
        "type": "object",
        "properties": {"destination": {"type": "string"}, "date": {"type": "string"}},
        "required": ["destination"],
    },
}

UNDECLARED_TRACK_ORDER_TOOL = {
    "name": "track_order",
    "parameters": {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    },
}


def new_harness(config, tools=None, provider=None, **kwargs):
    kwargs.setdefault("worker_latency_us", FAST_WORKER_LATENCY)
    return SimHarness(config, seed=1, tools=tools, provider=provider, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def test_fdb_v3_config_sets_the_expected_flags():
    c = fdb_v3_config()
    assert c.g4_exempt_undeclared_mutability is True
    assert c.settle_barrier_enabled is True
    assert c.incomplete_turn_settle_enabled is True
    assert c.settle_ms_incomplete > c.settle_ms
    assert c.max_read_retries == 0
    assert c.max_write_retries == 0


def test_fdb_v3_config_overrides_pass_through():
    c = fdb_v3_config(settle_ms=42)
    assert c.settle_ms == 42
    assert c.g4_exempt_undeclared_mutability is True  # unrelated fields unaffected


# --- pause-split correction (§5.4: "before commit, a correction simply
# invalidates the proposed call through read-set validation") ------------


def _paris_berlin_provider() -> ScriptedProvider:
    p = ScriptedProvider()
    p.register(
        "interpret", "book a flight to paris",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Paris"}]},
    )
    p.register(
        "interpret", "no, berlin",
        {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Berlin"}]},
    )
    p.register(
        "plan", "search_flights",
        {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]},
    )
    p.register("compose", "", {"text": "Found a flight.", "claims": []})
    return p


def test_pause_split_correction_never_calls_with_the_superseded_value():
    config = fdb_v3_config(settle_ms=300, settle_ms_incomplete=300)  # small, test-friendly
    h = new_harness(config, tools={"search_flights": {"latency_ms": 50, "response": {"flight_id": "FL1"}}}, provider=_paris_berlin_provider())
    h.send(0, [manifest_event([UNDECLARED_SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("book a flight to paris")])
    h.send(150_000, [eot_event()])
    h.send(300_000, [chunk_event("no, berlin")])
    h.send(350_000, [eot_event()])

    actions = drain(h, 3_000_000)
    calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert len(calls) == 1
    assert calls[0].body.arguments["destination"] == "Berlin"
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert_clean(h)


# --- incomplete-turn settle extension -------------------------------------


def _single_step_provider(transcript: str, *, destination: str = "Chicago") -> ScriptedProvider:
    p = ScriptedProvider()
    p.register(
        "interpret", transcript,
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": destination}]},
    )
    p.register(
        "plan", "search_flights",
        {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]},
    )
    p.register("compose", "", {"text": "Found a flight.", "claims": []})
    return p


def _time_to_first_call(config: Config, transcript: str) -> int | None:
    """INTERPRET/PLAN are worker-job results, not TimerWheel entries --
    `fire_liveness_if_due` only fires when a *timer* is due, so it would
    never even check for their results from a cold start. Let ordinary
    events carry the scenario up to the point where the call is PROPOSED
    and blocked on G10 (which *does* schedule a settle timer), then
    switch to liveness-only for the part this test actually cares about:
    proving the settle wait itself is released by TimerWheel liveness
    alone (T-05), at the *correct* (possibly extended) time, with no
    further external events -- the same "harness sends nothing more"
    shape D1 was found under."""
    h = new_harness(config, tools={"search_flights": {"latency_ms": 50, "response": {"flight_id": "FL1"}}}, provider=_single_step_provider(transcript))
    h.send(0, [manifest_event([UNDECLARED_SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event(transcript)])
    eot_ts = 150_000
    h.send(eot_ts, [eot_event()])
    # Small-step ticks, not one big jump: PLAN is only dispatched once
    # INTERPRET's result is *processed*, and its own 10ms latency window
    # starts from that processing time -- a single advance() straight to
    # a far-future timestamp makes both stages resolve "late" in the same
    # step, so PLAN's own dispatch (and 10ms wait) never gets a chance to
    # elapse before the target is reached.
    drain(h, eot_ts + 50_000, step_us=5_000, stop_on_final=False)
    # Liveness-only from here (T-05): captures now_us() at the exact fire
    # that produced the TOOL_CALL, not after the loop runs on (a later,
    # unrelated timer -- e.g. the call deadline -- can fire afterward
    # with nothing emitted, which would otherwise inflate the measured
    # delay well past the settle wait this test is actually checking).
    for _ in range(10):
        report = h.fire_liveness_if_due()
        if report is None:
            return None
        for er in report.emit_report.emitted:
            if er.action.action_type == ActionType.TOOL_CALL:
                return h.clock.now_us() - eot_ts
    return None


def test_incomplete_turn_waits_longer_than_a_complete_one():
    config = fdb_v3_config(settle_ms=300, settle_ms_incomplete=1500)
    complete_delay = _time_to_first_call(config, "book a flight to chicago")
    incomplete_delay = _time_to_first_call(config, "book a flight to chicago and")
    assert complete_delay is not None and incomplete_delay is not None
    assert complete_delay < 1_000_000  # settled at the base settle_ms (300ms)
    assert incomplete_delay >= 1_500_000  # settled only at the extended settle_ms (1500ms)


@pytest.mark.parametrize("transcript", [
    "book a flight to chicago...",  # the transcriber's trailing-off mark
    "book a flight to chicago\u2026",
    "book a flight to chicago and my budget is under",
    "book a flight to chicago for about",
    "book a flight to chicago, somewhere around",
    "book a flight to chicago -",
])
def test_a_trailing_off_turn_waits_longer(transcript):
    """1 Oct audit (R02): a turn ending in an ellipsis, a dash, or a word that
    introduces an amount was settled at the short window -- all 36 transcript
    chunks ending in "..." in the voice runs were followed by more speech."""
    config = fdb_v3_config(settle_ms=300, settle_ms_incomplete=1500)
    delay = _time_to_first_call(config, transcript)
    assert delay is not None and delay >= 1_500_000


@pytest.mark.parametrize("transcript", ["book a flight to chicago.", "book a flight to chicago, thanks!", "is it under 300?"])
def test_a_finished_turn_keeps_the_short_window(transcript):
    config = fdb_v3_config(settle_ms=300, settle_ms_incomplete=1500)
    delay = _time_to_first_call(config, transcript)
    assert delay is not None and delay < 1_000_000


def test_incomplete_turn_flag_off_uses_base_settle_only():
    config = fdb_v3_config(settle_ms=300, settle_ms_incomplete=1500, incomplete_turn_settle_enabled=False)
    delay = _time_to_first_call(config, "book a flight to chicago and")
    assert delay is not None
    assert delay < 1_000_000  # the "and" is ignored -- flag is off


# --- no automatic retries --------------------------------------------------


def test_no_retry_on_a_failed_call_under_the_profile():
    config = fdb_v3_config(settle_ms=50, settle_ms_incomplete=50)
    provider = ScriptedProvider()
    provider.register(
        "interpret", "track my order 5",
        {"act": "new_goal", "intent": "track_order", "slot_deltas": [{"name": "order_id", "scope": "goal", "op": "set", "value": "5"}]},
    )
    provider.register(
        "plan", "track_order",
        {"steps": [{"local_id": "s1", "tool": "track_order", "kind": "read", "bindings": {"order_id": {"type": "fact", "key": "slot.$G.order_id"}}, "after": []}]},
    )
    h = new_harness(config, tools={"track_order": {"latency_ms": 50, "error": {"code": "boom", "message": "boom"}}}, provider=provider)
    h.send(0, [manifest_event([UNDECLARED_TRACK_ORDER_TOOL])])
    h.send(100_000, [chunk_event("track my order 5")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 3_000_000)
    calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert len(calls) == 1  # never retried
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is False
    assert h.store.goals.all()[0].status == GoalStatus.ABANDONED
    assert_clean(h)


# --- same function called twice with different args is allowed -----------


def test_same_function_called_twice_with_different_args_is_allowed():
    config = fdb_v3_config(settle_ms=50, settle_ms_incomplete=50)
    provider = ScriptedProvider()
    provider.register(
        "interpret", "track orders 1 and 2",
        {
            "act": "new_goal", "intent": "track_order",
            "slot_deltas": [
                {"name": "order_id", "scope": "goal", "op": "set", "value": "1"},
                {"name": "order_id_2", "scope": "goal", "op": "set", "value": "2"},
            ],
        },
    )
    provider.register(
        "plan", "track_order",
        {
            "steps": [
                {"local_id": "s1", "tool": "track_order", "kind": "read", "bindings": {"order_id": {"type": "fact", "key": "slot.$G.order_id"}}, "after": []},
                {"local_id": "s2", "tool": "track_order", "kind": "read", "bindings": {"order_id": {"type": "fact", "key": "slot.$G.order_id_2"}}, "after": []},
            ]
        },
    )
    provider.register("compose", "", {"text": "Both orders checked.", "claims": []})
    h = new_harness(config, tools={"track_order": {"latency_ms": 50, "response": {"status": "ok"}}}, provider=provider)
    h.send(0, [manifest_event([UNDECLARED_TRACK_ORDER_TOOL])])
    h.send(100_000, [chunk_event("track orders 1 and 2")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 3_000_000)
    calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert sorted(c.body.arguments["order_id"] for c in calls) == ["1", "2"]
    assert_clean(h)


# --- flag-off parity: the whole existing suite (349 tests) already proves
# this, since none of them opt into fdb_v3_config() or the new flags.
