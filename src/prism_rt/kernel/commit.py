"""CommitGate: admission control for tool calls, G1-G9 (Appendix B of
`docs/sonnet_implementation_plan.md`).

G1, G2, G8 apply to every call. G3-G7 are write-only (§7.3: reads may be
speculated and retried freely; writes pass a single gate and are recorded
in the effect ledger before emission). G9 (read-set valid *at emission*) is
enforced by EmissionGate itself, not here — this method only evaluates the
call as of the decide phase.
"""

from __future__ import annotations

from dataclasses import dataclass

from prism_rt.model.actions import IntendedAction, ToolCallBody
from prism_rt.model.types import (
    BLOCKING_EFFECT_STATUSES,
    ActionType,
    CallRecord,
    EffectRecord,
    EffectStatus,
    FactStatus,
    FloorState,
    StepKind,
    ToolMutability,
)
from prism_rt.store.session import SessionStore


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    blocked_reason: str | None
    rule_id: str


class CommitGate:
    def evaluate(self, call: CallRecord, store: SessionStore, now_us: int) -> GateDecision:
        # G1: tool exists and is usable
        tool = store.catalog.get(call.tool)
        if tool is None or tool.status != "USABLE":
            return GateDecision(False, "tool_not_usable", "G1")

        # G2: read set valid
        if not store.facts.is_valid(call.read_set).is_valid:
            return GateDecision(False, "read_set_invalid", "G2")

        # G8: args valid against schema
        if not store.catalog.validate_args(call.tool, call.args).valid:
            return GateDecision(False, "args_invalid", "G8")

        if call.kind != StepKind.WRITE:
            return GateDecision(True, None, "G1,G2,G8")

        # --- write-only conditions ------------------------------------

        # G3: floor closed (no commit while the user may still be correcting)
        if store.floor_state != FloorState.USER_TURN_CLOSED:
            return GateDecision(False, "floor_open", "G3")

        # G4: commit_intent true for the call's goal
        intent_fact = store.facts.get(f"goal.{call.goal_id}.commit_intent")
        if intent_fact is None or intent_fact.status == FactStatus.RETRACTED or intent_fact.value is not True:
            return GateDecision(False, "commit_intent_false", "G4")

        # G5: no existing effect with the same fingerprint (non-FAILED)
        existing_fp = store.effect_ledger.by_fingerprint(call.fingerprint)
        if existing_fp is not None and existing_fp.status in BLOCKING_EFFECT_STATUSES:
            return GateDecision(False, "duplicate_fingerprint", "G5")

        # G6: no existing effect with the same lineage, PENDING or CONFIRMED
        lineage = f"{call.goal_id}:{call.step_key}"
        existing_lineage = store.effect_ledger.by_lineage(lineage)
        if existing_lineage is not None and existing_lineage.status in (
            EffectStatus.PENDING,
            EffectStatus.CONFIRMED,
        ):
            return GateDecision(False, "duplicate_lineage", "G6")

        # G7: no UNKNOWN effect for the same lineage (must reconcile first)
        if existing_lineage is not None and existing_lineage.status == EffectStatus.UNKNOWN:
            return GateDecision(False, "unknown_effect_outcome", "G7")

        return GateDecision(True, None, "G1-G8")

    def scan_and_admit(self, store: SessionStore, now_us: int) -> list[IntendedAction]:
        """Evaluate every PROPOSED call and turn the admitted ones into
        TOOL_CALL IntendedActions. A call that fails the gate simply stays
        PROPOSED — there is no separate Blocked status; it is re-evaluated
        on the next step once whatever blocked it may have changed."""
        actions: list[IntendedAction] = []
        for call in store.call_ledger.proposed():
            decision = self.evaluate(call, store, now_us)
            if not decision.allowed:
                continue

            if call.kind == StepKind.WRITE:
                lineage = f"{call.goal_id}:{call.step_key}"
                store.effect_ledger.create(
                    EffectRecord(
                        fingerprint=call.fingerprint,
                        lineage=lineage,
                        call_id=call.call_id,
                        status=EffectStatus.PENDING,
                    )
                )

            mutability = (
                ToolMutability.STATE_CHANGING if call.kind == StepKind.WRITE else ToolMutability.READ_ONLY
            )
            actions.append(
                IntendedAction(
                    action_type=ActionType.TOOL_CALL,
                    body=ToolCallBody(
                        call_id=call.call_id,
                        tool_name=call.tool,
                        arguments=call.args,
                        mutability=mutability,
                    ),
                    read_set=call.read_set,
                    rule_id=decision.rule_id,
                )
            )
        return actions
