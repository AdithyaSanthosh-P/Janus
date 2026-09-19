"""TraceChecker: verify kernel invariants against a run's StepReports (and,
for T1, against the source tree itself).

V0 shipped T1, P1, P3, W1, S3, C1 (`docs/sonnet_implementation_plan.md` §11.2
acceptance criteria; `docs/prompt 2.txt` §17) — renamed since to their
CS-xx blueprint numbers where one exists (P3->CS-08, W1->CS-13, S3->CS-17).
Bugfix round 3 (see `currentStatus.md`) added CS-05, CS-09, CS-14, CS-33
without independent audit against the blueprint text. Phase 7 (see
`docs/post_v4_implementation_plan.md`) did that audit and added CS-27,
bringing the total to 9 of the blueprint's CS-01..CS-34
(`docs/theme05_implementation_blueprint.md` lines 2298-2331) plus the
structural P1 check, the static no-wall-clock scan (T1), and replay
identity (C1). Each check is independent of the kernel's own internal
logic — it re-derives the expectation from the recorded trace rather than
trusting the component that produced it.

Phase 7 audit findings on the round-3 checks (see each method's docstring
for detail): CS-09 and CS-33 match the blueprint text closely. CS-05 checks
a real, valuable invariant (CANCEL always ordered first among a step's
emitted actions, per the fixed emission order in `docs/prompt 2.txt` §5.2)
but is not a literal implementation of the blueprint's own CS-05 text
("emission happens after invalidation in the same step", a step-level
phase-order property that isn't stampable from the data currently in
`StepReport`) — kept under the CS-05 slot since it's the closest real
invariant this trace can verify, but it is a reinterpretation, not the
blueprint's literal check. CS-14 only covers the call-emission half of
"at most one write per lineage is in_flight or unknown" (concurrent
TOOL_CALL emission for the same lineage); it does not additionally verify
the effect-ledger UNKNOWN-status half, because `EffectLedger.by_lineage`
holds only the current record per lineage, not a history, so an offline
check can't see whether two effects were ever simultaneously in a
blocking state for the same lineage from final state alone (the same
class of limitation `_check_cs17`'s own docstring already documents for
snapshot-equality checking).

Investigated but not built, with reasons (do not re-attempt without
addressing the reason first):
- **CS-15** ("no write emitted before settle_ms after its committing turn,
  or while floor open/triage/interpretation pending") is enforced live by
  `CommitGate` G3/G10/G11 (`kernel/commit.py`), but a *blocked* write never
  becomes an `IntendedAction` and so never reaches `EmitReport` at all —
  there is currently no recorded trace of a G3/G10/G11 rejection to check
  offline. Building this needs new instrumentation (recording gate
  decisions, not just emitted/rejected actions) before it's checkable.
- **CS-16** ("held proposals released only if read sets valid at
  release") uses blueprint terminology ("held", "triage") that has no
  literal analog in this codebase's actual mechanisms (grepped — no
  match). Needs a mapping exercise to whichever real mechanism this
  corresponds to (candidate: `kernel/task.py`'s speculative-interpretation
  EOT-wait path) before a check can be written against it.
- **CS-26** ("conflicting evidence on a write argument blocks the write")
  *is* genuinely enforced — not via `CommitGate` G4 as the blueprint's
  component column states, but earlier, in `PlanExecutor._bind`
  (`kernel/executor.py`, the `conflict_open` check ~line 292): a step
  bound to a slot with an open perception conflict is never proposed as a
  call at all, so `CommitGate` never sees it. An offline check is possible
  only against evidence-ledger *final* state, which has the same
  soundness gap as CS-17 (a conflict could open after a call already
  bound cleanly, producing a false positive) — deferred rather than
  shipped unsound.
- **CS-30** ("a promoted interpretation differs from the full text only by
  an inert tail") depends on inert-tail promotion, which this project has
  never built (explicitly deferred/CUT scope, see `currentStatus.md`) —
  nothing exists yet for this check to verify.
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
        violations.extend(self._check_cs27(reports))
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

    # CS-27 (Phase 7): snapshot revisions are non-decreasing. `Snapshot.
    # revision` is `store.facts.revision()` at projection time (`kernel/
    # snapshot.py`) -- a single session-wide counter that only ever
    # increments (`store/facts.py`), so every snapshot emitted later in the
    # trace must carry a revision >= every snapshot emitted earlier,
    # regardless of which goal or action carried it. Unlike CS-17 (snapshot
    # *equality*, which needs per-step history this project doesn't
    # retain), this only needs the emitted action stream itself, so it's
    # fully checkable and sound.
    def _check_cs27(self, reports: list) -> list[Violation]:
        violations = []
        max_seen = -1
        for report in reports:
            for er in report.emit_report.emitted:
                snapshot = er.action.snapshot
                if snapshot is None:
                    continue
                if snapshot.revision < max_seen:
                    violations.append(
                        Violation(
                            "CS-27",
                            f"snapshot revision {snapshot.revision} on action {er.action.action_id} "
                            f"is less than a previously emitted revision {max_seen}",
                            report.step_no,
                        )
                    )
                else:
                    max_seen = snapshot.revision
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
