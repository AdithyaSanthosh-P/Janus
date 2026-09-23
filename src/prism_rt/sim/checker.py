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
trusting the component that produced it. A second Phase 7 session added a
tenth check's worth of coverage in a different shape: CS-10 turned out to
have no live enforcement at all where the blueprint says it should
(fixed directly in `store/ledgers.py`, not here — offline checking isn't
the right tool for it; see below) plus one closed scenario gap (R-04,
`tests/test_phase7_scenario_coverage.py`) and CS-24 investigated and
deferred with a documented reason below. A fourth session added the
P-04 snapshot-validity check (`_check_p04`: keys ⊆ tool parameter names,
types match schema -- the half of P-04 CS-27 didn't cover), with
fault-injection tests in `tests/test_p04_snapshot_validity.py`. A fifth
session added CS-03 (`_check_cs03`: each report's own `batch` applied in
`Envelope.ordering_key` = (ts_us, class, seq) order) — sound purely from
the batch itself, same standard as CS-27, no store/history dependency —
bringing the offline-CS-check total to 10 of the blueprint's CS-01..CS-34.

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

A second Phase 7 session continued the audit and found one blueprint check
whose stated live enforcement didn't actually exist in this codebase (not
an offline-checker gap, so it's not added here -- see `store/ledgers.py`):
- **CS-10** ("terminal call statuses never change") -- the blueprint says
  this is enforced by "`set_call_status` raises." Grepped: nothing in
  `CallLedger.set_status`/`update` (`store/ledgers.py`) raised on a
  terminal-status transition at all before this session; it was a plain
  `dataclasses.replace`. Reading every one of the ~7 call sites that
  transition a call's status found each one happened to guard the
  terminal case itself first (`ResultRouter.route`'s branch-2 duplicate
  check, `InvalidationEngine._mark_dependents`'s explicit skip,
  `EmissionGate`'s pre-emit CANCEL validation) -- so the invariant held by
  convention, not by construction, across the whole codebase's current
  call sites, but nothing would have stopped a *future* call site from
  reintroducing exactly this bug. Fixed directly in `CallLedger.set_status`
  (a real guard now, matching the blueprint's own stated mechanism) rather
  than attempted here as an offline check, because an offline check would
  need per-step call-status history this project doesn't retain (the same
  limitation CS-17/CS-26 already document) to catch a transition that
  happened and was later overwritten -- a live guard is both the sounder
  and the literal fix the blueprint describes. `tests/test_cs10_terminal_
  call_guard.py` (4 tests) proves it fires via direct construction, the
  same fault-injection standard CS-27 set.
- **CS-24** ("session state does not persist across scenarios," "no
  module-level mutable state") was checked the same way: an AST scan of
  every module-level assignment in `kernel/` and `store/` found 8 mutable
  literals (`_EMISSION_ORDER`, `_HANDLERS`, `_RECONCILE_TEMPLATES`, etc.)
  -- all of them fixed dispatch/lookup tables built once at import and
  never mutated afterward (grepped for `.append(`/`.update(`/`__setitem__`
  against each name; none found). A literal-type-based static check (the
  same shape as T1's) would false-positive on all 8; a sound version needs
  to also prove none of them is ever mutated anywhere in the codebase, a
  heavier cross-file analysis not justified here since the manual audit
  already found zero real violations. `SessionStore.new` constructing a
  fresh store per scenario (the other half of CS-24) is exercised
  incidentally by every test in this suite using its own `SimHarness`
  instance, just never asserted as its own named check. Deferred, not
  built, for the same reason CS-16/CS-26 were: the check as stated isn't
  cheaply sound against what this codebase actually does.

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
  an inert tail") was deferred only because inert-tail promotion (C1)
  didn't exist. It does now (`kernel/turns.py._inert_tail`), and every
  such promotion leaves a `spec_interpret.<turn>.promoted_tail` fact, so
  `_check_cs30` verifies each one independently: the recorded tail really
  is the end of that turn's transcript, and every word in it is in the
  inert lexicon with no negation -- re-derived here with its own
  tokenization, not by calling the kernel's `is_inert_tail`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from prism_rt.model.types import ActionType, BindingKind, EffectStatus, StepKind, ToolMutability, fingerprint_for

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
        violations.extend(self._check_cs15(reports, store))
        violations.extend(self._check_cs33(reports))
        violations.extend(self._check_cs27(reports))
        violations.extend(self._check_p04(reports))
        violations.extend(self._check_cs03(reports))
        violations.extend(self._check_cs30(store))
        violations.extend(self._check_stale_consumption(store))
        violations.extend(self._check_false_claim(reports, store))
        return violations

    # P1 (docs/original_design_audit.md, "stale consumption (provenance
    # closure at consumption)"): D2's exact shape -- a call bound via a
    # STEP_OUTPUT binding must be grounded in its upstream step's
    # *current* result, not one a later correction superseded. Final-
    # store-state only (the same limitation `_check_cs17` already
    # documents for snapshot equality: no per-step fact history is
    # retained), but sound for it: by the time a scenario settles, a
    # correctly-invalidated chain has no such mismatch left standing,
    # while P0.3 reverted leaves one behind forever (D2's own repro).
    # Acceptance proof: reverting kernel/executor.py._bind's STEP_OUTPUT
    # fallback back to reading `result.<call_id>` directly (pre-P0.3)
    # makes this fire on the D2 scenario (tests/test_checker_stale_
    # consumption.py).
    def _check_stale_consumption(self, store) -> list[Violation]:
        if store is None:
            return []
        violations = []
        for goal in store.goals.all():
            plan = store.plans.current(goal.goal_id)
            if plan is None:
                continue
            for step in plan.steps:
                call = store.call_ledger.latest_by_step(goal.goal_id, step.step_key)
                if call is None or call.status.value != "consumed":
                    continue
                for param, binding in step.bindings.items():
                    if binding.kind != BindingKind.STEP_OUTPUT:
                        continue
                    upstream = store.call_ledger.latest_by_step(goal.goal_id, binding.step_key)
                    if upstream is None or upstream.status.value != "consumed":
                        continue
                    result_fact = store.facts.get(f"result.{upstream.call_id}")
                    if result_fact is None or result_fact.status.value == "retracted":
                        continue
                    value = result_fact.value
                    if binding.path:
                        for part in binding.path.split("."):
                            if isinstance(value, dict):
                                value = value.get(part)
                            elif isinstance(value, list) and part.lstrip("-").isdigit():
                                index = int(part)
                                value = value[index] if -len(value) <= index < len(value) else None
                            else:
                                value = None
                            if value is None:
                                break
                    if param in call.args and call.args[param] != value:
                        violations.append(
                            Violation(
                                "STALE-CONSUME",
                                f"call {call.call_id} (step {step.step_key}) consumed with {param}={call.args[param]!r} "
                                f"but upstream {upstream.call_id} (step {binding.step_key}) currently returns {value!r}",
                            )
                        )
        return violations

    # P1 ("false claim"): "That's already been taken care of." may only
    # be spoken when at least one write's effect is genuinely CONFIRMED
    # (kernel/responder.py._inform_blocked_writes, P0.2/D3). The same
    # invariant `sim/explorer.py`'s per-step oracle already checks live;
    # this is the offline/final-state form, checked over the whole
    # emitted-action stream against final ledger state.
    def _check_false_claim(self, reports: list, store) -> list[Violation]:
        if store is None:
            return []
        from config.templates import INFORM_DUPLICATE_WRITE

        confirmed = any(e.status == EffectStatus.CONFIRMED for e in store.effect_ledger.all())
        if confirmed:
            return []
        violations = []
        for report in reports:
            for er in report.emit_report.emitted:
                a = er.action
                if a.action_type == ActionType.SPEAK and getattr(a.body, "text", None) == INFORM_DUPLICATE_WRITE:
                    violations.append(
                        Violation("FALSE-CLAIM", f"'already taken care of' spoken (step {report.step_no}) with no CONFIRMED effect anywhere in the run", report.step_no)
                    )
        return violations

    # CS-30: every inert-tail promotion (C1) promoted across genuinely
    # inert words only, and the recorded tail is the actual end of the
    # turn's transcript.
    def _check_cs30(self, store) -> list[Violation]:
        if store is None:
            return []
        from config.lexicons import INERT_TOKENS, NEGATION_TOKENS

        violations = []
        for key, fact in sorted(store.facts.by_prefix("spec_interpret.").items()):
            if not key.endswith(".promoted_tail") or fact.status.value == "retracted":
                continue
            turn_id = key[len("spec_interpret."):-len(".promoted_tail")]
            tail = str(fact.value)
            words = [w.strip(".,!?;:\"") for w in tail.lower().split()]
            words = [w for w in words if w]
            if not words or any(w not in INERT_TOKENS or w in NEGATION_TOKENS for w in words):
                violations.append(Violation("CS-30", f"turn {turn_id} promoted across non-inert tail {tail!r}"))
            turn = store.turn_log.get(turn_id)
            full = " ".join(c.text for c in turn.chunks) if turn is not None else ""
            if not full.endswith(tail) or full == tail:
                violations.append(Violation("CS-30", f"turn {turn_id} promoted tail {tail!r} is not a strict suffix of its transcript"))
        return violations

    # CS-03 (Phase 7): events are applied in (ts_us, class, seq) order.
    # `Envelope.ordering_key` is exactly this tuple (`model/events.py`); each
    # report's own `batch` is the set of envelopes the kernel applied that
    # step, so this only needs the batch itself, no store/history -- sound
    # by construction, same standard as CS-27.
    def _check_cs03(self, reports: list) -> list[Violation]:
        violations = []
        for report in reports:
            prev_key = None
            for env in report.batch:
                key = getattr(env, "ordering_key", None)
                if key is None:
                    continue  # fixture stand-in without ordering_key -- nothing to check
                if prev_key is not None and key < prev_key:
                    violations.append(
                        Violation(
                            "CS-03",
                            f"envelope {env.event_id} (ordering_key={key}) applied after "
                            f"a later-ordered envelope (ordering_key={prev_key}) in step {report.step_no}",
                            report.step_no,
                        )
                    )
                prev_key = key
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

    # CS-15 (blueprint line 2312): "No write is emitted before settle_ms
    # after its committing turn, or while floor open, triage, or
    # interpretation pending." P1 (docs/original_design_audit.md):
    # previously unimplementable offline -- a write CommitGate blocked
    # left no trace at all in the emitted-action stream, so there was no
    # way to tell "correctly held back" from "never existed yet". Now
    # checkable directly against `StepReport.gate_rejections` (added this
    # package) plus emission timestamps. Two halves: (a) a call must
    # never be both rejected and admitted in the *same* step (a
    # structural sanity check on CommitGate's own single-pass semantics);
    # (b) when `settle_barrier_enabled`, a WRITE TOOL_CALL's own emission
    # step must be at or after `last EOT + settle_ms`. The floor/triage
    # halves of CS-15 are covered by CS-28 (floor) and the triage-hold
    # oracle in `sim/explorer.py`, not duplicated here.
    def _check_cs15(self, reports: list, store) -> list[Violation]:
        if store is None or not store.config.settle_barrier_enabled:
            return []
        violations = []
        last_eot_ts: int | None = None
        settle_us = store.config.settle_ms * 1000
        for report in reports:
            for env in report.batch:
                if env.payload_type == "end_of_turn":
                    last_eot_ts = env.ts_us
            rejected_ids = {r.call_id for r in report.gate_rejections}
            for er in report.emit_report.emitted:
                if er.action.action_type != ActionType.TOOL_CALL or er.action.body.mutability.name != "STATE_CHANGING":
                    continue
                call_id = er.action.body.call_id
                if call_id in rejected_ids:
                    violations.append(
                        Violation("CS-15", f"call {call_id} both rejected and admitted in step {report.step_no}", report.step_no)
                    )
                if last_eot_ts is not None and (report.now_us - last_eot_ts) < settle_us:
                    violations.append(
                        Violation(
                            "CS-15",
                            f"write {call_id} emitted only {report.now_us - last_eot_ts}us after EOT at {last_eot_ts} (settle_ms={store.config.settle_ms})",
                            report.step_no,
                        )
                    )
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

    # P-04 (Phase 7; blueprint line 2528, "Snapshot validity | Every
    # scenario | Snapshot keys ⊆ parameter names; types match schema;
    # revisions monotonic"). The monotonic half is CS-27 above; this is the
    # rest. The catalog is rebuilt *from the trace's own manifest events*,
    # step by step, not read from final store state -- a later manifest can
    # drop a tool (S-08), which would make a final-state check flag
    # snapshots that were valid when emitted (the same soundness trap
    # CS-17's docstring documents).
    _JSON_TYPES = {
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
        "array": (list, tuple),
        "object": (dict,),
    }

    def _check_p04(self, reports: list) -> list[Violation]:
        violations = []
        params: dict[str, dict] = {}
        for report in reports:
            for env in report.batch:
                if env.payload_type == "manifest":
                    params = {}
                    for tool in env.payload.tools or []:
                        schema = tool.get("parameters") or tool.get("params_schema") or {}
                        for name, prop in (schema.get("properties") or {}).items():
                            params.setdefault(name, prop if isinstance(prop, dict) else {})
            for er in report.emit_report.emitted:
                snapshot = er.action.snapshot
                if snapshot is None:
                    continue
                for key, value in snapshot.slots.items():
                    if key not in params:
                        violations.append(
                            Violation("P-04", f"snapshot key {key!r} on action {er.action.action_id} is not a parameter of any declared tool", report.step_no)
                        )
                        continue
                    expected = self._JSON_TYPES.get(params[key].get("type"))
                    if expected is None or value is None:
                        continue
                    if isinstance(value, bool) and bool not in expected:
                        ok = False
                    else:
                        ok = isinstance(value, expected)
                    if not ok:
                        violations.append(
                            Violation("P-04", f"snapshot key {key!r}={value!r} on action {er.action.action_id} doesn't match schema type {params[key].get('type')!r}", report.step_no)
                        )
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
