"""Regression: the production driver must never starve the event loop.

Found running `entry.py` against a live model (2026-09-24): after any tool
call completed, the run went silent until the 105 s watchdog fired, ~70 s
past the actual tail of the scenario -- the shared asyncio loop was blocked.

Cause: every TOOL_CALL schedules a `deadline:<call_id>` liveness timer
(`kernel/emission.py`), and nothing ever removed it once the call finished.
After the deadline passed, `TimerWheel.next_due_us()` returned that past
time forever, so `entry.py`'s idle loop fired a liveness step and
`continue`d on every iteration without ever awaiting -- a busy spin that
also blocked worker-result delivery and, more generally, any transport
sharing that event loop (never block the loop).

Fix: `Kernel.step()` retires every timer due at or before its own `now_us`
(a timer's only job is to make the driver step by then, `TimerWheel`'s
docstring), and the driver's liveness branch yields to the loop.
"""

from __future__ import annotations

import asyncio

from conftest import SEARCH_FLIGHTS_TOOL, chunk_event, drain, eot_event, manifest_event

from prism_rt import entry
from prism_rt.config import Config
from prism_rt.model.types import CallStatus, JobKind
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

_PLAN = {
    "steps": [
        {"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}
    ]
}


def _provider() -> ScriptedProvider:
    p = ScriptedProvider()
    p.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    p.register("plan", "search_flights", _PLAN)
    return p  # no compose rule: the goal stays open, so the run idles after the result


async def test_idle_driver_keeps_the_event_loop_responsive_after_a_deadline_passes():
    config = Config(call_deadline_read_ms=100, watchdog_timeout_ms=3000, log_decisions=False)
    runtime = entry.setup(config, provider=_provider())
    events: "asyncio.Queue[dict | None]" = asyncio.Queue()
    actions: "asyncio.Queue[dict]" = asyncio.Queue()
    for raw in (manifest_event([SEARCH_FLIGHTS_TOOL]), chunk_event("Find flights to Pune"), eot_event()):
        await events.put(dict(raw, ts_us=0))

    ticks = 0

    async def drive() -> None:
        nonlocal ticks
        call = None
        while call is None:
            action = await actions.get()
            if action["action_type"] == "tool_call":
                call = action["body"]["call_id"]
        await events.put({"ts_us": 0, "type": "tool_result", "payload": {"call_id": call, "status": "ok", "result": {"flights": []}}})
        # Idle well past the 100 ms deadline; the loop must keep running.
        for _ in range(40):
            await asyncio.sleep(0.01)
            ticks += 1
        await events.put(None)

    summary, _ = await asyncio.wait_for(asyncio.gather(runtime.run_scenario(events, actions), drive()), timeout=10.0)

    assert ticks == 40
    assert summary.watchdog_fired is False
    # Idle polling is ~20 steps/s at most; a spin is thousands.
    assert summary.step_count < 100, summary.step_count


def test_step_retires_timers_that_are_already_due():
    h = SimHarness(
        Config(call_deadline_read_ms=100, log_decisions=False),
        seed=1,
        provider=_provider(),
        tools={"search_flights": {"latency_ms": 20, "response": {"flights": []}}},
        worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000},
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 500_000, stop_on_final=False)

    [call] = h.store.call_ledger.all()
    assert call.status == CallStatus.CONSUMED
    # The deadline (emitted + 100 ms) is long past: no stale timer may remain.
    due = h.store.timers.next_due_us()
    assert due is None or due > h.clock.now_us(), due
