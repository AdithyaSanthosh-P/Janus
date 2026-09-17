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

from prism_rt.model.types import (
    BindingKind,
    CallStatus,
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
    return changed


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


def apply_interpretation(interp: TurnInterpretation, txn: StoreTxn, now_us: int, step_no: int, *, event_id: str) -> str | None:
    """Returns the goal_id this interpretation ended up affecting, if any."""
    # Read-set validity was already checked before this is called (the
    # WORKER_RESULT handler rejects a stale proposal upstream), so it's
    # always safe to consume the marker unconditionally here.
    txn.facts.retract("session.pending_interpretation_turn", rule="interpret_apply.consumed")

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
        _apply_slot_deltas(txn, gid, interp.slot_deltas, now_us, step_no, event_id=event_id)
        if interp.commit_intent:
            txn.facts.set(
                f"goal.{gid}.commit_intent",
                True,
                FactStatus.COMMITTED,
                Provenance(source="user", event_id=event_id, step_no=step_no, ts_us=now_us),
                rule="interpret_apply.commit_intent",
            )
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

        if interp.act == InterpretAct.ANSWER_CLARIFICATION or changed:
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
        return gid

    # BACKCHANNEL / SMALLTALK / UNCLEAR: nothing further to do.
    return active_goal_id(txn.store)
