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

from prism_rt.kernel.action_plans import bind_key, step_failed, step_failed_key, valid_bind_fact
from prism_rt.kernel.proposals import plain_slot_name
from prism_rt.kernel.interpret_apply import active_goal_id
from prism_rt.kernel.replies import duplicate_write_text
from prism_rt.model.actions import CancelBody, IntendedAction
from prism_rt.model.types import (
    EMPTY_READ_SET,
    ActionType,
    BindingKind,
    CallRecord,
    CallStatus,
    EffectStatus,
    FactStatus,
    GoalStatus,
    Provenance,
    Question,
    QuestionMode,
    QuestionStatus,
    QuestionTarget,
    ReadSet,
    StepKind,
    TaskState,
    ToolMutability,
    fingerprint_for,
)

# A camera frame newer than this counts as a live camera (`_look_before_asking`).
_LIVE_CAMERA_US = 5_000_000

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
class _FieldSearchResult:
    found: bool
    ambiguous: bool
    value: object = None


def _bfs_find_field(root: object, field_name: str) -> _FieldSearchResult:
    """Breadth-first, schema-free search for a dict key named
    `field_name` anywhere under `root` (docs/fdb_v3_implementation_plan.md
    §5.8): a live Planner names a chained-call binding's `path` from the
    upstream tool's *name* alone -- FDB publishes no result schemas, so a
    literal dotted path like "product_id" routinely doesn't match the
    real nesting (e.g. `search_products` returns
    `{"products": [{"product_id": ...}]}`, not a top-level `product_id`).
    Used only as a fallback once the literal path has already failed to
    resolve (`_bind`'s STEP_OUTPUT branch) -- never overrides a path that
    already worked. Two or more *distinct* matches are reported
    ambiguous rather than picking one: the step must block and clarify,
    never guess silently."""
    queue: list = [root]
    matches: list = []
    seen_ids: set[int] = set()
    while queue:
        node = queue.pop(0)
        if id(node) in seen_ids:
            continue
        seen_ids.add(id(node))
        if isinstance(node, dict):
            if field_name in node:
                matches.append(node[field_name])
            queue.extend(node.values())
        elif isinstance(node, list):
            queue.extend(node)
    if not matches:
        return _FieldSearchResult(found=False, ambiguous=False)
    distinct = []
    for m in matches:
        if m not in distinct:
            distinct.append(m)
    if len(distinct) > 1:
        return _FieldSearchResult(found=True, ambiguous=True)
    return _FieldSearchResult(found=True, ambiguous=False, value=distinct[0])


@dataclass(frozen=True)
class BindResult:
    args: dict
    read_set: ReadSet


def _fingerprint(store, tool: str, args: dict) -> str:
    """Duplicate-detection fingerprint over the arguments as the tool will
    receive them: schema defaults filled in. The FDB adapter fills them only
    after emission, so {K2} and {K2, quantity: 1} used to look like two
    different writes (1 Oct audit). Emitted arguments are unchanged."""
    spec = store.catalog.get(tool)
    props = ((spec.params_schema or {}).get("properties") or {}) if spec is not None else {}
    filled = dict(args)
    for name, schema in props.items():
        if name not in filled and isinstance(schema, dict) and "default" in schema:
            filled[name] = schema["default"]
    return fingerprint_for(tool, filled)


class PlanExecutor:
    def expire_deadlines(self, store, now_us: int) -> list[IntendedAction]:
        """P0.4 (call deadlines, docs/original_design_audit.md D4,
        blueprint §5.9): a call still `IN_FLIGHT` past its deadline
        (`Config.call_deadline_read_ms`/`call_deadline_write_ms`) is
        treated as dropped instead of silently hanging until the
        scenario-wide watchdog. Clock-driven -- checked every step
        regardless of events; the timer scheduled in `kernel/emission.py.
        _apply_side_effects` is a pure liveness hint (P0.1 is what makes
        the driver actually re-step often enough for this to matter with
        no further external event).

        Emits an ordinary CANCEL (reason `"deadline_expired"`) for each
        expired call, through the *same* channel every other cancellation
        in this architecture uses (`kernel/invalidation.py.
        cancellation_actions`'s `IntendedAction`/`EmissionGate` path) --
        not a direct internal status mutation. This matters for real
        reasons, not just trace tidiness: nothing in the trace otherwise
        tells a real harness (or `sim/checker.py`'s CS-14) that this
        specific call has been given up on before a replacement might be
        proposed, and the call's real-world side effect could still be
        genuinely in flight. Emitting CANCEL also means a late, real
        result -- if the drop was actually just slow, not permanent --
        is correctly routed through `ResultRouter`'s existing
        `CANCEL_REQUESTED -> completed_after_cancel` branch and W4's
        reconciliation (`kernel/responder.py._reconcile_completed_after_
        cancel`), instead of being silently discarded as a duplicate of
        an already-terminal call.

        The retry-table decision itself (§5.9: reads retry bounded by
        `max_read_retries`; writes get an honest, ambiguous-outcome
        INFORM + CLARIFY instead of auto-retrying) happens in
        `propose_ready_calls`'s `CANCEL_REQUESTED` branch, once this
        CANCEL has actually been emitted and applied (`cancel_reason ==
        "deadline_expired"` distinguishes it from an ordinary correction-
        triggered cancel, which gets an immediate fresh attempt with no
        such gating).

        Only ever acts on a call still `CallStatus.IN_FLIGHT` -- a call
        already `CANCEL_REQUESTED` (an interruption/correction in
        flight, R-04's shape) is a different status and is silently
        skipped here, so its own late "deliver anyway" result is never
        misread as a timeout."""
        read_deadline_us = store.config.call_deadline_read_ms * 1000
        write_deadline_us = store.config.call_deadline_write_ms * 1000
        actions: list[IntendedAction] = []
        for call in store.call_ledger.all():
            if call.status != CallStatus.IN_FLIGHT or call.emitted_ts_us is None:
                continue
            deadline_us = write_deadline_us if call.kind == StepKind.WRITE else read_deadline_us
            if now_us - call.emitted_ts_us < deadline_us:
                continue
            actions.append(
                IntendedAction(
                    action_type=ActionType.CANCEL,
                    body=CancelBody(target_call_id=call.call_id, reason="deadline_expired"),
                    read_set=EMPTY_READ_SET,
                    rule_id="P0.4",
                )
            )
        return actions

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
        waiting_on_user: set[str] = set()  # steps that just asked the user for a value
        for step in plan.steps:
            if step_failed(store, gid, step.step_key):
                continue  # failed on its own (Config.partial_failure_continues)
            failed_input = next(
                (b.step_key for b in step.bindings.values() if b.step_key and step_failed(store, gid, b.step_key)), None
            )
            if failed_input is not None:
                upstream = next((s.tool for s in plan.steps if s.step_key == failed_input), failed_input)
                self._fail_goal(store, gid, step, now_us, step_no, reason=f"it needed the result of {upstream}, which failed")
                continue
            latest = store.call_ledger.latest_by_step(gid, step.step_key)

            if latest is not None and latest.status == CallStatus.IN_FLIGHT:
                continue  # already in progress

            attempt = 1
            if latest is not None and latest.status == CallStatus.PROPOSED:
                if self._write_lineage_confirmed_elsewhere(store, gid, step, latest.fingerprint):
                    # This call is sitting PROPOSED, its own read set still
                    # valid, waiting on CommitGate — but a *different*
                    # write for the same lineage confirmed *after* this
                    # one was created (the exact race: the corrected call
                    # was proposed before the stale original's real result
                    # arrived). G6 will now block it forever; see
                    # _write_lineage_confirmed_elsewhere's docstring.
                    self._fail_goal(
                        store, gid, step, now_us, step_no,
                        reason=f"a previous {step.tool} request already went through before I could change it",
                    )
                    continue
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
                # S-01 / §5.9 (P0.2, docs/original_design_audit.md D3): a
                # write is retried only when the failed result explicitly
                # said `retryable: true` -- absent or `false` means the
                # tool told us this outcome is final. Retrying anyway was
                # exactly D3: a second call with the identical fingerprint
                # got created, was immediately blocked by CommitGate G5
                # (the first attempt's effect, now FAILED/UNKNOWN, still
                # occupies that fingerprint), and sat PROPOSED forever --
                # while FastResponder spoke a false "already taken care
                # of". Reads are the mirror image: absent still retries
                # (bounded by max_read_retries), only an explicit `false`
                # blocks it -- §5.9's retry table gives reads and writes
                # opposite defaults for the absent case.
                if step.kind == StepKind.WRITE:
                    if latest.retryable is not True:
                        self._fail_goal(
                            store, gid, step, now_us, step_no,
                            reason=f"{step.tool} failed and can't be safely retried",
                        )
                        continue
                    max_retries = store.config.max_write_retries
                else:
                    if latest.retryable is False:
                        self._fail_goal(store, gid, step, now_us, step_no)
                        continue
                    max_retries = store.config.max_read_retries
                if latest.attempt > max_retries:
                    self._fail_goal(store, gid, step, now_us, step_no)
                    continue
                attempt = latest.attempt + 1
            elif latest is not None and latest.status == CallStatus.CANCEL_REQUESTED and latest.cancel_reason == "deadline_expired":
                # P0.4 (S-01/S-02, docs/original_design_audit.md D4):
                # distinct from an ordinary correction-triggered cancel
                # just below -- this call was cancelled because its
                # deadline expired (kernel/executor.py.PlanExecutor.
                # expire_deadlines), not because a correction made it
                # stale. §5.9's retry table applies, not "immediate fresh
                # attempt, no gating".
                if step.kind == StepKind.WRITE:
                    lineage = f"{gid}:{step.step_key}"
                    declined = store.facts.get(f"retry_declined.{lineage}")
                    if declined is not None and declined.status != FactStatus.RETRACTED and declined.value:
                        # kernel/interpret_apply.py: the user already said
                        # "no" to retrying this exact lineage -- ending
                        # the wait must not mean asking again forever.
                        # The effect stays UNKNOWN (permanently
                        # unresolved); this project builds no automatic
                        # retry path for a declined confirmation.
                        self._fail_goal(
                            store, gid, step, now_us, step_no,
                            reason=f"you asked me not to retry {step.tool}, so I stopped — its outcome is still unknown",
                        )
                        continue
                    effect = store.effect_ledger.by_fingerprint(latest.fingerprint)
                    if effect is not None and effect.status == EffectStatus.PENDING:
                        # The outcome is genuinely unknown, not a definite
                        # failure (never FAILED here -- that's P0.2's
                        # definite-error case).
                        store.effect_ledger.set_status(latest.fingerprint, EffectStatus.UNKNOWN)
                        effect = store.effect_ledger.by_fingerprint(latest.fingerprint)
                    if effect is not None and effect.status == EffectStatus.UNKNOWN:
                        # Still unresolved. Bounded exactly like a
                        # retryable write error (P0.2, max_write_retries)
                        # -- a *confirmed* retry that also times out must
                        # not ask again forever.
                        if latest.attempt > store.config.max_write_retries:
                            self._fail_goal(store, gid, step, now_us, step_no)
                            continue
                        # No auto-retry; ask the user (kernel/responder.py
                        # renders this as INFORM + CLARIFY) and wait.
                        # Re-entering this branch on a later step (still
                        # unanswered) is a harmless no-op -- _ask_for only
                        # speaks once per target.
                        self._ask_for(store, gid, f"retry:{lineage}", now_us, step_no)
                        continue
                    # The user has since confirmed (kernel/interpret_
                    # apply.py flipped the effect UNKNOWN -> FAILED) --
                    # exactly one further attempt, same as an ordinary
                    # correction-triggered cancel below.
                    attempt = latest.attempt + 1
                else:
                    if latest.attempt > store.config.max_read_retries:
                        self._fail_goal(store, gid, step, now_us, step_no)
                        continue
                    attempt = latest.attempt + 1
            elif latest is not None and latest.status == CallStatus.CANCEL_REQUESTED:
                # A step whose call was just cancelled (a correction
                # invalidated it) gets a fresh attempt immediately — this is
                # not a failure retry, so it never waits on max_*_retries or
                # on the stale call's eventual (irrelevant) result.
                attempt = latest.attempt + 1

            if not all(
                self._step_done(store, gid, dep)
                or step_failed(store, gid, dep)  # failed on its own; not read (checked above)
                or (
                    store.config.parallel_independent_actions
                    and dep in waiting_on_user
                    and not any(b.step_key == dep for b in step.bindings.values())
                )
                for dep in step.after
            ):
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
                if missing_key is not None and (
                    missing_key.startswith("claim.")
                    or missing_key.startswith("bind.")  # S3: kernel/binder.py will decide it -- never ask the user
                    or self._perception_will_answer(store, gid, missing_key)
                    or self._look_before_asking(store, gid, step, missing_key, now_us)
                ):
                    continue
                self._ask_for(store, gid, missing_key, now_us, step_no)
                waiting_on_user.add(step.step_key)
                continue

            validation = store.catalog.validate_args(step.tool, bind_result.args)
            if not validation.valid:
                # G8 (args valid) can never pass for these exact args -- they
                # only change if a bound fact changes, and that path already
                # replaces a stale call. Creating the call anyway left it
                # PROPOSED forever, silently (same livelock class as S-07's
                # unknown tool): e.g. a live model returning destination=42
                # for a string param. A bad user-supplied slot is asked about
                # (the user can fix it); anything else fails honestly.
                bad_slot = self._invalid_slot_input(step, gid, validation.errors)
                if bad_slot is not None:
                    self._ask_for(store, gid, bad_slot, now_us, step_no)
                    waiting_on_user.add(step.step_key)
                else:
                    self._fail_goal(
                        store, gid, step, now_us, step_no,
                        reason=f"{step.tool} was given an input it can't accept",
                    )
                continue

            if step.kind == StepKind.WRITE:
                stale_key = self._stale_current_state_input(store, gid, bind_result.read_set, now_us)
                if stale_key is not None:
                    self._request_fresh_view(store, gid, stale_key, now_us, step_no)
                    continue

            new_fingerprint = _fingerprint(store, step.tool, bind_result.args)
            if self._write_lineage_confirmed_elsewhere(store, gid, step, new_fingerprint):
                self._fail_goal(
                    store, gid, step, now_us, step_no,
                    reason=f"a previous {step.tool} request already went through before I could change it",
                )
                continue
            if self._already_done_elsewhere(store, gid, plan, step, new_fingerprint):
                self._finish_with_prior_effect(store, gid, step, bind_result.args, now_us, step_no)
                continue

            call_id = store.ids.next("call")
            call = CallRecord(
                call_id=call_id,
                goal_id=gid,
                step_key=step.step_key,
                tool=step.tool,
                kind=step.kind,
                args=bind_result.args,
                fingerprint=new_fingerprint,
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
                    name = plain_slot_name(key[len(f"slot.{goal_id}."):])
                    if store.evidence.conflict_open(goal_id, name):
                        return None, key
                fact = store.facts.get(key)
                if (fact is None or fact.status in (FactStatus.RETRACTED, FactStatus.HYPOTHESIS)) and step.slot_prefix:
                    # A compiled step binds its own per-action key
                    # (slot.<g>.a0.led_state), but perception writes the plain
                    # slot (slot.<g>.led_state). Found live, 30 Sep: the camera
                    # answered and the goal kept asking. Use a perception-sourced
                    # plain slot when the action has no value of its own -- a
                    # value the user stated for the action always wins.
                    flat_key = f"slot.{goal_id}.{param}"
                    flat = store.facts.get(flat_key)
                    if (
                        store.config.vision_enabled
                        and flat is not None
                        and flat.status not in (FactStatus.RETRACTED, FactStatus.HYPOTHESIS)
                        and flat.provenance.source == "perception"
                        and not store.evidence.conflict_open(goal_id, param)
                    ):
                        # Keep the action's own key in the read set (as
                        # absent): a value the user states for it later must
                        # invalidate a call built on the camera's value.
                        read_keys.append(key)
                        key, fact = flat_key, flat
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

            if binding.kind == BindingKind.LATE:
                # S3 (kernel/binder.py): the value BindScheduler picked from
                # the upstream step's real result. Missing, retracted, or
                # grounded in a result that has since changed -> wait (see
                # propose_ready_calls: a `bind.` key never clarifies).
                key = bind_key(goal_id, step.step_key, param)
                fact = valid_bind_fact(store, key)
                if fact is None:
                    return None, key
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

                # P0.3 (C5 core, docs/original_design_audit.md D2): read
                # the step-key-scoped fact (kernel/reducers.py._write_
                # stepout_fact), not `result.<call_id>` directly — that
                # key is pinned to one specific call forever and never
                # goes stale, so a downstream step bound to it silently
                # kept reading a superseded upstream call's result after a
                # correction re-ran that step under a new call_id. This
                # fact is stable across re-executions of the same step and
                # carries the producing call's own read set, so it's
                # retracted/changed exactly when the upstream step
                # actually re-runs with different bindings.
                result_key = f"stepout.{goal_id}.{binding.step_key}"
                fact = store.facts.get(result_key)
                if fact is None or fact.status == FactStatus.RETRACTED:
                    return None, result_key
                value = fact.value
                if binding.path:
                    resolved = value
                    for part in binding.path.split("."):
                        if isinstance(resolved, dict):
                            resolved = resolved.get(part)
                        elif isinstance(resolved, list) and part.lstrip("-").isdigit():
                            index = int(part)
                            resolved = resolved[index] if -len(resolved) <= index < len(resolved) else None
                        else:
                            resolved = None
                        if resolved is None:
                            break
                    if resolved is None:
                        # Day 1 (docs/fdb_v3_implementation_plan.md §5.8):
                        # the literal path didn't resolve -- fall back to
                        # a generic, schema-free search for a field with
                        # that name anywhere in the upstream result,
                        # rather than silently binding None. Never guess
                        # on an ambiguous match: block the step for a
                        # clarify instead (same path a missing fact takes).
                        field_name = binding.path.rsplit(".", 1)[-1]
                        found = _bfs_find_field(value, field_name)
                        if found.ambiguous:
                            return None, f"ambiguous_field.{step.tool}.{param}.{field_name}"
                        if found.found:
                            resolved = found.value
                    value = resolved
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

            read_keys.extend(self._schema_complete_absence_keys(store, goal_id, step, args))

        read_set = store.facts.build_read_set(sorted(set(read_keys)))
        return BindResult(args=args, read_set=read_set), None

    def _schema_complete_absence_keys(self, store, goal_id: str, step, args: dict) -> list[str]:
        """C6 (`docs/prompt 3.txt`): schema-complete read sets. A call's
        read set must cover every parameter in its tool's schema, not just
        the ones this plan step's bindings happened to mention -- otherwise
        a consumed result stays valid after the user states an optional
        constraint the call never read at all (the "omission escape" bug
        class, L2: "results consumed despite a later-set optional parameter
        in the same goal before FINAL"). Every OPTIONAL schema parameter
        not already in `args` gets tracked too, at the same `slot.$G.<name>`
        key every FACT binding and `step.absence_keys` entry already use --
        `build_read_set` below resolves each to whatever it currently is
        (present or absent), so this is "track the key," not "force
        absent." Required parameters are excluded: `validate_args` already
        refuses to create a call missing one, so a required param is
        always already bound by the time a call is actually created --
        adding it here would be a harmless no-op at best, never a gap.

        absence_sensitivity (policy default in the architecture doc: user-
        sourced facts only) -- a slot name currently targeted by the
        goal's tracked perception question is deliberately excluded. V3's
        own claim-acceptance path (`kernel/perception.py.on_perception_
        result`) already governs exactly those names (conflict handling,
        clarify-resolution, leases); the only non-user writer of
        `slot.<goal>.<name>` facts in this codebase is perception, so
        without this exemption a HIGH-confidence vision claim landing on
        an unrelated optional parameter would retroactively invalidate
        already-completed work purely because no call ever happened to
        bind that name -- exactly the perception-driven churn the doc
        calls out as the default policy to avoid."""
        perception_governed: frozenset[str] = frozenset()
        if store.config.vision_enabled:
            question = store.evidence.latest_active_question(goal_id)
            if question is not None:
                perception_governed = frozenset(t.name for t in question.targets)
        spec = store.catalog.get(step.tool)
        if spec is None:
            return []
        schema = spec.params_schema or {}
        required = frozenset(schema.get("required") or ())
        properties = schema.get("properties") or {}
        # S3: a compiled step's own slots live at slot.<gid>.a<i>.<name>
        # (kernel/action_plans.py) -- tracking the flat key would let a
        # later "a1.mode" correction miss the consumed call entirely.
        prefix = f"slot.{goal_id}.{step.slot_prefix}." if step.slot_prefix else f"slot.{goal_id}."
        return [
            f"{prefix}{name}"
            for name in properties
            if name not in args and name not in required and name not in perception_governed
        ]

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

    def _perception_will_answer(self, store, goal_id: str, key: str) -> bool:
        """V3: a step bound through `slot.$G.<name>` (AT_UTTERANCE targets,
        and what the live PLAN prompt teaches) was treated as missing *user*
        input whenever VISION was slower than PLAN -- the agent asked the
        user about the very thing it was looking at, and the goal then sat
        in CLARIFYING forever, since a perception-filled slot never cleared
        `clarify_target`. Same rule as the `claim.*` skip in
        `propose_ready_calls`, extended to slot keys: wait while perception
        can still answer (a VISION job is in flight, or the current evidence
        hasn't been analyzed yet). Once analyzed without an answer (LOW --
        M-09), or with an open conflict (M-06), fall through and ask."""
        if not store.config.vision_enabled or not key.startswith(f"slot.{goal_id}."):
            return False
        name = plain_slot_name(key[len(f"slot.{goal_id}."):])  # a compiled action's key too
        if store.evidence.conflict_open(goal_id, name):
            return False
        question = store.evidence.latest_active_question(goal_id)
        if question is None or question.status != QuestionStatus.OPEN:
            return False
        if name not in {t.name for t in question.targets}:
            return False
        return question.pending_job_id is not None or question.analyzed_obs_id is None

    def _look_before_asking(self, store, goal_id: str, step, key: str, now_us: int) -> bool:
        """With the camera on, a lookup's missing value is looked for in the
        latest frame once before the user is asked. Perception otherwise ran
        only when the interpreter flagged a visual reference, and it missed
        "I'm going to show it to you": found live, 30 Sep, the agent asked
        for the LED colour three times while the camera was pointed at it.
        Read-only tools only (a booking's date is never on camera), only
        while frames are arriving, and only once per goal: if the look
        comes back without the value, `_perception_will_answer` lets the
        question through to the user."""
        if not store.config.vision_enabled or not key.startswith(f"slot.{goal_id}."):
            return False
        spec = store.catalog.get(step.tool)
        if spec is None or spec.mutability != ToolMutability.READ_ONLY:
            return False
        frames = store.evidence.observations_by_modality("frame")
        if not frames or now_us - frames[-1].capture_ts_us > _LIVE_CAMERA_US:
            return False
        if store.evidence.latest_active_question(goal_id) is not None:
            return False
        names = []
        for param, binding in step.bindings.items():
            if binding.kind != BindingKind.FACT or not (binding.fact_key or "").startswith("slot.$G."):
                continue
            slot_key = binding.fact_key.replace("$G", goal_id)
            fact = store.facts.get(slot_key)
            if fact is None or fact.status in (FactStatus.RETRACTED, FactStatus.HYPOTHESIS):
                names.append(param)
        if plain_slot_name(key[len(f"slot.{goal_id}."):]) not in names:
            return False
        store.evidence.create_question(
            Question(
                question_id=store.ids.next("question"),
                goal_id=goal_id,
                targets=tuple(
                    QuestionTarget(name=n, description=self._param_schema(store, step.tool, n).get("description", ""))
                    for n in names
                ),
                mode=QuestionMode.AT_UTTERANCE,
                anchor_ts_us=now_us,
                created_by="demand",
            )
        )
        # PerceptionScheduler ran earlier in this DECIDE phase; wake the
        # kernel so it dispatches the look without waiting for the next frame.
        store.timers.schedule(f"look_wake:{goal_id}", now_us + 1)
        return True

    def _invalid_slot_input(self, step, goal_id: str, errors) -> str | None:
        """The `slot.<goal>.<name>` key behind the first invalid argument,
        if that argument is FACT-bound to a user/perception slot -- else
        None (planner literal, step output, derived fact)."""
        for error in errors:
            _, _, param = error.partition(":")
            binding = step.bindings.get(param)
            if binding is None or binding.kind != BindingKind.FACT:
                continue
            key = (binding.fact_key or "").replace("$G", goal_id)
            if key.startswith(f"slot.{goal_id}."):
                return key
        return None

    def _stale_current_state_input(self, store, goal_id: str, read_set, now_us: int) -> str | None:
        """M-05 / CS-25 (`docs/prompt 2.txt` §10 "Freshness"): a frame gap
        doesn't invalidate a CURRENT_STATE claim, but a WRITE must not
        assume the state persisted across one. Returns the first
        perception-sourced input this write would bind if the goal's
        CURRENT_STATE question has had no frame for longer than
        `frame_gap_ms`, else None. Checked against the latest frame's
        *capture* time -- a claim's own provenance timestamp is when the
        frame was analyzed, which is exactly how a 5s-old frame analyzed
        just now used to look fresh. A user-stated value (provenance
        "user") is never gated: they told us, not the camera."""
        if not store.config.vision_enabled:
            return None
        question = store.evidence.latest_active_question(goal_id)
        if question is None or question.mode != QuestionMode.CURRENT_STATE:
            return None
        target_names = {t.name for t in question.targets}
        stale_key = None
        for entry in read_set.entries:
            key = entry.key
            if key.startswith(f"slot.{goal_id}."):
                name = key[len(f"slot.{goal_id}."):]
            elif key.startswith(f"claim.{question.question_id}."):
                name = key[len(f"claim.{question.question_id}."):]
            else:
                continue
            fact = store.facts.get(key)
            if name in target_names and fact is not None and fact.provenance.source == "perception":
                stale_key = key
                break
        if stale_key is None:
            return None
        frames = store.evidence.observations_by_modality("frame")
        if frames and now_us - frames[-1].capture_ts_us <= store.config.frame_gap_ms * 1000:
            return None
        return stale_key

    def _request_fresh_view(self, store, goal_id: str, target_key: str, now_us: int, step_no: int) -> None:
        """Reuses lease renewal's shape (`PerceptionScheduler.expire_leases`)
        without retracting anything -- the claim stays valid for reads.
        The question re-opens with the stale frame already marked analyzed
        (so it's never re-sent); the next *new* frame gets analyzed, and a
        HIGH claim on this slot resolves the clarification through the
        existing `perception.clarify_resolved` path."""
        question = store.evidence.latest_active_question(goal_id)
        frames = store.evidence.observations_by_modality("frame")
        store.evidence.update_question(
            question.question_id,
            status=QuestionStatus.OPEN,
            created_by="renewal",
            pending_job_id=None,
            pending_obs_id=None,
            analyzed_obs_id=frames[-1].obs_id if frames else None,
        )
        store.facts.set(
            f"perception.{goal_id}.fresh_view_for",
            target_key,
            FactStatus.COMMITTED,
            Provenance(source="system", step_no=step_no, ts_us=now_us),
            rule="executor.fresh_view",
        )
        self._ask_for(store, goal_id, target_key, now_us, step_no)

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
        # TaskStateMachine runs before this in DECIDE, so it only sees the
        # new CLARIFYING state (and dispatches the Q5 re-extraction) on the
        # next step. Found in the 30 Sep voice rerun: nothing else woke the
        # kernel, and the goal sat silent until the 15 s stall salvage.
        store.timers.schedule(f"clarify_wake:{goal_id}", now_us + 1)

    def _write_lineage_confirmed_elsewhere(self, store, goal_id: str, step, fingerprint: str) -> bool:
        """True iff `CommitGate`'s G6 ("no existing effect with the same
        lineage, PENDING or CONFIRMED") will block a call for this step
        *forever*: a different-fingerprint effect on the same lineage
        (`{goal_id}:{step_key}`) has already `CONFIRMED`. A same-fingerprint
        match is a literal retry — G5's job, not this one, and excluded by
        the fingerprint check.

        This is a real, found-and-confirmed livelock (the same class as
        S-07's unusable-tool one): a corrected write (different arguments,
        proposed *before* the original's real result was known) can sit
        PROPOSED with a perfectly valid read set — nothing about *it* is
        stale — while the original, despite being cancelled, goes on to
        confirm on the same lineage. `docs/prompt 2.txt` §8.8's own worked
        example is exactly this shape (a 6pm booking goes through anyway;
        the corrected 8pm booking must still get a chance) and describes
        the intended resolution as a "modify" tool call or an interactive
        CLARIFY — machinery this project doesn't build. The safe default
        used here instead is an honest failure naming what already
        happened, via `_fail_goal` — never a silent hang, and never an
        automatic double-booking attempt. Checked at both the moment a
        fresh call would be created and every step an already-PROPOSED
        call is re-examined, since the confirming result can arrive
        either before or after the corrected call exists."""
        if step.kind != StepKind.WRITE:
            return False
        lineage_effect = store.effect_ledger.by_lineage(f"{goal_id}:{step.step_key}")
        return (
            lineage_effect is not None
            and lineage_effect.status == EffectStatus.CONFIRMED
            and lineage_effect.fingerprint != fingerprint
        )

    def _already_done_elsewhere(self, store, goal_id: str, plan, step, fingerprint: str) -> bool:
        """An identical write (same tool, same arguments with schema defaults
        filled) already CONFIRMED this session, and it is this goal's only
        unfinished work. Creating the call would leave it blocked by G5
        forever: the goal never finished, an ACK said "working on it" and
        the stall salvage later claimed nothing had been done (1 Oct audit,
        ecommerce_13). With other steps still to run, the call is created
        as before and G5's notice explains the block."""
        if step.kind != StepKind.WRITE:
            return False
        effect = store.effect_ledger.by_fingerprint(fingerprint)
        if effect is None or effect.status != EffectStatus.CONFIRMED:
            return False
        return all(other.step_key == step.step_key or self._step_done(store, goal_id, other.step_key) for other in plan.steps)

    def _finish_with_prior_effect(self, store, goal_id: str, step, args: dict, now_us: int, step_no: int) -> None:
        """The success counterpart of `_fail_goal`: the request is already
        satisfied, so the goal answers with what went through and completes
        (status stays ACTIVE until the FINAL is emitted, so it reports
        task_completed=True)."""
        goal = store.goals.get(goal_id)
        if goal is not None and goal.task_state == TaskState.RESPONDING:
            return
        store.goals.update(goal_id, task_state=TaskState.RESPONDING)
        store.facts.set(
            f"compose.{goal_id}.text",
            duplicate_write_text(step.tool, args),
            FactStatus.COMMITTED,
            Provenance(source="system", step_no=step_no, ts_us=now_us),
            rule="executor.already_done",
        )

    def _fail_step_only(self, store, goal_id: str, step, now_us: int, step_no: int, reason: str | None) -> bool:
        """Config.partial_failure_continues, compiled plans: record this
        step as failed and keep the goal going while any other step can
        still complete, or has completed (TaskStateMachine then composes an
        answer that lists the failed actions as not done). False -- the
        whole goal fails as before -- when nothing else ran or can run."""
        plan = store.plans.current(goal_id)
        if not store.config.partial_failure_continues or plan is None or plan.origin != "compiled" or len(plan.steps) < 2:
            return False
        others = [s for s in plan.steps if s.step_key != step.step_key and not step_failed(store, goal_id, s.step_key)]
        if not others:
            return False
        store.facts.set(
            step_failed_key(goal_id, step.step_key),
            reason or f"{step.tool} kept failing",
            FactStatus.COMMITTED,
            Provenance(source="system", step_no=step_no, ts_us=now_us),
            rule="executor.step_failed_only",
        )
        return True

    def _fail_goal(self, store, goal_id: str, step, now_us: int, step_no: int, *, reason: str | None = None) -> None:
        """A step that can never complete is a genuine task failure, not
        a completion — `goal.status` must become `ABANDONED` (not left
        `ACTIVE`) so `FastResponder._final`'s existing `task_completed =
        goal.status != GoalStatus.ABANDONED` check correctly reports
        `task_completed=False`. Before this fix, a completely ordinary
        scenario (a tool erroring past `max_read_retries`/
        `max_write_retries`) produced a FINAL whose own text said "I
        couldn't complete this" while still claiming `task_completed=True`
        — a real false-completion-claim bug, against this project's own
        core invariant, found by an independent review's F-SONNET-2
        investigation into a related livelock and confirmed by direct
        reproduction (`tests/test_failure_honesty_regression.py`).
        `reason`, when given, replaces the default "kept failing" wording
        for callers whose step never even attempted a call (an unusable
        tool, or a permanently-blocked write lineage) — see this
        method's other two call sites."""
        goal = store.goals.get(goal_id)
        if goal is not None and goal.task_state == TaskState.RESPONDING:
            return  # already reported
        if self._fail_step_only(store, goal_id, step, now_us, step_no, reason):
            return
        store.goals.update(goal_id, status=GoalStatus.ABANDONED, task_state=TaskState.RESPONDING)
        text = f"I couldn't complete this — {reason}." if reason else f"I couldn't complete this — {step.tool} kept failing."
        store.facts.set(
            f"compose.{goal_id}.text",
            text,
            FactStatus.COMMITTED,
            Provenance(source="system", step_no=step_no, ts_us=now_us),
            rule="executor.step_failed",
        )
