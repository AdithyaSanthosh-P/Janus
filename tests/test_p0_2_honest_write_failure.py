"""P0.2 (S-01, `docs/original_design_audit.md` D3): honest non-retryable
write failure.

Before this fix, `kernel/executor.py`'s FAILED-call retry logic ignored
the tool result's own `retryable` flag entirely -- it retried *any* FAILED
call (read or write) up to `max_*_retries`, regardless of what the tool
said. For a write whose error explicitly said `retryable: false` (or said
nothing at all), that meant: a second call with the *identical*
fingerprint got created, was immediately blocked by CommitGate G5 (the
first attempt's effect still occupies that fingerprint), and sat
`PROPOSED` forever -- while `kernel/responder.py` spoke the generic
"That's already been taken care of." for *any* G5/G6 block, never
checking whether the blocking effect was actually `CONFIRMED`. A false
completion claim, plus a livelock, on a completely ordinary scenario (S-01
with `retryable` false/absent is the kit's own worked example, not an edge
case).

Fix: `kernel/results.py` now persists the tool's `retryable` value on the
`CallRecord` itself (True / False / None-for-absent) and always sets the
effect `FAILED` (never `UNKNOWN`) for a *definite* error result -- `UNKNOWN`
is reserved for a genuinely ambiguous outcome (a malformed payload, or a
dropped/timed-out result, P0.4). `kernel/executor.py` now gates a FAILED
call's retry on that flag: writes retry only when `retryable is True`
(absent or `False` -> `_fail_goal` immediately, §5.9's write column);
reads retry unless `retryable is False` (absent still retries, §5.9's
read column -- the two columns have opposite defaults for "absent").
`kernel/responder.py`'s `_inform_blocked_writes` now only says "already
taken care of" when the blocking effect is genuinely `CONFIRMED`; anything
else (`PENDING`/`UNKNOWN`) gets an honest uncertainty message instead.
"""

from __future__ import annotations

from conftest import BOOK_FLIGHT_TOOL, FAST_WORKER_LATENCY, SEARCH_FLIGHTS_TOOL, chunk_event, drain, eot_event, manifest_event, tool_result_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, EffectStatus, GoalStatus, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.explorer import Scenario, TimedEvent, baseline_schedule, run_schedule
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider
import prism_rt.kernel.responder as responder_module
from unittest import mock


def new_harness(config=None, tools=None, provider=None, **kwargs):
    kwargs.setdefault("worker_latency_us", FAST_WORKER_LATENCY)
    return SimHarness(config or Config(), seed=1, tools=tools, provider=provider, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def _book_flight_provider() -> ScriptedProvider:
    p = ScriptedProvider()
    p.register(
        "interpret",
        "Book flight AI-1",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True},
    )
    p.register(
        "plan",
        "book_flight",
        {"steps": [{"local_id": "s1", "tool": "book_flight", "kind": "write", "bindings": {"flight_id": {"type": "fact", "key": "slot.$G.flight_id"}}, "after": []}]},
    )
    p.register("compose", "", {"text": "Booked.", "claims": []})
    return p


# --- D3 repro, exactly (Appendix A.3) -----------------------------------


def test_d3_non_retryable_write_failure_is_honest():
    h = new_harness(
        tools={"book_flight": {"latency_ms": 50, "error": {"code": "E", "retryable": False}}},
        provider=_book_flight_provider(),
    )
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 8_000_000, stop_on_final=False)

    calls = h.store.call_ledger.all()
    assert len(calls) == 1, "no second (stuck) call should ever be created"
    assert calls[0].status == CallStatus.FAILED
    assert calls[0].retryable is False

    fp = calls[0].fingerprint
    effect = h.store.effect_ledger.by_fingerprint(fp)
    assert effect.status == EffectStatus.FAILED

    speaks = [a for a in actions if a.action_type == ActionType.SPEAK]
    assert all("already" not in a.body.text.lower() and "taken care of" not in a.body.text.lower() for a in speaks)

    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is False
    assert "book_flight" in finals[0].body.text

    goal = h.store.goals.all()[0]
    assert goal.status == GoalStatus.ABANDONED
    assert_clean(h)


def test_d3_absent_retryable_write_failure_is_also_honest():
    """`retryable` absent from the error payload entirely -- S-01's other
    no-retry case, distinct from an explicit `false`."""
    h = new_harness(
        tools={"book_flight": {"latency_ms": 50, "error": {"code": "E"}}},
        provider=_book_flight_provider(),
    )
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 8_000_000, stop_on_final=False)

    assert len(h.store.call_ledger.all()) == 1
    assert h.store.call_ledger.all()[0].retryable is None

    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is False
    assert_clean(h)


# --- retryable=True: the one case that DOES retry -----------------------


def test_write_retryable_true_retries_once_then_succeeds():
    """Regression: must-stay-green per the audit's own 'could break' note --
    mirrors `test_s01_write_retryable_error_retries_once` but lives here
    too since it's the load-bearing case this whole package must not
    break."""
    provider = _book_flight_provider()
    # settle_barrier off (matches test_v1.py's own `config` fixture) --
    # this test is about the retry gate, not the settle barrier's own
    # timing, which would otherwise delay the first TOOL_CALL past this
    # test's drain window.
    h = new_harness(Config(settle_barrier_enabled=False), tools={"book_flight": {"latency_ms": 10_000_000}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 300_000, stop_on_final=False)
    calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert len(calls) == 1
    first_call_id = calls[0].body.call_id

    r = h.send(h.clock.now_us() + 50_000, [tool_result_event(first_call_id, status="error", error={"retryable": True})])
    assert h.store.call_ledger.get(first_call_id).retryable is True

    retry_actions = [er.action for er in r.emit_report.emitted] + drain(h, h.clock.now_us() + 500_000, stop_on_final=False)
    retry_calls = [a for a in retry_actions if a.action_type == ActionType.TOOL_CALL]
    assert len(retry_calls) == 1
    second_call_id = retry_calls[0].body.call_id
    assert second_call_id != first_call_id

    h.send(h.clock.now_us() + 50_000, [tool_result_event(second_call_id, status="ok", result={"confirmation": "XYZ"})])
    final_actions = drain(h, h.clock.now_us() + 300_000)
    finals = [a for a in final_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is True
    assert h.store.call_ledger.get(second_call_id).status == CallStatus.CONSUMED
    assert_clean(h)


def test_write_retryable_true_exhausts_and_fails_honestly():
    """The retry itself also fails -- must exhaust cleanly, not livelock or
    falsely claim completion (the write-side twin of the read-side
    retry-exhaustion regression in test_failure_honesty_regression.py)."""
    provider = _book_flight_provider()
    h = new_harness(Config(settle_barrier_enabled=False), tools={"book_flight": {"latency_ms": 10_000_000}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 300_000, stop_on_final=False)
    first_call_id = [a for a in actions if a.action_type == ActionType.TOOL_CALL][0].body.call_id

    r = h.send(h.clock.now_us() + 50_000, [tool_result_event(first_call_id, status="error", error={"retryable": True})])
    retry_actions = [er.action for er in r.emit_report.emitted] + drain(h, h.clock.now_us() + 500_000, stop_on_final=False)
    second_call_id = [a for a in retry_actions if a.action_type == ActionType.TOOL_CALL][0].body.call_id

    r2 = h.send(h.clock.now_us() + 50_000, [tool_result_event(second_call_id, status="error", error={"retryable": True})])
    final_actions = [er.action for er in r2.emit_report.emitted] + drain(h, h.clock.now_us() + 300_000, stop_on_final=False)

    assert h.store.call_ledger.all()[-1].status == CallStatus.FAILED
    assert len(h.store.call_ledger.all()) == 2, "exhausted at max_write_retries=1: no third call"
    finals = [a for a in final_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is False
    assert h.store.goals.all()[0].status == GoalStatus.ABANDONED
    assert_clean(h)


# --- Reads: opposite default for the absent case -------------------------


def test_read_explicit_non_retryable_error_does_not_retry():
    """Asymmetry vs writes: reads retry when `retryable` is absent, but an
    *explicit* `false` still blocks a retry for reads too (§5.9)."""
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register("plan", "search_flights", {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]})
    h = new_harness(
        tools={"search_flights": {"latency_ms": 50, "error": {"code": "E", "retryable": False}}},
        provider=provider,
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 2_000_000, stop_on_final=False)

    calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert len(calls) == 1, "a read must not retry once told explicitly not to"
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is False
    assert_clean(h)


def test_read_absent_retryable_still_retries():
    """Unlike writes: a read with no `retryable` key at all still retries
    (bounded by max_read_retries) -- the existing `fail_first` behavior
    every other scenario in this suite already relies on must be unchanged."""
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register("plan", "search_flights", {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]})
    provider.register("compose", "s1", {"text": "Found it.", "claims": ["result:s1"]})
    h = new_harness(
        tools={"search_flights": {"latency_ms": 50, "response": {"flight_id": "AI-1"}, "fail_first": 1}},
        provider=provider,
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 2_000_000)

    calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert len(calls) == 2, "first attempt fails transiently, must retry once and then succeed"
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is True
    assert_clean(h)


# --- responder: "already done" only when CONFIRMED ------------------------


def test_responder_confirmed_effect_says_already_done():
    provider = _book_flight_provider()
    h = new_harness(tools={"book_flight": {"latency_ms": 100, "response": {"confirmation": "XYZ"}}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])
    drain(h, 2_000_000)
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED

    from prism_rt.model.types import CallRecord, StepKind, FactStatus, Provenance

    fp_call = next(c for c in h.store.call_ledger.all() if c.tool == "book_flight")

    def inject_dup(txn, now_us, step_no):
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

    r = h.send(h.clock.now_us() + 100_000, inject=inject_dup)
    dup_actions = [er.action for er in r.emit_report.emitted] + drain(h, h.clock.now_us() + 100_000, stop_on_final=False)
    informs = [a for a in dup_actions if a.action_type == ActionType.SPEAK and a.body.kind == "inform"]
    assert len(informs) == 1
    assert "already" in informs[0].body.text.lower() or "taken care of" in informs[0].body.text.lower()
    assert_clean(h)


def test_responder_unknown_effect_says_uncertain_not_done():
    """A malformed first result leaves the effect UNKNOWN (never proven
    failed or succeeded); a real second request (new goal, identical
    fingerprint) must never hear the false 'already done' claim."""
    provider = _book_flight_provider()
    # No "response"/"error" configured -> MockToolRegistry delivers
    # status="ok", result=None -- ResultRouter's own malformed branch
    # (§5.8 step 4), independent of this package's retryable fix.
    h = new_harness(tools={"book_flight": {"latency_ms": 50}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 1_000_000, stop_on_final=False)
    assert any(a.action_type == ActionType.FINAL and a.body.task_completed is False for a in actions)

    fp_call = next(c for c in h.store.call_ledger.all() if c.tool == "book_flight")
    assert h.store.effect_ledger.by_fingerprint(fp_call.fingerprint).status == EffectStatus.UNKNOWN

    t = h.clock.now_us()
    h.send(t + 50_000, [chunk_event("Book flight AI-1")])
    h.send(t + 100_000, [eot_event()])
    dup_actions = drain(h, t + 2_000_000, stop_on_final=False)

    assert len(h.store.call_ledger.all()) == 2
    assert h.store.call_ledger.all()[1].fingerprint == fp_call.fingerprint
    assert h.store.call_ledger.all()[1].status == CallStatus.PROPOSED  # blocked by G5, never emitted

    informs = [a for a in dup_actions if a.action_type == ActionType.SPEAK and a.body.kind == "inform"]
    assert len(informs) == 1
    text = informs[0].body.text.lower()
    assert "already" not in text and "taken care of" not in text
    assert_clean(h)


# --- explorer oracle: fault-injection proof, not just "clean today" ------


def _manifest(tools):
    return {"type": "manifest", "payload": {"tools": tools}}


def _chunk(text):
    return {"type": "text_chunk", "payload": {"text": text}}


def _eot():
    return {"type": "end_of_turn", "payload": {}}


_D3_WORKERS = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000}

D3_NON_RETRYABLE_WRITE_FAILURE = Scenario(
    name="d3_non_retryable_write_failure",
    config=Config(),
    tools={"book_flight": {"latency_ms": 50, "error": {"code": "E", "retryable": False}}},
    provider=_book_flight_provider,
    events=(
        TimedEvent(0, _manifest([BOOK_FLIGHT_TOOL])),
        TimedEvent(100_000, _chunk("Book flight AI-1")),
        TimedEvent(150_000, _eot()),
    ),
    worker_latency_us=_D3_WORKERS,
    tail_us=2_000_000,
)

D3_UNKNOWN_EFFECT_DUPLICATE = Scenario(
    name="d3_unknown_effect_duplicate_write",
    config=Config(),
    tools={"book_flight": {"latency_ms": 50}},  # no response/error -> malformed -> effect UNKNOWN
    provider=_book_flight_provider,
    events=(
        TimedEvent(0, _manifest([BOOK_FLIGHT_TOOL])),
        TimedEvent(100_000, _chunk("Book flight AI-1")),
        TimedEvent(150_000, _eot()),
        TimedEvent(1_200_000, _chunk("Book flight AI-1")),
        TimedEvent(1_250_000, _eot()),
    ),
    worker_latency_us=_D3_WORKERS,
    tail_us=3_000_000,
)


def test_explorer_clean_with_the_fix():
    run, _ = run_schedule(D3_NON_RETRYABLE_WRITE_FAILURE, baseline_schedule(D3_NON_RETRYABLE_WRITE_FAILURE))
    assert run.violations == []

    # This scenario's second goal is *genuinely* stuck -- G6 blocks a
    # fingerprint permanently once its effect is UNKNOWN, and this project
    # (like the audit's own scope for P0.2) does not build a reconcile/
    # modify-booking flow to resolve it; the honest INFORM was already
    # sent (proven above), which is the actual P0.2 acceptance bar. The
    # explorer's generic liveness oracle can't distinguish "informed and
    # inherently blocked pending reconciliation" from "silently stuck", so
    # only assert what P0.2 promises: no false completion claim.
    run2, _ = run_schedule(D3_UNKNOWN_EFFECT_DUPLICATE, baseline_schedule(D3_UNKNOWN_EFFECT_DUPLICATE))
    assert not any(v.startswith("claim:") for v in run2.violations), run2.violations


def test_explorer_oracle_catches_the_false_already_done_claim_if_reintroduced():
    """Fault injection (matching this project's established practice, e.g.
    `tests/test_checker_cs27.py`): revert *only* the responder's
    CONFIRMED-vs-not distinction back to the pre-fix "always say already
    done" behavior and confirm the new oracle actually fires on the
    UNKNOWN-effect scenario -- proving it has real teeth, not just "zero
    violations on the code as it stands"."""

    def _always_duplicate(self, store, now_us, step_no, skip_call_ids):
        from config.templates import INFORM_DUPLICATE_WRITE
        from prism_rt.model.actions import IntendedAction, SpeakBody
        from prism_rt.model.types import EMPTY_READ_SET, ActionType, FactStatus, Provenance, StepKind

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

    with mock.patch.object(responder_module.FastResponder, "_inform_blocked_writes", _always_duplicate):
        run, _ = run_schedule(D3_UNKNOWN_EFFECT_DUPLICATE, baseline_schedule(D3_UNKNOWN_EFFECT_DUPLICATE))

    assert any(v.startswith("claim: 'already taken care of'") for v in run.violations), run.violations
