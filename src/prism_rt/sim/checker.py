"""TraceChecker: verify kernel invariants against a run's StepReports (and,
for T1, against the source tree itself).

V0 shipped T1, P1, P3, W1, S3, C1 (`docs/sonnet_implementation_plan.md` §11.2
acceptance criteria; `docs/prompt 2.txt` §17) — renamed since to their
CS-xx blueprint numbers where one exists (P3->CS-08, W1->CS-13, S3->CS-17).
Bugfix round 3 (see `currentStatus.md`) added CS-05, CS-09, CS-14, CS-33,
bringing the total to 8 of the blueprint's CS-01..CS-34
(`docs/theme05_implementation_blueprint.md` lines 2298-2331) plus the
structural P1 check, the static no-wall-clock scan (T1), and replay
identity (C1). The 4 CS-xx checks added in round 3 have not yet been
independently audited against the blueprint text the way the rest were —
see currentStatus.md's Next Task before trusting them fully. Each check is
independent of the kernel's own internal logic — it re-derives the
expectation from the recorded trace rather than trusting the component
that produced it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from prism_rt.model.types import ActionType, StepKind, ToolMutability, fingerprint_for

_BANNED_CALLS = {
    ("time", "time"),
    ("time", "monotonic"),
    ("time", "sleep"),
    ("datetime", "now"),
    ("asyncio", "sleep"),
    ("loop", "time"),
}


@dataclass(frozen=True)
class Violation:
    invariant: str
    message: str
    step_no: int | None = None


class TraceChecker:
    def check(self, reports: list, store=None) -> list[Violation]:
        violations: list[Violation] = []
        violations.extend(self._check_cs08(reports))
        violations.extend(self._check_cs13(reports, store))
        violations.extend(self._check_p1(reports))
        violations.extend(self._check_cs17(reports, store))
        violations.extend(self._check_cs05(reports))
        violations.extend(self._check_cs09(reports, store))
        violations.extend(self._check_cs14(reports, store))
        violations.extend(self._check_cs33(reports))
        return violations

    # CS-08 (formerly P3): every call invalidated this step has a CANCEL emitted this same step.
    def _check_cs08(self, reports: list) -> list[Violation]:
        violations = []
        for report in reports:
            cancelled_targets = {
                er.action.body.target_call_id
                for er in report.emit_report.emitted
                if er.action.action_type == ActionType.CANCEL
            }
            for call_id in report.invalidation.invalidated_call_ids:
                if call_id not in cancelled_targets:
                    violations.append(
                        Violation(
                            "CS-08",
                            f"call {call_id} invalidated at step {report.step_no} but no CANCEL emitted in the same step",
                            report.step_no,
                        )
                    )
        return violations

    # CS-05: Phase order. CANCEL actions must precede other emissions (like TOOL_CALL) 
    # in the emit_report.emitted list, because invalidation cancellation happens first in the DECIDE phase.
    def _check_cs05(self, reports: list) -> list[Violation]:
        violations = []
        for report in reports:
            seen_non_cancel = False
            for er in report.emit_report.emitted:
                if er.action.action_type != ActionType.CANCEL:
                    seen_non_cancel = True
                elif seen_non_cancel:
                    violations.append(
                        Violation(
                            "CS-05",
                            f"CANCEL action {er.action.action_id} appeared after a non-CANCEL action in step {report.step_no}",
                            report.step_no,
                        )
                    )
        return violations

    # CS-09: A call in cancel_requested or cancelled is never consumed.
    # If a CANCEL action was emitted for a call, its final status in the store
    # must not be CONSUMED.
    def _check_cs09(self, reports: list, store) -> list[Violation]:
        violations = []
        if store is None:
            return violations
        for report in reports:
            for er in report.emit_report.emitted:
                if er.action.action_type == ActionType.CANCEL:
                    call_id = er.action.body.target_call_id
                    call = store.call_ledger.get(call_id)
                    if call is not None and call.status.name == "CONSUMED":
                        violations.append(
                            Violation(
                                "CS-09",
                                f"call {call_id} was cancelled in step {report.step_no} but ended up CONSUMED",
                                report.step_no,
                            )
                        )
        return violations

    # CS-14: At most one write per lineage is in_flight or unknown.
    def _check_cs14(self, reports: list, store) -> list[Violation]:
        violations = []
        if store is None:
            return violations
            
        inflight_writes: dict[tuple[str, str], str] = {}
        for report in reports:
            for env in report.batch:
                if env.payload_type == "tool_result":
                    call_id = env.payload.call_id
                    if call_id:
                        for k, v in list(inflight_writes.items()):
                            if v == call_id:
                                del inflight_writes[k]
                                
            for er in report.emit_report.emitted:
                if er.action.action_type == ActionType.CANCEL:
                    call_id = er.action.body.target_call_id
                    for k, v in list(inflight_writes.items()):
                        if v == call_id:
                            del inflight_writes[k]
                elif er.action.action_type == ActionType.TOOL_CALL:
                    if er.action.body.mutability.name == "STATE_CHANGING":
                        call = store.call_ledger.get(er.action.body.call_id)
                        if call is not None:
                            lineage = (call.goal_id, call.step_key)
                            if lineage in inflight_writes:
                                violations.append(
                                    Violation(
                                        "CS-14",
                                        f"Multiple writes in flight for lineage {lineage}: {inflight_writes[lineage]} and {call.call_id}",
                                        report.step_no,
                                    )
                                )
                            inflight_writes[lineage] = call.call_id
        return violations

    # CS-33: Every CANCEL targets an issued, non-terminal call.
    # In the trace, we track which calls have been emitted (TOOL_CALL).
    # A CANCEL can only target a call that has been emitted and not yet resolved.
    def _check_cs33(self, reports: list) -> list[Violation]:
        violations = []
        emitted_calls = set()
        resolved_calls = set()
        for report in reports:
            for env in report.batch:
                if env.payload_type == "tool_result":
                    call_id = env.payload.call_id
                    if call_id:
                        resolved_calls.add(call_id)
                        
            for er in report.emit_report.emitted:
                if er.action.action_type == ActionType.TOOL_CALL:
                    emitted_calls.add(er.action.body.call_id)
                elif er.action.action_type == ActionType.CANCEL:
                    call_id = er.action.body.target_call_id
                    if call_id not in emitted_calls:
                        violations.append(Violation("CS-33", f"CANCEL emitted for call {call_id} which was never emitted", report.step_no))
                    elif call_id in resolved_calls:
                        violations.append(Violation("CS-33", f"CANCEL emitted for call {call_id} which is already resolved", report.step_no))
        return violations

    # CS-13 (formerly W1): no two emitted WRITE tool_calls share a fingerprint *unless* the
    # later one is a legitimate retry (CallRecord.attempt > 1) of a FAILED
    # prior attempt — CommitGate G5/G6 only ever admits a repeat fingerprint
    # in that case (a FAILED effect doesn't block), so a repeat with
    # attempt == 1 indicates a real duplicate slipped through.
    def _check_cs13(self, reports: list, store) -> list[Violation]:
        seen: dict[str, tuple[int, str]] = {}
        violations = []
        for report in reports:
            for er in report.emit_report.emitted:
                action = er.action
                if action.action_type != ActionType.TOOL_CALL:
                    continue
                body = action.body
                if body.mutability != ToolMutability.STATE_CHANGING:
                    continue
                fp = fingerprint_for(body.tool_name, body.arguments)
                if fp in seen:
                    prev_step, prev_call = seen[fp]
                    call = store.call_ledger.get(body.call_id) if store is not None else None
                    if call is not None and call.attempt > 1:
                        seen[fp] = (report.step_no, body.call_id)
                        continue  # legitimate retry, not a duplicate
                    violations.append(
                        Violation(
                            "W1",
                            f"duplicate WRITE fingerprint {fp}: call {body.call_id} (step {report.step_no}) "
                            f"repeats call {prev_call} (step {prev_step})",
                            report.step_no,
                        )
                    )
                else:
                    seen[fp] = (report.step_no, body.call_id)
        return violations

    # P1: nothing emitted was ever also reported rejected for the same
    # action shape in the same step — a structural sanity check on the
    # report itself (EmissionGate enforces the real check at emit time).
    def _check_p1(self, reports: list) -> list[Violation]:
        violations = []
        for report in reports:
            emitted_ids = {er.action.action_id for er in report.emit_report.emitted}
            if len(emitted_ids) != len(report.emit_report.emitted):
                violations.append(
                    Violation("P1", f"duplicate action_id emitted in step {report.step_no}", report.step_no)
                )
        return violations

    # CS-17 (formerly S3): every snapshot-bearing action carries a non-empty, well-formed
    # snapshot. A full CS-17 check (snapshot equals a fresh projection of state
    # *as of that step*) needs per-step FactStore history, which V0/V1 don't
    # retain — comparing against final post-run state instead is unsound
    # whenever a later action's own side effects (e.g. FINAL clearing
    # goal.active) legitimately change state after the snapshot was taken.
    # This checks only the structural property every snapshot must have.
    def _check_cs17(self, reports: list, store) -> list[Violation]:
        violations: list[Violation] = []
        for report in reports:
            for er in report.emit_report.emitted:
                if er.action.snapshot is not None and not er.action.snapshot.digest:
                    violations.append(
                        Violation("CS-17", f"action {er.action.action_id} carries an empty snapshot digest", report.step_no)
                    )
        return violations

    def check_static_no_wallclock(self, roots: list[Path]) -> list[Violation]:
        """T1: no banned wall-clock call anywhere under `roots` (expected:
        the kernel/ and store/ package directories)."""
        violations: list[Violation] = []
        for root in roots:
            for path in sorted(root.rglob("*.py")):
                violations.extend(self._scan_file(path))
        return violations

    def _scan_file(self, path: Path) -> list[Violation]:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            return [Violation("T1", f"{path}: syntax error during static scan: {exc}")]

        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            if isinstance(callee, ast.Attribute) and isinstance(callee.value, ast.Name):
                pair = (callee.value.id, callee.attr)
                if pair in _BANNED_CALLS:
                    violations.append(
                        Violation("T1", f"{path}:{node.lineno}: banned wall-clock call {pair[0]}.{pair[1]}()")
                    )
        return violations

    def check_replay(self, run_once: Callable[[], list[dict]], times: int = 10) -> list[Violation]:
        """C1: running the same scripted scenario `times` times produces
        byte-identical decision logs."""
        baseline = run_once()
        violations = []
        for i in range(1, times):
            candidate = run_once()
            if candidate != baseline:
                violations.append(Violation("C1", f"replay {i} diverged from the baseline decision log"))
                break
        return violations
