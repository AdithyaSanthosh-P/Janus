"""V0 acceptance tests: 7 scenarios with injected plans, no LLM workers.

Per docs/sonnet_implementation_plan.md §4 VERSION 0 and §11.2, freezing
v0-skeleton requires: all 7 scenarios pass, TraceChecker reports 0
violations (T1, P1, P3, W1, S3, C1) on all of them, and replay is
byte-identical.
"""

from __future__ import annotations

from pathlib import Path

from conftest import BOOK_FLIGHT_TOOL, SEARCH_FLIGHTS_TOOL, manifest_event, tool_result_event

from prism_rt.kernel.snapshot import SnapshotProjector
from prism_rt.model.types import (
    ActionType,
    CallRecord,
    CallStatus,
    EffectStatus,
    FactStatus,
    Provenance,
    StepKind,
    fingerprint_for,
)
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness

REPO_ROOT = Path(__file__).resolve().parents[1]


def assert_clean(harness: SimHarness) -> None:
    checker = TraceChecker()
    violations = checker.check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def assert_no_wallclock() -> None:
    checker = TraceChecker()
    violations = checker.check_static_no_wallclock(
        [REPO_ROOT / "src" / "prism_rt" / "kernel", REPO_ROOT / "src" / "prism_rt" / "store"]
    )
    assert violations == [], violations


def make_read_call(txn, *, call_id: str, destination: str) -> CallRecord:
    rs = txn.facts.build_read_set(["slot.g1.destination"])
    call = CallRecord(
        call_id=call_id,
        goal_id="g1",
        step_key="s1",
        tool="search_flights",
        kind=StepKind.READ,
        args={"destination": destination},
        fingerprint=fingerprint_for("search_flights", {"destination": destination}),
        read_set=rs,
    )
    txn.call_ledger.create(call)
    return call


# --- Test 1: happy path -----------------------------------------------------


def test_happy_path_read_call_consumed_and_snapshot_correct(config):
    h = SimHarness(config, seed=1, tools={"search_flights": {"latency_ms": 500, "response": {"flights": ["AI-505"]}}})

    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    assert [t.name for t in h.store.catalog.usable_tools()] == ["search_flights"]

    def inject(txn, now_us, step_no):
        txn.facts.set("goal.active", "g1", FactStatus.COMMITTED, Provenance(source="test"), rule="setup")
        txn.facts.set(
            "goal.g1.intent", "search_flights", FactStatus.COMMITTED, Provenance(source="test"), rule="setup"
        )
        txn.facts.set("slot.g1.destination", "Mumbai", FactStatus.COMMITTED, Provenance(source="test"), rule="setup")
        make_read_call(txn, call_id=txn.store.ids.next("call"), destination="Mumbai")

    report = h.send(100_000, inject=inject)
    emitted_types = [er.action.action_type for er in report.emit_report.emitted]
    assert emitted_types == [ActionType.TOOL_CALL]
    call_id = report.emit_report.emitted[0].action.body.call_id
    assert h.store.call_ledger.get(call_id).status == CallStatus.IN_FLIGHT

    h.advance(600_000)  # mock result delivered
    assert h.store.call_ledger.get(call_id).status == CallStatus.CONSUMED
    assert h.store.facts.get(f"result.{call_id}").value == {"flights": ["AI-505"]}

    snapshot = SnapshotProjector().project(h.store)
    assert snapshot.intent == "search_flights"
    assert snapshot.slots == {"destination": "Mumbai"}
    assert snapshot.digest

    assert_clean(h)
    assert_no_wallclock()


# --- Test 2: same-step invalidation + cancellation, new call emitted -------


def test_slot_correction_cancels_in_flight_call_same_step_and_emits_new_call(config):
    h = SimHarness(
        config,
        seed=1,
        tools={"search_flights": {"latency_ms": 500, "response": {"flights": ["AI-505"]}}},
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])

    def inject_original(txn, now_us, step_no):
        txn.facts.set("goal.active", "g1", FactStatus.COMMITTED, Provenance(source="test"), rule="setup")
        txn.facts.set("slot.g1.destination", "Pune", FactStatus.COMMITTED, Provenance(source="test"), rule="setup")
        make_read_call(txn, call_id=txn.store.ids.next("call"), destination="Pune")

    report1 = h.send(100_000, inject=inject_original)
    original_call_id = report1.emit_report.emitted[0].action.body.call_id
    assert h.store.call_ledger.get(original_call_id).status == CallStatus.IN_FLIGHT

    def inject_correction(txn, now_us, step_no):
        txn.facts.set("slot.g1.destination", "Mumbai", FactStatus.COMMITTED, Provenance(source="test"), rule="correction")
        # New call proposed off the corrected value, in the *same* step as
        # the correction — this is what makes cancel + re-issue same-step.
        make_read_call(txn, call_id=txn.store.ids.next("call"), destination="Mumbai")

    report2 = h.send(150_000, inject=inject_correction)
    action_types = [er.action.action_type for er in report2.emit_report.emitted]
    assert action_types == [ActionType.CANCEL, ActionType.TOOL_CALL]

    cancel_action = report2.emit_report.emitted[0].action
    assert cancel_action.body.target_call_id == original_call_id
    assert h.store.call_ledger.get(original_call_id).status == CallStatus.CANCEL_REQUESTED

    new_call_id = report2.emit_report.emitted[1].action.body.call_id
    assert new_call_id != original_call_id
    assert h.store.call_ledger.get(new_call_id).status == CallStatus.IN_FLIGHT
    assert h.store.call_ledger.get(new_call_id).args["destination"] == "Mumbai"

    h.advance(700_000)
    assert h.store.call_ledger.get(new_call_id).status == CallStatus.CONSUMED

    assert_clean(h)
    assert_no_wallclock()


# --- Test 3: CommitGate + EffectLedger --------------------------------------


def test_write_passes_commit_gate_effect_confirmed_no_duplicate(config):
    h = SimHarness(config, seed=1, tools={"book_flight": {"latency_ms": 200, "response": {"confirmation": "XYZ"}}})
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])

    def inject(txn, now_us, step_no):
        txn.facts.set("goal.active", "g1", FactStatus.COMMITTED, Provenance(source="test"), rule="setup")
        txn.facts.set("goal.g1.commit_intent", True, FactStatus.COMMITTED, Provenance(source="test"), rule="setup")
        rs = txn.facts.build_read_set(["goal.g1.commit_intent"])
        call = CallRecord(
            call_id=txn.store.ids.next("call"),
            goal_id="g1",
            step_key="s1",
            tool="book_flight",
            kind=StepKind.WRITE,
            args={"flight_id": "AI-505"},
            fingerprint=fingerprint_for("book_flight", {"flight_id": "AI-505"}),
            read_set=rs,
        )
        txn.call_ledger.create(call)

    report = h.send(100_000, inject=inject)
    assert [er.action.action_type for er in report.emit_report.emitted] == [ActionType.TOOL_CALL]
    call_id = report.emit_report.emitted[0].action.body.call_id
    fp = fingerprint_for("book_flight", {"flight_id": "AI-505"})
    assert h.store.effect_ledger.by_fingerprint(fp).status == EffectStatus.PENDING

    # A second, identical write proposed before the first resolves must be
    # blocked by CommitGate G5/G6 — no second TOOL_CALL for the same fingerprint.
    def inject_duplicate(txn, now_us, step_no):
        rs = txn.facts.build_read_set(["goal.g1.commit_intent"])
        dup = CallRecord(
            call_id=txn.store.ids.next("call"),
            goal_id="g1",
            step_key="s1",
            tool="book_flight",
            kind=StepKind.WRITE,
            args={"flight_id": "AI-505"},
            fingerprint=fp,
            read_set=rs,
        )
        txn.call_ledger.create(dup)

    report_dup = h.send(150_000, inject=inject_duplicate)
    # No second TOOL_CALL for the duplicate fingerprint — CommitGate G5/G6
    # blocked it. V1's FastResponder truthfully informs about the block
    # (it didn't exist in V0, so there was nothing to assert here then).
    emitted_types = [er.action.action_type for er in report_dup.emit_report.emitted]
    assert ActionType.TOOL_CALL not in emitted_types

    h.advance(400_000)
    assert h.store.call_ledger.get(call_id).status == CallStatus.CONSUMED
    assert h.store.effect_ledger.by_fingerprint(fp).status == EffectStatus.CONFIRMED

    assert_clean(h)
    assert_no_wallclock()


# --- Test 4: result after cancel --------------------------------------------


def test_result_for_cancelled_call_is_completed_after_cancel_not_consumed(config):
    h = SimHarness(config, seed=1, tools={"search_flights": {"latency_ms": 1_000_000}})  # never auto-delivers
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])

    def inject(txn, now_us, step_no):
        txn.facts.set("goal.active", "g1", FactStatus.COMMITTED, Provenance(source="test"), rule="setup")
        txn.facts.set("slot.g1.destination", "Pune", FactStatus.COMMITTED, Provenance(source="test"), rule="setup")
        make_read_call(txn, call_id=txn.store.ids.next("call"), destination="Pune")

    report1 = h.send(100_000, inject=inject)
    call_id = report1.emit_report.emitted[0].action.body.call_id

    def inject_correction(txn, now_us, step_no):
        txn.facts.set("slot.g1.destination", "Mumbai", FactStatus.COMMITTED, Provenance(source="test"), rule="correction")

    report2 = h.send(150_000, inject=inject_correction)
    assert report2.emit_report.emitted[0].action.action_type == ActionType.CANCEL
    assert h.store.call_ledger.get(call_id).status == CallStatus.CANCEL_REQUESTED

    # The (stale) tool result arrives anyway.
    report3 = h.send(600_000, [tool_result_event(call_id, result={"flights": ["AI-505"]})])
    assert report3.emit_report.emitted == ()
    assert h.store.call_ledger.get(call_id).status == CallStatus.COMPLETED_AFTER_CANCEL
    assert h.store.facts.get(f"result.{call_id}") is None

    assert_clean(h)
    assert_no_wallclock()


# --- Test 5: unknown call_id -------------------------------------------------


def test_unknown_call_id_is_ignored_without_crash(config):
    h = SimHarness(config, seed=1)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])

    report = h.send(100_000, [tool_result_event("c-9999", result={"ok": True})])
    assert report.emit_report.emitted == ()
    assert h.store.call_ledger.get("c-9999") is None

    assert_clean(h)
    assert_no_wallclock()


# --- Test 6: duplicate result delivery --------------------------------------


def test_duplicate_result_delivery_is_ignored(config):
    h = SimHarness(config, seed=1)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])

    def inject(txn, now_us, step_no):
        txn.facts.set("goal.active", "g1", FactStatus.COMMITTED, Provenance(source="test"), rule="setup")
        txn.facts.set("slot.g1.destination", "Pune", FactStatus.COMMITTED, Provenance(source="test"), rule="setup")
        make_read_call(txn, call_id=txn.store.ids.next("call"), destination="Pune")

    report1 = h.send(100_000, inject=inject)
    call_id = report1.emit_report.emitted[0].action.body.call_id

    h.send(200_000, [tool_result_event(call_id, result={"flights": ["AI-505"]})])
    assert h.store.call_ledger.get(call_id).status == CallStatus.CONSUMED

    # Same result delivered again.
    report_dup = h.send(210_000, [tool_result_event(call_id, result={"flights": ["AI-505"]})])
    assert report_dup.emit_report.emitted == ()
    assert h.store.call_ledger.get(call_id).status == CallStatus.CONSUMED  # unchanged

    assert_clean(h)
    assert_no_wallclock()


# --- Test 7: malformed manifest ---------------------------------------------


def test_malformed_manifest_quarantines_bad_tool_keeps_good_one(config):
    h = SimHarness(config, seed=1)
    bad_tool = {"name": "broken_tool"}  # missing params_schema entirely
    duplicate_tool = {**SEARCH_FLIGHTS_TOOL}  # same name as the good tool, arrives after it

    report = h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, bad_tool, duplicate_tool])])
    assert report.emit_report.emitted == ()

    assert [t.name for t in h.store.catalog.usable_tools()] == ["search_flights"]
    good = h.store.catalog.get("search_flights")
    assert good.status == "USABLE"
    bad = h.store.catalog.get("broken_tool")
    assert bad.status == "QUARANTINED"

    assert_clean(h)
    assert_no_wallclock()


# --- Replay identity (C1) ---------------------------------------------------


def test_replay_identity_across_ten_runs(config):
    def run_once() -> list[dict]:
        h = SimHarness(config, seed=7, tools={"search_flights": {"latency_ms": 300, "response": {"flights": ["AI-1"]}}})
        h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])

        def inject(txn, now_us, step_no):
            txn.facts.set("goal.active", "g1", FactStatus.COMMITTED, Provenance(source="test"), rule="setup")
            txn.facts.set("slot.g1.destination", "Delhi", FactStatus.COMMITTED, Provenance(source="test"), rule="setup")
            make_read_call(txn, call_id=txn.store.ids.next("call"), destination="Delhi")

        h.send(100_000, inject=inject)
        h.advance(500_000)
        return h.log.records

    checker = TraceChecker()
    violations = checker.check_replay(run_once, times=10)
    assert violations == []
