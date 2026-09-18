"""FastResponder: templated ACK, CLARIFY, INFORM, FINAL.

V1 trims the utterance kinds from `docs/prompt 2.txt` §9.3 down to what the
15-scenario suite needs: ACK (once per plan dispatch), CLARIFY (once per
distinct clarify target), INFORM (duplicate-write / unknown-outcome
notices), and FINAL. PROGRESS and HOLD are real utterance kinds the
architecture describes but nothing in V1's scope requires them — they are
not implemented, not silently dropped from something that used to emit
them.

V2 adds (gated by config.claim_grades_enabled unless noted): ClaimGrade on
ACK/FINAL bodies; an INTENDED-grade utterance while a write is held by the
settle barrier ("Booking the flight now" — pending, not yet emitted); and
(unconditional — a safety property, not an innovation to toggle off)
reconcile INFORM for a write that completed after being cancelled, so a
booking that went through anyway is never left unreported (W4).

Every "have we already said this" check is a small fact
(`ack.<goal>.acked_job`, `clarify.<goal>.asked`, `inform.<call>.sent`)
rather than a formal OutputLedger — sufficient for one utterance per
trigger, and it participates in the same read-set/invalidation machinery
as everything else for free.
"""

from __future__ import annotations

from config.templates import ACK_DEFAULT, CLARIFY_TEMPLATE, FINAL_FALLBACK, INFORM_DUPLICATE_WRITE
from prism_rt.kernel.commit import CommitGate
from prism_rt.kernel.interpret_apply import active_goal_id
from prism_rt.model.actions import FinalBody, IntendedAction, SpeakBody
from prism_rt.model.types import (
    EMPTY_READ_SET,
    ActionType,
    CallStatus,
    ClaimGrade,
    ConflictStatus,
    EffectStatus,
    FactStatus,
    GoalStatus,
    JobKind,
    Provenance,
    StepKind,
    TaskState,
)

_RECONCILE_TEMPLATES = {
    EffectStatus.CONFIRMED: "Just so you know — the earlier booking had already gone through before I could cancel it.",
    EffectStatus.UNKNOWN: "I'm not sure whether the earlier booking went through — I couldn't get a result back after cancelling.",
}


class FastResponder:
    def __init__(self) -> None:
        self._commit_gate = CommitGate()

    def decide(self, store, now_us: int, step_no: int, *, skip_call_ids: frozenset[str] = frozenset()) -> list[IntendedAction]:
        actions: list[IntendedAction] = []
        actions.extend(self._inform_blocked_writes(store, now_us, step_no, skip_call_ids))
        actions.extend(self._reconcile_completed_after_cancel(store, now_us, step_no))

        gid = active_goal_id(store)
        if gid is None:
            return actions
        goal = store.goals.get(gid)
        if goal is None:
            return actions

        if goal.status == GoalStatus.ACTIVE and goal.task_state == TaskState.PLANNING:
            actions.extend(self._ack_plan_dispatch(store, gid, now_us, step_no))
        if goal.status == GoalStatus.ACTIVE and goal.task_state == TaskState.CLARIFYING:
            actions.extend(self._clarify(store, gid, now_us, step_no))
        if goal.status == GoalStatus.ACTIVE and goal.task_state == TaskState.EXECUTING:
            actions.extend(self._intended_for_settling_write(store, gid, now_us, step_no, skip_call_ids))
        if goal.task_state == TaskState.RESPONDING:
            actions.extend(self._final(store, goal, now_us, step_no))

        return actions

    def _ack_plan_dispatch(self, store, goal_id: str, now_us: int, step_no: int) -> list[IntendedAction]:
        running = store.jobs.running_by_kind_goal(JobKind.PLAN, goal_id)
        if not running:
            return []
        job_id = running[0].job_id
        acked = store.facts.get(f"ack.{goal_id}.acked_job")
        if acked is not None and acked.status != FactStatus.RETRACTED and acked.value == job_id:
            return []
        store.facts.set(
            f"ack.{goal_id}.acked_job",
            job_id,
            FactStatus.COMMITTED,
            Provenance(source="system", step_no=step_no, ts_us=now_us),
            rule="responder.ack",
        )
        grade = ClaimGrade.UNDERSTOOD if store.config.claim_grades_enabled else None
        text = self._content_ack_text(store) or ACK_DEFAULT
        return [
            IntendedAction(
                action_type=ActionType.SPEAK,
                body=SpeakBody(text=text, kind="ack", claim_grade=grade),
                read_set=EMPTY_READ_SET,
                rule_id="responder.ack",
            )
        ]

    def _content_ack_text(self, store) -> str | None:
        """Phase 6 (`docs/prompt 2.txt` §9.3): "emit a content-bearing ACK
        only if QuickDetector produced at least one HIGH value" — instead
        of the fixed generic string, name what was actually understood (a
        `hyp.<turn>.<name>` fact Phase 4's `detect_values` already
        recorded for the turn that triggered this plan dispatch). Since
        `kernel/turns.py._detect_chunk_anchor` writes that fact
        unconditionally — regardless of whether a goal existed yet when
        the chunk was heard — this covers a brand-new first utterance
        exactly as well as a correction on an active goal, as long as
        QuickDetector found a HIGH value at all; this call only runs once
        `_ack_plan_dispatch` itself runs (a plan is actually dispatching),
        so interpretation has necessarily already applied by then either
        way. When QuickDetector found nothing HIGH-confidence for that
        turn, there is nothing to name and the generic string is used."""
        if not store.config.speculative_interpretation_enabled:
            return None
        turns = store.turn_log.all()
        if not turns:
            return None
        turn = turns[-1]
        hyp_facts = store.facts.by_prefix(f"hyp.{turn.turn_id}.")
        live = {
            key[len(f"hyp.{turn.turn_id}.") :]: fact.value
            for key, fact in hyp_facts.items()
            if fact.status != FactStatus.RETRACTED
        }
        if not live:
            return None
        name, value = sorted(live.items())[0]  # deterministic pick
        return f"Got it — {name.replace('_', ' ')}: {value}."

    def _clarify(self, store, goal_id: str, now_us: int, step_no: int) -> list[IntendedAction]:
        target_fact = store.facts.get(f"goal.{goal_id}.clarify_target")
        if target_fact is None or target_fact.status == FactStatus.RETRACTED:
            return []
        target = target_fact.value
        asked = store.facts.get(f"clarify.{goal_id}.asked")
        if asked is not None and asked.status != FactStatus.RETRACTED and asked.value == target:
            return []
        store.facts.set(
            f"clarify.{goal_id}.asked",
            target,
            FactStatus.COMMITTED,
            Provenance(source="system", step_no=step_no, ts_us=now_us),
            rule="responder.clarify",
        )
        friendly = target.rsplit(".", 1)[-1]
        text = CLARIFY_TEMPLATE.format(target=friendly)
        # V3: a slot blocked by an open perception conflict gets a more
        # specific question naming both candidate values, rather than the
        # generic "what should X be" (§9.4 "Claim acceptance" example:
        # "The camera shows a Tab S9. Is that the device you mean?").
        if store.config.vision_enabled and target.startswith(f"slot.{goal_id}."):
            conflict = store.evidence.get_conflict(goal_id, friendly)
            if conflict is not None and conflict.status == ConflictStatus.OPEN:
                user_val = next((c["value"] for c in conflict.candidates if c["source"] == "user"), None)
                perception_val = next((c["value"] for c in conflict.candidates if c["source"] == "perception"), None)
                text = f"The camera shows {perception_val}. Did you mean {user_val}, or {perception_val}?"
        return [
            IntendedAction(
                action_type=ActionType.CLARIFY,
                body=SpeakBody(text=text, kind="clarify"),
                read_set=EMPTY_READ_SET,
                rule_id="responder.clarify",
            )
        ]

    def _final(self, store, goal, now_us: int, step_no: int) -> list[IntendedAction]:
        text_fact = store.facts.get(f"compose.{goal.goal_id}.text")
        if text_fact is None or text_fact.status == FactStatus.RETRACTED:
            return []
        text = text_fact.value or FINAL_FALLBACK
        # Phase A: a watchdog-salvaged FINAL must never claim completion —
        # that's the entire point of the salvage path (§8.9). Keyed off
        # *this fact's own provenance* (source="watchdog", set only by
        # reducers._apply_watchdog), not "has the watchdog fired at all" —
        # a real compose result that happens to land the same step the
        # watchdog fires (or after it, for some other goal) is still a
        # genuine completion and must not be falsely marked incomplete.
        # goal.status itself still becomes COMPLETED via EmissionGate's
        # generic post-FINAL side effect either way; this field is what
        # the protocol output actually reports.
        is_watchdog_salvage = text_fact.provenance.source == "watchdog"
        task_completed = goal.status != GoalStatus.ABANDONED and not is_watchdog_salvage

        grade = None
        if store.config.claim_grades_enabled:
            grade = ClaimGrade.EFFECT_DONE if self._has_confirmed_write(store, goal.goal_id) else ClaimGrade.RESULT

        return [
            IntendedAction(
                action_type=ActionType.FINAL,
                body=FinalBody(text=text, task_completed=task_completed, claim_grade=grade),
                read_set=EMPTY_READ_SET,
                needs_snapshot=True,
                rule_id="responder.final",
                goal_id=goal.goal_id,
            )
        ]

    def _has_confirmed_write(self, store, goal_id: str) -> bool:
        plan = store.plans.current(goal_id)
        if plan is None:
            return False
        for step in plan.steps:
            if step.kind != StepKind.WRITE:
                continue
            call = store.call_ledger.latest_by_step(goal_id, step.step_key)
            if call is None:
                continue
            effect = store.effect_ledger.by_fingerprint(call.fingerprint)
            if effect is not None and effect.status == EffectStatus.CONFIRMED:
                return True
        return False

    def _inform_blocked_writes(self, store, now_us: int, step_no: int, skip_call_ids) -> list[IntendedAction]:
        actions: list[IntendedAction] = []
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
                f"inform.{call.call_id}.sent",
                True,
                FactStatus.COMMITTED,
                Provenance(source="system", step_no=step_no, ts_us=now_us),
                rule="responder.inform_duplicate",
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

    def _intended_for_settling_write(self, store, goal_id: str, now_us: int, step_no: int, skip_call_ids) -> list[IntendedAction]:
        """S-10: while a write sits PROPOSED behind the settle barrier
        (G10), say so with an INTENDED claim — never IN_PROGRESS, since the
        call hasn't been emitted yet (§9.3's own rule: never claim
        IN_PROGRESS for a call not yet emitted)."""
        if not store.config.claim_grades_enabled or not store.config.settle_barrier_enabled:
            return []
        for call in store.call_ledger.proposed():
            if call.kind != StepKind.WRITE or call.goal_id != goal_id or call.call_id in skip_call_ids:
                continue
            decision = self._commit_gate.evaluate(call, store, now_us)
            if decision.allowed or decision.rule_id not in ("G10", "G11"):
                continue
            acked = store.facts.get(f"ack.{goal_id}.intended_call")
            if acked is not None and acked.status != FactStatus.RETRACTED and acked.value == call.call_id:
                return []
            store.facts.set(
                f"ack.{goal_id}.intended_call",
                call.call_id,
                FactStatus.COMMITTED,
                Provenance(source="system", step_no=step_no, ts_us=now_us),
                rule="responder.intended",
            )
            return [
                IntendedAction(
                    action_type=ActionType.SPEAK,
                    body=SpeakBody(text=f"On it — {call.tool.replace('_', ' ')} now.", kind="ack", claim_grade=ClaimGrade.INTENDED),
                    read_set=EMPTY_READ_SET,
                    rule_id="responder.intended",
                )
            ]
        return []

    def _reconcile_completed_after_cancel(self, store, now_us: int, step_no: int) -> list[IntendedAction]:
        """I-15: a write that got cancelled but completed anyway (or whose
        outcome is now unknown) must be reported — never silently dropped
        (W4: no false undo claims). Unconditional: this is a safety
        property, not an innovation to gate behind a flag."""
        actions: list[IntendedAction] = []
        for call in store.call_ledger.all():
            if call.kind != StepKind.WRITE or call.status != CallStatus.COMPLETED_AFTER_CANCEL:
                continue
            already = store.facts.get(f"inform.{call.call_id}.sent")
            if already is not None and already.status != FactStatus.RETRACTED:
                continue
            effect = store.effect_ledger.by_fingerprint(call.fingerprint)
            template = _RECONCILE_TEMPLATES.get(effect.status) if effect is not None else None
            if template is None:
                continue  # FAILED (genuinely didn't happen) needs no reconciliation notice
            store.facts.set(
                f"inform.{call.call_id}.sent",
                True,
                FactStatus.COMMITTED,
                Provenance(source="system", step_no=step_no, ts_us=now_us),
                rule="responder.reconcile",
            )
            actions.append(
                IntendedAction(
                    action_type=ActionType.SPEAK,
                    body=SpeakBody(text=template, kind="inform"),
                    read_set=EMPTY_READ_SET,
                    rule_id="responder.reconcile",
                )
            )
        return actions
