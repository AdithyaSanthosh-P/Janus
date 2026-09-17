"""ResultRouter: decide whether an inbound tool result is still relevant,
per the seven branches in Appendix C of `docs/sonnet_implementation_plan.md`
(matching `docs/prompt 2.txt` §3.6). Checks apply in order; the first one
that decides terminates the procedure.
"""

from __future__ import annotations

from dataclasses import dataclass

from prism_rt.model.events import ToolResultPayload
from prism_rt.model.types import (
    CallStatus,
    EffectStatus,
    FactStatus,
    Provenance,
    StepKind,
    TERMINAL_CALL_STATUSES,
)
from prism_rt.store.session import SessionStore


@dataclass(frozen=True)
class RouteOutcome:
    call_id: str | None
    branch: str
    new_status: CallStatus | None
    rule_id: str


def _active_goal_id(store: SessionStore) -> str | None:
    fact = store.facts.get("goal.active")
    if fact is None or fact.status == FactStatus.RETRACTED:
        return None
    return fact.value


def _settle_pending_effect(store: SessionStore, call, ok: bool) -> None:
    """A write's effect can never be un-confirmed once we stop tracking it
    as PENDING — that is what W4 (no false undo claims) and reconciliation
    depend on. ok=False maps to FAILED/UNKNOWN rather than silently leaving
    PENDING, so a later CommitGate scan doesn't treat the lineage as free."""
    if call.kind != StepKind.WRITE:
        return
    effect = store.effect_ledger.by_fingerprint(call.fingerprint)
    if effect is None or effect.status != EffectStatus.PENDING:
        return
    store.effect_ledger.set_status(call.fingerprint, EffectStatus.CONFIRMED if ok else EffectStatus.UNKNOWN)


class ResultRouter:
    def route(
        self,
        result: ToolResultPayload,
        store: SessionStore,
        now_us: int,
        *,
        step_no: int = 0,
        event_id: str = "",
    ) -> RouteOutcome:
        call = store.call_ledger.get(result.call_id)

        # Branch 1: unknown call_id
        if call is None:
            return RouteOutcome(result.call_id, "unknown_call", None, "RES.UNKNOWN_CALL")

        # Branch 2: call already terminal -> duplicate delivery
        if call.status in TERMINAL_CALL_STATUSES:
            return RouteOutcome(call.call_id, "duplicate", None, "RES.DUPLICATE")

        # Branch 3: malformed payload
        if result.status not in ("ok", "error"):
            store.call_ledger.set_status(call.call_id, CallStatus.FAILED)
            _settle_pending_effect(store, call, ok=False)
            return RouteOutcome(call.call_id, "malformed", CallStatus.FAILED, "RES.MALFORMED")
        if result.status == "ok" and result.result is None:
            store.call_ledger.set_status(call.call_id, CallStatus.FAILED)
            _settle_pending_effect(store, call, ok=False)
            return RouteOutcome(call.call_id, "malformed", CallStatus.FAILED, "RES.MALFORMED")

        # Branch 4: cancellation already requested for this call
        if call.status == CallStatus.CANCEL_REQUESTED:
            store.call_ledger.set_status(call.call_id, CallStatus.COMPLETED_AFTER_CANCEL)
            _settle_pending_effect(store, call, ok=(result.status == "ok"))
            return RouteOutcome(
                call.call_id, "completed_after_cancel", CallStatus.COMPLETED_AFTER_CANCEL, "RES.COMPLETED_AFTER_CANCEL"
            )

        # Branch 5: call's goal is no longer the active goal
        active_goal_id = _active_goal_id(store)
        if active_goal_id is not None and call.goal_id != active_goal_id:
            store.call_ledger.set_status(call.call_id, CallStatus.RETAINED)
            _settle_pending_effect(store, call, ok=(result.status == "ok"))
            return RouteOutcome(call.call_id, "retained", CallStatus.RETAINED, "RES.RETAINED")

        # Branch 6: call's read set is no longer valid
        if not store.facts.is_valid(call.read_set).is_valid:
            store.call_ledger.set_status(call.call_id, CallStatus.STALE)
            _settle_pending_effect(store, call, ok=(result.status == "ok"))
            return RouteOutcome(call.call_id, "stale", CallStatus.STALE, "RES.STALE")

        # Branch 7: relevant -> consume
        if result.status == "ok":
            store.facts.set(
                f"result.{call.call_id}",
                result.result,
                FactStatus.COMMITTED,
                Provenance(source="tool", event_id=event_id, call_id=call.call_id, step_no=step_no, ts_us=now_us),
                rule="results.consumed",
            )
            store.call_ledger.set_status(call.call_id, CallStatus.CONSUMED)
            _settle_pending_effect(store, call, ok=True)
            return RouteOutcome(call.call_id, "consumed", CallStatus.CONSUMED, "RES.CONSUMED")

        # result.status == "error": tool executed but failed
        retryable = bool((result.error or {}).get("retryable"))
        store.call_ledger.set_status(call.call_id, CallStatus.FAILED)
        if call.kind == StepKind.WRITE:
            effect = store.effect_ledger.by_fingerprint(call.fingerprint)
            if effect is not None and effect.status == EffectStatus.PENDING:
                store.effect_ledger.set_status(
                    call.fingerprint, EffectStatus.FAILED if retryable else EffectStatus.UNKNOWN
                )
        return RouteOutcome(call.call_id, "consumed", CallStatus.FAILED, "RES.CONSUMED_ERROR")
