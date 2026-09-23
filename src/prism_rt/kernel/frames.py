"""FrameScheduler: C2 response frames (`docs/prompt 3.txt`, Phase 5 of
`docs/post_v4_implementation_plan.md`).

Same two-phase split as `kernel/perception.py`/`kernel/audio.py`:
`decide()` dispatches a FRAME job (called from `kernel/step.py`'s DECIDE
phase, store-only) the moment the plan's last required step's call is
*emitted* (in flight, not yet resolved) — the model writes a template with
typed holes bound to result paths using a fixed operator set (`field`,
`count`, `min`/`max` by field, `first_k`, `exists`), with nothing but the
tool's declared output schema and the goal's facts to go on; no result
data reaches it. `on_frame_result()` stores the parsed template (called
from `kernel/reducers.py._apply_worker_result`, APPLY phase). `try_render()`
is called from `kernel/reducers.py._apply_tool_result` right after the
plan's last call is consumed: it evaluates the template's holes against
the *real* result, deterministically, entirely inside the kernel, and — if
every hole resolves — writes `compose.<goal>.text` directly, the same fact
Composer would have written, so `kernel/responder.py.FastResponder._final`
needs no changes at all to render FINAL from it. `kernel/task.py`'s
ordinary COMPOSE dispatch is skipped whenever that fact already exists;
Composer remains the fallback whenever it doesn't (no template yet, a
stale one from a correction, a hole that fails to evaluate, or
`Config.frame_rendering_enabled` off).

Grounding guarantee: the frame's static wording is written *before* the
result exists, so any per-instance value in the rendered text must have
come through a `{hole}` substitution evaluated against the real, consumed
result — the model cannot embed an invented number or fact directly in the
template text and have it survive into a specific response.

Scope trim: only two of the three declared branches (`success`, `empty`)
are ever *selected* by this implementation. The `error` branch is parsed
and validated (so the FRAME job's own schema still requires it, and a
future extension can wire it up) but never chosen here, because a tool
result routed as `"error"` sets the call's status to `FAILED`, not
`CONSUMED` (`kernel/results.py.ResultRouter`) — in this architecture a
failed call is retried, not immediately "the last done step," and only
becomes a terminal outcome once retries are exhausted via
`PlanExecutor._fail_goal`, a distinct goal-failure path this phase
doesn't touch. Wiring the `error` branch into that path is future work,
not a correctness gap in what's built: a failed call simply never
reaches `try_render` at all, so Composer's own (separately handled)
failure narration is unaffected either way.
"""

from __future__ import annotations

from dataclasses import dataclass

from prism_rt.kernel.interpret_apply import active_goal_id, set_grounded_compose_text
from prism_rt.model.types import CallStatus, FactStatus, GoalStatus, JobKind, JobRecord, Provenance, ReadSet, TaskState


@dataclass(frozen=True)
class FrameDispatchRequest:
    """Same shape as `kernel.task.DispatchRequest` — duck-typed by
    `Kernel.step`'s DISPATCH phase, which only reads `.job_id`/`.kind`/
    `.view`. A separate class, matching `kernel/perception.py` and
    `kernel/audio.py`'s existing precedent for this kind of request."""

    job_id: str
    kind: JobKind
    view: dict
    goal_id: str | None
    turn_id: str | None
    read_set: ReadSet


def _pending_step(store, goal_id: str):
    """The plan's single not-yet-consumed-and-valid step (and its latest
    call, if any), iff every other step already is — `None` if zero or
    more than one remain undone."""
    plan = store.plans.current(goal_id)
    if plan is None:
        return None
    pending = []
    for step in plan.steps:
        latest = store.call_ledger.latest_by_step(goal_id, step.step_key)
        done = (
            latest is not None
            and latest.status == CallStatus.CONSUMED
            and store.facts.is_valid(latest.read_set).is_valid
        )
        if not done:
            pending.append((step, latest))
    if len(pending) != 1:
        return None
    return pending[0]


def _traverse_path(value, path: str | None):
    if not path:
        return value
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.lstrip("-").isdigit():
            index = int(part)
            value = value[index] if -len(value) <= index < len(value) else None
        else:
            return None
        if value is None:
            return None
    return value


def _evaluate_hole(spec: dict, result_value) -> str | None:
    op = spec["op"]
    target = _traverse_path(result_value, spec.get("path"))

    if op == "field":
        if target is None or isinstance(target, (dict, list)):
            return None
        return str(target)

    if op == "count":
        return str(len(target)) if isinstance(target, list) else None

    if op in ("min", "max"):
        if not isinstance(target, list) or not target:
            return None
        field = spec.get("field")
        raw = [(item.get(field) if isinstance(item, dict) and field else item) for item in target]
        numeric = [v for v in raw if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not numeric:
            return None
        return str(min(numeric) if op == "min" else max(numeric))

    if op == "first_k":
        if not isinstance(target, list):
            return None
        k = spec.get("k") or 3
        field = spec.get("field")
        items = target[:k]
        if field:
            items = [item.get(field) for item in items if isinstance(item, dict)]
        rendered = [str(i) for i in items if i is not None]
        return ", ".join(rendered) if rendered else None

    if op == "exists":
        return "yes" if target is not None else "no"

    return None


class FrameScheduler:
    # --- dispatch (called from kernel/step.py, DECIDE phase) ---

    def decide(self, store, now_us: int, step_no: int) -> list[FrameDispatchRequest]:
        if not store.config.frame_rendering_enabled:
            return []
        gid = active_goal_id(store)
        if gid is None:
            return []
        goal = store.goals.get(gid)
        if goal is None or goal.status != GoalStatus.ACTIVE or goal.task_state != TaskState.EXECUTING:
            return []
        pending = _pending_step(store, gid)
        if pending is None:
            return []
        step, latest = pending
        if latest is None or latest.status != CallStatus.IN_FLIGHT:
            return []  # the plan's last call hasn't been emitted yet
        existing = store.facts.get(f"frame.{gid}.template")
        if existing is not None and existing.status != FactStatus.RETRACTED:
            existing_read_set = existing.provenance.derivation_read_set
            existing_stale = existing_read_set is not None and not store.facts.is_valid(existing_read_set).is_valid
            if not existing_stale:
                return []  # already have a valid one, or one still being written
        if store.jobs.running_by_kind_goal(JobKind.FRAME, gid):
            return []

        tool_spec = store.catalog.get(step.tool)
        intent_fact = store.facts.get(f"goal.{gid}.intent")
        facts = {
            key[len(f"slot.{gid}.") :]: value
            for key, value in store.facts.snapshot_committed().items()
            if key.startswith(f"slot.{gid}.")
        }
        # C6 (`docs/prompt 3.txt`): include every key the pending call
        # itself read, not just its already-bound slot values -- the same
        # pattern `kernel/task.py._build_compose_request` uses for exactly
        # the same reason (found first there, `v10-1-correction-race-fix`).
        # Since `PlanExecutor._bind` now makes a call's own read set
        # schema-complete (every optional tool param it didn't bind, not
        # just the ones it did), this line is what lets the frame inherit
        # that completeness automatically instead of needing its own
        # separate schema walk.
        read_keys = (
            ["goal.active", "catalog.version"]
            + [f"slot.{gid}.{name}" for name in facts]
            + [entry.key for entry in latest.read_set.entries]
        )
        read_set = store.facts.build_read_set(sorted(set(read_keys)))
        view = {
            "intent": intent_fact.value if intent_fact is not None and intent_fact.status != FactStatus.RETRACTED else None,
            "facts": facts,
            "tool": step.tool,
            "output_schema": tool_spec.output_schema if tool_spec is not None else None,
        }
        job_id = store.ids.next("job")
        store.jobs.create(JobRecord(job_id=job_id, kind=JobKind.FRAME, goal_id=gid, turn_id=None, read_set=read_set))
        return [FrameDispatchRequest(job_id=job_id, kind=JobKind.FRAME, view=view, goal_id=gid, turn_id=None, read_set=read_set)]

    # --- result application (called from kernel/reducers.py, APPLY phase) ---

    def on_frame_result(self, txn, job, template: dict, step_no: int, now_us: int, *, event_id: str) -> None:
        if job.goal_id is None:
            return
        txn.facts.set(
            f"frame.{job.goal_id}.template",
            template,
            FactStatus.DERIVED,
            Provenance(
                source="system",
                event_id=event_id,
                step_no=step_no,
                ts_us=now_us,
                derivation_read_set=job.read_set,
            ),
            rule="frames.template",
        )

    # --- rendering (called from kernel/reducers.py._apply_tool_result, APPLY phase) ---

    def try_render(self, txn, call, now_us: int, step_no: int, *, event_id: str) -> bool:
        if not txn.store.config.frame_rendering_enabled:
            return False
        goal_id = call.goal_id
        if _pending_step(txn.store, goal_id) is not None:
            return False  # this wasn't the plan's last step
        template_fact = txn.facts.get(f"frame.{goal_id}.template")
        if template_fact is None or template_fact.status == FactStatus.RETRACTED:
            return False
        read_set = template_fact.provenance.derivation_read_set
        if read_set is not None and not txn.facts.is_valid(read_set).is_valid:
            return False  # a correction changed a goal fact the frame was written against
        result_fact = txn.facts.get(f"result.{call.call_id}")
        text = self._render(template_fact.value, result_fact.value if result_fact is not None else None)
        if text is None:
            return False
        # Same grounding contract as a COMPOSE result: the frame's read set
        # (which folds in the call's own read set, C6).
        set_grounded_compose_text(
            txn,
            goal_id,
            text,
            Provenance(source="system", event_id=event_id, call_id=call.call_id, step_no=step_no, ts_us=now_us, derivation_read_set=read_set),
            rule="frames.render",
        )
        return True

    def _render(self, template: dict, result_value) -> str | None:
        branch = template["branches"][self._select_branch(template, result_value)]
        holes = branch.get("holes") or {}
        values: dict[str, str] = {}
        for name, spec in holes.items():
            value = _evaluate_hole(spec, result_value)
            if value is None:
                return None
            values[name] = value
        try:
            return branch["template"].format(**values)
        except (KeyError, IndexError, ValueError):
            return None

    def _select_branch(self, template: dict, result_value) -> str:
        list_path = template.get("list_path")
        if list_path:
            items = _traverse_path(result_value, list_path)
            if isinstance(items, list) and len(items) == 0:
                return "empty"
        return "success"
