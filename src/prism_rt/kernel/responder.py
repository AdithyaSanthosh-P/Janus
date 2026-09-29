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

from config.templates import (
    ACK_DEFAULT,
    CLARIFY_RETRY_WRITE,
    CLARIFY_TEMPLATE,
    FINAL_FALLBACK,
    FRESH_VIEW_REQUEST,
    INFORM_DUPLICATE_WRITE,
    INFORM_UNKNOWN_WRITE_OUTCOME,
    UNCLEAR_NO_GOAL,
)
from prism_rt.kernel.commit import CommitGate
from prism_rt.kernel.replies import HONEST_REPLY_KEY
from prism_rt.kernel.interpret_apply import active_goal_id, grounded_compose_text, user_content_pending
from prism_rt.model.actions import FinalBody, IntendedAction, SpeakBody
from prism_rt.model.types import (
    EMPTY_READ_SET,
    ActionType,
    CallStatus,
    ClaimGrade,
    ConflictStatus,
    EffectStatus,
    FactStatus,
    FloorState,
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


def _is_watchdog_salvage(store) -> bool:
    """Only the salvage FINAL itself is exempt from the hold -- found by
    `sim/explorer.py` (V4): exempting everything once the watchdog had
    fired let a *genuine* answer race past a pending correction."""
    gid = active_goal_id(store)
    if gid is None:
        return False
    text = store.facts.get(f"compose.{gid}.text")
    return text is not None and text.status != FactStatus.RETRACTED and text.provenance.source == "watchdog"


class FastResponder:
    def __init__(self) -> None:
        self._commit_gate = CommitGate()

    def decide(self, store, now_us: int, step_no: int, *, skip_call_ids: frozenset[str] = frozenset()) -> list[IntendedAction]:
        # CS-28 (`docs/prompt 2.txt` line 370, policy speak_during_open_turn,
        # default OFF): no SPEAK, CLARIFY, or FINAL while the floor is
        # USER_TURN_OPEN -- this method is the only source of those three
        # action types (CANCEL/TOOL_CALL come from InvalidationEngine/
        # CommitGate elsewhere, unaffected). Returning [] here rather than
        # filtering afterward means none of this method's "already handled"
        # facts (ack/clarify/inform-sent) get set either, so a floor-blocked
        # notice is deferred to the next step the floor is closed, never
        # silently dropped.
        if store.floor_state == FloorState.USER_TURN_OPEN and not store.config.speak_during_open_turn:
            return []
        # TRIAGE hold (`docs/prompt 2.txt` §8.3, transitions 23-31): the
        # floor just closed on a turn spoken during an active goal, but
        # that turn hasn't been interpreted yet -- it may be a correction
        # ("actually Mumbai") that makes whatever we'd say now stale. Found
        # by `sim/explorer.py`: the floor rule held FINAL("Pune flights
        # found") while the user said "actually Mumbai", then released it
        # in the very step their EOT closed the floor. Hold, don't discard:
        # every responder output is re-derived from state each step, so
        # once the interpretation lands, whatever is still valid is spoken
        # and whatever it invalidated simply never is. The watchdog's
        # salvage FINAL is exempt -- the budget is spent either way.
        if (
            store.config.triage_hold_enabled
            and active_goal_id(store) is not None
            and user_content_pending(store)
            and not _is_watchdog_salvage(store)
        ):
            return []
        actions: list[IntendedAction] = []
        actions.extend(self._inform_blocked_writes(store, now_us, step_no, skip_call_ids))
        actions.extend(self._inform_write_timeout(store, now_us, step_no))
        actions.extend(self._reconcile_completed_after_cancel(store, now_us, step_no))
        actions.extend(self._unclear_no_goal(store, now_us, step_no))
        actions.extend(self._honest_reply(store, now_us, step_no))

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
        if goal.status == GoalStatus.ACTIVE and goal.task_state in (TaskState.EXECUTING, TaskState.CLARIFYING):
            # S3: before the S-10 notice below, which it suppresses for the
            # same write -- one acknowledgement, not two.
            actions.extend(self._ack_compiled_plan(store, gid, now_us, step_no))
        if goal.status == GoalStatus.ACTIVE and goal.task_state == TaskState.EXECUTING:
            actions.extend(self._intended_for_settling_write(store, gid, now_us, step_no, skip_call_ids))
            actions.extend(self._confirm_unconfirmed_write(store, gid, now_us, step_no, skip_call_ids))
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
        text = self._echo_ack_text(store, goal_id) or self._content_ack_text(store) or ACK_DEFAULT
        return [
            IntendedAction(
                action_type=ActionType.SPEAK,
                body=SpeakBody(text=text, kind="ack", claim_grade=grade),
                read_set=EMPTY_READ_SET,
                rule_id="responder.ack",
            )
        ]

    def _ack_compiled_plan(self, store, goal_id: str, now_us: int, step_no: int) -> list[IntendedAction]:
        """S3 (`kernel/action_plans.py`): a compiled plan never dispatches a
        PLAN job, so `_ack_plan_dispatch` (which keys off one) never fires
        for it. Same acknowledgement, same text priority, once per goal --
        a later in-place recompile (plan_rev + 1) never re-ACKs. Also marks
        the first settle-held write as already acknowledged, so the S-10
        "On it — <tool> now." notice doesn't speak a second ACK for the
        same call in the same step."""
        plan = store.plans.current(goal_id)
        if plan is None or plan.origin != "compiled":
            return []
        acked = store.facts.get(f"ack.{goal_id}.compiled")
        if acked is not None and acked.status != FactStatus.RETRACTED:
            return []
        provenance = Provenance(source="system", step_no=step_no, ts_us=now_us)
        store.facts.set(f"ack.{goal_id}.compiled", plan.plan_rev, FactStatus.COMMITTED, provenance, rule="responder.ack")
        for call in store.call_ledger.proposed():
            if call.goal_id == goal_id and call.kind == StepKind.WRITE:
                store.facts.set(f"ack.{goal_id}.intended_call", call.call_id, FactStatus.COMMITTED, provenance, rule="responder.ack")
                break
        grade = ClaimGrade.UNDERSTOOD if store.config.claim_grades_enabled else None
        text = self._echo_ack_text(store, goal_id) or self._content_ack_text(store) or ACK_DEFAULT
        unsupported = store.facts.get(f"goal.{goal_id}.unsupported")
        if unsupported is not None and unsupported.status != FactStatus.RETRACTED and unsupported.value:
            text = f"{text} I can't do \"{unsupported.value}\" here, though."
        return [
            IntendedAction(
                action_type=ActionType.SPEAK,
                body=SpeakBody(text=text, kind="ack", claim_grade=grade),
                read_set=EMPTY_READ_SET,
                rule_id="responder.ack",
            )
        ]

    def _echo_ack_text(self, store, goal_id: str) -> str | None:
        """Q7 (win_plan §6.2): the Interpreter's own `ack_phrase`, when the
        model provided one — takes priority over the single-value
        `_content_ack_text` since it names every requested action, not
        just the one HIGH-confidence value QuickDetector happened to
        catch."""
        if not store.config.echo_ack_enabled:
            return None
        fact = store.facts.get(f"goal.{goal_id}.ack_phrase")
        if fact is None or fact.status == FactStatus.RETRACTED or not fact.value:
            return None
        return fact.value

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
        name = name.rsplit(".", 1)[-1]  # S3: a per-action slot name ("a0.city") speaks as "city"
        return f"Got it — {name.replace('_', ' ')}: {value}."

    def _clarify(self, store, goal_id: str, now_us: int, step_no: int) -> list[IntendedAction]:
        target_fact = store.facts.get(f"goal.{goal_id}.clarify_target")
        if target_fact is None or target_fact.status == FactStatus.RETRACTED:
            return []
        target = target_fact.value
        # Q5 (win_plan §6.2): a plain missing-slot target gets one narrow
        # re-extraction attempt (kernel/task.py.TaskStateMachine._maybe_
        # reextract) before this ever speaks -- hold until that attempt
        # has actually resolved (`reextract.<goal>.<target>` set), so the
        # generic "what should X be" question doesn't go out the same
        # step the targeted attempt was dispatched. A resolved-with-value
        # attempt retracts clarify_target itself (short-circuiting the
        # check above on the next step); a resolved-with-null attempt
        # falls through to the ordinary clarify below.
        if (
            store.config.clarify_reextract_enabled
            and isinstance(target, str)
            and target.startswith(f"slot.{goal_id}.")
        ):
            tried = store.facts.get(f"reextract.{goal_id}.{target}")
            if tried is None or tried.status == FactStatus.RETRACTED:
                return []
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
        # P0.4 (S-02, docs/original_design_audit.md D4): a write-timeout
        # retry confirmation (kernel/executor.py.PlanExecutor.
        # expire_deadlines) is a yes/no question, not a missing-slot one --
        # the generic "what should X be" template would be nonsensical
        # against a `retry:<lineage>` target.
        if target.startswith("retry:"):
            text = CLARIFY_RETRY_WRITE
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
            fresh = store.facts.get(f"perception.{goal_id}.fresh_view_for")
            if fresh is not None and fresh.status != FactStatus.RETRACTED and fresh.value == target:
                text = FRESH_VIEW_REQUEST  # M-05: stale evidence before a write, not missing information
        return [
            IntendedAction(
                action_type=ActionType.CLARIFY,
                body=SpeakBody(text=text, kind="clarify"),
                read_set=EMPTY_READ_SET,
                rule_id="responder.clarify",
            )
        ]

    def _final(self, store, goal, now_us: int, step_no: int) -> list[IntendedAction]:
        text_fact = grounded_compose_text(store, goal.goal_id)
        if text_fact is None:
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
            # P0.4 (docs/original_design_audit.md D4): G7 (unknown_effect_
            # outcome) added alongside G5/G6 -- a DENY on a retry
            # confirmation (kernel/interpret_apply.py) leaves the
            # blocking effect UNKNOWN rather than resolving it, and a
            # subsequent replan attempt for the same lineage would
            # otherwise sit G7-blocked with no message at all, a silent
            # block this check already exists to prevent for G5/G6.
            if decision.allowed or decision.rule_id not in ("G5", "G6", "G7"):
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
            # P0.2 (docs/original_design_audit.md D3): "already taken care
            # of" is only true when the blocking effect is actually
            # CONFIRMED -- a PENDING or UNKNOWN blocker (G6/G5's other two
            # blocking statuses) means we genuinely don't know the outcome
            # yet, so saying "done" would be exactly the false completion
            # claim D3 found. CS-06's spirit: a claim must be supported by
            # ledger state, not just "some earlier attempt exists".
            blocking_effect = store.effect_ledger.by_fingerprint(call.fingerprint)
            text = (
                INFORM_DUPLICATE_WRITE
                if blocking_effect is not None and blocking_effect.status == EffectStatus.CONFIRMED
                else INFORM_UNKNOWN_WRITE_OUTCOME
            )
            actions.append(
                IntendedAction(
                    action_type=ActionType.SPEAK,
                    body=SpeakBody(text=text, kind="inform"),
                    read_set=EMPTY_READ_SET,
                    rule_id="responder.inform_duplicate",
                )
            )
        return actions

    def _inform_write_timeout(self, store, now_us: int, step_no: int) -> list[IntendedAction]:
        """P0.4 (S-02, docs/original_design_audit.md D4): a write whose
        call deadline expired (`kernel/executor.py.PlanExecutor.
        expire_deadlines` emits CANCEL for it; `propose_ready_calls`'
        `CANCEL_REQUESTED`/`"deadline_expired"` branch settles its
        effect) has an effect that's genuinely UNKNOWN, not a definite
        failure -- distinct from P0.2's non-retryable-error case (which
        always settles the effect to FAILED, never UNKNOWN). Say so
        honestly once, in the same INFORM wording a blocked duplicate
        write with an UNKNOWN effect already uses (`_inform_blocked_
        writes`); the accompanying "should I try again?" CLARIFY comes
        from `_clarify` once that same branch's own `_ask_for` call put
        the goal into CLARIFYING."""
        actions: list[IntendedAction] = []
        for call in store.call_ledger.all():
            if (
                call.kind != StepKind.WRITE
                or call.status != CallStatus.CANCEL_REQUESTED
                or call.cancel_reason != "deadline_expired"
            ):
                continue
            effect = store.effect_ledger.by_fingerprint(call.fingerprint)
            if effect is None or effect.status != EffectStatus.UNKNOWN:
                continue
            already = store.facts.get(f"timeout_inform.{call.call_id}.sent")
            if already is not None and already.status != FactStatus.RETRACTED:
                continue
            store.facts.set(
                f"timeout_inform.{call.call_id}.sent",
                True,
                FactStatus.COMMITTED,
                Provenance(source="system", step_no=step_no, ts_us=now_us),
                rule="responder.inform_write_timeout",
            )
            actions.append(
                IntendedAction(
                    action_type=ActionType.SPEAK,
                    body=SpeakBody(text=INFORM_UNKNOWN_WRITE_OUTCOME, kind="inform"),
                    read_set=EMPTY_READ_SET,
                    rule_id="responder.inform_write_timeout",
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

    def _unclear_no_goal(self, store, now_us: int, step_no: int) -> list[IntendedAction]:
        """Q6a (win_plan §6.2): the spoken half of the housing_11/
        housing_13 silent-stall fix -- `kernel/interpret_apply.py` flags
        exactly one turn_id here, at most once per turn (a later turn
        overwrites it with its own turn_id, which is fine — the "sent"
        fact below is keyed per turn_id, so a *new* unclear turn still
        gets its own honest re-ask). Unconditional read of the flag
        (config gating already happened where it was set)."""
        fact = store.facts.get("session.unclear_no_goal_turn")
        if fact is None or fact.status == FactStatus.RETRACTED:
            return []
        turn_id = fact.value
        already = store.facts.get(f"unclear.{turn_id}.sent")
        if already is not None and already.status != FactStatus.RETRACTED:
            return []
        # Superseded: the user has already said more (a newer turn exists) or
        # a goal now exists -- their follow-up is being handled; re-asking
        # about the fragment would talk over it.
        turns = store.turn_log.all()
        if (turns and turns[-1].turn_id != turn_id) or active_goal_id(store) is not None:
            return []
        delay_ms = store.config.unclear_reask_delay_ms
        if delay_ms:
            due_us = fact.provenance.ts_us + delay_ms * 1000
            if now_us < due_us:
                store.timers.schedule(f"unclear_reask:{turn_id}", due_us)
                return []
        store.facts.set(
            f"unclear.{turn_id}.sent",
            True,
            FactStatus.COMMITTED,
            Provenance(source="system", step_no=step_no, ts_us=now_us),
            rule="responder.unclear_no_goal",
        )
        return [
            IntendedAction(
                action_type=ActionType.SPEAK,
                body=SpeakBody(text=UNCLEAR_NO_GOAL, kind="clarify"),
                read_set=EMPTY_READ_SET,
                rule_id="responder.unclear_no_goal",
            )
        ]

    def _confirm_unconfirmed_write(self, store, goal_id: str, now_us: int, step_no: int, skip_call_ids) -> list[IntendedAction]:
        """Config.conversational_replies_enabled: a write held only because
        the user never confirmed it (CommitGate G4) used to sit PROPOSED in
        silence until the stall salvage gave up. Ask once, naming the call;
        a CONFIRM answer supplies the commit intent (interpret_apply)."""
        if not store.config.conversational_replies_enabled:
            return []
        for call in store.call_ledger.proposed():
            if call.goal_id != goal_id or call.kind != StepKind.WRITE or call.call_id in skip_call_ids:
                continue
            decision = self._commit_gate.evaluate(call, store, now_us)
            if decision.allowed or decision.rule_id != "G4":
                continue
            asked = store.facts.get(f"confirm.{call.call_id}.asked")
            if asked is not None and asked.status != FactStatus.RETRACTED:
                return []
            prov = Provenance(source="system", step_no=step_no, ts_us=now_us)
            store.facts.set(f"confirm.{call.call_id}.asked", True, FactStatus.COMMITTED, prov, rule="responder.confirm_write")
            store.facts.set(f"goal.{goal_id}.awaiting_confirmation", call.call_id, FactStatus.COMMITTED, prov, rule="responder.confirm_write")
            values = ", ".join(str(v) for v in call.args.values())
            text = f"Shall I go ahead and {call.tool.replace('_', ' ')}" + (f" ({values})" if values else "") + "?"
            return [
                IntendedAction(
                    action_type=ActionType.SPEAK,
                    body=SpeakBody(text=text, kind="clarify"),
                    read_set=EMPTY_READ_SET,
                    rule_id="responder.confirm_write",
                )
            ]
        return []

    def _honest_reply(self, store, now_us: int, step_no: int) -> list[IntendedAction]:
        """Config.conversational_replies_enabled (kernel/replies.py): the
        one reply kernel/interpret_apply.py chose for a turn -- an honest
        "can't do that", a status summary, or a polite answer to thanks.
        Once per turn, and dropped if the user has already said more."""
        fact = store.facts.get(HONEST_REPLY_KEY)
        if fact is None or fact.status == FactStatus.RETRACTED or not isinstance(fact.value, dict):
            return []
        turn_id = fact.value.get("turn")
        already = store.facts.get(f"honest.{turn_id}.sent")
        if already is not None and already.status != FactStatus.RETRACTED:
            return []
        turns = store.turn_log.all()
        if turns and turns[-1].turn_id != turn_id:
            return []
        store.facts.set(
            f"honest.{turn_id}.sent", True, FactStatus.COMMITTED,
            Provenance(source="system", step_no=step_no, ts_us=now_us), rule="responder.honest_reply",
        )
        return [
            IntendedAction(
                action_type=ActionType.SPEAK,
                body=SpeakBody(text=fact.value.get("text") or "", kind="inform"),
                read_set=EMPTY_READ_SET,
                rule_id="responder.honest_reply",
            )
        ]

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
