"""Fault-injection test for TraceChecker's CS-15 (P1, `docs/original_
design_audit.md`): "No write is emitted before settle_ms after its
committing turn."

Previously unimplementable offline: a write CommitGate blocked left no
trace at all -- there was no way to tell "correctly held back" from
"never existed yet". P1 adds `StepReport.gate_rejections` (every G-rule
rejection, not just admissions) precisely to make this checkable; this
test proves the resulting CS-15 check actually has teeth (a synthetic
fault, not just "zero violations on the real suite").
"""

from __future__ import annotations

from types import SimpleNamespace

from prism_rt.kernel.commit import GateRejection
from prism_rt.kernel.emission import EmittedRecord, EmitReport
from prism_rt.model.actions import Action, ToolCallBody
from prism_rt.model.types import EMPTY_READ_SET, ActionType, ToolMutability
from prism_rt.sim.checker import TraceChecker


def _write_call_action(call_id: str, ts_us: int) -> Action:
    return Action(
        action_type=ActionType.TOOL_CALL,
        action_id=call_id,
        ts_us=ts_us,
        body=ToolCallBody(call_id=call_id, tool_name="book_flight", arguments={}, mutability=ToolMutability.STATE_CHANGING),
        read_set=EMPTY_READ_SET,
    )


def _eot_env(ts_us: int) -> SimpleNamespace:
    return SimpleNamespace(payload_type="end_of_turn", ts_us=ts_us)


def _report(step_no: int, now_us: int, *, batch=(), actions=(), gate_rejections=()) -> SimpleNamespace:
    return SimpleNamespace(
        step_no=step_no,
        now_us=now_us,
        batch=batch,
        gate_rejections=gate_rejections,
        invalidation=SimpleNamespace(invalidated_call_ids=()),
        emit_report=EmitReport(emitted=tuple(EmittedRecord(action=a) for a in actions), rejected=()),
    )


def _store(*, settle_barrier_enabled=True, settle_ms=300):
    # A minimal stand-in covering every attribute TraceChecker.check()'s
    # *other* checks touch, so this store can run through the full
    # `check()` pipeline (not just CS-15 in isolation) like every other
    # checker fault-injection test in this suite.
    return SimpleNamespace(
        config=SimpleNamespace(settle_barrier_enabled=settle_barrier_enabled, settle_ms=settle_ms),
        call_ledger=SimpleNamespace(get=lambda call_id: None),
        facts=SimpleNamespace(by_prefix=lambda prefix: {}, get=lambda key: None),
        goals=SimpleNamespace(all=lambda: []),
        effect_ledger=SimpleNamespace(all=lambda: []),
    )


def test_cs15_passes_when_the_write_waits_out_settle_ms():
    reports = [
        _report(1, 0, batch=(_eot_env(0),)),
        _report(2, 300_000, actions=[_write_call_action("c-1", 300_000)]),  # exactly settle_ms later
    ]
    violations = [v for v in TraceChecker().check(reports, store=_store()) if v.invariant == "CS-15"]
    assert violations == []


def test_cs15_catches_a_write_emitted_before_settle_ms_elapsed():
    reports = [
        _report(1, 0, batch=(_eot_env(0),)),
        _report(2, 150_000, actions=[_write_call_action("c-1", 150_000)]),  # only 150ms, needs 300ms
    ]
    violations = [v for v in TraceChecker().check(reports, store=_store()) if v.invariant == "CS-15"]
    assert len(violations) == 1
    assert "150000us" in violations[0].message


def test_cs15_catches_a_call_both_rejected_and_admitted_in_the_same_step():
    reports = [
        _report(1, 0, batch=(_eot_env(0),)),
        _report(
            2,
            400_000,
            actions=[_write_call_action("c-1", 400_000)],
            gate_rejections=(GateRejection(call_id="c-1", tool="book_flight", rule_id="G10", blocked_reason="settle_not_elapsed"),),
        ),
    ]
    violations = [v for v in TraceChecker().check(reports, store=_store()) if v.invariant == "CS-15"]
    assert any("both rejected and admitted" in v.message for v in violations)


def test_cs15_is_a_noop_when_settle_barrier_disabled():
    reports = [
        _report(1, 0, batch=(_eot_env(0),)),
        _report(2, 1_000, actions=[_write_call_action("c-1", 1_000)]),
    ]
    violations = [v for v in TraceChecker().check(reports, store=_store(settle_barrier_enabled=False)) if v.invariant == "CS-15"]
    assert violations == []


def test_cs15_is_a_noop_with_no_store():
    reports = [_report(1, 1_000, actions=[_write_call_action("c-1", 1_000)])]
    violations = [v for v in TraceChecker().check(reports, store=None) if v.invariant == "CS-15"]
    assert violations == []
