"""P0.1 (C9c): production clock and timer liveness.

`docs/original_design_audit.md`'s D1: the real async driver (`entry.py`)
never advanced its clock, and never re-stepped the kernel, between
external events. A write blocked behind the settle barrier (G10,
`kernel/commit.py`) therefore never got re-evaluated once the harness
stopped sending events -- it stalled until the watchdog salvaged it with
`task_completed=False`, on every default-config write (`settle_ms=300`,
`settle_barrier_enabled=True` are both defaults).

Fix: `adapters/clock.py` gains `CoupledClock` (Model A, `docs/prompt 2.txt`
§13.2) -- `now_us()` derives from real elapsed wall time on top of the
last harness timestamp, the only place a wall clock is read outside the
watchdog/telemetry. `entry.py`'s driver now also re-steps the kernel
whenever `StepReport.next_wake_us` (the `TimerWheel` liveness hint) is
pending, instead of silently looping on a bare poll timeout. `sim/harness.
py` gets a matching Model B liveness primitive (`fire_liveness_if_due`,
`tests/conftest.py`'s `drain_liveness_only`) so the exact "harness sends
nothing more" shape can be reproduced deterministically too.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import (
    BOOK_FLIGHT_TOOL,
    FAST_WORKER_LATENCY,
    chunk_event,
    drain,
    drain_liveness_only,
    eot_event,
    interruption_event,
    manifest_event,
)

from prism_rt import entry
from prism_rt.adapters.clock import CoupledClock, SteppedClock
from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.observability import metrics
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider


def _book_flight_provider(*, second_value: str | None = None) -> ScriptedProvider:
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book flight AI-1",
        {
            "act": "new_goal",
            "intent": "book_flight",
            "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}],
            "commit_intent": True,
        },
    )
    if second_value is not None:
        provider.register(
            "interpret",
            f"actually {second_value}",
            {
                "act": "slot_update",
                "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": second_value}],
                "commit_intent": True,
            },
        )
    provider.register(
        "plan",
        "book_flight",
        {"steps": [{"local_id": "s1", "tool": "book_flight", "kind": "write", "bindings": {"flight_id": {"type": "fact", "key": "slot.$G.flight_id"}}, "after": []}]},
    )
    provider.register("compose", "", {"text": "Booked.", "claims": []})
    return provider


def new_harness(config, *, tools=None, provider=None, worker_latency_us=None):
    return SimHarness(config, seed=1, tools=tools, provider=provider, worker_latency_us=worker_latency_us or FAST_WORKER_LATENCY)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


# --- adapters/clock.py: CoupledClock (Model A) unit tests -----------------


def test_clock_models_are_named_correctly():
    assert CoupledClock().model == "A"
    assert SteppedClock().model == "B"


def test_coupled_clock_derives_now_us_from_wall_elapsed_time():
    wall = [100.0]
    clock = CoupledClock(start_us=1_000_000, wall_clock=lambda: wall[0])
    assert clock.now_us() == 1_000_000
    wall[0] += 0.25  # 250ms of real time passes with no explicit advance_to
    assert clock.now_us() == 1_250_000
    wall[0] += 0.05
    assert clock.now_us() == 1_300_000


def test_coupled_clock_advance_to_reanchors_the_wall_offset():
    wall = [0.0]
    clock = CoupledClock(start_us=0, wall_clock=lambda: wall[0])
    wall[0] += 0.5
    assert clock.now_us() == 500_000
    clock.advance_to(2_000_000)  # a real harness event carrying ts_us=2_000_000
    assert clock.now_us() == 2_000_000
    wall[0] += 0.1
    assert clock.now_us() == 2_100_000


def test_coupled_clock_rejects_moving_backward():
    wall = [0.0]
    clock = CoupledClock(start_us=1_000_000, wall_clock=lambda: wall[0])
    with pytest.raises(ValueError):
        clock.advance_to(500_000)


# --- D1: the real async driver (entry.py) ----------------------------------


async def test_d1_async_entry_point_releases_settled_write_via_liveness():
    """Appendix A.1's exact repro shape: manifest + chunk + end_of_turn,
    then the harness sends nothing further. Before this fix, the write
    never went out and the watchdog salvaged it with `task_completed=False`
    (D1). Now it's released once real elapsed wall time (Model A's
    CoupledClock, the new `Config.clock_model` default) satisfies the
    settle window, well inside the watchdog budget."""
    runtime = entry.setup(Config(settle_ms=300, watchdog_timeout_ms=3000, log_decisions=False), provider=_book_flight_provider())
    events: "asyncio.Queue[dict | None]" = asyncio.Queue()
    actions: "asyncio.Queue[dict]" = asyncio.Queue()
    for raw in [
        {"ts_us": 0, "type": "manifest", "payload": {"tools": [BOOK_FLIGHT_TOOL]}},
        {"ts_us": 100_000, "type": "text_chunk", "payload": {"text": "Book flight AI-1"}},
        {"ts_us": 150_000, "type": "end_of_turn", "payload": {}},
    ]:
        await events.put(raw)
    # No further events -- the harness "waits for the agent" (A.1).

    collected: list[dict] = []

    async def collect() -> None:
        while True:
            encoded = await asyncio.wait_for(actions.get(), timeout=4.0)
            collected.append(encoded)
            if encoded["action_type"] == "tool_call":
                await events.put(None)  # end the scenario now that we've seen what we came for
                return

    summary = await asyncio.wait_for(
        asyncio.gather(runtime.run_scenario(events, actions, meta={"seed": 3}), collect()),
        timeout=5.0,
    )
    summary = summary[0]

    tool_calls = [a for a in collected if a["action_type"] == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["body"]["tool_name"] == "book_flight"
    assert summary.watchdog_fired is False


async def test_d1_settle_zero_still_emits_immediately():
    """Regression: settle_ms=0 (settle barrier effectively disabled) must
    keep working exactly as before -- this path never depended on
    liveness."""
    runtime = entry.setup(Config(settle_ms=0, watchdog_timeout_ms=3000, log_decisions=False), provider=_book_flight_provider())
    events: "asyncio.Queue[dict | None]" = asyncio.Queue()
    actions: "asyncio.Queue[dict]" = asyncio.Queue()
    for raw in [
        {"ts_us": 0, "type": "manifest", "payload": {"tools": [BOOK_FLIGHT_TOOL]}},
        {"ts_us": 100_000, "type": "text_chunk", "payload": {"text": "Book flight AI-1"}},
        {"ts_us": 150_000, "type": "end_of_turn", "payload": {}},
    ]:
        await events.put(raw)

    collected: list[dict] = []

    async def collect() -> None:
        while True:
            encoded = await asyncio.wait_for(actions.get(), timeout=2.0)
            collected.append(encoded)
            if encoded["action_type"] == "tool_call":
                await events.put(None)
                return

    summary = await asyncio.wait_for(
        asyncio.gather(runtime.run_scenario(events, actions, meta={"seed": 3}), collect()),
        timeout=3.0,
    )
    summary = summary[0]
    assert any(a["action_type"] == "tool_call" for a in collected)
    assert summary.watchdog_fired is False


# --- T-05: the deterministic simulator, "no idle advance" -----------------


def test_t05_no_idle_advance_liveness_releases_settled_write():
    """T-05 (docs/original_design_audit.md P0.1's own Tests bullet): drive
    the harness up to the point where the write is proposed and blocked on
    G10, then advance *only* via `fire_liveness_if_due` -- never a blind
    `step_us` tick (`drain`'s usual mechanism, which is why the existing
    246-test suite never caught D1 in the first place). `liveness_fires`
    must be >= 1, and the write must proceed."""
    config = Config(settle_ms=300)
    h = new_harness(config, tools={"book_flight": {"latency_ms": 50, "response": {"confirmation": "OK"}}}, provider=_book_flight_provider())
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])
    h.advance(170_000)  # INTERPRET resolves (10ms latency) -> PLANNING
    h.advance(190_000)  # PLAN resolves -> write proposed, blocked on G10 (due 450_000)

    call = next(c for c in h.store.call_ledger.all() if c.tool == "book_flight")
    assert call.status.value == "proposed"

    # From here on, only liveness (TimerWheel / next_wake_us) may move
    # time forward -- exactly the "harness sends nothing more" shape D1
    # was found under.
    actions, fires = drain_liveness_only(h, stop_on_final=False)

    tool_calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert len(tool_calls) == 1
    assert tool_calls[0].body.tool_name == "book_flight"
    assert fires >= 1
    assert metrics.liveness_fire_count(h.run_log.reports) == fires
    assert_clean(h)


def test_t05_no_pending_timer_is_a_true_noop():
    """`fire_liveness_if_due` must not fabricate a step when nothing is
    scheduled -- e.g. before any write has even been proposed."""
    config = Config(settle_ms=300)
    h = new_harness(config, provider=_book_flight_provider())
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    assert h.fire_liveness_if_due() is None


# --- "Could break": a liveness fire while a correction's INTERPRET runs ---


def test_liveness_fire_during_correction_interpret_stays_triage_blocked():
    """docs/original_design_audit.md P0.1's own risk note: 'a liveness fire
    while the correction's INTERPRET is running... is safe only because
    the TRIAGE clause blocks first.' Reproduced directly: the original
    write's settle timer comes due while a slow correction INTERPRET job
    is still RUNNING -- firing liveness at exactly that moment must not
    let the stale (pre-correction) write through, because G3's TRIAGE
    hold (`user_content_pending`, `kernel/commit.py`) is checked before
    G10 regardless of what triggered the step."""
    config = Config(settle_ms=600)
    provider = _book_flight_provider(second_value="AI-2")
    h = new_harness(
        config,
        tools={"book_flight": {"latency_ms": 50, "response": {"confirmation": "OK"}}},
        provider=provider,
        worker_latency_us={JobKind.INTERPRET: 500_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000},
    )
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])
    h.advance(650_000)  # first INTERPRET (500ms) resolves -> PLANNING
    h.advance(670_000)  # PLAN resolves -> write proposed, blocked on G10 (settle_ms=600, due 750_000)

    call = next(c for c in h.store.call_ledger.all() if c.tool == "book_flight")
    assert call.status.value == "proposed"

    # A correction arrives; its INTERPRET job (500ms) is still RUNNING when
    # the original write's settle timer (due 750_000) comes due.
    h.send(680_000, [interruption_event()])
    h.send(690_000, [chunk_event("actually AI-2")])
    h.send(700_000, [eot_event()])

    report = h.fire_liveness_if_due()
    assert report is not None
    assert h.clock.now_us() == 750_000
    tool_calls_this_step = [er.action for er in report.emit_report.emitted if er.action.action_type == ActionType.TOOL_CALL]
    assert tool_calls_this_step == []
    assert h.store.call_ledger.get(call.call_id).status.value != "in_flight"

    # Let the correction resolve normally; the corrected value books, the
    # stale one never does.
    actions = drain(h, h.clock.now_us() + 2_000_000, stop_on_final=False)
    all_tool_calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert any(a.body.arguments.get("flight_id") == "AI-2" for a in all_tool_calls)
    assert all(a.body.arguments.get("flight_id") != "AI-1" for a in all_tool_calls)
    assert_clean(h)


# --- Replay identity --------------------------------------------------------


def test_p0_1_liveness_replay_identity():
    def run_once() -> list[dict]:
        config = Config(settle_ms=300)
        h = new_harness(config, tools={"book_flight": {"latency_ms": 50, "response": {"confirmation": "OK"}}}, provider=_book_flight_provider())
        h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
        h.send(100_000, [chunk_event("Book flight AI-1")])
        h.send(150_000, [eot_event()])
        h.advance(170_000)
        h.advance(190_000)
        drain_liveness_only(h, stop_on_final=False)
        return h.log.records

    violations = TraceChecker().check_replay(run_once, times=5)
    assert violations == []
