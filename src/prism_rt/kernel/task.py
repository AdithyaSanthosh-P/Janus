"""TaskStateMachine: the sole dispatcher of INTERPRET/PLAN/COMPOSE jobs.

Called once per kernel step, in the DECIDE phase, after cancellation and
before PlanExecutor/CommitGate/FastResponder (the fixed order in
`docs/prompt 2.txt` §5.2). It is a pure function of current state —
idempotent, safe to call every step — because it always checks
`store.jobs` for an already-running job of the kind it would otherwise
request before requesting another.

V1 has no explicit TRIAGE state (`docs/sonnet_implementation_plan.md` §4
VERSION 1 Known Limitations: no speculative execution, no chunk-anchored
cancellation). Interruptions are handled uniformly: whatever the
Interpreter's committed act says (SLOT_UPDATE, NEW_GOAL, ABORT,
RETURN_TO_GOAL, ...) is applied via `kernel/interpret_apply.py` regardless
of what task_state the goal was in when the turn started — a real TRIAGE
hold-mode is a V2 concern once speculative work exists to hold.
"""

from __future__ import annotations

from dataclasses import dataclass

from prism_rt.kernel.interpret_apply import active_goal_id
from prism_rt.model.types import CallStatus, FactStatus, GoalStatus, JobKind, JobRecord, ReadSet, StepKind, TaskState


@dataclass(frozen=True)
class DispatchRequest:
    """The JobRecord (job_id included) is already written to store.jobs by
    the time this is returned — only the actual runner.submit() call is
    deferred to the DISPATCH phase (which runs outside the txn, since
    submit() has no store mutation to guard). Allocating job_id here,
    inside DECIDE, is what lets FastResponder (which runs right after
    TaskStateMachine in the same phase) see "a PLAN job is now running for
    this goal" and ACK in the same step the plan was requested."""

    job_id: str
    kind: JobKind
    view: dict
    goal_id: str | None
    turn_id: str | None
    read_set: ReadSet


class TaskStateMachine:
    def decide(self, store, now_us: int, step_no: int) -> list[DispatchRequest]:
        requests: list[DispatchRequest] = []

        pending_turn = store.facts.get("session.pending_interpretation_turn")
        if pending_turn is not None and pending_turn.status != FactStatus.RETRACTED:
            turn_id = pending_turn.value
            if not store.jobs.running_by_kind_turn(JobKind.INTERPRET, turn_id):
                requests.append(self._build_interpret_request(store, turn_id))

        gid = active_goal_id(store)
        if gid is not None:
            goal = store.goals.get(gid)
            if goal is not None and goal.status == GoalStatus.ACTIVE:
                if goal.task_state == TaskState.PLANNING:
                    if not store.jobs.running_by_kind_goal(JobKind.PLAN, gid):
                        requests.append(self._build_plan_request(store, gid))
                elif goal.task_state == TaskState.EXECUTING:
                    if self._all_required_steps_done(store, gid):
                        store.goals.update(gid, task_state=TaskState.RESPONDING)
                        # Phase 5 (C2): a response frame may have already
                        # rendered compose.<gid>.text this very step, in
                        # APPLY, the instant the plan's last result was
                        # consumed (kernel/frames.py.FrameScheduler.
                        # try_render, called from
                        # kernel/reducers.py._apply_tool_result) — skip the
                        # redundant COMPOSE job when it did. This is the
                        # only place that decision is made; frame_rendering_
                        # enabled=False means this fact is never pre-set, so
                        # behavior is unchanged from every earlier version.
                        pending_text = store.facts.get(f"compose.{gid}.text")
                        if pending_text is None or pending_text.status == FactStatus.RETRACTED:
                            requests.append(self._build_compose_request(store, gid))
                elif goal.task_state == TaskState.CLARIFYING:
                    target = store.facts.get(f"goal.{gid}.clarify_target")
                    if target is None or target.status == FactStatus.RETRACTED:
                        store.goals.update(gid, task_state=TaskState.PLANNING)
                elif goal.task_state == TaskState.RESPONDING:
                    pending_text = store.facts.get(f"compose.{gid}.text")
                    if pending_text is None and not store.jobs.running_by_kind_goal(JobKind.COMPOSE, gid):
                        requests.append(self._build_compose_request(store, gid))

        return requests

    def _all_required_steps_done(self, store, goal_id: str) -> bool:
        plan = store.plans.current(goal_id)
        if plan is None:
            return False
        for step in plan.steps:
            latest = store.call_ledger.latest_by_step(goal_id, step.step_key)
            if latest is None or latest.status != CallStatus.CONSUMED:
                return False
        return True

    def _goal_slots(self, store, goal_id: str) -> dict:
        prefix = f"slot.{goal_id}."
        return {
            key[len(prefix):]: value
            for key, value in store.facts.snapshot_committed().items()
            if key.startswith(prefix)
        }

    def _build_interpret_request(self, store, turn_id: str) -> DispatchRequest:
        turn = store.turn_log.get(turn_id)
        transcript = " ".join(chunk.text for chunk in turn.chunks) if turn is not None else ""
        gid = active_goal_id(store)

        read_keys = ["session.pending_interpretation_turn", f"turn.{turn_id}.prefix", "goal.active"]
        active_intent = None
        active_slots: dict = {}
        if gid is not None:
            intent_fact = store.facts.get(f"goal.{gid}.intent")
            active_intent = intent_fact.value if intent_fact is not None and intent_fact.status != FactStatus.RETRACTED else None
            read_keys.append(f"goal.{gid}.intent")
            active_slots = self._goal_slots(store, gid)
            read_keys.extend(f"slot.{gid}.{name}" for name in active_slots)

        view = {
            "transcript": transcript,
            "active_intent": active_intent,
            "active_slots": active_slots,
            "suspended_goals": [g.goal_id for g in store.goals.suspended()],
            "pending_clarification": None,
        }
        read_set = store.facts.build_read_set(sorted(set(read_keys)))
        return self._make_request(store, JobKind.INTERPRET, view, gid, turn_id, read_set)

    def _build_plan_request(self, store, goal_id: str) -> DispatchRequest:
        intent_fact = store.facts.get(f"goal.{goal_id}.intent")
        intent = intent_fact.value if intent_fact is not None and intent_fact.status != FactStatus.RETRACTED else None
        facts = self._goal_slots(store, goal_id)
        read_keys = ["goal.active", f"goal.{goal_id}.intent", "catalog.version"]
        read_keys.extend(f"slot.{goal_id}.{name}" for name in facts)

        catalog_summary = [
            {
                "name": tool.name,
                "description": tool.description,
                "params_schema": tool.params_schema,
                "mutability": tool.mutability.value,
            }
            for tool in store.catalog.usable_tools()
        ]
        view = {"intent": intent, "facts": facts, "catalog": catalog_summary, "change_context": None}
        read_set = store.facts.build_read_set(sorted(set(read_keys)))
        return self._make_request(store, JobKind.PLAN, view, goal_id, None, read_set)

    def _build_compose_request(self, store, goal_id: str) -> DispatchRequest:
        plan = store.plans.current(goal_id)
        facts: dict = {}
        effects: dict = {}
        read_keys = ["goal.active"]
        if plan is not None:
            for step in plan.steps:
                call = store.call_ledger.latest_by_step(goal_id, step.step_key)
                if call is None or call.status != CallStatus.CONSUMED:
                    continue
                result_fact = store.facts.get(f"result.{call.call_id}")
                if result_fact is not None:
                    facts[step.step_key] = result_fact.value
                    read_keys.append(f"result.{call.call_id}")
                if step.kind == StepKind.WRITE:
                    effect = store.effect_ledger.by_fingerprint(call.fingerprint)
                    if effect is not None:
                        effects[step.step_key] = effect.status.value

        view = {"facts": facts, "effects": effects, "open_questions": []}
        read_set = store.facts.build_read_set(sorted(set(read_keys)))
        return self._make_request(store, JobKind.COMPOSE, view, goal_id, None, read_set)

    def _make_request(
        self, store, kind: JobKind, view: dict, goal_id: str | None, turn_id: str | None, read_set: ReadSet
    ) -> DispatchRequest:
        job_id = store.ids.next("job")
        store.jobs.create(
            JobRecord(job_id=job_id, kind=kind, goal_id=goal_id, turn_id=turn_id, read_set=read_set)
        )
        return DispatchRequest(job_id=job_id, kind=kind, view=view, goal_id=goal_id, turn_id=turn_id, read_set=read_set)
