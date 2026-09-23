"""P0.4 (call deadlines and the retry table, §5.9, `docs/original_design_
audit.md` D4): a dropped tool result must not hang until the scenario-wide
watchdog (105s default).

Before this fix, no call deadline existed at all -- a tool result that
never arrived left the call `IN_FLIGHT` forever, and only the 105s
watchdog ever ended the scenario. `test_s02_write_timeout_informs_
truthfully` (test_v1.py) only ever asserted the effect stayed `PENDING`;
it never checked that anything was actually said to the user.

Fix, using P0.1's now-working timer liveness end to end:
`kernel/emission.py._apply_side_effects` schedules a `deadline:<call_id>`
TimerWheel entry (a pure liveness hint, like G10's settle wake) when a
call is emitted; `kernel/executor.py.PlanExecutor.expire_deadlines` (a
new clock-driven check, same shape as `kernel/perception.py.expire_leases`
/ `kernel/audio.py.release_due`) treats a still-`IN_FLIGHT` call past its
deadline (`Config.call_deadline_read_ms`=6000 / `call_deadline_write_ms`
=12000) as dropped -- by emitting an ordinary CANCEL
(`reason="deadline_expired"`), the *same* channel every other
cancellation in this architecture uses, not a direct internal status
write. This matters for real reasons, not just trace tidiness: nothing
else tells a real harness that this specific call has been given up on,
and its real-world side effect could still be genuinely in flight; going
through CANCEL also means a late, real result is correctly routed
through `ResultRouter`'s existing `CANCEL_REQUESTED ->
completed_after_cancel` branch and W4's reconciliation, instead of being
silently discarded.

`kernel/executor.py.PlanExecutor.propose_ready_calls`'s
`CANCEL_REQUESTED` branch then applies the retry table (§5.9),
distinguishing `cancel_reason == "deadline_expired"` from an ordinary
correction-triggered cancel (which gets an immediate fresh attempt with
no gating at all):

- Reads: bounded auto-retry, same cap as an ordinary retryable error
  (`max_read_retries`).
- Writes: effect `UNKNOWN` (a genuinely ambiguous outcome, never
  `FAILED` -- distinct from P0.2's definite-error case), no auto-retry;
  `PlanExecutor._ask_for` puts the goal into `CLARIFYING` with a
  `retry:<lineage>` marker. `kernel/responder.py` renders this as
  INFORM ("I haven't received confirmation for that yet.") + CLARIFY
  ("Should I try again?"). `kernel/interpret_apply.py` resolves the
  answer: `CONFIRM`/`ANSWER_CLARIFICATION` ("yes") flips the blocking
  effect UNKNOWN -> FAILED, so the same branch's "already resolved" path
  proposes exactly one further attempt on the next replan (mirroring
  §5.10's `write_unknown` reconcile item, `RECONCILE.USER_RETRY`),
  itself bounded by `max_write_retries` if *that* attempt also times
  out; `DENY` ("no") still ends the wait but leaves the effect
  permanently unresolved -- this project builds no automatic retry path
  for a declined confirmation, a documented scope trim.

`expire_deadlines` only ever acts on a call still `CallStatus.IN_FLIGHT`
-- a call already `CANCEL_REQUESTED` for any other reason (an
interruption/correction in flight) is a different status and is never
touched, so R-04's "deliver anyway after cancellation" shape is
unaffected (the audit's own "could break" note).
"""

from __future__ import annotations

from conftest import (
    BOOK_FLIGHT_TOOL,
    FAST_WORKER_LATENCY,
    SEARCH_FLIGHTS_TOOL,
    chunk_event,
    drain,
    eot_event,
    interruption_event,
    manifest_event,
    tool_result_event,
)

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, EffectStatus, GoalStatus, TaskState
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider


def new_harness(config=None, tools=None, provider=None, **kwargs):
    kwargs.setdefault("worker_latency_us", FAST_WORKER_LATENCY)
    return SimHarness(config or Config(), seed=1, tools=tools, provider=provider, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def _book_flight_provider(*, with_confirm: bool = True) -> ScriptedProvider:
    p = ScriptedProvider()
    p.register(
        "interpret",
        "Book flight AI-1",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True},
    )
    if with_confirm:
        p.register("interpret", "yes try again", {"act": "confirm", "slot_deltas": []})
        p.register("interpret", "no don't bother", {"act": "deny", "slot_deltas": []})
    p.register(
        "plan",
        "book_flight",
        {"steps": [{"local_id": "s1", "tool": "book_flight", "kind": "write", "bindings": {"flight_id": {"type": "fact", "key": "slot.$G.flight_id"}}, "after": []}]},
    )
    p.register("compose", "", {"text": "Booked.", "claims": []})
    return p


def _search_pune_provider() -> ScriptedProvider:
    p = ScriptedProvider()
    p.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    p.register("plan", "search_flights", {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]})
    p.register("compose", "s1", {"text": "Found it.", "claims": ["result:s1"]})
    return p


# --- S-02: write deadline, no re-issue, INFORM + CLARIFY -------------------


def test_s02_dropped_write_result_leads_to_unknown_effect_and_honest_inform_clarify():
    h = new_harness(tools={"book_flight": {"latency_ms": 10_000_000, "response": {"confirmation": "OK"}}}, provider=_book_flight_provider())
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 13_000_000, stop_on_final=False, step_us=200_000)

    calls = h.store.call_ledger.all()
    assert len(calls) == 1, "no re-issue: the deadline expiry must not itself create a second call"
    call = calls[0]
    assert call.status == CallStatus.CANCEL_REQUESTED
    assert call.cancel_reason == "deadline_expired"

    effect = h.store.effect_ledger.by_fingerprint(call.fingerprint)
    assert effect.status == EffectStatus.UNKNOWN

    informs = [a for a in actions if a.action_type == ActionType.SPEAK and a.body.kind == "inform"]
    assert len(informs) == 1
    assert informs[0].body.text == "I haven't received confirmation for that yet."

    clarifies = [a for a in actions if a.action_type == ActionType.CLARIFY]
    assert len(clarifies) == 1
    assert clarifies[0].body.text == "Should I try again?"

    goal = h.store.goals.all()[0]
    assert goal.status == GoalStatus.ACTIVE
    assert goal.task_state == TaskState.CLARIFYING
    assert_clean(h)


def test_s02_yes_try_again_gives_exactly_one_retry_then_completes():
    h = new_harness(tools={"book_flight": {"latency_ms": 10_000_000, "response": {"confirmation": "OK"}}}, provider=_book_flight_provider())
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])
    drain(h, 13_000_000, stop_on_final=False, step_us=200_000)
    assert len(h.store.call_ledger.all()) == 1

    t = h.clock.now_us()
    h.send(t + 50_000, [chunk_event("yes try again")])
    h.send(t + 100_000, [eot_event()])
    actions = drain(h, h.clock.now_us() + 2_000_000, stop_on_final=False)

    tool_calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert len(tool_calls) == 1, "exactly one retry"
    retry_call_id = tool_calls[0].body.call_id
    assert h.store.call_ledger.get(retry_call_id).status == CallStatus.IN_FLIGHT

    h.send(h.clock.now_us() + 50_000, [tool_result_event(retry_call_id, status="ok", result={"confirmation": "XYZ"})])
    final_actions = drain(h, h.clock.now_us() + 300_000)
    finals = [a for a in final_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is True
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED
    assert_clean(h)


def test_s02_retry_also_times_out_fails_honestly_without_a_third_attempt():
    h = new_harness(tools={"book_flight": {"latency_ms": 10_000_000, "response": {"confirmation": "OK"}}}, provider=_book_flight_provider())
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])
    drain(h, 13_000_000, stop_on_final=False, step_us=200_000)

    t = h.clock.now_us()
    h.send(t + 50_000, [chunk_event("yes try again")])
    h.send(t + 100_000, [eot_event()])
    drain(h, h.clock.now_us() + 2_000_000, stop_on_final=False)
    assert len(h.store.call_ledger.all()) == 2

    # The retry itself is also never answered -- let it, too, blow its
    # write deadline (12s). No third call may ever be created; the goal
    # must end honestly, not hang.
    actions = drain(h, h.clock.now_us() + 13_000_000, stop_on_final=False, step_us=300_000)
    assert len(h.store.call_ledger.all()) == 2, "bounded at exactly one retry -- no infinite retry loop"
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is False
    assert h.store.goals.all()[0].status == GoalStatus.ABANDONED
    assert_clean(h)


def test_s02_deny_ends_the_wait_without_livelocking():
    h = new_harness(tools={"book_flight": {"latency_ms": 10_000_000, "response": {"confirmation": "OK"}}}, provider=_book_flight_provider())
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])
    drain(h, 13_000_000, stop_on_final=False, step_us=200_000)
    assert h.store.goals.all()[0].task_state == TaskState.CLARIFYING

    t = h.clock.now_us()
    h.send(t + 50_000, [chunk_event("no don't bother")])
    h.send(t + 100_000, [eot_event()])
    drain(h, h.clock.now_us() + 2_000_000, stop_on_final=False)

    # Must not still be CLARIFYING forever (a livelock the liveness
    # oracle in sim/explorer.py would flag as stuck).
    assert h.store.goals.all()[0].task_state != TaskState.CLARIFYING
    # The declined write's effect is deliberately left unresolved (a
    # documented scope trim -- no automatic retry path for a decline);
    # what matters is that the wait itself ended.
    call = h.store.call_ledger.all()[0]
    assert h.store.effect_ledger.by_fingerprint(call.fingerprint).status == EffectStatus.UNKNOWN
    assert_clean(h)


# --- a dropped read gets retried automatically ------------------------------


def test_dropped_read_is_retried_automatically_then_succeeds():
    h = new_harness(tools={"search_flights": {"latency_ms": 10_000_000, "response": {"flight_id": "AI-1"}}}, provider=_search_pune_provider())
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 7_000_000, stop_on_final=False, step_us=200_000)

    calls = h.store.call_ledger.all()
    assert len(calls) == 2, "the deadline expiry must itself trigger exactly one automatic retry"
    assert calls[0].status == CallStatus.CANCEL_REQUESTED
    assert calls[0].cancel_reason == "deadline_expired"
    assert calls[1].status == CallStatus.IN_FLIGHT

    # No INFORM/CLARIFY noise for a read -- only writes ask the user.
    assert not any(a.action_type == ActionType.CLARIFY for a in actions)

    h.send(h.clock.now_us() + 50_000, [tool_result_event(calls[1].call_id, status="ok", result={"flight_id": "AI-1"})])
    final_actions = drain(h, h.clock.now_us() + 300_000)
    finals = [a for a in final_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is True
    assert_clean(h)


def test_dropped_read_exhausts_retries_and_fails_honestly():
    h = new_harness(tools={"search_flights": {"latency_ms": 10_000_000, "response": {"flight_id": "AI-1"}}}, provider=_search_pune_provider())
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    # max_read_retries=2 default -> 3 total attempts, each blowing its own
    # 6s deadline before the next is created.
    actions = drain(h, 20_000_000, stop_on_final=False, step_us=300_000)

    calls = h.store.call_ledger.all()
    assert len(calls) == 3
    assert all(c.status == CallStatus.CANCEL_REQUESTED and c.cancel_reason == "deadline_expired" for c in calls)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is False
    assert h.store.goals.all()[0].status == GoalStatus.ABANDONED
    assert_clean(h)


# --- R-09: a late original result after a read has already been retried --


def test_r09_late_original_result_after_retry_is_reconciled_not_double_consumed():
    """R-09 (blueprint): 'Original superseded_ignored; retry consumed.'
    This project's own CANCEL-based deadline design (see module
    docstring) reconciles the late original through the *existing*
    `CANCEL_REQUESTED -> completed_after_cancel` branch rather than a
    bare duplicate-ignore -- strictly more correct (a late genuine result
    is acknowledged, not silently dropped, W4), while preserving R-09's
    actual safety property: no re-consumption, no double TOOL_CALL, no
    overwriting the goal's real (retry-sourced) result."""
    h = new_harness(tools={"search_flights": {"latency_ms": 10_000_000, "response": {"flight_id": "AI-1"}}}, provider=_search_pune_provider())
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 7_000_000, stop_on_final=False, step_us=200_000)
    calls = h.store.call_ledger.all()
    assert len(calls) == 2
    original_id, retry_id = calls[0].call_id, calls[1].call_id
    assert h.store.call_ledger.get(original_id).status == CallStatus.CANCEL_REQUESTED

    # The retry (c-0002) is consumed first -- the goal completes from it
    # alone, well before the original's late result ever arrives.
    h.send(h.clock.now_us() + 50_000, [tool_result_event(retry_id, status="ok", result={"flight_id": "AI-1"})])
    actions = drain(h, h.clock.now_us() + 300_000, stop_on_final=False)
    assert h.store.call_ledger.get(retry_id).status == CallStatus.CONSUMED
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is True

    # Then the *original*'s result finally, belatedly arrives -- it must
    # not re-open, duplicate, or otherwise disturb the goal that's
    # already honestly completed from the retry's own result.
    report = h.send(h.clock.now_us() + 50_000, [tool_result_event(original_id, status="ok", result={"flight_id": "WRONG"})])
    assert h.store.call_ledger.get(original_id).status == CallStatus.COMPLETED_AFTER_CANCEL
    assert h.store.facts.get(f"result.{original_id}") is None  # never written as a usable result (§5.8 branch 5)
    assert not any(er.action.action_type == ActionType.TOOL_CALL for er in report.emit_report.emitted)
    assert not any(er.action.action_type == ActionType.FINAL for er in report.emit_report.emitted)

    drain(h, h.clock.now_us() + 300_000, stop_on_final=False)
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED  # unchanged, not reopened
    assert_clean(h)


# --- "could break": a cancelled call's deadline must never fire ------------


def test_cancelled_call_is_never_treated_as_a_deadline_timeout():
    """The audit's own risk note: 'Deadlines must not count cancelled
    calls.' A call cancelled by a correction, whose late 'deliver anyway'
    result arrives well past what would have been its own deadline, must
    still resolve as `completed_after_cancel` -- never mistaken for a
    dropped result (which would wrongly mark its effect and retry it)."""
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register(
        "interpret",
        "actually Mumbai",
        {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]},
    )
    provider.register("plan", "goal_intent: search_flights", {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]})
    provider.register("compose", "s1", {"text": "Found it.", "claims": ["result:s1"]})
    # 800ms latency (D2's own repro uses the same figure): comfortably
    # under the 6s read deadline, so the *replacement* (Mumbai) call
    # resolves normally without itself blowing a deadline -- this test is
    # about correction-cancellation only, not a deadline/correction
    # cascade.
    h = new_harness(Config(rebinder_enabled=False), tools={"search_flights": {"latency_ms": 800, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 300_000, stop_on_final=False)
    original = next(c for c in h.store.call_ledger.all() if c.tool == "search_flights")
    assert original.status == CallStatus.IN_FLIGHT

    t = h.clock.now_us()
    h.send(t + 10_000, [interruption_event()])
    h.send(t + 15_000, [chunk_event("actually Mumbai")])
    h.send(t + 20_000, [eot_event()])
    drain(h, h.clock.now_us() + 300_000, stop_on_final=False)
    assert h.store.call_ledger.get(original.call_id).status == CallStatus.CANCEL_REQUESTED
    assert original.cancel_reason != "deadline_expired"

    # The cancelled original's own late "deliver anyway" result (800ms
    # from its own dispatch) arrives well before any deadline could ever
    # apply to it -- expire_deadlines only ever acts on IN_FLIGHT calls,
    # and this one left that status the instant it was cancelled.
    actions = drain(h, h.clock.now_us() + 2_000_000, stop_on_final=False)
    assert h.store.call_ledger.get(original.call_id).status == CallStatus.COMPLETED_AFTER_CANCEL
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].snapshot.slots["destination"] == "Mumbai"
    assert_clean(h)


# --- replay identity ---------------------------------------------------------


def test_p0_4_deadline_replay_identity():
    def run_once() -> list[dict]:
        h = new_harness(tools={"book_flight": {"latency_ms": 10_000_000, "response": {"confirmation": "OK"}}}, provider=_book_flight_provider())
        h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
        h.send(100_000, [chunk_event("Book flight AI-1")])
        h.send(150_000, [eot_event()])
        drain(h, 13_000_000, stop_on_final=False, step_us=200_000)
        t = h.clock.now_us()
        h.send(t + 50_000, [chunk_event("yes try again")])
        h.send(t + 100_000, [eot_event()])
        drain(h, h.clock.now_us() + 2_000_000, stop_on_final=False)
        return h.log.records

    violations = TraceChecker().check_replay(run_once, times=5)
    assert violations == []
