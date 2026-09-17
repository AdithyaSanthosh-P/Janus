"""InvalidationEngine: map changed fact keys to affected in-flight/proposed
calls and mark them, in the same step the facts changed (P3).

V0 is non-transitive: only direct dependents registered in the
DependencyIndex are marked. Transitive retraction through derived facts is
a V2 addition (`docs/sonnet_implementation_plan.md` §4, VERSION 2).
"""

from __future__ import annotations

from dataclasses import dataclass

from prism_rt.model.actions import CancelBody, IntendedAction
from prism_rt.model.types import EMPTY_READ_SET, TERMINAL_CALL_STATUSES, ActionType, CallStatus
from prism_rt.store.session import SessionStore


@dataclass(frozen=True)
class InvalidationReport:
    changed_keys: frozenset[str]
    invalidated_call_ids: tuple[str, ...]
    discarded_call_ids: tuple[str, ...]


class InvalidationEngine:
    def invalidate(self, changed_keys: set[str], store: SessionStore) -> InvalidationReport:
        if not changed_keys:
            return InvalidationReport(frozenset(), (), ())

        dep_ids = store.dep_index.dependents(changed_keys)
        invalidated: list[str] = []
        discarded: list[str] = []

        for dep_id in dep_ids:
            call = store.call_ledger.get(dep_id)
            if call is None:
                continue  # a dependent that isn't a call (future: jobs, utterances)
            if call.status in TERMINAL_CALL_STATUSES:
                continue  # terminal calls never change state again

            if call.status == CallStatus.IN_FLIGHT:
                store.call_ledger.set_status(dep_id, CallStatus.INVALIDATED)
                invalidated.append(dep_id)
            elif call.status == CallStatus.PROPOSED:
                # Never emitted; nothing to cancel, just drop it (§8.1: "Dropped").
                store.call_ledger.set_status(dep_id, CallStatus.DISCARDED)
                discarded.append(dep_id)
            # CANCEL_REQUESTED calls: a cancel is already pending; leave as-is.

            store.dep_index.unregister(dep_id)

        return InvalidationReport(
            changed_keys=frozenset(changed_keys),
            invalidated_call_ids=tuple(invalidated),
            discarded_call_ids=tuple(discarded),
        )

    def cancellation_actions(self, report: InvalidationReport, store: SessionStore) -> list[IntendedAction]:
        """CANCEL is emitted for every call marked INVALIDATED this step —
        DISCARDED calls were never emitted, so there is nothing to cancel."""
        actions = []
        for call_id in report.invalidated_call_ids:
            call = store.call_ledger.get(call_id)
            if call is None or call.status != CallStatus.INVALIDATED:
                continue
            actions.append(
                IntendedAction(
                    action_type=ActionType.CANCEL,
                    body=CancelBody(target_call_id=call_id, reason="read_set_invalidated"),
                    read_set=EMPTY_READ_SET,
                    rule_id="P3",
                )
            )
        return actions
