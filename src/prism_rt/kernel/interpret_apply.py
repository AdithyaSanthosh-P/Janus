"""interpret_apply: TurnInterpretation -> goal/fact mutations.

This is where a correction becomes an ordinary fact change — slot deltas
are written through `FactStore.set`, so the *existing*, unmodified
DependencyIndex/InvalidationEngine from V0 does the cancellation. Nothing
here needs to know what was in flight.

V1 had no rebinder: any slot change on an ACTIVE goal sent it back to
PLANNING for a fresh plan. V2 adds one (`config.rebinder_enabled`,
§8.4 "rebind first, plan second"): a purely-local correction — every
changed slot is FACT-bound in an existing step, and no affected step's
tool choice depends on that slot (`PlanStep.structure_depends_on`) —
stays in EXECUTING instead, letting the ordinary invalidation + rebind
cycle handle it with no Planner round-trip. Anything else (new goal,
structural change, not currently executing) still replans.
"""

from __future__ import annotations

from prism_rt.kernel.perception import PerceptionScheduler
from prism_rt.model.types import (
    BindingKind,
    CallStatus,
    EffectStatus,
    FactStatus,
    GoalRecord,
    GoalStatus,
    InterpretAct,
    Provenance,
    SlotOp,
    TaskState,
    TurnInterpretation,
)
from prism_rt.store.session import StoreTxn

_PERCEPTION = PerceptionScheduler()

_SLOT_UPDATE_ACTS = (
    InterpretAct.SLOT_UPDATE,
    InterpretAct.ADDITION,
    InterpretAct.ANSWER_CLARIFICATION,
    InterpretAct.CONFIRM,
    InterpretAct.DENY,
)


def active_goal_id(store) -> str | None:
    fact = store.facts.get("goal.active")
    if fact is None or fact.status == FactStatus.RETRACTED or not fact.value:
        return None
    return fact.value


def _slot_key(goal_id: str, delta) -> str:
    if delta.scope == "session":
        return f"session.{delta.name}"
    return f"slot.{goal_id}.{delta.name}"


def _can_rebind(store, goal_id: str, changed_names: set[str]) -> bool:
    """True iff every step the changed slots touch can just be re-bound
    against the new value (§8.4) — false if there's no plan to rebind
    against, or if any touched step's tool choice depends on the value
    (`structure_depends_on`), which needs a real replan instead."""
    if not changed_names:
        return False
    plan = store.plans.current(goal_id)
    if plan is None:
        return False

    prefix = f"slot.{goal_id}."
    referenced = False
    for step in plan.steps:
        step_slot_names = {
            binding.fact_key.replace("$G", goal_id)[len(prefix):]
            for binding in step.bindings.values()
            if binding.kind == BindingKind.FACT
            and binding.fact_key
            and binding.fact_key.replace("$G", goal_id).startswith(prefix)
        }
        touched = step_slot_names & changed_names
        if not touched:
            continue
        referenced = True
        if touched & set(step.structure_depends_on):
            return False
    return referenced


def _set_active_goal(txn: StoreTxn, goal_id: str | None, now_us: int, step_no: int, *, event_id: str) -> None:
    txn.facts.set(
        "goal.active",
        goal_id,
        FactStatus.COMMITTED,
        Provenance(source="system", event_id=event_id, step_no=step_no, ts_us=now_us),
        rule="interpret_apply.goal_active",
    )


def _suspend_current_goal(txn: StoreTxn) -> None:
    current = active_goal_id(txn.store)
    if current is None:
        return
    goal = txn.store.goals.get(current)
    if goal is not None and goal.status == GoalStatus.ACTIVE:
        txn.store.goals.update(current, status=GoalStatus.SUSPENDED)


def _apply_slot_deltas(txn: StoreTxn, goal_id: str, slot_deltas, now_us: int, step_no: int, *, event_id: str) -> bool:
    changed = False
    for delta in slot_deltas:
        key = _slot_key(goal_id, delta)
        if delta.op == SlotOp.CLEAR:
            if txn.facts.retract(key, rule="interpret_apply.slot_clear"):
                changed = True
        else:
            provenance = Provenance(source="user", event_id=event_id, step_no=step_no, ts_us=now_us)
            if txn.facts.set(key, delta.value, FactStatus.COMMITTED, provenance, rule="interpret_apply.slot_set"):
                changed = True
        # V3: a fresh user statement about a slot name resolves whatever
        # perception conflict was open on it (§9.1's "answer binding" —
        # "any delta on the target sets status answered").
        if delta.scope != "session" and txn.store.config.vision_enabled:
            txn.evidence.void_conflict(goal_id, delta.name)
    return changed


def _apply_visual_reference(
    txn: StoreTxn, interp: TurnInterpretation, goal_id: str, now_us: int, step_no: int
) -> None:
    """V3: an accepted interpretation whose `visual_reference` is set
    creates or retargets that goal's question (`docs/prompt 2.txt` §10.4
    "Demand" trigger — the only trigger this project implements; see
    `kernel/perception.py`'s module docstring). `anchor_ts_us` uses the
    turn's close time as a simplified stand-in for "timestamp of the chunk
    carrying the visual reference" (not tracked per-chunk)."""
    if not txn.store.config.vision_enabled or interp.visual_reference == "none":
        return
    turn = txn.store.turn_log.get(interp.turn_id)
    anchor_ts_us = turn.closed_ts_us if turn is not None and turn.closed_ts_us is not None else now_us
    _PERCEPTION.create_or_retarget_question(
        txn,
        goal_id,
        interp.visual_candidates,
        interp.visual_reference,
        anchor_ts_us=anchor_ts_us,
        created_by="demand",
    )


def _abandon_goal(txn: StoreTxn, goal_id: str, now_us: int, step_no: int, *, event_id: str) -> None:
    goal = txn.store.goals.get(goal_id)
    if goal is None:
        return

    # Abandonment has no single fact whose change should invalidate exactly
    # this goal's calls (clearing goal.active would also spuriously
    # invalidate a *different* goal's calls if one becomes active later the
    # same step, and FastResponder still needs goal.active to find this
    # goal to speak the abort FINAL). So the goal's own non-terminal calls
    # are invalidated directly here, same marking InvalidationEngine would
    # apply — InvalidationEngine.cancellation_actions picks them up from
    # the ledger regardless of how they got marked (P3 still holds: this
    # runs in the same step as the ABORT interpretation is applied).
    for call in txn.store.call_ledger.non_terminal():
        if call.goal_id != goal_id:
            continue
        if call.status == CallStatus.IN_FLIGHT:
            txn.store.call_ledger.set_status(call.call_id, CallStatus.INVALIDATED)
            txn.store.dep_index.unregister(call.call_id)
        elif call.status == CallStatus.PROPOSED:
            txn.store.call_ledger.set_status(call.call_id, CallStatus.DISCARDED)

    txn.store.goals.update(goal_id, status=GoalStatus.ABANDONED, task_state=TaskState.RESPONDING)
    txn.facts.set(
        f"compose.{goal_id}.text",
        "Okay, cancelled.",
        FactStatus.COMMITTED,
        Provenance(source="system", event_id=event_id, step_no=step_no, ts_us=now_us),
        rule="interpret_apply.abort_final",
    )


def grounded_compose_text(store, goal_id: str):
    """The goal's composed final text, or None if there is none *or* it was
    grounded in facts that have since changed. A COMPOSE result (or a
    rendered response frame) records the read set it was built from as
    `derivation_read_set`; once that is invalid, the text describes
    results the user has since corrected away from and must never be
    spoken -- callers treat it exactly like "no text yet" and compose again.
    Found by `sim/explorer.py`: a COMPOSE job grounded in the Pune result
    landed the instant the user said "actually Mumbai"; after the Mumbai
    search completed, the existing text suppressed a fresh COMPOSE and the
    FINAL spoke the Pune-grounded text under a Mumbai snapshot. Texts with
    no derivation read set (abort, watchdog salvage, honest failure) are
    not grounded in results and are always usable."""
    fact = store.facts.get(f"compose.{goal_id}.text")
    if fact is None or fact.status == FactStatus.RETRACTED:
        return None
    grounding = fact.provenance.derivation_read_set
    if grounding is not None and not store.facts.is_valid(grounding).is_valid:
        return None
    return fact


def set_grounded_compose_text(txn: StoreTxn, goal_id: str, text: str, provenance: Provenance, *, rule: str) -> None:
    """Write a result-grounded final text. `FactStore.set` is a no-op when
    value and status are unchanged (D4) -- so a re-compose that produces
    the *same words* from *new* results would keep the old, stale
    grounding, and `grounded_compose_text` would reject it forever (found
    by the explorer as a COMPOSE re-dispatch livelock). Retract first
    whenever the grounding differs, so the new grounding always lands."""
    key = f"compose.{goal_id}.text"
    existing = txn.facts.get(key)
    if (
        existing is not None
        and existing.status != FactStatus.RETRACTED
        and existing.provenance.derivation_read_set != provenance.derivation_read_set
    ):
        txn.facts.retract(key, rule=f"{rule}.regrounded")
    txn.facts.set(key, text, FactStatus.COMMITTED, provenance, rule=rule)


_PENDING = "session.pending_interpretation_turn"
_QUEUED = "session.queued_interpretation_turns"


def _active(fact) -> bool:
    return fact is not None and fact.status != FactStatus.RETRACTED


def interpretation_busy(store) -> bool:
    """True while some closed user turn is still awaiting interpretation --
    either the ordinary pending marker, or a turn waiting on its own
    in-flight speculative job (Phase 6's `eot_waiting`). While this holds,
    the turn might be a correction, so nothing that could go stale because
    of it may take effect: this is the TRIAGE hold (`docs/prompt 2.txt`
    §8.3), checked by `FastResponder` (speech) and `CommitGate` (writes)."""
    if _active(store.facts.get(_PENDING)):
        return True
    return any(
        key.endswith(".eot_waiting") and _active(fact) and fact.value
        for key, fact in store.facts.by_prefix("spec_interpret.").items()
    )


def audio_covered_by_text(store, obs) -> bool:
    """AUTO mode (`docs/prompt 2.txt` line 909): the harness's own text
    transcript covers this clip's capture window, so its ASR output will be
    discarded -- and since later text can never un-cover it, that is known
    the moment the covering chunk arrives."""
    if store.config.audio_mode != "auto":
        return False
    lo = obs.capture_ts_us
    hi = lo + store.config.asr_dedupe_window_ms * 1000
    return any(
        chunk.source == "text" and lo <= chunk.ts_us <= hi
        for turn in store.turn_log.all()
        for chunk in turn.chunks
    )


def transcription_pending(store) -> bool:
    """An audio clip the user spoke hasn't been transcribed yet -- user
    content not yet understood, same as an uninterpreted turn. A clip
    already covered by text isn't pending: the text *is* its content."""
    if not store.config.asr_enabled or store.config.audio_mode == "transcript_primary":
        return False
    return any(
        not obs.asr_done and not audio_covered_by_text(store, obs)
        for obs in store.evidence.observations_by_modality("audio")
    )


def user_content_pending(store) -> bool:
    """What the TRIAGE hold waits on (`docs/prompt 2.txt` transition 23:
    "Interruption signal, or user content while a goal is active"): a
    closed turn awaiting interpretation, *or* speech still being
    transcribed. Found by `sim/explorer.py` (A2): with only the former, a
    FINAL for "Pune" went out while the user's next clip -- "actually
    Mumbai" -- was still in ASR. Deliberately separate from
    `interpretation_busy`, which orders the interpretation queue and must
    not wait on ASR (the queue only advances when interpretations land)."""
    return interpretation_busy(store) or transcription_pending(store)


def enqueue_interpretation(txn: StoreTxn, turn_id: str, provenance: Provenance, *, rule: str) -> None:
    """Request interpretation of `turn_id`. If an earlier turn is still
    being interpreted, queue behind it rather than overwrite it -- found by
    `sim/explorer.py`: "mm-hmm" closing before "Find flights to Pune" had
    been interpreted replaced the pending marker, and the first request's
    result was then silently discarded (spec transition 7: new user content
    before interpretation completes is a rapid follow-up, not a
    replacement). Turns are interpreted strictly in order."""
    if interpretation_busy(txn.store):
        queued = txn.facts.get(_QUEUED)
        current = tuple(queued.value) if _active(queued) else ()
        txn.facts.set(_QUEUED, current + (turn_id,), FactStatus.COMMITTED, provenance, rule=rule)
        return
    txn.facts.set(_PENDING, turn_id, FactStatus.COMMITTED, provenance, rule=rule)


def advance_interpretation_queue(txn: StoreTxn, now_us: int, step_no: int, *, event_id: str) -> None:
    """Once nothing is outstanding, promote the next queued turn (if any)
    to pending, so TaskStateMachine dispatches its INTERPRET job."""
    if interpretation_busy(txn.store):
        return
    queued = txn.facts.get(_QUEUED)
    if not _active(queued) or not queued.value:
        return
    head, rest = queued.value[0], tuple(queued.value[1:])
    prov = Provenance(source="user", event_id=event_id, turn_id=head, step_no=step_no, ts_us=now_us)
    txn.facts.set(_PENDING, head, FactStatus.COMMITTED, prov, rule="interpret_apply.dequeued")
    if rest:
        txn.facts.set(_QUEUED, rest, FactStatus.COMMITTED, prov, rule="interpret_apply.dequeued")
    else:
        txn.facts.retract(_QUEUED, rule="interpret_apply.dequeued")


def apply_interpretation(interp: TurnInterpretation, txn: StoreTxn, now_us: int, step_no: int, *, event_id: str) -> str | None:
    """Returns the goal_id this interpretation ended up affecting, if any."""
    # Read-set validity was already checked before this is called (the
    # WORKER_RESULT handler rejects a stale proposal upstream). Consume the
    # pending marker only if it names *this* turn -- a speculative/eot-
    # waiting application must never consume a different turn's marker.
    pending = txn.facts.get(_PENDING)
    if _active(pending) and pending.value == interp.turn_id:
        txn.facts.retract(_PENDING, rule="interpret_apply.consumed")

    if interp.act == InterpretAct.ABORT:
        gid = active_goal_id(txn.store)
        if gid is not None:
            _abandon_goal(txn, gid, now_us, step_no, event_id=event_id)
        return gid

    if interp.act == InterpretAct.RETURN_TO_GOAL and interp.resume_goal_id:
        target = txn.store.goals.get(interp.resume_goal_id)
        if target is None or target.status != GoalStatus.SUSPENDED:
            return active_goal_id(txn.store)  # nothing sane to resume; drop
        _suspend_current_goal(txn)
        txn.store.goals.update(interp.resume_goal_id, status=GoalStatus.ACTIVE)
        _set_active_goal(txn, interp.resume_goal_id, now_us, step_no, event_id=event_id)
        gid = interp.resume_goal_id
        _apply_slot_deltas(txn, gid, interp.slot_deltas, now_us, step_no, event_id=event_id)
        if interp.commit_intent:
            txn.facts.set(
                f"goal.{gid}.commit_intent",
                True,
                FactStatus.COMMITTED,
                Provenance(source="user", event_id=event_id, step_no=step_no, ts_us=now_us),
                rule="interpret_apply.commit_intent",
            )
        txn.store.goals.update(gid, task_state=TaskState.PLANNING)
        return gid

    if interp.act == InterpretAct.NEW_GOAL:
        _suspend_current_goal(txn)
        gid = txn.store.ids.next("goal")
        txn.store.goals.create(
            GoalRecord(
                goal_id=gid,
                status=GoalStatus.ACTIVE,
                created_turn=interp.turn_id,
                created_step=step_no,
                task_state=TaskState.PLANNING,
            )
        )
        _set_active_goal(txn, gid, now_us, step_no, event_id=event_id)
        if interp.intent:
            txn.facts.set(
                f"goal.{gid}.intent",
                interp.intent,
                FactStatus.COMMITTED,
                Provenance(source="user", event_id=event_id, step_no=step_no, ts_us=now_us),
                rule="interpret_apply.intent",
            )
        # Day 2 WP2 (docs/fdb_v3_day2_plan.md): every tool the turn asked
        # for, in order -- tells the Planner to emit one step per action
        # instead of just one for `intent`. Additive fact; a plan built
        # without this flag never reads it.
        if txn.store.config.multi_action_enabled and interp.requested_actions:
            txn.facts.set(
                f"goal.{gid}.actions",
                list(interp.requested_actions),
                FactStatus.COMMITTED,
                Provenance(source="user", event_id=event_id, step_no=step_no, ts_us=now_us),
                rule="interpret_apply.requested_actions",
            )
        _apply_slot_deltas(txn, gid, interp.slot_deltas, now_us, step_no, event_id=event_id)
        if interp.commit_intent:
            txn.facts.set(
                f"goal.{gid}.commit_intent",
                True,
                FactStatus.COMMITTED,
                Provenance(source="user", event_id=event_id, step_no=step_no, ts_us=now_us),
                rule="interpret_apply.commit_intent",
            )
        _apply_visual_reference(txn, interp, gid, now_us, step_no)
        return gid

    if interp.act in _SLOT_UPDATE_ACTS:
        gid = active_goal_id(txn.store)
        if gid is None:
            return None
        goal = txn.store.goals.get(gid)
        if goal is None or goal.status != GoalStatus.ACTIVE:
            return gid

        changed = _apply_slot_deltas(txn, gid, interp.slot_deltas, now_us, step_no, event_id=event_id)

        if interp.act == InterpretAct.DENY:
            txn.facts.set(
                f"goal.{gid}.commit_intent",
                False,
                FactStatus.COMMITTED,
                Provenance(source="user", event_id=event_id, step_no=step_no, ts_us=now_us),
                rule="interpret_apply.deny",
            )
        elif interp.commit_intent:
            txn.facts.set(
                f"goal.{gid}.commit_intent",
                True,
                FactStatus.COMMITTED,
                Provenance(source="user", event_id=event_id, step_no=step_no, ts_us=now_us),
                rule="interpret_apply.commit_intent",
            )

        if interp.act in (InterpretAct.ANSWER_CLARIFICATION, InterpretAct.CONFIRM, InterpretAct.DENY) or changed:
            # P0.4 (S-02 / blueprint §5.10's `write_unknown` reconcile
            # item, docs/original_design_audit.md D4): a `retry:<lineage>`
            # clarify_target (kernel/executor.py.PlanExecutor.
            # propose_ready_calls' CANCEL_REQUESTED/"deadline_expired"
            # branch, set when a write's deadline expired with a
            # genuinely unknown outcome) is a yes/no confirmation, not a
            # missing-slot question. CONFIRM/ANSWER_CLARIFICATION ("yes")
            # flips the blocking UNKNOWN effect to FAILED -- mirroring
            # RECONCILE.USER_RETRY -- so that same branch, re-examining
            # the step on the next replan, takes its "already resolved"
            # path and proposes exactly one further attempt. DENY ("no")
            # still ends the wait (retracted below either way) but leaves
            # the effect UNKNOWN, permanently unresolved -- CONFIRM/
            # ANSWER_CLARIFICATION are the only acts this project builds
            # a path to actually retry through.
            target_fact = txn.facts.get(f"goal.{gid}.clarify_target")
            if (
                target_fact is not None
                and target_fact.status != FactStatus.RETRACTED
                and isinstance(target_fact.value, str)
                and target_fact.value.startswith("retry:")
            ):
                lineage = target_fact.value[len("retry:") :]
                if interp.act == InterpretAct.DENY:
                    # Recorded so PlanExecutor's CANCEL_REQUESTED/
                    # "deadline_expired" branch stops re-asking once
                    # declined -- without this, the goal returns to
                    # PLANNING (below), re-examines the same still-
                    # UNKNOWN effect, and asks again forever.
                    txn.facts.set(
                        f"retry_declined.{lineage}",
                        True,
                        FactStatus.COMMITTED,
                        Provenance(source="user", event_id=event_id, step_no=step_no, ts_us=now_us),
                        rule="interpret_apply.retry_declined",
                    )
                else:
                    effect = txn.store.effect_ledger.by_lineage(lineage)
                    if effect is not None and effect.status == EffectStatus.UNKNOWN:
                        txn.store.effect_ledger.set_status(effect.fingerprint, EffectStatus.FAILED)
            txn.facts.retract(f"goal.{gid}.clarify_target", rule="interpret_apply.clarify_resolved")
        if changed and goal.task_state not in (TaskState.COMPLETED, TaskState.FAILED):
            changed_names = {d.name for d in interp.slot_deltas if d.scope != "session"}
            if (
                txn.store.config.rebinder_enabled
                and goal.task_state == TaskState.EXECUTING
                and _can_rebind(txn.store, gid, changed_names)
            ):
                # Purely a fact change a bound step already reads — leave
                # task_state as EXECUTING. The slot write above already
                # invalidated the stale call through the ordinary
                # DependencyIndex mechanism (§8.4: "rebind first, plan
                # second"); PlanExecutor re-binds it against the new value
                # next DECIDE phase, same step, no model round-trip.
                pass
            else:
                txn.store.goals.update(gid, task_state=TaskState.PLANNING)
        _apply_visual_reference(txn, interp, gid, now_us, step_no)
        return gid

    # BACKCHANNEL / SMALLTALK / UNCLEAR: nothing further to do.
    return active_goal_id(txn.store)
