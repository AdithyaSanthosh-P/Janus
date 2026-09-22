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
    Conflict,
    ConflictStatus,
    EffectRecord,
    EffectStatus,
    GoalRecord,
    GoalStatus,
    JobKind,
    JobRecord,
    JobStatus,
    Observation,
    Plan,
    Question,
    QuestionStatus,
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
        """CS-10 (`docs/theme05_implementation_blueprint.md` line 2314):
        "Terminal call statuses never change." The blueprint states this is
        enforced by `set_call_status` raising; before Phase 7 audited it,
        no call site in this codebase actually did that -- every one of
        the ~7 places that transition a call's status happens to guard the
        terminal case itself first (`ResultRouter.route`'s branch-2
        duplicate check, `InvalidationEngine._mark_dependents`'s explicit
        `TERMINAL_CALL_STATUSES` skip, `EmissionGate`'s pre-emit CANCEL
        validation, etc. -- verified by reading all of them), so the
        invariant held by convention, not by construction. This closes
        that gap for real, matching the blueprint's own stated mechanism,
        so a future call site can't reintroduce the bug by forgetting the
        same guard everyone else remembered to add."""
        existing = self._calls.get(call_id)
        if existing is not None and existing.status in TERMINAL_CALL_STATUSES and status != existing.status:
            from prism_rt.errors import InvariantViolation

            raise InvariantViolation(
                f"call {call_id}: cannot transition terminal status {existing.status.value} -> {status.value} (CS-10)"
            )
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


class TimerWheel:
    """V2: harness-time timers, keyed by an idempotent caller-chosen
    timer_id (e.g. `f"settle:{call_id}"` — re-scheduling the same id just
    updates its due time rather than creating a duplicate).

    Nothing in the kernel needs a timer to *fire* to make a correctness
    decision — CommitGate re-evaluates every blocked call every step
    regardless of triggers, same as it always has. A timer's only job is
    liveness: telling whatever drives the kernel loop the earliest time
    it must call `step()` again even with no new event, so a settle
    window (`docs/prompt 2.txt` §13.3, §20) can't stall forever waiting
    for a chunk that may never come (T-05). `Kernel.step()` surfaces this
    as `StepReport.next_wake_us`.
    """

    def __init__(self, guard: MutationGuard) -> None:
        self._guard = guard
        self._due: dict[str, int] = {}

    def schedule(self, timer_id: str, due_us: int) -> None:
        self._guard.check()
        self._due[timer_id] = due_us

    def cancel(self, timer_id: str) -> None:
        self._guard.check()
        self._due.pop(timer_id, None)

    def next_due_us(self) -> int | None:
        return min(self._due.values()) if self._due else None

    def due(self, now_us: int) -> list[str]:
        return [tid for tid, due_us in self._due.items() if due_us <= now_us]


class EvidenceStore:
    """V3: observations (stored frames), questions (what perception is
    trying to answer), and conflicts (user statement vs. perception claim).
    `docs/prompt 2.txt` §10, `docs/theme05_implementation_blueprint.md`
    §2.10. Simplified per `model/types.py`'s V3 section docstring: one
    active question tracked per goal, one open conflict tracked per
    (goal, name)."""

    def __init__(self, guard: MutationGuard) -> None:
        self._guard = guard
        self._observations: list[Observation] = []
        self._questions: dict[str, Question] = {}
        self._question_order: list[str] = []
        self._current_question_by_goal: dict[str, str] = {}
        self._conflicts: dict[tuple[str, str], Conflict] = {}

    # --- observations -------------------------------------------------

    def add_observation(self, obs: Observation) -> None:
        self._guard.check()
        self._observations.append(obs)

    def observations(self) -> list[Observation]:
        return list(self._observations)

    def observations_by_modality(self, modality: str) -> list[Observation]:
        return [o for o in self._observations if o.modality == modality]

    def update_observation(self, obs_id: str, **changes) -> Observation:
        self._guard.check()
        for i, obs in enumerate(self._observations):
            if obs.obs_id == obs_id:
                updated = dataclasses.replace(obs, **changes)
                self._observations[i] = updated
                return updated
        raise KeyError(obs_id)

    # --- questions ------------------------------------------------------

    def create_question(self, question: Question) -> None:
        self._guard.check()
        self._questions[question.question_id] = question
        self._question_order.append(question.question_id)
        self._current_question_by_goal[question.goal_id] = question.question_id

    def get_question(self, question_id: str) -> Question | None:
        return self._questions.get(question_id)

    def update_question(self, question_id: str, **changes) -> Question:
        self._guard.check()
        updated = dataclasses.replace(self._questions[question_id], **changes)
        self._questions[question_id] = updated
        return updated

    def all_questions(self) -> list[Question]:
        return [self._questions[qid] for qid in self._question_order]

    def latest_active_question(self, goal_id: str) -> Question | None:
        """The goal's current question, if it hasn't been voided —
        simplification: one tracked question per goal (see class docstring)."""
        qid = self._current_question_by_goal.get(goal_id)
        if qid is None:
            return None
        question = self._questions.get(qid)
        if question is None or question.status == QuestionStatus.VOID:
            return None
        return question

    def question_by_pending_job(self, job_id: str) -> Question | None:
        for qid in self._question_order:
            q = self._questions[qid]
            if q.pending_job_id == job_id:
                return q
        return None

    # --- conflicts --------------------------------------------------------

    def create_conflict(self, conflict: Conflict) -> None:
        self._guard.check()
        self._conflicts[(conflict.goal_id, conflict.name)] = conflict

    def get_conflict(self, goal_id: str, name: str) -> Conflict | None:
        return self._conflicts.get((goal_id, name))

    def conflict_open(self, goal_id: str, name: str) -> bool:
        conflict = self._conflicts.get((goal_id, name))
        return conflict is not None and conflict.status == ConflictStatus.OPEN

    def void_conflict(self, goal_id: str, name: str) -> None:
        self._guard.check()
        conflict = self._conflicts.get((goal_id, name))
        if conflict is not None and conflict.status == ConflictStatus.OPEN:
            self._conflicts[(goal_id, name)] = dataclasses.replace(conflict, status=ConflictStatus.VOID)
