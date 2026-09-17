"""SessionStore aggregate and the StoreTxn mutation guard.

One SessionStore per scenario/session — never shared or reused across
sessions (C2: session isolation, enforced by having no module-level mutable
state anywhere in this package).
"""

from __future__ import annotations

from prism_rt.config import Config
from prism_rt.ids import IdGenerator
from prism_rt.model.types import ChangeSet, FloorState
from prism_rt.store.catalog import ToolCatalog
from prism_rt.store.facts import DependencyIndex, FactStore, MutationGuard
from prism_rt.store.ledgers import (
    CallLedger,
    EffectLedger,
    EvidenceStore,
    GoalRegistry,
    JobTable,
    PlanStore,
    TimerWheel,
    TurnLog,
)


class SessionStore:
    """Owns every sub-store for one session. All of them share one
    MutationGuard, so a single `StoreTxn` gates mutation across the whole
    store (S1: single writer)."""

    def __init__(self, config: Config, ids: IdGenerator) -> None:
        self.config = config
        self.ids = ids
        self._guard = MutationGuard()
        self.facts = FactStore(self._guard)
        self.dep_index = DependencyIndex(self._guard)
        self.catalog = ToolCatalog(self._guard)
        self.call_ledger = CallLedger(self._guard)
        self.effect_ledger = EffectLedger(self._guard)
        self.goals = GoalRegistry(self._guard)
        self.plans = PlanStore(self._guard)
        self.turn_log = TurnLog(self._guard)
        self.jobs = JobTable(self._guard)
        self.timers = TimerWheel(self._guard)
        self.evidence = EvidenceStore(self._guard)
        self.floor_state = FloorState.USER_TURN_CLOSED

    @classmethod
    def new(cls, config: Config, ids: IdGenerator) -> "SessionStore":
        return cls(config, ids)

    def begin_txn(self, step_no: int) -> "StoreTxn":
        return StoreTxn(self, step_no)

    def set_floor(self, state: FloorState) -> None:
        """Mutates floor state; only valid while a txn guard is active."""
        self._guard.check()
        self.floor_state = state


class StoreTxn:
    """The single-writer boundary for one kernel step. Active (guard on)
    from construction until `commit()` — kernel step phases APPLY through
    COMMIT (K3). Use as a context manager so an exception mid-step still
    releases the guard."""

    def __init__(self, store: SessionStore, step_no: int) -> None:
        self.store = store
        self.step_no = step_no
        self._committed = False
        self.store._guard.active = True

    @property
    def facts(self) -> FactStore:
        return self.store.facts

    @property
    def catalog(self) -> ToolCatalog:
        return self.store.catalog

    @property
    def call_ledger(self) -> CallLedger:
        return self.store.call_ledger

    @property
    def effect_ledger(self) -> EffectLedger:
        return self.store.effect_ledger

    @property
    def dep_index(self) -> DependencyIndex:
        return self.store.dep_index

    @property
    def goals(self) -> GoalRegistry:
        return self.store.goals

    @property
    def plans(self) -> PlanStore:
        return self.store.plans

    @property
    def turn_log(self) -> TurnLog:
        return self.store.turn_log

    @property
    def jobs(self) -> JobTable:
        return self.store.jobs

    @property
    def timers(self) -> TimerWheel:
        return self.store.timers

    @property
    def evidence(self) -> EvidenceStore:
        return self.store.evidence

    def set_floor(self, state: FloorState) -> None:
        self.store.set_floor(state)

    def changed_keys(self) -> set[str]:
        """Keys changed so far this step (peek — does not clear). Used by
        the INVALIDATE phase, which runs before COMMIT drains them."""
        return self.store.facts.changed_keys_pending()

    def commit(self) -> ChangeSet:
        """Finalize this step's ChangeSet and close the mutation window.
        Facts were already live the moment each reducer called `.set()`;
        this only drains the pending-changes buffer into an immutable
        record for the decision log and closes the guard."""
        changes = self.store.facts.drain_changes()
        change_set = ChangeSet(
            step_no=self.step_no,
            changes=changes,
            new_revision=self.store.facts.revision(),
        )
        self._committed = True
        self.store._guard.active = False
        return change_set

    def __enter__(self) -> "StoreTxn":
        self.store._guard.active = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if not self._committed:
            self.store._guard.active = False
        return False
