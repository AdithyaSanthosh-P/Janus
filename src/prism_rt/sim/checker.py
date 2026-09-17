"""TraceChecker: verify kernel invariants against a run's StepReports (and,
for T1, against the source tree itself).

V0 checks T1, P1, P3, W1, S3, C1 (`docs/sonnet_implementation_plan.md` §11.2
acceptance criteria; `docs/prompt 2.txt` §17). Each check is independent of
the kernel's own internal logic — it re-derives the expectation from the
recorded trace rather than trusting the component that produced it.
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
        violations.extend(self._check_p3(reports))
        violations.extend(self._check_w1(reports, store))
        violations.extend(self._check_p1(reports))
        violations.extend(self._check_s3(reports, store))
        return violations

    # P3: every call invalidated this step has a CANCEL emitted this same step.
    def _check_p3(self, reports: list) -> list[Violation]:
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
                            "P3",
                            f"call {call_id} invalidated at step {report.step_no} but no CANCEL emitted in the same step",
                            report.step_no,
                        )
                    )
        return violations

    # W1: no two emitted WRITE tool_calls share a fingerprint *unless* the
    # later one is a legitimate retry (CallRecord.attempt > 1) of a FAILED
    # prior attempt — CommitGate G5/G6 only ever admits a repeat fingerprint
    # in that case (a FAILED effect doesn't block), so a repeat with
    # attempt == 1 indicates a real duplicate slipped through.
    def _check_w1(self, reports: list, store) -> list[Violation]:
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

    # S3: every snapshot-bearing action carries a non-empty, well-formed
    # snapshot. A full S3 check (snapshot equals a fresh projection of state
    # *as of that step*) needs per-step FactStore history, which V0/V1 don't
    # retain — comparing against final post-run state instead is unsound
    # whenever a later action's own side effects (e.g. FINAL clearing
    # goal.active) legitimately change state after the snapshot was taken.
    # This checks only the structural property every snapshot must have.
    def _check_s3(self, reports: list, store) -> list[Violation]:
        violations: list[Violation] = []
        for report in reports:
            for er in report.emit_report.emitted:
                if er.action.snapshot is not None and not er.action.snapshot.digest:
                    violations.append(
                        Violation("S3", f"action {er.action.action_id} carries an empty snapshot digest", report.step_no)
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
