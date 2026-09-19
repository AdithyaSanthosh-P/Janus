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

import re
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

# V4 / C10 (docs/theme05_implementation_blueprint.md §5.5): a WRITE
# parameter is identifier-like if the schema marks it (enum, format=uuid)
# or its name looks like one. Scope trim: the blueprint's fourth criterion
# ("a value for p appeared as a field value in any consumed result this
# session") would need scanning arbitrary nested result shapes for a
# matching field name and isn't implemented — the first three already
# cover every identifier-like parameter this project's own tools use.
_IDENTIFIER_NAME_RE = re.compile(r"(_id|_code|_number|_ref|_reference)$|^id$")


def _is_identifier_param(param: str, prop_schema: dict) -> bool:
    if prop_schema.get("enum") is not None:
        return True
    if prop_schema.get("format") == "uuid":
        return True
    return bool(_IDENTIFIER_NAME_RE.search(param))


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

            if latest is not None and latest.status == CallStatus.IN_FLIGHT:
                continue  # already in progress

            attempt = 1
            if latest is not None and latest.status == CallStatus.PROPOSED:
                # A call CommitGate hasn't admitted yet (e.g. still waiting
                # out the settle barrier, G10) can go stale before it's ever
                # emitted — a slot it reads changes while it sits blocked.
                # Without this check it stays PROPOSED forever failing G2
                # for the wrong reason, and FastResponder could still speak
                # about a call nobody cares about anymore.
                if store.facts.is_valid(latest.read_set).is_valid:
                    continue  # still valid, waiting on CommitGate — nothing to do
                store.call_ledger.set_status(latest.call_id, CallStatus.DISCARDED)
                attempt = latest.attempt + 1
            elif latest is not None and latest.status == CallStatus.CONSUMED:
                # §3.5: a step carries over across plan revisions only while
                # its resolved arguments are unchanged — i.e. its read set
                # is still valid. If a slot it read has since changed, this
                # is stale, not done: fall through and re-execute with the
                # new value (D4). Without this check a corrected step never
                # re-runs, and everything downstream silently binds to data
                # from before the correction.
                if store.facts.is_valid(latest.read_set).is_valid:
                    continue  # still correct — carry over, nothing to do
                attempt = latest.attempt + 1
            elif latest is not None and latest.status == CallStatus.FAILED:
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

            if self._commit_last_blocked(store, gid, step, plan, now_us):
                continue

            tool_spec = store.catalog.get(step.tool)
            if tool_spec is None or tool_spec.status != "USABLE":
                # G1 ("tool exists and is usable" — kernel/commit.py)
                # can never pass for this step no matter how many times
                # it's retried: the tool simply isn't in the current
                # catalog (a planner referencing an unknown tool, or one
                # a later manifest update dropped). Left alone, this step
                # would still get a CallRecord created below, which
                # CommitGate.scan_and_admit leaves PROPOSED forever (its
                # own docstring: "a call that fails the gate simply stays
                # PROPOSED... re-evaluated... once whatever blocked it
                # may have changed" — nothing ever will here) — a real,
                # confirmed livelock (S-07): the goal never completes,
                # never CLARIFYs, never fails, until the scenario-wide
                # watchdog eventually times out and salvages it 100+
                # seconds later. Found by an independent review
                # (F-SONNET-2), confirmed by direct reproduction. Fail
                # the goal immediately instead, the same honest way
                # retry-exhaustion already does.
                self._fail_goal(store, gid, step, now_us, step_no)
                continue

            bind_result, missing_key = self._bind(step, gid, store)
            if bind_result is None:
                # V3: a missing `claim.*` binding means perception hasn't
                # answered yet (still analyzing, or mid lease-renewal after
                # `kernel/perception.py.expire_leases` retracted a stale
                # claim) — that resolves itself once the VISION job
                # completes; asking the user about it would be a spurious
                # interruption. (Scope trim: the full spec also clarifies
                # when a WRITE step needs a MEDIUM-confidence claim, which
                # this doesn't distinguish — no test scenario needs that
                # nuance; see kernel/perception.py's module docstring.)
                if missing_key is not None and missing_key.startswith("claim."):
                    continue
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

    def _commit_last_blocked(self, store, goal_id: str, step, plan, now_us: int) -> bool:
        """C9 (`docs/post_v4_implementation_plan.md` Phase 5, N-02b): a
        WRITE step whose own `after` dependencies are satisfied still
        waits for every READ step in the plan that isn't downstream of it
        (a sibling/independent read, not one that reads *this* write's
        output) to resolve first — bounded by `commit_last_wait_cap_ms` so
        one slow unrelated read can't block the write forever."""
        if not store.config.commit_last_ordering or step.kind != StepKind.WRITE:
            return False
        downstream = self._downstream_step_keys(plan, step.step_key)
        unresolved = any(
            s.kind == StepKind.READ and s.step_key != step.step_key and s.step_key not in downstream
            and not self._step_done(store, goal_id, s.step_key)
            for s in plan.steps
        )
        if not unresolved:
            return False
        key = f"commit_last.{goal_id}.{step.step_key}.first_ready_us"
        fact = store.facts.get(key)
        if fact is None:
            store.facts.set(
                key,
                now_us,
                FactStatus.COMMITTED,
                Provenance(source="system", step_no=0, ts_us=now_us),
                rule="executor.commit_last_first_ready",
            )
            return True
        return (now_us - fact.value) / 1000 < store.config.commit_last_wait_cap_ms

    def _downstream_step_keys(self, plan, step_key: str) -> set[str]:
        """Every step_key transitively reachable via `after` edges *from*
        `step_key` — the steps that read this step's output, directly or
        through a chain."""
        downstream: set[str] = set()
        changed = True
        while changed:
            changed = False
            for s in plan.steps:
                if s.step_key in downstream:
                    continue
                if step_key in s.after or any(a in downstream for a in s.after):
                    downstream.add(s.step_key)
                    changed = True
        return downstream

    def _step_done(self, store, goal_id: str, step_key: str) -> bool:
        """"Done" means consumed *and still valid* — a dependency whose
        consumed result has since gone stale (its read set no longer
        valid) must block downstream steps from binding to it, the same
        way §3.5's carry-over rule keeps it from counting as done for its
        own re-execution above."""
        latest = store.call_ledger.latest_by_step(goal_id, step_key)
        if latest is None or latest.status != CallStatus.CONSUMED:
            return False
        return store.facts.is_valid(latest.read_set).is_valid

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
                if store.config.reference_bound_identifiers and step.kind == StepKind.WRITE:
                    prop_schema = self._param_schema(store, step.tool, param)
                    if _is_identifier_param(param, prop_schema) and not self._identifier_origin_ok(
                        store, binding, binding.value, None
                    ):
                        return None, f"identifier.{step.tool}.{param}"
                args[param] = binding.value
                continue

            if binding.kind == BindingKind.FACT:
                key = (binding.fact_key or "").replace("$G", goal_id)
                if not key:
                    return None, f"slot.{goal_id}.{param}"
                if "$Q" in key:
                    # V3: substitute the goal's current perception question
                    # id, same substitution style as $G. No question yet ->
                    # blocked exactly like a missing fact (waits, no ask —
                    # see propose_ready_calls' claim.* skip above).
                    question = store.evidence.latest_active_question(goal_id)
                    if question is None:
                        return None, key
                    key = key.replace("$Q", question.question_id)
                if store.config.vision_enabled and key.startswith(f"slot.{goal_id}."):
                    # V3: an open perception conflict on this slot name
                    # blocks the step even though the user's own value is
                    # still on record (§9.1: rank 1/2 vs rank 3, different
                    # -> "Open conflict; blocks steps using the slot").
                    name = key[len(f"slot.{goal_id}."):]
                    if store.evidence.conflict_open(goal_id, name):
                        return None, key
                fact = store.facts.get(key)
                if fact is None or fact.status in (FactStatus.RETRACTED, FactStatus.HYPOTHESIS):
                    return None, key
                if store.config.reference_bound_identifiers and step.kind == StepKind.WRITE:
                    prop_schema = self._param_schema(store, step.tool, param)
                    if _is_identifier_param(param, prop_schema) and not self._identifier_origin_ok(
                        store, binding, fact.value, fact
                    ):
                        return None, f"identifier.{step.tool}.{param}"
                args[param] = fact.value
                read_keys.append(key)
                continue

            if binding.kind == BindingKind.STEP_OUTPUT:
                upstream = store.call_ledger.latest_by_step(goal_id, binding.step_key)
                if upstream is None or upstream.status != CallStatus.CONSUMED:
                    return None, f"result.<{binding.step_key}>"

                # V2: if the upstream step mapped this exact path to a
                # derived fact, bind to *that* instead of re-deriving from
                # the raw result — the derived fact carries a
                # derivation_read_set, so a later slot change transitively
                # retracts it and cancels this (downstream) call in the same
                # step, instead of silently reading a now-stale value
                # (`kernel/invalidation.py`). Falls back to the V1 path
                # (read result.<call_id> directly) whenever there's no
                # matching output_map entry, so plans that don't use
                # output_map keep working exactly as before.
                if store.config.transitive_invalidation and binding.path:
                    upstream_step = self._find_step(store, goal_id, binding.step_key)
                    derived_name = upstream_step.output_map.get(binding.path) if upstream_step else None
                    if derived_name:
                        derived_key = derived_name.replace("$G", goal_id)
                        fact = store.facts.get(derived_key)
                        if fact is None or fact.status == FactStatus.RETRACTED:
                            return None, derived_key
                        args[param] = fact.value
                        read_keys.append(derived_key)
                        continue

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

        # V2: absence entries — an optional constraint the step's validity
        # implicitly assumes is unset (I-12). Only added while it's still
        # genuinely absent; if it's already present the step must be
        # re-planned around it, not bound as if it weren't there.
        if store.config.absence_read_sets:
            for key_template in step.absence_keys:
                key = key_template.replace("$G", goal_id)
                fact = store.facts.get(key)
                if fact is not None and fact.status not in (FactStatus.RETRACTED, FactStatus.HYPOTHESIS):
                    return None, key
                read_keys.append(key)

        read_set = store.facts.build_read_set(sorted(set(read_keys)))
        return BindResult(args=args, read_set=read_set), None

    def _param_schema(self, store, tool: str, param: str) -> dict:
        spec = store.catalog.get(tool)
        if spec is None:
            return {}
        return (spec.params_schema.get("properties") or {}).get(param) or {}

    def _identifier_origin_ok(self, store, binding, value, fact) -> bool:
        """C10: an identifier argument must come from a consumed read
        result (`step_output` — always acceptable, checked by the caller
        before this path is reached), a tool-derived fact (V2
        `derived.*`/`output_map`, `provenance.source == "tool"`), a
        user-stated fact (`provenance.source == "user"`), or a literal that
        matches verbatim text the user actually said. A perception-sourced
        fact (V3) is deliberately *not* accepted — booking against a vision
        guess is exactly what this rule exists to prevent."""
        if binding.kind == BindingKind.FACT:
            return fact is not None and fact.provenance.source in ("user", "tool")
        if binding.kind == BindingKind.LITERAL:
            if not isinstance(value, str) or not value:
                return False
            return any(value in " ".join(c.text for c in turn.chunks) for turn in store.turn_log.all())
        return False

    def _find_step(self, store, goal_id: str, step_key: str):
        plan = store.plans.current(goal_id)
        if plan is None:
            return None
        return next((s for s in plan.steps if s.step_key == step_key), None)

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
        """A step whose retries are exhausted is a genuine task failure,
        not a completion — `goal.status` must become `ABANDONED` (not
        left `ACTIVE`) so `FastResponder._final`'s existing
        `task_completed = goal.status != GoalStatus.ABANDONED` check
        correctly reports `task_completed=False`. Before this fix, a
        completely ordinary scenario (a tool erroring past
        `max_read_retries`/`max_write_retries`) produced a FINAL whose
        own text said "I couldn't complete this" while still claiming
        `task_completed=True` — a real false-completion-claim bug,
        against this project's own core invariant, found by an
        independent review's F-SONNET-2 investigation into a related
        livelock and confirmed by direct reproduction
        (`tests/test_fail_goal_regression.py`)."""
        goal = store.goals.get(goal_id)
        if goal is not None and goal.task_state == TaskState.RESPONDING:
            return  # already reported
        store.goals.update(goal_id, status=GoalStatus.ABANDONED, task_state=TaskState.RESPONDING)
        store.facts.set(
            f"compose.{goal_id}.text",
            f"I couldn't complete this — {step.tool} kept failing.",
            FactStatus.COMMITTED,
            Provenance(source="system", step_no=step_no, ts_us=now_us),
            rule="executor.step_failed",
        )
