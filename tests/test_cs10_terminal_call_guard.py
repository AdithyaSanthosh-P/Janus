"""CS-10 (`docs/theme05_implementation_blueprint.md` line 2314): "Terminal
call statuses never change." Phase 7's audit of the blueprint's CS table
found the blueprint's own stated enforcement ("`set_call_status` raises")
didn't actually exist anywhere in this codebase -- `CallLedger.set_status`
was a plain `dataclasses.replace` with no transition guard at all. Every
current call site happened to check the terminal case itself first
(verified by reading all ~7), so the invariant held by convention, not by
construction -- `store/ledgers.py.CallLedger.set_status` now enforces it
directly, closing that gap. This isn't a `sim/checker.py` offline rule
(there's no per-step call-status history in `StepReport` to check it
against after the fact -- the same limitation already documented for
CS-17/CS-26), it's a live guard, tested here the same fault-injection way
CS-27 was: construct the exact violation and prove it's rejected, not just
that the existing suite produces zero violations of it.
"""

from __future__ import annotations

import pytest

from prism_rt.errors import InvariantViolation
from prism_rt.model.types import (
    CallRecord,
    CallStatus,
    EMPTY_READ_SET,
    StepKind,
    fingerprint_for,
)
from prism_rt.store.facts import MutationGuard
from prism_rt.store.ledgers import CallLedger


def _make_ledger() -> CallLedger:
    guard = MutationGuard()
    guard.active = True
    return CallLedger(guard)


def _call(call_id: str, status: CallStatus) -> CallRecord:
    return CallRecord(
        call_id=call_id,
        goal_id="g1",
        step_key="s1",
        tool="search_flights",
        kind=StepKind.READ,
        args={"destination": "Pune"},
        fingerprint=fingerprint_for("search_flights", {"destination": "Pune"}),
        read_set=EMPTY_READ_SET,
        status=status,
    )


def test_terminal_call_status_cannot_be_overwritten():
    ledger = _make_ledger()
    ledger.create(_call("c-1", CallStatus.CONSUMED))
    with pytest.raises(InvariantViolation):
        ledger.set_status("c-1", CallStatus.INVALIDATED)
    # The rejected attempt must not have partially applied.
    assert ledger.get("c-1").status == CallStatus.CONSUMED


def test_every_terminal_status_rejects_a_further_transition():
    ledger = _make_ledger()
    terminal = [
        CallStatus.CANCELLED,
        CallStatus.COMPLETED_AFTER_CANCEL,
        CallStatus.CONSUMED,
        CallStatus.STALE,
        CallStatus.RETAINED,
        CallStatus.FAILED,
        CallStatus.DISCARDED,
    ]
    for i, status in enumerate(terminal):
        call_id = f"c-{i}"
        ledger.create(_call(call_id, status))
        with pytest.raises(InvariantViolation):
            ledger.set_status(call_id, CallStatus.IN_FLIGHT)


def test_re_setting_the_same_terminal_status_is_not_a_violation():
    """Not every write to an already-terminal call is a real transition --
    e.g. `_settle_pending_effect`-adjacent code paths may legitimately call
    `set_status` again with the identical status. Only an actual change
    away from it must raise."""
    ledger = _make_ledger()
    ledger.create(_call("c-1", CallStatus.FAILED))
    ledger.set_status("c-1", CallStatus.FAILED)  # no-op, must not raise
    assert ledger.get("c-1").status == CallStatus.FAILED


def test_non_terminal_transitions_are_unaffected():
    ledger = _make_ledger()
    ledger.create(_call("c-1", CallStatus.PROPOSED))
    ledger.set_status("c-1", CallStatus.IN_FLIGHT)
    ledger.set_status("c-1", CallStatus.CANCEL_REQUESTED)
    ledger.set_status("c-1", CallStatus.CONSUMED)
    assert ledger.get("c-1").status == CallStatus.CONSUMED
