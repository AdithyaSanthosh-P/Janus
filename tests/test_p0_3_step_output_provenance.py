"""P0.3 (C5 core, `docs/original_design_audit.md` D2): step-output
provenance for live chained plans, without needing `output_map`.

Before this fix, a STEP_OUTPUT binding with no matching `output_map`
entry (`kernel/executor.py._bind`'s fallback path) read `result.<call_id>`
directly and put that call-id-specific key in its own read set -- a key
that, by construction, never changes once a call is CONSUMED (`CallStatus.
CONSUMED` is terminal; `InvalidationEngine` never revisits a terminal
call). When a correction made the upstream step re-run under a *new*
call_id (e.g. a search re-run for a corrected destination), the
downstream call's read set never went stale, so it stayed "valid"
forever, bound to a call_id nobody cared about anymore -- a stale chained
result silently consumed, with the FINAL grounded in the wrong data
(D2). The live Planner schema's `output_map` field is a bare
`{"type": "object"}` with no explanation, so a real model essentially
never produces it -- this bug hit *every* live multi-step chained plan,
not an edge case.

Fix: `kernel/reducers.py._write_stepout_fact` now writes a stable,
step-key-scoped `stepout.<gid>.<step_key>` fact for *every* consumed
call (unconditional -- a correctness fix, not a flag-gated innovation),
retract-then-set so D4's same-value no-op rule can't keep stale
provenance around. `kernel/executor.py._bind`'s fallback path now reads
*that* instead of `result.<call_id>`, so a downstream call's read set
actually tracks "the current result for this step" and goes stale
exactly when the upstream step genuinely re-runs -- the same mechanism
`output_map`'s `derived.*` facts already had, now available without
needing `output_map` declared at all. The `output_map`-preferring branch
itself is untouched.
"""

from __future__ import annotations

from conftest import GET_SEAT_MAP_TOOL, SEARCH_FLIGHTS_TOOL, FAST_WORKER_LATENCY, chunk_event, drain, eot_event, interruption_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, FactStatus, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.explorer import Scenario, TimedEvent, baseline_schedule, run_schedule
from prism_rt.sim.harness import SimHarness, MockToolRegistry
from prism_rt.workers.gateway import ScriptedProvider


class ArgMock(MockToolRegistry):
    """Responds with an args-dependent result, so a stale-vs-fresh call is
    distinguishable by its result content, not just its call_id."""

    def on_call(self, call_id, tool_name, args, ts_us):
        super().on_call(call_id, tool_name, args, ts_us)
        due, st, resp, err = self._scheduled[call_id]
        if tool_name == "search_flights":
            resp = {"flight_id": {"Pune": "PNQ-1", "Mumbai": "BOM-2"}[args["destination"]]}
        if tool_name == "get_seat_map":
            resp = {"seats_for": args["flight_id"]}
        self._scheduled[call_id] = (due, st, resp, err)


def _chain_plan(output_map: bool) -> dict:
    s1 = {"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}
    if output_map:
        s1["output_map"] = {"flight_id": "derived.$G.selected_flight"}
    return {
        "steps": [
            s1,
            {"local_id": "s2", "tool": "get_seat_map", "kind": "read", "bindings": {"flight_id": {"type": "step_output", "step_key": "s1", "path": "flight_id"}}, "after": ["s1"]},
        ]
    }


def _chain_provider() -> ScriptedProvider:
    p = ScriptedProvider()
    p.register("interpret", "Find flights to Pune", {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]})
    p.register("interpret", "actually Mumbai", {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]})
    p.register("compose", "seats", {"text": "Seat map ready.", "claims": []})
    return p


def new_harness(config=None, tools=None, provider=None, **kwargs):
    kwargs.setdefault("worker_latency_us", FAST_WORKER_LATENCY)
    return SimHarness(config or Config(), seed=1, tools=tools, provider=provider, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def _run_d2(output_map: bool) -> SimHarness:
    provider = _chain_provider()
    provider.register("plan", "goal_intent: search_flights", _chain_plan(output_map))
    h = new_harness(provider=provider)
    h.mock_tools = ArgMock({"search_flights": {"latency_ms": 100}, "get_seat_map": {"latency_ms": 800}})
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, GET_SEAT_MAP_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 400_000, stop_on_final=False)
    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("actually Mumbai")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])
    drain(h, h.clock.now_us() + 3_000_000)
    return h


# --- D2 repro, exactly (Appendix A.2) -------------------------------------


def test_d2_stale_seat_map_cancelled_without_output_map():
    """The exact D2 regression: no `output_map` declared at all, yet a
    correction to the upstream slot must still cancel the stale
    downstream call and re-run it against the corrected value."""
    h = _run_d2(output_map=False)

    stale = next(c for c in h.store.call_ledger.all() if c.tool == "get_seat_map" and c.args["flight_id"] == "PNQ-1")
    fresh = next(c for c in h.store.call_ledger.all() if c.tool == "get_seat_map" and c.args["flight_id"] == "BOM-2")
    assert stale.status == CallStatus.COMPLETED_AFTER_CANCEL, "the Pune-grounded seat map must be superseded, not silently consumed"
    assert fresh.status == CallStatus.CONSUMED

    finals = [a for a in h.run_log.emitted_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].snapshot.slots["destination"] == "Mumbai"
    assert_clean(h)


def test_d2_output_map_path_still_works_unchanged():
    """Regression: the pre-existing `output_map`-preferring branch in
    `_bind` must behave identically to before -- this package only fixes
    the *fallback* path."""
    h = _run_d2(output_map=True)
    stale = next(c for c in h.store.call_ledger.all() if c.tool == "get_seat_map" and c.args["flight_id"] == "PNQ-1")
    fresh = next(c for c in h.store.call_ledger.all() if c.tool == "get_seat_map" and c.args["flight_id"] == "BOM-2")
    assert stale.status == CallStatus.COMPLETED_AFTER_CANCEL
    assert fresh.status == CallStatus.CONSUMED
    assert_clean(h)


# --- stepout fact: retracted-result treated as missing --------------------


def test_bind_treats_a_retracted_stepout_fact_as_missing_not_stale_data():
    """§ P0.3 step 3: a RETRACTED stepout fact must block the downstream
    binding (ask/wait), never fall through to stale cached data."""
    provider = _chain_provider()
    provider.register("plan", "goal_intent: search_flights", _chain_plan(output_map=False))
    h = new_harness(provider=provider)
    h.mock_tools = ArgMock({"search_flights": {"latency_ms": 100}, "get_seat_map": {"latency_ms": 100}})
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, GET_SEAT_MAP_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 300_000, stop_on_final=False)  # search_flights consumed, stepout.<gid>.s1 written

    gid = h.store.goals.all()[0].goal_id
    key = f"stepout.{gid}.s1"
    assert h.store.facts.get(key) is not None
    assert h.store.facts.get(key).status != FactStatus.RETRACTED

    def retract_it(txn, now_us, step_no):
        txn.facts.retract(key, rule="test.force_retract")

    h.send(h.clock.now_us() + 10_000, inject=retract_it)
    assert h.store.facts.get(key).status == FactStatus.RETRACTED

    # get_seat_map's in-flight call must be invalidated by the retraction
    # (its read set now fails validity), and -- the actual point of this
    # test -- no *new* get_seat_map call may ever get created bound to
    # the now-missing fact: `_bind` must return "missing" (ask/wait), not
    # silently fall through to stale or absent data.
    drain(h, h.clock.now_us() + 2_000_000, stop_on_final=False)
    seat_map_calls = [c for c in h.store.call_ledger.all() if c.tool == "get_seat_map"]
    assert len(seat_map_calls) == 1, "no new get_seat_map call should ever be created bound to a retracted stepout fact"
    assert_clean(h)


# --- cutoff: the underlying digest-stability property still holds --------


def test_stepout_digest_reverts_when_the_upstream_value_does():
    """Early cutoff (`docs/prompt 2.txt` §19, D4): `FactStore.is_valid`
    compares only the digest, never the version, by design -- so if the
    upstream value cycles A -> B -> A, the stepout fact's digest after
    the third step is identical to what it was after the first (same
    value -> same digest via `compute_digest`), the same guarantee
    `derived.*` (output_map) facts already had. Call-level *reuse* of an
    old, already-superseded call (as opposed to a fresh re-execution that
    happens to land on the same result) is a separate capability this
    project doesn't build yet -- explicitly out of P0.3's scope (see
    `docs/original_design_audit.md`'s own P3 "Finish C5" package)."""
    provider = _chain_provider()
    provider.register(
        "interpret",
        "no wait Pune",
        {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register("plan", "goal_intent: search_flights", _chain_plan(output_map=False))
    h = new_harness(provider=provider)
    # get_seat_map deliberately never resolves within this test's window --
    # keeps the goal ACTIVE/EXECUTING throughout so corrections keep
    # landing normally, sidestepping the unrelated, already-documented I-06
    # (correction after FINAL) limitation entirely.
    h.mock_tools = ArgMock({"search_flights": {"latency_ms": 50}, "get_seat_map": {"latency_ms": 900_000_000}})
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, GET_SEAT_MAP_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 300_000, stop_on_final=False)

    gid = h.store.goals.all()[0].goal_id
    key = f"stepout.{gid}.s1"
    digest_after_pune_1 = h.store.facts.get(key).digest

    t = h.clock.now_us()
    h.send(t + 10_000, [interruption_event()])
    h.send(t + 15_000, [chunk_event("actually Mumbai")])
    h.send(t + 20_000, [eot_event()])
    drain(h, h.clock.now_us() + 300_000, stop_on_final=False)
    digest_after_mumbai = h.store.facts.get(key).digest
    assert digest_after_mumbai != digest_after_pune_1

    t = h.clock.now_us()
    h.send(t + 10_000, [interruption_event()])
    h.send(t + 15_000, [chunk_event("no wait Pune")])
    h.send(t + 20_000, [eot_event()])
    drain(h, h.clock.now_us() + 300_000, stop_on_final=False)
    digest_after_pune_2 = h.store.facts.get(key).digest

    assert digest_after_pune_2 == digest_after_pune_1
    assert h.store.goals.all()[0].status.value == "active"  # never hit I-06's territory
    assert_clean(h)


# --- explorer: a real chained plan without output_map ----------------------


def _manifest(tools):
    return {"type": "manifest", "payload": {"tools": tools}}


def _chunk(text):
    return {"type": "text_chunk", "payload": {"text": text}}


def _eot():
    return {"type": "end_of_turn", "payload": {}}


def _interrupt():
    return {"type": "interruption", "payload": {}}


def _chain_scenario_provider() -> ScriptedProvider:
    p = _chain_provider()
    p.register("plan", "goal_intent: search_flights", _chain_plan(output_map=False))
    return p


_D2_WORKERS = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000}

D2_CHAIN_WITHOUT_OUTPUT_MAP = Scenario(
    name="d2_chain_without_output_map",
    config=Config(),
    tools={"search_flights": {"latency_ms": 100}, "get_seat_map": {"latency_ms": 800}},
    provider=_chain_scenario_provider,
    events=(
        TimedEvent(0, _manifest([SEARCH_FLIGHTS_TOOL, GET_SEAT_MAP_TOOL])),
        TimedEvent(100_000, _chunk("Find flights to Pune")),
        TimedEvent(150_000, _eot()),
        TimedEvent(400_000, _interrupt()),
        TimedEvent(410_000, _chunk("actually Mumbai")),
        TimedEvent(420_000, _eot()),
    ),
    worker_latency_us=_D2_WORKERS,
    tail_us=3_000_000,
)


def test_explorer_chain_scenario_clean_without_output_map():
    """The audit's own explicit ask: an explorer scenario for the exact D2
    chain shape, with no `output_map` declared. `ArgMock` (args-dependent
    tool responses) is patched in for just this run -- `run_schedule`
    otherwise always uses the plain, content-independent
    `MockToolRegistry` -- so a stale-vs-fresh grounding is detectable by
    *content*, exercising the existing 'grounding'/'text-grounding'
    oracles in `sim/explorer.py._step_oracles` (not a new oracle; those
    two already had the capability to catch D2, they just never ran
    against this exact scenario shape -- the audit's own note: 'its
    oracles missed D2')."""
    import prism_rt.sim.harness as harness_module
    from unittest import mock

    with mock.patch.object(harness_module, "MockToolRegistry", ArgMock):
        run, _ = run_schedule(D2_CHAIN_WITHOUT_OUTPUT_MAP, baseline_schedule(D2_CHAIN_WITHOUT_OUTPUT_MAP))
    assert run.violations == []
