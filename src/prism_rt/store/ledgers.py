"""CallLedger, EffectLedger (V0), and the V1 additions: GoalRegistry,
PlanStore, TurnLog, JobTable (`docs/sonnet_implementation_plan.md` §1.1,
Phase 11).
"""

from __future__ import annotations

import dataclasses

from prism_rt.model.types import (
    TERMINAL_CALL_STATUSES,
    CallRecord,
    CallStatus,
    ChunkRecord,
    EffectRecord,
    EffectStatus,
    GoalRecord,
    GoalStatus,
    JobKind,
    JobRecord,
    JobStatus,
    Plan,
    Turn,
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

    def by_step(self, goal_id: str, step_key: str) -> list[CallRecord]:
        """All attempts (retry lineage) for one plan step, in creation order."""
        return [c for c in self.all() if c.goal_id == goal_id and c.step_key == step_key]

    def latest_by_step(self, goal_id: str, step_key: str) -> CallRecord | None:
        attempts = self.by_step(goal_id, step_key)
        return attempts[-1] if attempts else None


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


class GoalRegistry:
    """Every goal ever created, keyed by goal_id. Only one goal is
    GoalStatus.ACTIVE at a time — tracked separately as fact `goal.active`
    (Appendix A), not derived from here, since read sets need to depend on
    it directly."""

    def __init__(self, guard: MutationGuard) -> None:
        self._guard = guard
        self._goals: dict[str, GoalRecord] = {}
        self._order: list[str] = []

    def create(self, goal: GoalRecord) -> None:
        self._guard.check()
        if goal.goal_id in self._goals:
            from prism_rt.errors import InvariantViolation

            raise InvariantViolation(f"duplicate goal_id: {goal.goal_id}")
        self._goals[goal.goal_id] = goal
        self._order.append(goal.goal_id)

    def get(self, goal_id: str) -> GoalRecord | None:
        return self._goals.get(goal_id)

    def update(self, goal_id: str, **changes) -> GoalRecord:
        self._guard.check()
        existing = self._goals.get(goal_id)
        if existing is None:
            raise KeyError(goal_id)
        updated = dataclasses.replace(existing, **changes)
        self._goals[goal_id] = updated
        return updated

    def all(self) -> list[GoalRecord]:
        return [self._goals[gid] for gid in self._order]

    def suspended(self) -> list[GoalRecord]:
        return [g for g in self.all() if g.status == GoalStatus.SUSPENDED]


class PlanStore:
    """Plans by (goal_id, plan_rev). `current(goal_id)` is the latest
    revision — the only one PlanExecutor ever reads from."""

    def __init__(self, guard: MutationGuard) -> None:
        self._guard = guard
        self._plans: dict[tuple[str, int], Plan] = {}
        self._current_rev: dict[str, int] = {}

    def create(self, plan: Plan) -> None:
        self._guard.check()
        self._plans[(plan.goal_id, plan.plan_rev)] = plan
        self._current_rev[plan.goal_id] = plan.plan_rev

    def get(self, goal_id: str, plan_rev: int) -> Plan | None:
        return self._plans.get((goal_id, plan_rev))

    def current(self, goal_id: str) -> Plan | None:
        rev = self._current_rev.get(goal_id)
        return self._plans.get((goal_id, rev)) if rev is not None else None

    def current_rev(self, goal_id: str) -> int:
        return self._current_rev.get(goal_id, 0)

    def next_rev(self, goal_id: str) -> int:
        return self._current_rev.get(goal_id, 0) + 1


class TurnLog:
    """User turns. Exactly one turn may be open (unclosed) at a time —
    `open_turn_id()` is how TurnManager finds it without a separate fact."""

    def __init__(self, guard: MutationGuard) -> None:
        self._guard = guard
        self._turns: dict[str, Turn] = {}
        self._order: list[str] = []
        self._open_turn_id: str | None = None

    def open_turn(
        self, turn_id: str, ts_us: int, *, is_interruption: bool = False, during_goal: str | None = None
    ) -> Turn:
        self._guard.check()
        turn = Turn(turn_id=turn_id, opened_ts_us=ts_us, is_interruption=is_interruption, during_goal=during_goal)
        self._turns[turn_id] = turn
        self._order.append(turn_id)
        self._open_turn_id = turn_id
        return turn

    def append_chunk(self, turn_id: str, ts_us: int, text: str) -> Turn:
        self._guard.check()
        turn = self._turns[turn_id]
        updated = dataclasses.replace(turn, chunks=turn.chunks + (ChunkRecord(ts_us=ts_us, text=text),))
        self._turns[turn_id] = updated
        return updated

    def close_turn(self, turn_id: str, ts_us: int) -> Turn:
        self._guard.check()
        updated = dataclasses.replace(self._turns[turn_id], closed_ts_us=ts_us)
        self._turns[turn_id] = updated
        if self._open_turn_id == turn_id:
            self._open_turn_id = None
        return updated

    def get(self, turn_id: str) -> Turn | None:
        return self._turns.get(turn_id)

    def open_turn_id(self) -> str | None:
        return self._open_turn_id

    def all(self) -> list[Turn]:
        return [self._turns[tid] for tid in self._order]


class JobTable:
    """Bookkeeping for dispatched worker jobs — lets TaskStateMachine avoid
    re-dispatching a job whose kind is already running for a goal/turn.
    Acceptance of a job's eventual result never depends on this table; that
    is decided by read-set validity alone (K4)."""

    def __init__(self, guard: MutationGuard) -> None:
        self._guard = guard
        self._jobs: dict[str, JobRecord] = {}

    def create(self, job: JobRecord) -> None:
        self._guard.check()
        self._jobs[job.job_id] = job

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def set_status(self, job_id: str, status: JobStatus) -> JobRecord:
        self._guard.check()
        updated = dataclasses.replace(self._jobs[job_id], status=status)
        self._jobs[job_id] = updated
        return updated

    def running_by_kind_goal(self, kind: JobKind, goal_id: str | None) -> list[JobRecord]:
        return [j for j in self._jobs.values() if j.kind == kind and j.goal_id == goal_id and j.status == JobStatus.RUNNING]

    def running_by_kind_turn(self, kind: JobKind, turn_id: str | None) -> list[JobRecord]:
        return [j for j in self._jobs.values() if j.kind == kind and j.turn_id == turn_id and j.status == JobStatus.RUNNING]
