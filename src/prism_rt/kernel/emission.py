"""EmissionGate: validate, order, stamp, and write every action. Nothing
reaches the harness except through here, and nothing here is buffered — an
action becomes stale only by failing this same-step validation, never by
sitting unvalidated somewhere (K3, P1).

Emission order: CANCEL -> SPEAK -> TOOL_CALL -> CLARIFY -> FINAL
(`docs/prompt 2.txt` §5.2).
"""

from __future__ import annotations

from dataclasses import dataclass

from prism_rt.adapters.output_writer import OutputWriter
from prism_rt.ids import IdGenerator
from prism_rt.kernel.snapshot import SnapshotProjector
from prism_rt.model.actions import (
    Action,
    CancelBody,
    FinalBody,
    IntendedAction,
    SpeakBody,
    ToolCallBody,
)
from prism_rt.model.types import TERMINAL_CALL_STATUSES, ActionType, CallStatus
from prism_rt.store.session import SessionStore

_EMISSION_ORDER = {
    ActionType.CANCEL: 0,
    ActionType.SPEAK: 1,
    ActionType.TOOL_CALL: 2,
    ActionType.CLARIFY: 3,
    ActionType.FINAL: 4,
}


@dataclass(frozen=True)
class EmittedRecord:
    action: Action


@dataclass(frozen=True)
class RejectedRecord:
    intended: IntendedAction
    reason_code: str


@dataclass(frozen=True)
class EmitReport:
    emitted: tuple[EmittedRecord, ...]
    rejected: tuple[RejectedRecord, ...]


class EmissionGate:
    def __init__(self, writer: OutputWriter) -> None:
        self._writer = writer
        self._snapshot_projector = SnapshotProjector()

    def emit(
        self,
        intended: list[IntendedAction],
        store: SessionStore,
        now_us: int,
        ids: IdGenerator,
    ) -> EmitReport:
        ordered = sorted(
            intended,
            key=lambda ia: (_EMISSION_ORDER.get(ia.action_type, 99),),
        )

        emitted: list[EmittedRecord] = []
        rejected: list[RejectedRecord] = []

        for ia in ordered:
            reason = self._validate(ia, store)
            if reason is not None:
                rejected.append(RejectedRecord(ia, reason))
                continue

            action_id = ids.next("action")
            snapshot = self._snapshot_projector.project(store) if ia.needs_snapshot else None
            action = Action(
                action_type=ia.action_type,
                action_id=action_id,
                ts_us=now_us,
                body=ia.body,
                read_set=ia.read_set,
                snapshot=snapshot,
                trigger_event_id=ia.trigger_event_id,
                rule_id=ia.rule_id,
            )

            write_result = self._writer.write(action)
            if not write_result.ok:
                rejected.append(RejectedRecord(ia, f"write_failed:{write_result.error}"))
                continue

            self._apply_side_effects(ia, store, now_us)
            emitted.append(EmittedRecord(action))

        return EmitReport(emitted=tuple(emitted), rejected=tuple(rejected))

    def _validate(self, ia: IntendedAction, store: SessionStore) -> str | None:
        # P1 / G9: every action's read set must still be valid right now.
        # CANCEL is exempt — it targets a call, not a read set of facts.
        if ia.action_type != ActionType.CANCEL:
            if not store.facts.is_valid(ia.read_set).is_valid:
                return "read_set_invalid"

        if ia.action_type == ActionType.TOOL_CALL:
            body = ia.body
            assert isinstance(body, ToolCallBody)
            existing = store.call_ledger.get(body.call_id)
            if existing is None:
                return "unknown_call_id"
            if existing.status != CallStatus.PROPOSED:
                return "call_not_proposed"
            tool = store.catalog.get(body.tool_name)
            if tool is None or tool.status != "USABLE":
                return "tool_not_usable"
            if not store.catalog.validate_args(body.tool_name, body.arguments).valid:
                return "args_invalid"

        elif ia.action_type == ActionType.CANCEL:
            body = ia.body
            assert isinstance(body, CancelBody)
            target = store.call_ledger.get(body.target_call_id)
            if target is None:
                return "unknown_cancel_target"
            if target.status in TERMINAL_CALL_STATUSES or target.status == CallStatus.CANCEL_REQUESTED:
                return "cancel_target_not_cancellable"

        elif isinstance(ia.body, (SpeakBody, FinalBody)):
            if not ia.body.text or not ia.body.text.strip():
                return "empty_text"

        return None

    def _apply_side_effects(self, ia: IntendedAction, store: SessionStore, now_us: int) -> None:
        if ia.action_type == ActionType.TOOL_CALL:
            body = ia.body
            assert isinstance(body, ToolCallBody)
            store.call_ledger.update(body.call_id, status=CallStatus.IN_FLIGHT, emitted_ts_us=now_us)
            store.dep_index.register(body.call_id, ia.read_set)
        elif ia.action_type == ActionType.CANCEL:
            body = ia.body
            assert isinstance(body, CancelBody)
            store.call_ledger.set_status(body.target_call_id, CallStatus.CANCEL_REQUESTED, cancel_reason=body.reason)
            store.dep_index.unregister(body.target_call_id)
