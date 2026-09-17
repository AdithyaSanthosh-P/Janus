"""PlanExecutor: turn ready plan steps into PROPOSED calls.

This does *not* emit TOOL_CALL actions itself — it only creates
CallRecords. The unmodified V0 `CommitGate.scan_and_admit` (kernel/commit.py)
already scans PROPOSED calls every step and emits the ones that pass G1-G9;
reusing it here rather than duplicating gate logic is the point.

V1 has no rebinder: a step becomes "ready" purely from `after` dependencies
and its bindings resolving against current facts — there is no attempt to
patch an existing call when only one binding changed. Retries (reads up to
`config.max_read_retries`, writes up to `config.max_write_retries`, per
§1.2's "SIMPLIFIED" retry note) are handled here by re-proposing a fresh
call for the same step_key with `attempt` incremented.
"""

from __future__ import annotations

from dataclasses import dataclass

from prism_rt.kernel.interpret_apply import active_goal_id
from prism_rt.model.types import (
    BindingKind,
    CallRecord,
    CallStatus,
    FactStatus,
    GoalStatus,
    Provenance,
    ReadSet,
    StepKind,
    TaskState,
    fingerprint_for,
)


@dataclass(frozen=True)
class BindResult:
    args: dict
    read_set: ReadSet


class PlanExecutor:
    def propose_ready_calls(self, store, now_us: int, step_no: int) -> list[str]:
        gid = active_goal_id(store)
        if gid is None:
            return []
        goal = store.goals.get(gid)
        if goal is None or goal.status != GoalStatus.ACTIVE or goal.task_state != TaskState.EXECUTING:
            return []
        plan = store.plans.current(gid)
        if plan is None:
            return []

        created: list[str] = []
        for step in plan.steps:
            latest = store.call_ledger.latest_by_step(gid, step.step_key)

            if latest is not None and latest.status in (
                CallStatus.PROPOSED,
                CallStatus.IN_FLIGHT,
                CallStatus.CONSUMED,
            ):
                continue  # already in progress or done

            attempt = 1
            if latest is not None and latest.status == CallStatus.FAILED:
                max_retries = store.config.max_write_retries if step.kind == StepKind.WRITE else store.config.max_read_retries
                if latest.attempt > max_retries:
                    self._fail_goal(store, gid, step, now_us, step_no)
                    continue
                attempt = latest.attempt + 1
            elif latest is not None and latest.status == CallStatus.CANCEL_REQUESTED:
                # A step whose call was just cancelled (a correction
                # invalidated it) gets a fresh attempt immediately — this is
                # not a failure retry, so it never waits on max_*_retries or
                # on the stale call's eventual (irrelevant) result.
                attempt = latest.attempt + 1

            if not all(self._step_done(store, gid, dep) for dep in step.after):
                continue

            bind_result, missing_key = self._bind(step, gid, store)
            if bind_result is None:
                self._ask_for(store, gid, missing_key, now_us, step_no)
                continue

            call_id = store.ids.next("call")
            call = CallRecord(
                call_id=call_id,
                goal_id=gid,
                step_key=step.step_key,
                tool=step.tool,
                kind=step.kind,
                args=bind_result.args,
                fingerprint=fingerprint_for(step.tool, bind_result.args),
                read_set=bind_result.read_set,
                attempt=attempt,
                created_step=step_no,
            )
            store.call_ledger.create(call)
            created.append(call_id)
        return created

    def _step_done(self, store, goal_id: str, step_key: str) -> bool:
        latest = store.call_ledger.latest_by_step(goal_id, step_key)
        return latest is not None and latest.status == CallStatus.CONSUMED

    def _bind(self, step, goal_id: str, store) -> tuple[BindResult | None, str | None]:
        args: dict = {}
        # Every call's read set includes goal.active and catalog.version
        # (`docs/prompt 2.txt` §4.4), regardless of whether a binding
        # touched them — this is what makes goal abandonment/replacement
        # cancel in-flight calls through the ordinary invalidation
        # mechanism, with no separate "cancel all calls of this goal" path.
        read_keys: list[str] = ["goal.active", "catalog.version"]
        for param, binding in step.bindings.items():
            if binding.kind == BindingKind.LITERAL:
                args[param] = binding.value
                continue

            if binding.kind == BindingKind.FACT:
                key = (binding.fact_key or "").replace("$G", goal_id)
                if not key:
                    return None, f"slot.{goal_id}.{param}"
                fact = store.facts.get(key)
                if fact is None or fact.status in (FactStatus.RETRACTED, FactStatus.HYPOTHESIS):
                    return None, key
                args[param] = fact.value
                read_keys.append(key)
                continue

            if binding.kind == BindingKind.STEP_OUTPUT:
                upstream = store.call_ledger.latest_by_step(goal_id, binding.step_key)
                if upstream is None or upstream.status != CallStatus.CONSUMED:
                    return None, f"result.<{binding.step_key}>"
                result_key = f"result.{upstream.call_id}"
                fact = store.facts.get(result_key)
                if fact is None:
                    return None, result_key
                value = fact.value
                if binding.path:
                    for part in binding.path.split("."):
                        if isinstance(value, dict):
                            value = value.get(part)
                        elif isinstance(value, list) and part.lstrip("-").isdigit():
                            index = int(part)
                            value = value[index] if -len(value) <= index < len(value) else None
                        else:
                            value = None
                        if value is None:
                            break
                args[param] = value
                read_keys.append(result_key)
                continue

        read_set = store.facts.build_read_set(sorted(set(read_keys)))
        return BindResult(args=args, read_set=read_set), None

    def _ask_for(self, store, goal_id: str, target_key: str | None, now_us: int, step_no: int) -> None:
        if target_key is None:
            return
        current = store.facts.get(f"goal.{goal_id}.clarify_target")
        if current is not None and current.status != FactStatus.RETRACTED and current.value == target_key:
            return  # already asking about this
        store.facts.set(
            f"goal.{goal_id}.clarify_target",
            target_key,
            FactStatus.COMMITTED,
            Provenance(source="system", step_no=step_no, ts_us=now_us),
            rule="executor.clarify",
        )
        store.goals.update(goal_id, task_state=TaskState.CLARIFYING)

    def _fail_goal(self, store, goal_id: str, step, now_us: int, step_no: int) -> None:
        goal = store.goals.get(goal_id)
        if goal is not None and goal.task_state == TaskState.RESPONDING:
            return  # already reported
        store.goals.update(goal_id, task_state=TaskState.RESPONDING)
        store.facts.set(
            f"compose.{goal_id}.text",
            f"I couldn't complete this — {step.tool} kept failing.",
            FactStatus.COMMITTED,
            Provenance(source="system", step_no=step_no, ts_us=now_us),
            rule="executor.step_failed",
        )
