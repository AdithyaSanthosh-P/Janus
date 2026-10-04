"""BindScheduler: S3 late binding (`docs-personal/private-docs/
s3_plan_2026-09-28.md`, Increment II).

A compiled plan (kernel/action_plans.py) binds a chained argument ("check
the commute from the cheapest one", "track whatever you find") as a LATE
binding: its value is chosen from the upstream step's *real* result, once
that result exists -- never guessed before it. Found against FDB-v3: the
Planner used to write such bindings before any result existed and bound
the literal words ("the cheapest option", "there"); FDB's own mock
`search_apartments` returns `{"results": [{"id": ...}]}` with no address
field at all, so the value has to be picked from what actually came back.

Runs in DECIDE *before* TaskStateMachine and PlanExecutor, so a value it
writes is bound by PlanExecutor in the same step. Two ways to decide:
- fast path: every chained parameter names a result `field` and a
  schema-free search (`executor._bfs_find_field`) finds exactly one distinct
  value for it -- no model call;
- otherwise a `JobKind.BIND` job (`workers/binder.py`) picks the values
  from the upstream results; `kernel/reducers.py` writes them.
A conditional step (Config.conditional_actions_enabled: "if one is under
$50, add two; otherwise track my order") is decided the same way, always
by a BIND job: the job checks the step's `condition.test` against the
source step's real result and the verdict lands at `cond.<gid>.<step>`
(`action_plans.write_condition_fact`), grounded like a bind value. A step
whose condition source failed or was itself skipped cannot be checked and
is skipped -- an action the user made conditional never runs unverified.
Either way the value lands at `bind.<gid>.<step>.<param>` as a DERIVED fact
grounded in the upstream results (`action_plans.write_bind_facts`), so an
upstream re-run makes it stale through the ordinary invalidation path and
the downstream call re-binds -- no new cancellation machinery. A BIND job
that keeps failing gives up honestly after `max_interpret_retries` (the
same budget as INTERPRET: both are live model calls) via
`PlanExecutor._fail_goal` -- unlike PLAN/EXTRACT, never retried forever.
"""

from __future__ import annotations

import re

from prism_rt.kernel.action_plans import (
    bind_failure_key,
    bind_key,
    coerce_to_schema,
    cond_key,
    step_failed,
    step_skipped,
    tool_props,
    valid_bind_fact,
    write_bind_facts,
    write_condition_fact,
)
from prism_rt.kernel.executor import PlanExecutor, _bfs_find_field
from prism_rt.kernel.interpret_apply import active_goal_id
from prism_rt.kernel.task import DispatchRequest
from prism_rt.model.types import BindingKind, CallStatus, FactStatus, GoalStatus, JobKind, JobRecord, TaskState

_PATH_TOKENS = re.compile(r"[.\[\]]")




def _field_name(path: str | None) -> str | None:
    """"apartments[0].address" -> "address"; "id" -> "id"."""
    if not path:
        return None
    tokens = [t for t in _PATH_TOKENS.split(path) if t and not t.isdigit()]
    return tokens[-1] if tokens else None


def _step_done(store, goal_id: str, step_key: str) -> bool:
    latest = store.call_ledger.latest_by_step(goal_id, step_key)
    return latest is not None and latest.status == CallStatus.CONSUMED and store.facts.is_valid(latest.read_set).is_valid


class BindScheduler:
    def __init__(self) -> None:
        self._executor = PlanExecutor()

    def decide(self, store, now_us: int, step_no: int) -> list[DispatchRequest]:
        gid = active_goal_id(store)
        if gid is None:
            return []
        goal = store.goals.get(gid)
        if goal is None or goal.status != GoalStatus.ACTIVE or goal.task_state != TaskState.EXECUTING:
            return []
        plan = store.plans.current(gid)
        if plan is None or plan.origin != "compiled":
            return []

        requests: list[DispatchRequest] = []
        for step in plan.steps:
            late = {p: b for p, b in step.bindings.items() if b.kind == BindingKind.LATE}
            undecided = step.condition is not None and valid_bind_fact(store, cond_key(gid, step.step_key)) is None
            if step_skipped(store, gid, step.step_key):
                continue  # its condition does not hold: nothing to bind
            if not undecided and all(valid_bind_fact(store, bind_key(gid, step.step_key, p)) is not None for p in late):
                continue
            if undecided:
                source = step.condition.step_key
                if step_failed(store, gid, source) or step_skipped(store, gid, source):
                    # Nothing to check the condition against: not run.
                    read_set = store.facts.build_read_set(["goal.active", "catalog.version", cond_key(gid, source), f"step_failed.{gid}.{source}"])
                    write_condition_fact(store, gid, step.step_key, False, read_set, rule="binder.condition_unverifiable", now_us=now_us, step_no=step_no)
                    continue
            sources = sorted({b.step_key for b in late.values()} | ({step.condition.step_key} if undecided else set()))
            if not all(_step_done(store, gid, s) for s in sources):
                continue
            if any(j.target == step.step_key for j in store.jobs.running_by_kind_goal(JobKind.BIND, gid)):
                continue
            # valid_bind_fact also checks the count's grounding: failures
            # against a since-superseded upstream result don't count.
            failures = valid_bind_fact(store, bind_failure_key(gid, step.step_key))
            if failures is not None and failures.value > store.config.max_interpret_retries:
                names = ", ".join(p.replace("_", " ") for p in sorted(late))
                self._executor._fail_goal(store, gid, step, now_us, step_no, reason=f"I couldn't work out the {names} from the earlier results")
                return requests

            read_set = self._read_set(store, gid, sources)
            fast = None if undecided else self._fast_path(store, gid, step, late)
            if fast is not None:
                write_bind_facts(store, gid, step.step_key, fast, read_set, rule="binder.fast_path", now_us=now_us, step_no=step_no)
                continue
            requests.append(self._dispatch(store, gid, step, late, sources, read_set, condition=step.condition.test if undecided else None))
        return requests

    def _read_set(self, store, goal_id: str, sources: list[str]):
        """The upstream step-output facts *and* the keys those calls
        themselves read -- the same transitive rule `_build_compose_request`
        needs (v10-1): a stepout fact alone can stay valid-looking while the
        call behind it has been superseded."""
        keys = ["goal.active", "catalog.version"]
        for source in sources:
            keys.append(f"stepout.{goal_id}.{source}")
            call = store.call_ledger.latest_by_step(goal_id, source)
            if call is not None:
                keys.extend(entry.key for entry in call.read_set.entries)
        return store.facts.build_read_set(sorted(set(keys)))

    def _fast_path(self, store, goal_id: str, step, late: dict) -> dict | None:
        props = tool_props(store, step.tool)[0]
        values: dict = {}
        for param, binding in late.items():
            field = _field_name(binding.path)
            result = store.facts.get(f"stepout.{goal_id}.{binding.step_key}")
            if field is None or result is None or result.status == FactStatus.RETRACTED:
                return None
            found = _bfs_find_field(result.value, field)
            if not found.found or found.ambiguous or found.value is None:
                return None
            values[param] = coerce_to_schema(found.value, props.get(param) or {}, param)
        return values

    def _dispatch(self, store, goal_id: str, step, late: dict, sources: list[str], read_set, *, condition: str | None = None) -> DispatchRequest:
        props = tool_props(store, step.tool)[0]
        plan_steps = {s.step_key: s for s in store.plans.current(goal_id).steps}
        known_args = {}
        for param, binding in step.bindings.items():
            if binding.kind == BindingKind.FACT and binding.fact_key:
                fact = store.facts.get(binding.fact_key.replace("$G", goal_id))
                if fact is not None and fact.status != FactStatus.RETRACTED:
                    known_args[param] = fact.value
        earlier = []
        for source in sources:
            call = store.call_ledger.latest_by_step(goal_id, source)
            result = store.facts.get(f"stepout.{goal_id}.{source}")
            earlier.append({
                "action": source,
                "tool": plan_steps[source].tool if source in plan_steps else None,
                "args": dict(call.args) if call is not None else {},
                "result": result.value if result is not None else None,
            })
        view = {
            "tool": step.tool,
            "request": " ".join(chunk.text for turn in store.turn_log.all() for chunk in turn.chunks),
            "known_args": known_args,
            "fill": [
                {
                    "param": param,
                    "type": (props.get(param) or {}).get("type"),
                    "description": (props.get(param) or {}).get("description"),
                    "from": binding.step_key,
                    "which": binding.hint,
                    "field": binding.path,
                }
                for param, binding in sorted(late.items())
            ],
            "earlier_results": earlier,
        }
        if condition is not None:
            view["condition"] = condition
        job_id = store.ids.next("job")
        store.jobs.create(JobRecord(job_id=job_id, kind=JobKind.BIND, goal_id=goal_id, turn_id=None, read_set=read_set, target=step.step_key))
        return DispatchRequest(job_id=job_id, kind=JobKind.BIND, view=view, goal_id=goal_id, turn_id=None, read_set=read_set)
