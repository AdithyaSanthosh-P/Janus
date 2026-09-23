"""C1 inert-tail promotion (`docs/prompt 3.txt` C1, the one unbuilt piece
of the M1 "prepared reactions" layer besides C3).

Phase 6 promoted a speculative interpretation only on an exact prefix
digest match, so a turn ending "... please" threw away an interpretation
that was already done and paid the full model round-trip again after EOT
(L6). `kernel/turns.py._inert_tail` now promotes across a tail made only
of inert words, under the original analysis's own guards: no value, no
change cue, no negation, no pending clarification, not commit-bearing.
Every inert promotion leaves a `spec_interpret.<turn>.promoted_tail` fact
that `TraceChecker._check_cs30` re-verifies independently on every test.
"""

from __future__ import annotations

import pytest
from conftest import BOOK_FLIGHT_TOOL, chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.ids import IdGenerator
from prism_rt.kernel.detector import is_inert_tail
from prism_rt.model.types import ActionType, FactStatus, JobKind, Provenance
from prism_rt.observability import metrics
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.store.session import SessionStore
from prism_rt.workers.gateway import ScriptedProvider

SEARCH_ENUM_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"destination": {"type": "string", "enum": ["Pune", "Mumbai"]}}, "required": ["destination"]},
}


def _plan(tool, param):
    return {"steps": [{"local_id": "s1", "tool": tool, "kind": "read", "bindings": {param: {"type": "fact", "key": f"slot.$G.{param}"}}, "after": []}]}


def _provider():
    p = ScriptedProvider()
    # Order matters: first match wins, and the longer transcript contains
    # the shorter one -- the meaningful-tail case must not reuse Pune.
    p.register("interpret", "transcript: 'Find flights to Pune to Mumbai'", {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]})
    p.register("interpret", "Find flights to Pune", {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]})
    p.register("plan", "search_flights", _plan("search_flights", "destination"))
    p.register("compose", "s1", {"text": "Found flights.", "claims": ["result:s1"]})
    return p


def _config(**overrides):
    base = dict(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
        speculative_interpretation_enabled=True,
    )
    base.update(overrides)
    return Config(**base)


def _run(config, *, tail: str | None, tail_ts=500_000, eot_ts=700_000, provider=None, tools=None):
    """T-06 shape (INTERPRET 300ms): the speculative job over "Find flights
    to Pune" resolves at 400ms; the tail (if any) arrives after that."""
    h = SimHarness(
        config,
        seed=1,
        provider=provider or _provider(),
        tools=tools or {"search_flights": {"latency_ms": 50, "response": {"flight_id": "AI-1"}}},
        worker_latency_us={JobKind.INTERPRET: 300_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000},
    )
    h.send(0, [manifest_event([SEARCH_ENUM_TOOL, BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    t = 100_000
    while t + 10_000 < tail_ts:
        t += 10_000
        h.advance(t)
    if tail is not None:
        h.send(tail_ts, [chunk_event(tail)])
    t = h.clock.now_us()
    while t + 10_000 < eot_ts:
        t += 10_000
        h.advance(t)
    h.send(eot_ts, [eot_event()])
    drain(h, eot_ts + 2_000_000, stop_on_final=False)
    return h


def _ttfs_eot(h) -> int | None:
    return metrics.ttfs(h.run_log.reports).per_trigger["end_of_turn"].stats.p50


def _destination(h):
    finals = [er.action for r in h.run_log.reports for er in r.emit_report.emitted if er.action.action_type == ActionType.FINAL]
    assert len(finals) == 1
    return finals[0].snapshot.slots["destination"]


def _clean(h):
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def _promoted_tail(h):
    f = h.store.facts.get("spec_interpret.t-0001.promoted_tail")
    return f.value if f is not None and f.status != FactStatus.RETRACTED else None


# --- lexical predicate --------------------------------------------------------


@pytest.mark.parametrize("text", ["please", "please.", "um, please", "thank you", "Thanks!"])
def test_inert(text):
    assert is_inert_tail(text)


@pytest.mark.parametrize("text", ["", "to Mumbai", "not", "no please", "actually", "okay", "fine", "please book it"])
def test_not_inert(text):
    assert not is_inert_tail(text)


# --- promotion ------------------------------------------------------------------


def test_inert_tail_is_promoted_with_zero_eot_latency():
    h = _run(_config(), tail="please")
    assert _ttfs_eot(h) == 0
    assert _promoted_tail(h) == "please"
    assert _destination(h) == "Pune"
    _clean(h)


def test_without_c1_the_same_turn_pays_the_full_model_round_trip():
    h = _run(_config(inert_tail_promotion=False), tail="please")
    assert _ttfs_eot(h) > 0
    assert _promoted_tail(h) is None
    assert _destination(h) == "Pune"
    _clean(h)


def test_meaningful_tail_is_never_promoted():
    h = _run(_config(), tail="to Mumbai")
    assert _promoted_tail(h) is None
    assert _destination(h) == "Mumbai"  # interpreted from the full turn, not the stale prefix
    _clean(h)


def test_commit_bearing_interpretation_is_not_promoted_across_a_tail():
    p = ScriptedProvider()
    p.register("interpret", "Find flights to Pune", {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "F1"}], "commit_intent": True})
    p.register("plan", "book_flight", {"steps": [{"local_id": "s1", "tool": "book_flight", "kind": "write", "bindings": {"flight_id": {"type": "fact", "key": "slot.$G.flight_id"}}, "after": []}]})
    p.register("compose", "s1", {"text": "Booked.", "claims": ["effect:s1"]})
    h = _run(_config(), tail="please", provider=p, tools={"book_flight": {"latency_ms": 50, "response": {"ok": True}}})
    assert _promoted_tail(h) is None
    _clean(h)


def test_exact_prefix_promotion_unchanged():
    h = _run(_config(), tail=None)
    assert _ttfs_eot(h) == 0
    assert _promoted_tail(h) is None
    _clean(h)


# --- timing sweep ---------------------------------------------------------------


@pytest.mark.parametrize("tail_ts", [410_000, 450_000, 600_000])
@pytest.mark.parametrize("eot_gap", [10_000, 100_000, 400_000])
def test_inert_tail_timing_sweep(tail_ts, eot_gap):
    """Whenever the speculative job has finished before the inert tail
    arrives (it resolves at 400ms), EOT is answered with zero model
    latency, and the result is always the prefix's own interpretation.
    Never a stale value, never a checker violation."""
    h = _run(_config(), tail="please", tail_ts=tail_ts, eot_ts=tail_ts + eot_gap)
    assert _destination(h) == "Pune"
    assert _ttfs_eot(h) == 0
    _clean(h)


# --- CS-30 has teeth ----------------------------------------------------------


def test_cs30_flags_a_promotion_across_a_non_inert_tail():
    store = SessionStore.new(Config(), IdGenerator(1))
    with store.begin_txn(1) as txn:
        turn = txn.turn_log.open_turn("t-0001", 0, is_interruption=False, during_goal=None)
        txn.turn_log.append_chunk("t-0001", 0, "Find flights to Pune")
        txn.turn_log.append_chunk("t-0001", 10, "to Mumbai")
        txn.facts.set("spec_interpret.t-0001.promoted_tail", "to Mumbai", FactStatus.COMMITTED, Provenance(source="system"), rule="test")
    violations = [v for v in TraceChecker().check([], store=store) if v.invariant == "CS-30"]
    assert len(violations) == 1
    assert "non-inert" in violations[0].message
