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
    def invalidate(self, changed_keys: set[str], store: SessionStore, *, txn=None) -> InvalidationReport:
        """`txn` is only needed for the V2 transitive pass (it retracts
        stale derived facts, a mutation) — V0/V1 callers that never enable
        `config.transitive_invalidation` can omit it."""
        if not changed_keys:
            return InvalidationReport(frozenset(), (), ())

        all_changed = set(changed_keys)
        invalidated: list[str] = []
        discarded: list[str] = []

        invalidated.extend(self._mark_dependents(all_changed, store, discarded))

        if txn is not None and store.config.transitive_invalidation:
            # Fixpoint: a fact derived from something that just went stale
            # is itself stale, and anything that read *it* cascades the
            # same way — search -> derived.selected_flight -> get_seat_map,
            # all cancelled in this same step. Bounded to 8 rounds (a
            # correction's dependency chain is never realistically deeper).
            for _round in range(8):
                newly_retracted = self._retract_stale_derived(all_changed, store, txn)
                if not newly_retracted:
                    break
                all_changed.update(newly_retracted)
                invalidated.extend(self._mark_dependents(set(newly_retracted), store, discarded))

        return InvalidationReport(
            changed_keys=frozenset(all_changed),
            invalidated_call_ids=tuple(invalidated),
            discarded_call_ids=tuple(discarded),
        )

    def _mark_dependents(self, keys: set[str], store: SessionStore, discarded: list[str]) -> list[str]:
        dep_ids = store.dep_index.dependents(keys)
        invalidated: list[str] = []
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
        return invalidated

    def _retract_stale_derived(self, changed_keys: set[str], store: SessionStore, txn) -> list[str]:
        retracted: list[str] = []
        for fact in store.facts.all_derived():
            if fact.key in changed_keys:
                continue  # already accounted for this round
            read_set = fact.provenance.derivation_read_set
            if read_set is None:
                continue
            if store.facts.is_valid(read_set).is_valid:
                continue
            if txn.facts.retract(fact.key, rule="invalidation.transitive"):
                retracted.append(fact.key)
        return retracted

    def cancellation_actions(self, report: InvalidationReport, store: SessionStore) -> list[IntendedAction]:
        """CANCEL is emitted for every call currently marked INVALIDATED —
        scanning the ledger directly (not just `report.invalidated_call_ids`)
        so this also covers calls a reducer invalidated directly rather than
        through a fact change this engine tracked (V1: goal abandonment,
        which has no single fact whose change alone should invalidate every
        other goal's calls too — see kernel/interpret_apply.py's
        `_abandon_goal`). DISCARDED calls were never emitted, so there is
        nothing to cancel for those."""
        actions = []
        for call in store.call_ledger.all():
            if call.status != CallStatus.INVALIDATED:
                continue
            actions.append(
                IntendedAction(
                    action_type=ActionType.CANCEL,
                    body=CancelBody(target_call_id=call.call_id, reason="read_set_invalidated"),
                    read_set=EMPTY_READ_SET,
                    rule_id="P3",
                )
            )
        return actions
