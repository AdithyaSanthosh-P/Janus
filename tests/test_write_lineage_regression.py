"""Regression test for a real correctness bug found while independently
verifying a third round of review (a fresh Sonnet session, continued on
Gemini 3.1 Pro after Sonnet's budget ran out — see `reviews/gemini/` and
`reviews/sonnet/pass2_semantic.py`'s TEST-13).

Neither of that round's own two stated findings held up under direct,
independent reproduction:
- "STALE/RETAINED writes get no reconciliation" (Finding 1): its own
  TEST-13 shows the call landing in `CallStatus.COMPLETED_AFTER_CANCEL`
  (branch 4, which already has reconciliation), not `STALE`/`RETAINED`
  (branches 5/6, which don't) — because `InvalidationEngine` always
  marks an in-flight, registered call `CANCEL_REQUESTED` the instant a
  fact it depends on changes (every call's read set always includes
  `goal.active`), intercepting it before either of those branches could
  ever apply. Confirmed independently with a second, different scenario
  (goal replacement mid-write) before finding TEST-13 already showed the
  same thing.
- "False `EFFECT_DONE` claim via `latest_by_step`" (Finding 2): never
  actually tested by the review. `EffectLedger.by_fingerprint` keys by
  canonical (tool, args) — a corrected write's different arguments give
  it a genuinely different fingerprint, so `_has_confirmed_write` cannot
  cross-contaminate an old call's confirmed effect onto a new one's grade.
  Confirmed directly below (`test_claim_grade_is_not_falsely_effect_done`).

But following the same code path (`CommitGate` G6, `EffectLedger.
by_lineage`) surfaced a real, third bug the review didn't report at all:
a corrected WRITE (different arguments, proposed *before* the original's
real result was known) can get permanently stuck `PROPOSED` once the
*original* — despite being cancelled — goes on to confirm on the same
plan-step lineage. `CommitGate`'s G6 ("no existing effect with the same
lineage, PENDING or CONFIRMED") blocks it forever, since nothing ever
un-confirms a CONFIRMED effect and G6 doesn't distinguish "a literal
retry" (same fingerprint, correctly and permanently blocked) from "a
genuinely different corrected request" (different fingerprint, should
still get a chance). `docs/prompt 2.txt` §8.8's own worked example is
exactly this shape and describes the intended resolution as a "modify"
tool call or an interactive CLARIFY — machinery this project doesn't
build. Fixed with the safe default instead: `PlanExecutor.
_write_lineage_confirmed_elsewhere` detects the permanent block (checked
both at call-creation time and every step an existing PROPOSED call is
re-examined, since the confirming result can arrive either before or
after the corrected call exists) and routes to `_fail_goal` — an honest
failure naming what already happened, never a silent hang, never an
automatic double-booking attempt.
"""

from __future__ import annotations

from conftest import BOOK_FLIGHT_TOOL, chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import CallStatus, ClaimGrade, GoalStatus, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider
from prism_rt.model.actions import FinalBody


def flat_write_plan(tool: str, *, param: str) -> dict:
    return {"steps": [{"local_id": "s1", "tool": tool, "kind": "write", "bindings": {param: {"type": "fact", "key": f"slot.$G.{param}"}}, "after": []}]}


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def _harness(config, *, book_latency_ms: int = 400) -> SimHarness:
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "book flight f1",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "f1"}], "commit_intent": True},
    )
    provider.register(
        "interpret",
        "actually f2",
        {"act": "slot_update", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "f2"}], "commit_intent": True},
    )
    provider.register("plan", "book_flight", flat_write_plan("book_flight", param="flight_id"))
    provider.register("compose", "s1", {"text": "Done.", "claims": ["effect:s1"]})
    return SimHarness(
        config,
        seed=1,
        provider=provider,
        tools={"book_flight": {"latency_ms": book_latency_ms, "response": {"success": True}}},
        worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000},
    )


def _config(**overrides) -> Config:
    overrides.setdefault("transitive_invalidation", True)
    overrides.setdefault("settle_barrier_enabled", False)
    overrides.setdefault("absence_read_sets", False)
    overrides.setdefault("claim_grades_enabled", False)
    overrides.setdefault("rebinder_enabled", True)
    return Config(**overrides)


def test_corrected_write_fails_honestly_instead_of_livelocking_forever():
    """A correction to a WRITE's arguments, landing while the original is
    in flight, gets an immediate CANCEL -- but the original tool call may
    still complete on the provider side (COMPLETED_AFTER_CANCEL, effect
    CONFIRMED). The corrected call must not sit PROPOSED forever once
    that happens; it must fail honestly, naming what went through."""
    config = _config()
    h = _harness(config)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("book flight f1")])
    h.send(150_000, [eot_event()])
    drain(h, 300_000, stop_on_final=False)

    in_flight = [c for c in h.store.call_ledger.all() if c.status == CallStatus.IN_FLIGHT]
    assert len(in_flight) == 1
    original_call = in_flight[0]

    h.send(h.clock.now_us() + 10_000, [chunk_event("actually f2")])
    h.send(h.clock.now_us() + 10_000, [eot_event()])

    # A real livelock would still show nothing after a very long drain.
    actions = drain(h, 3_000_000)
    finals = [a for a in actions if hasattr(a, "body") and isinstance(a.body, FinalBody)]
    assert len(finals) == 1
    assert finals[0].body.task_completed is False
    assert "book_flight" in finals[0].body.text
    assert "already went through" in finals[0].body.text

    gid = h.store.goals.all()[0].goal_id
    assert h.store.goals.get(gid).status == GoalStatus.ABANDONED

    # The original call really did confirm despite the cancel -- this is
    # the race being tested, not a fixture bug.
    assert h.store.call_ledger.get(original_call.call_id).status == CallStatus.COMPLETED_AFTER_CANCEL
    assert_clean(h)


def test_claim_grade_is_not_falsely_effect_done():
    """The reviewed-but-untested Finding 2: even with claim_grades_enabled,
    the FINAL for the failed correction must never claim EFFECT_DONE --
    EffectLedger.by_fingerprint keys by (tool, args), so a different
    correction's fingerprint cannot inherit the original's CONFIRMED
    status."""
    config = _config(claim_grades_enabled=True)
    h = _harness(config)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("book flight f1")])
    h.send(150_000, [eot_event()])
    drain(h, 300_000, stop_on_final=False)

    h.send(h.clock.now_us() + 10_000, [chunk_event("actually f2")])
    h.send(h.clock.now_us() + 10_000, [eot_event()])
    actions = drain(h, 3_000_000)

    finals = [a for a in actions if hasattr(a, "body") and isinstance(a.body, FinalBody)]
    assert len(finals) == 1
    assert finals[0].body.claim_grade != ClaimGrade.EFFECT_DONE


def test_write_lineage_replay_identity():
    def run_once() -> list[dict]:
        config = _config()
        h = _harness(config)
        h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
        h.send(100_000, [chunk_event("book flight f1")])
        h.send(150_000, [eot_event()])
        drain(h, 300_000, stop_on_final=False)
        h.send(h.clock.now_us() + 10_000, [chunk_event("actually f2")])
        h.send(h.clock.now_us() + 10_000, [eot_event()])
        drain(h, 3_000_000)
        return h.log.records

    violations = TraceChecker().check_replay(run_once, times=5)
    assert violations == []
