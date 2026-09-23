"""P1 (verification base, `docs/original_design_audit.md`): the new
STALE-CONSUME and FALSE-CLAIM checks in `sim/checker.py`, proven with the
package's own stated acceptance bar -- "the new oracles fire when P0.2 or
P0.3 is reverted" -- via direct mutation, not just "zero violations on
the suite as it stands today."

`sim/explorer.py._end_oracles` already funnels every `TraceChecker`
violation into the explorer's own oracle set (`out.extend(f"checker:
{v.invariant} {v.message}" for v in TraceChecker().check(...))`), so
adding these two checks to `sim/checker.py` gives both offline and
explorer coverage from one implementation -- no separate explorer-side
oracle code was needed.

FALSE-CLAIM's own live-kernel mutation proof already exists from P0.2:
`tests/test_p0_2_honest_write_failure.py::
test_explorer_oracle_catches_the_false_already_done_claim_if_reintroduced`
reverts `FastResponder._inform_blocked_writes` to its pre-fix behavior and
confirms the (then-P0.2-only) oracle fires; the offline `_check_false_
claim` here is the exact same invariant, evaluated over final store state
instead of live per-step state, so that same scenario is reused below to
prove the offline form too.
"""

from __future__ import annotations

from conftest import BOOK_FLIGHT_TOOL, GET_SEAT_MAP_TOOL, SEARCH_FLIGHTS_TOOL, FAST_WORKER_LATENCY, chunk_event, drain, eot_event, interruption_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import CallStatus, FactStatus, Provenance
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness, MockToolRegistry
from prism_rt.workers.gateway import ScriptedProvider


class ArgMock(MockToolRegistry):
    def on_call(self, call_id, tool_name, args, ts_us):
        super().on_call(call_id, tool_name, args, ts_us)
        due, st, resp, err = self._scheduled[call_id]
        if tool_name == "search_flights":
            resp = {"flight_id": {"Pune": "PNQ-1", "Mumbai": "BOM-2"}[args["destination"]]}
        if tool_name == "get_seat_map":
            resp = {"seats_for": args["flight_id"]}
        self._scheduled[call_id] = (due, st, resp, err)


def new_harness(config=None, tools=None, provider=None, **kwargs):
    kwargs.setdefault("worker_latency_us", FAST_WORKER_LATENCY)
    return SimHarness(config or Config(), seed=1, tools=tools, provider=provider, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def _chain_provider() -> ScriptedProvider:
    p = ScriptedProvider()
    p.register("interpret", "Find flights to Pune", {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]})
    p.register("interpret", "actually Mumbai", {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]})
    p.register(
        "plan",
        "goal_intent: search_flights",
        {
            "steps": [
                {"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
                {"local_id": "s2", "tool": "get_seat_map", "kind": "read", "bindings": {"flight_id": {"type": "step_output", "step_key": "s1", "path": "flight_id"}}, "after": ["s1"]},
            ]
        },
    )
    p.register("compose", "seats", {"text": "Seat map ready.", "claims": []})
    return p


def test_stale_consume_is_clean_on_the_d2_scenario_with_the_fix():
    """Sanity: the exact D2 shape, with P0.3's fix in place, is clean."""
    h = new_harness(provider=_chain_provider())
    h.mock_tools = ArgMock({"search_flights": {"latency_ms": 100}, "get_seat_map": {"latency_ms": 800}})
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, GET_SEAT_MAP_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 400_000, stop_on_final=False)
    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("actually Mumbai")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])
    drain(h, h.clock.now_us() + 3_000_000)
    assert_clean(h)


def test_stale_consume_fires_when_p0_3_is_effectively_reverted():
    """Mutation proof: force the exact store state P0.3-reverted code
    would have produced (D2's own observed symptom, Appendix A.2 --
    `get_seat_map(PNQ-1)` left CONSUMED instead of superseded) via direct
    store mutation, the same technique `tests/test_v1.py`'s S-03 test and
    this project's other oracle-teeth tests use, and confirm STALE-
    CONSUME actually fires on it."""
    from prism_rt.model.types import JobKind

    # COMPOSE deliberately never resolves within this test's window --
    # keeps the goal ACTIVE (never reaches FINAL) so the later correction
    # doesn't run into the unrelated, already-documented I-06 (correction
    # after FINAL) limitation; both tools resolve fast so s1 *and* s2 are
    # genuinely CONSUMED (not merely in flight) before the mutation and
    # correction land.
    h = new_harness(
        provider=_chain_provider(),
        worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000_000},
    )
    h.mock_tools = ArgMock({"search_flights": {"latency_ms": 100}, "get_seat_map": {"latency_ms": 100}})
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, GET_SEAT_MAP_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 400_000, stop_on_final=False)  # both s1 and s2 fully CONSUMED for Pune/PNQ-1

    seat_call = next(c for c in h.store.call_ledger.all() if c.tool == "get_seat_map")
    assert seat_call.status == CallStatus.CONSUMED
    assert seat_call.args["flight_id"] == "PNQ-1"

    # P0.3-reverted's exact mechanism (not just its symptom): the call's
    # own read set never actually tracked "the current result for step
    # s1" (that's the whole bug -- `result.<call_id>` is call-id-pinned
    # and never changes). Strip the `stepout.*` entry P0.3 added so this
    # call's read set matches what the pre-fix binder would have built,
    # then let the correction land normally -- with a read set that can
    # never go invalid, ordinary invalidation has nothing to catch, and
    # this call is never replaced (stays `latest_by_step` forever,
    # exactly D2's shape) even though a *fresh* search now disagrees
    # with it.
    def strip_stepout_dependency(txn, now_us, step_no):
        reduced = txn.store.facts.build_read_set(["goal.active", "catalog.version"])
        txn.call_ledger.update(seat_call.call_id, read_set=reduced)

    h.send(h.clock.now_us() + 5_000, inject=strip_stepout_dependency)

    t = h.clock.now_us()
    h.send(t + 10_000, [interruption_event()])
    h.send(t + 15_000, [chunk_event("actually Mumbai")])
    h.send(t + 20_000, [eot_event()])
    drain(h, h.clock.now_us() + 400_000, stop_on_final=False)  # s1 re-runs for Mumbai/BOM-2; s2 never does

    violations = TraceChecker().check(h.run_log.reports, store=h.store)
    stale = [v for v in violations if v.invariant == "STALE-CONSUME"]
    assert len(stale) == 1, violations
    assert seat_call.call_id in stale[0].message
    assert "PNQ-1" in stale[0].message


def test_false_claim_offline_check_fires_when_p0_2_is_reverted():
    """Mutation proof, offline form: the exact D3 shape -- a non-retryable
    write failure -- with the responder reverted to its pre-P0.2 "always
    say already done" behavior (the same revert `test_p0_2_honest_write_
    failure.py`'s own mutation test uses for the live/explorer form)."""
    from unittest import mock

    import prism_rt.kernel.responder as responder_module
    from config.templates import INFORM_DUPLICATE_WRITE
    from prism_rt.model.actions import IntendedAction, SpeakBody
    from prism_rt.model.types import EMPTY_READ_SET, ActionType, StepKind

    def _always_duplicate(self, store, now_us, step_no, skip_call_ids):
        actions = []
        for call in store.call_ledger.proposed():
            if call.kind != StepKind.WRITE or call.call_id in skip_call_ids:
                continue
            decision = self._commit_gate.evaluate(call, store, now_us)
            if decision.allowed or decision.rule_id not in ("G5", "G6"):
                continue
            already = store.facts.get(f"inform.{call.call_id}.sent")
            if already is not None and already.status != FactStatus.RETRACTED:
                continue
            store.facts.set(
                f"inform.{call.call_id}.sent", True, FactStatus.COMMITTED,
                Provenance(source="system", step_no=step_no, ts_us=now_us), rule="responder.inform_duplicate",
            )
            actions.append(
                IntendedAction(
                    action_type=ActionType.SPEAK,
                    body=SpeakBody(text=INFORM_DUPLICATE_WRITE, kind="inform"),
                    read_set=EMPTY_READ_SET,
                    rule_id="responder.inform_duplicate",
                )
            )
        return actions

    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book flight AI-1",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True},
    )
    provider.register(
        "plan",
        "book_flight",
        {"steps": [{"local_id": "s1", "tool": "book_flight", "kind": "write", "bindings": {"flight_id": {"type": "fact", "key": "slot.$G.flight_id"}}, "after": []}]},
    )
    provider.register("compose", "", {"text": "Booked.", "claims": []})

    h = new_harness(
        tools={"book_flight": {"latency_ms": 50, "error": {"code": "E", "retryable": False}}},
        provider=provider,
    )
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])

    with mock.patch.object(responder_module.FastResponder, "_inform_blocked_writes", _always_duplicate):
        drain(h, 1_000_000, stop_on_final=False)
        # A second, duplicate proposal is what the reverted responder needs
        # to actually speak against -- construct it directly (mirroring
        # test_v1.py's S-03 technique) rather than depending on the
        # reverted executor auto-retrying it.
        fp_call = next(c for c in h.store.call_ledger.all() if c.tool == "book_flight")

        def inject_dup(txn, now_us, step_no):
            from prism_rt.model.types import CallRecord, EffectStatus

            # P0.2's own fix (kernel/results.py) already settles a
            # definite error's effect to FAILED, which correctly does
            # *not* block G5/G6 (BLOCKING_EFFECT_STATUSES excludes
            # FAILED by design) -- that part of P0.2 isn't being
            # reverted here, only the responder's wording choice is.
            # Force the blocking effect back to UNKNOWN (what pre-P0.2
            # code produced for a non-retryable error) so the duplicate
            # actually gets G5-blocked for the reverted responder to
            # (mis)report on.
            txn.store.effect_ledger.set_status(fp_call.fingerprint, EffectStatus.UNKNOWN)
            txn.facts.set("goal.active", fp_call.goal_id, FactStatus.COMMITTED, Provenance(source="test"), rule="test")
            txn.call_ledger.create(
                CallRecord(
                    call_id=txn.store.ids.next("call"),
                    goal_id=fp_call.goal_id,
                    step_key=fp_call.step_key,
                    tool="book_flight",
                    kind=StepKind.WRITE,
                    args=fp_call.args,
                    fingerprint=fp_call.fingerprint,
                    read_set=fp_call.read_set,
                )
            )

        h.send(h.clock.now_us() + 100_000, inject=inject_dup)
        drain(h, h.clock.now_us() + 100_000, stop_on_final=False)

    violations = TraceChecker().check(h.run_log.reports, store=h.store)
    false_claims = [v for v in violations if v.invariant == "FALSE-CLAIM"]
    assert len(false_claims) >= 1, violations
