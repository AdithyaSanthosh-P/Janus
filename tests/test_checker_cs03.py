"""Fault-injection test for TraceChecker's CS-03 (Phase 7, fifth session):
events within a step's batch must be applied in `Envelope.ordering_key` =
(ts_us, event_class, seq) order.

Same standard CS-27 set: a synthetic fault (an envelope appearing after a
later-ordered one) is injected directly, so the check is proven to have
real teeth, not just an absence of complaints on the existing suite.
"""

from __future__ import annotations

from types import SimpleNamespace

from prism_rt.kernel.emission import EmitReport
from prism_rt.model.events import Envelope
from prism_rt.model.types import EventClass
from prism_rt.sim.checker import TraceChecker


def _env(event_id: str, ts_us: int, event_class: EventClass, seq: int) -> Envelope:
    return Envelope(ts_us=ts_us, event_class=event_class, seq=seq, event_id=event_id, payload_type="text_chunk", payload=None)


def _report(step_no: int, batch: list[Envelope]) -> SimpleNamespace:
    return SimpleNamespace(
        step_no=step_no,
        batch=tuple(batch),
        invalidation=SimpleNamespace(invalidated_call_ids=()),
        emit_report=EmitReport(emitted=(), rejected=()),
    )


def test_cs03_passes_on_correctly_ordered_batch():
    reports = [
        _report(1, [_env("e1", 1000, EventClass.SETUP, 0), _env("e2", 1000, EventClass.USER_CONTENT, 1)]),
        _report(2, [_env("e3", 2000, EventClass.TOOL_RESULT, 0)]),
    ]
    violations = [v for v in TraceChecker().check(reports) if v.invariant == "CS-03"]
    assert violations == []


def test_cs03_catches_an_out_of_order_batch():
    reports = [
        _report(
            1,
            [
                _env("e1", 1000, EventClass.USER_CONTENT, 0),
                _env("e2", 1000, EventClass.SETUP, 1),  # fault: lower class, same ts, but placed after
            ],
        ),
    ]
    violations = [v for v in TraceChecker().check(reports) if v.invariant == "CS-03"]
    assert len(violations) == 1
    assert violations[0].step_no == 1
    assert "e2" in violations[0].message


def test_cs03_catches_a_later_ts_placed_before_an_earlier_one():
    reports = [
        _report(
            1,
            [
                _env("e1", 2000, EventClass.SETUP, 0),
                _env("e2", 1000, EventClass.SETUP, 0),  # fault: earlier ts placed second
            ],
        ),
    ]
    violations = [v for v in TraceChecker().check(reports) if v.invariant == "CS-03"]
    assert len(violations) == 1
    assert violations[0].step_no == 1
    assert "e2" in violations[0].message
