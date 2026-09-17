"""CallLedger and EffectLedger — the V0 subset of the session store's
ledgers. GoalRegistry, PlanStore, and TurnLog are added in V1
(`docs/sonnet_implementation_plan.md` §1.1, Phase 11).
"""

from __future__ import annotations

import dataclasses

from prism_rt.model.types import (
    TERMINAL_CALL_STATUSES,
    CallRecord,
    CallStatus,
    EffectRecord,
    EffectStatus,
)
from prism_rt.store.facts import MutationGuard


class CallLedger:
    """Every tool call ever proposed, keyed by call_id. Insertion order is
    preserved for deterministic iteration (replay identity, C1)."""

    def __init__(self, guard: MutationGuard) -> None:
        self._guard = guard
        self._calls: dict[str, CallRecord] = {}
        self._order: list[str] = []

    def create(self, call: CallRecord) -> None:
        self._guard.check()
        if call.call_id in self._calls:
            from prism_rt.errors import InvariantViolation

            raise InvariantViolation(f"duplicate call_id: {call.call_id}")
        self._calls[call.call_id] = call
        self._order.append(call.call_id)

    def get(self, call_id: str) -> CallRecord | None:
        return self._calls.get(call_id)

    def update(self, call_id: str, **changes) -> CallRecord:
        self._guard.check()
        existing = self._calls.get(call_id)
        if existing is None:
            raise KeyError(call_id)
        updated = dataclasses.replace(existing, **changes)
        self._calls[call_id] = updated
        return updated

    def set_status(self, call_id: str, status: CallStatus, *, cancel_reason: str | None = None) -> CallRecord:
        changes: dict = {"status": status}
        if cancel_reason is not None:
            changes["cancel_reason"] = cancel_reason
        return self.update(call_id, **changes)

    def all(self) -> list[CallRecord]:
        return [self._calls[cid] for cid in self._order]

    def proposed(self) -> list[CallRecord]:
        return [c for c in self.all() if c.status == CallStatus.PROPOSED]

    def in_flight(self) -> list[CallRecord]:
        return [c for c in self.all() if c.status == CallStatus.IN_FLIGHT]

    def non_terminal(self) -> list[CallRecord]:
        return [c for c in self.all() if c.status not in TERMINAL_CALL_STATUSES]


class EffectLedger:
    """State-modifying effects, keyed by fingerprint and by lineage
    (`{goal_id}:{step_key}`) for the CommitGate G5/G6/G7 duplicate and
    unknown-outcome checks (§7.3, Appendix B)."""

    def __init__(self, guard: MutationGuard) -> None:
        self._guard = guard
        self._by_fingerprint: dict[str, EffectRecord] = {}
        self._by_lineage: dict[str, EffectRecord] = {}

    def create(self, effect: EffectRecord) -> None:
        self._guard.check()
        self._by_fingerprint[effect.fingerprint] = effect
        self._by_lineage[effect.lineage] = effect

    def by_fingerprint(self, fingerprint: str) -> EffectRecord | None:
        return self._by_fingerprint.get(fingerprint)

    def by_lineage(self, lineage: str) -> EffectRecord | None:
        return self._by_lineage.get(lineage)

    def set_status(self, fingerprint: str, status: EffectStatus) -> EffectRecord:
        self._guard.check()
        existing = self._by_fingerprint.get(fingerprint)
        if existing is None:
            raise KeyError(fingerprint)
        updated = dataclasses.replace(existing, status=status)
        self._by_fingerprint[fingerprint] = updated
        if self._by_lineage.get(updated.lineage) is not None and self._by_lineage[updated.lineage].fingerprint == fingerprint:
            self._by_lineage[updated.lineage] = updated
        return updated
