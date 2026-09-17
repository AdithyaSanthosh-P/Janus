"""FastResponder: templated ACK, CLARIFY, INFORM, FINAL.

V1 trims the utterance kinds from `docs/prompt 2.txt` §9.3 down to what the
15-scenario suite needs: ACK (once per plan dispatch), CLARIFY (once per
distinct clarify target), INFORM (duplicate-write / unknown-outcome
notices), and FINAL. PROGRESS and HOLD are real utterance kinds the
architecture describes but nothing in V1's scope requires them — they are
not implemented, not silently dropped from something that used to emit
them.

Every "have we already said this" check is a small fact
(`ack.<goal>.acked_job`, `clarify.<goal>.asked`, `inform.<call>.sent`)
rather than a formal OutputLedger — sufficient for one utterance per
trigger, and it participates in the same read-set/invalidation machinery
as everything else for free.
"""

from __future__ import annotations

from config.templates import ACK_DEFAULT, CLARIFY_TEMPLATE, FINAL_FALLBACK, INFORM_DUPLICATE_WRITE
from prism_rt.kernel.commit import CommitGate
from prism_rt.kernel.interpret_apply import active_goal_id
from prism_rt.model.actions import FinalBody, IntendedAction, SpeakBody
from prism_rt.model.types import (
    EMPTY_READ_SET,
    ActionType,
    FactStatus,
    GoalStatus,
    JobKind,
    Provenance,
    StepKind,
    TaskState,
)


class FastResponder:
    def __init__(self) -> None:
        self._commit_gate = CommitGate()

    def decide(self, store, now_us: int, step_no: int, *, skip_call_ids: frozenset[str] = frozenset()) -> list[IntendedAction]:
        actions: list[IntendedAction] = []
        actions.extend(self._inform_blocked_writes(store, now_us, step_no, skip_call_ids))

        gid = active_goal_id(store)
        if gid is None:
            return actions
        goal = store.goals.get(gid)
        if goal is None:
            return actions

        if goal.status == GoalStatus.ACTIVE and goal.task_state == TaskState.PLANNING:
            actions.extend(self._ack_plan_dispatch(store, gid, now_us, step_no))
        if goal.status == GoalStatus.ACTIVE and goal.task_state == TaskState.CLARIFYING:
            actions.extend(self._clarify(store, gid, now_us, step_no))
        if goal.task_state == TaskState.RESPONDING:
            actions.extend(self._final(store, goal, now_us, step_no))

        return actions

    def _ack_plan_dispatch(self, store, goal_id: str, now_us: int, step_no: int) -> list[IntendedAction]:
        running = store.jobs.running_by_kind_goal(JobKind.PLAN, goal_id)
        if not running:
            return []
        job_id = running[0].job_id
        acked = store.facts.get(f"ack.{goal_id}.acked_job")
        if acked is not None and acked.status != FactStatus.RETRACTED and acked.value == job_id:
            return []
        store.facts.set(
            f"ack.{goal_id}.acked_job",
            job_id,
            FactStatus.COMMITTED,
            Provenance(source="system", step_no=step_no, ts_us=now_us),
            rule="responder.ack",
        )
        return [
            IntendedAction(
                action_type=ActionType.SPEAK,
                body=SpeakBody(text=ACK_DEFAULT, kind="ack"),
                read_set=EMPTY_READ_SET,
                rule_id="responder.ack",
            )
        ]

    def _clarify(self, store, goal_id: str, now_us: int, step_no: int) -> list[IntendedAction]:
        target_fact = store.facts.get(f"goal.{goal_id}.clarify_target")
        if target_fact is None or target_fact.status == FactStatus.RETRACTED:
            return []
        target = target_fact.value
        asked = store.facts.get(f"clarify.{goal_id}.asked")
        if asked is not None and asked.status != FactStatus.RETRACTED and asked.value == target:
            return []
        store.facts.set(
            f"clarify.{goal_id}.asked",
            target,
            FactStatus.COMMITTED,
            Provenance(source="system", step_no=step_no, ts_us=now_us),
            rule="responder.clarify",
        )
        friendly = target.rsplit(".", 1)[-1]
        return [
            IntendedAction(
                action_type=ActionType.CLARIFY,
                body=SpeakBody(text=CLARIFY_TEMPLATE.format(target=friendly), kind="clarify"),
                read_set=EMPTY_READ_SET,
                rule_id="responder.clarify",
            )
        ]

    def _final(self, store, goal, now_us: int, step_no: int) -> list[IntendedAction]:
        text_fact = store.facts.get(f"compose.{goal.goal_id}.text")
        if text_fact is None or text_fact.status == FactStatus.RETRACTED:
            return []
        text = text_fact.value or FINAL_FALLBACK
        task_completed = goal.status != GoalStatus.ABANDONED
        return [
            IntendedAction(
                action_type=ActionType.FINAL,
                body=FinalBody(text=text, task_completed=task_completed),
                read_set=EMPTY_READ_SET,
                needs_snapshot=True,
                rule_id="responder.final",
                goal_id=goal.goal_id,
            )
        ]

    def _inform_blocked_writes(self, store, now_us: int, step_no: int, skip_call_ids) -> list[IntendedAction]:
        actions: list[IntendedAction] = []
        for call in store.call_ledger.proposed():
            if call.kind != StepKind.WRITE or call.call_id in skip_call_ids:
                continue
            decision = self._commit_gate.evaluate(call, store, now_us)
            if decision.allowed or decision.rule_id not in ("G5", "G6"):
                continue
            already = store.facts.get(f"inform.{call.call_id}.sent")
            if already is not None and already.status != FactStatus.RETRACTED:
                continue
            store.facts.set(
                f"inform.{call.call_id}.sent",
                True,
                FactStatus.COMMITTED,
                Provenance(source="system", step_no=step_no, ts_us=now_us),
                rule="responder.inform_duplicate",
            )
            actions.append(
                IntendedAction(
                    action_type=ActionType.SPEAK,
                    body=SpeakBody(text=INFORM_DUPLICATE_WRITE, kind="inform"),
                    read_set=EMPTY_READ_SET,
                    rule_id="responder.inform_duplicate",
                )
            )
        return actions
