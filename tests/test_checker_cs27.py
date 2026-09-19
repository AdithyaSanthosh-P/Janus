"""Fault-injection test for TraceChecker's CS-27 (Phase 7): snapshot
revisions must be non-decreasing across the emitted action stream.

CS-05/CS-09/CS-14/CS-33 (added in bugfix round 3) shipped with no test that
actually forces a violation -- every check for them was "zero violations
across the real test suite," which proves no false positive, not that the
check would catch a real one. CS-27 is built with a synthetic fault
injected directly (a lower revision emitted after a higher one) so the
check is proven to have real teeth, not just an absence of complaints.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from prism_rt.kernel.emission import EmittedRecord, EmitReport
from prism_rt.model.actions import Action, SpeakBody
from prism_rt.model.types import EMPTY_READ_SET, ActionType, Snapshot
from prism_rt.sim.checker import TraceChecker


def _speak_action(action_id: str, revision: int | None) -> Action:
    snapshot = None if revision is None else Snapshot(intent="book_flight", slots={}, revision=revision, digest="d")
    return Action(
        action_type=ActionType.SPEAK,
        action_id=action_id,
        ts_us=0,
        body=SpeakBody(text="ok", kind="progress"),
        read_set=EMPTY_READ_SET,
        snapshot=snapshot,
    )


def _report(step_no: int, actions: list[Action]) -> SimpleNamespace:
    """A minimal stand-in for `kernel.step.StepReport` -- just enough shape
    for every check `TraceChecker.check()` runs to execute without error
    (most no-op on a store-less, batch-less fake; CS-27 is the one under
    test)."""
    return SimpleNamespace(
        step_no=step_no,
        batch=(),
        invalidation=SimpleNamespace(invalidated_call_ids=()),
        emit_report=EmitReport(emitted=tuple(EmittedRecord(action=a) for a in actions), rejected=()),
    )


def test_cs27_passes_on_non_decreasing_revisions():
    reports = [
        _report(1, [_speak_action("a1", 1)]),
        _report(2, [_speak_action("a2", None)]),  # no snapshot -- ignored, not a violation
        _report(3, [_speak_action("a3", 3)]),
        _report(4, [_speak_action("a4", 3)]),  # equal -- still non-decreasing, no violation
    ]
    violations = [v for v in TraceChecker().check(reports) if v.invariant == "CS-27"]
    assert violations == []


def test_cs27_catches_a_regressed_snapshot_revision():
    reports = [
        _report(1, [_speak_action("a1", 5)]),
        _report(2, [_speak_action("a2", 2)]),  # fault: goes backwards
    ]
    violations = [v for v in TraceChecker().check(reports) if v.invariant == "CS-27"]
    assert len(violations) == 1
    assert violations[0].step_no == 2
    assert "a2" in violations[0].message
