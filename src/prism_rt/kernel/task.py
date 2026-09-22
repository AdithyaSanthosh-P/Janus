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
from prism_rt.model.types import (
    CallStatus,
    FactStatus,
    GoalStatus,
    JobKind,
    JobRecord,
    QuestionStatus,
    ReadSet,
    StepKind,
    TaskState,
)


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
        elif store.config.speculative_interpretation_enabled:
            requests.extend(self._speculative_interpret(store))

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
                    # Matches the EXECUTING branch's identical check above
                    # (line ~78) — a RETRACTED compose text must be
                    # treated the same as no text at all. Currently
                    # inert (the only retraction site,
                    # emission.py._complete_goal, always transitions
                    # task_state away from RESPONDING in the same write),
                    # but a real inconsistency between two copies of the
                    # same check is exactly the shape of bug this project
                    # has already been bitten by once (the correction-race
                    # fix, kernel/task.py._all_required_steps_done vs.
                    # kernel/executor.py.PlanExecutor._step_done) — found
                    # by an independent review (F-SONNET-1).
                    if (
                        pending_text is None or pending_text.status == FactStatus.RETRACTED
                    ) and not store.jobs.running_by_kind_goal(JobKind.COMPOSE, gid):
                        requests.append(self._build_compose_request(store, gid))

        return requests

    def _all_required_steps_done(self, store, goal_id: str) -> bool:
        """"Done" means CONSUMED *and still valid* — matching
        `PlanExecutor._step_done`'s own rule (§3.5's carry-over condition).
        Without the validity check, a step whose read set has gone stale
        since it consumed (e.g. a correction changed the slot it was bound
        to) still counts as "done," transitioning the goal straight to
        RESPONDING/COMPOSE in the same step `PlanExecutor.propose_ready_
        calls` — which runs *after* this in the fixed DECIDE order — would
        otherwise have re-executed it. That race let a COMPOSE job
        dispatched before the correction (whose own read set at the time
        only pinned `result.<call_id>`, not the slot behind it) go on to
        produce a FINAL grounded in pre-correction data. Found via an
        independent review (Antigravity/Opus, `reviews/opus/`), confirmed
        by direct reproduction; see `_build_compose_request`'s docstring
        for the other half of the fix."""
        plan = store.plans.current(goal_id)
        if plan is None:
            return False
        for step in plan.steps:
            latest = store.call_ledger.latest_by_step(goal_id, step.step_key)
            if latest is None or latest.status != CallStatus.CONSUMED:
                return False
            if not store.facts.is_valid(latest.read_set).is_valid:
                return False
        return True

    def _goal_slots(self, store, goal_id: str) -> dict:
        prefix = f"slot.{goal_id}."
        return {
            key[len(prefix):]: value
            for key, value in store.facts.snapshot_committed().items()
            if key.startswith(prefix)
        }

    def _speculative_interpret(self, store) -> list[DispatchRequest]:
        """Phase 6 (`docs/prompt 2.txt` §11.3): while a turn is open, keep
        at most one INTERPRET job in flight per turn, dispatched against
        the current prefix. Coalescing: if one is already running, do
        nothing here — `kernel/turns.py.TurnManager._detect_chunk_anchor`
        doesn't touch this, and a growing prefix simply makes the running
        (or last completed) job's cached digest stale, which this method
        notices next call and redispatches against the latest prefix.
        Never touches `session.pending_interpretation_turn` — that fact
        does not exist yet while the turn is open, and a speculative job's
        read set must never include it (excluded in `_build_interpret_
        request`), or the turn later closing (which sets that fact) would
        self-invalidate the very job this mechanism exists to promote."""
        turn_id = store.turn_log.open_turn_id()
        if turn_id is None:
            return []
        turn = store.turn_log.get(turn_id)
        if turn is None or not turn.chunks:
            return []
        if store.jobs.running_by_kind_turn(JobKind.INTERPRET, turn_id):
            return []  # coalesced — the running job will be redispatched against a newer prefix once it resolves
        prefix_fact = store.facts.get(f"turn.{turn_id}.prefix")
        if prefix_fact is None:
            return []
        cached_digest = store.facts.get(f"spec_interpret.{turn_id}.digest")
        if cached_digest is not None and cached_digest.status != FactStatus.RETRACTED and cached_digest.value == prefix_fact.value:
            return []  # already have a completed speculative result at exactly this prefix
        return [self._build_interpret_request(store, turn_id, speculative=True)]

    def _build_interpret_request(self, store, turn_id: str, *, speculative: bool = False) -> DispatchRequest:
        turn = store.turn_log.get(turn_id)
        transcript = " ".join(chunk.text for chunk in turn.chunks) if turn is not None else ""
        gid = active_goal_id(store)

        read_keys = [f"turn.{turn_id}.prefix", "goal.active", "catalog.version"]
        if not speculative:
            read_keys.append("session.pending_interpretation_turn")
        active_intent = None
        active_slots: dict = {}
        if gid is not None:
            intent_fact = store.facts.get(f"goal.{gid}.intent")
            active_intent = intent_fact.value if intent_fact is not None and intent_fact.status != FactStatus.RETRACTED else None
            read_keys.append(f"goal.{gid}.intent")
            active_slots = self._goal_slots(store, gid)
            read_keys.extend(f"slot.{gid}.{name}" for name in active_slots)

        # Live-model reliability fix: the Interpreter previously saw only
        # the bare transcript, with no indication of what slot *names* a
        # live model should use in slot_deltas -- nothing tied its guess to
        # this session's actual tool parameters, so a live model could (and
        # sometimes did) extract a value under a plausible-but-wrong name
        # (e.g. "city" instead of "destination"), which then bound to
        # nothing and produced a spurious CLARIFY. Mirrors
        # `_build_plan_request`'s existing `catalog_summary`, trimmed to
        # just name + required params (the Interpreter only needs to name
        # slots correctly, not validate full JSON Schema). `catalog.version`
        # added to the read set to match (a manifest change mid-turn should
        # invalidate an in-flight INTERPRET job the same way it already
        # invalidates PLAN/calls).
        tool_summary = [
            {"name": tool.name, "required_params": (tool.params_schema or {}).get("required", [])}
            for tool in store.catalog.usable_tools()
        ]

        view = {
            "transcript": transcript,
            "active_intent": active_intent,
            "active_slots": active_slots,
            "suspended_goals": [g.goal_id for g in store.goals.suspended()],
            "pending_clarification": None,
            "tools": tool_summary,
            "vision_enabled": store.config.vision_enabled,
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
        # V3: a live model has no way to know a step can legitimately bind
        # to a slot fact that doesn't exist *yet* -- one a pending VISION
        # question will fill in once it resolves (`kernel/perception.py`'s
        # module docstring: the Question is created in APPLY, before
        # DECIDE dispatches this PLAN job, in the same step, so it's
        # always visible here already). Without this, a live PLAN call has
        # no reason to ever emit `{"type": "fact", "key": "slot.$G.<name>"}`
        # for a name not already in `facts` -- it either invents a literal
        # from the transcript or leaves the parameter unbound, neither of
        # which waits for or grounds in the real visual claim. Not read-set
        # material (the question rarely changes between INTERPRET and PLAN
        # in the same step) -- purely additive prompt context, same
        # pattern as `_build_interpret_request`'s `tool_summary`.
        pending_visual_targets: list[dict] = []
        if store.config.vision_enabled:
            question = store.evidence.latest_active_question(goal_id)
            if question is not None and question.status != QuestionStatus.ANSWERED:
                pending_visual_targets = [{"name": t.name, "description": t.description} for t in question.targets]
        view = {
            "intent": intent,
            "facts": facts,
            "catalog": catalog_summary,
            "change_context": None,
            "pending_visual_targets": pending_visual_targets,
        }
        read_set = store.facts.build_read_set(sorted(set(read_keys)))
        return self._make_request(store, JobKind.PLAN, view, goal_id, None, read_set)

    def _build_compose_request(self, store, goal_id: str) -> DispatchRequest:
        """The read set includes not just each consumed call's own
        `result.<call_id>` key but every key *that call itself* read
        (`call.read_set`, e.g. the `slot.<gid>.<name>` it was bound to) —
        `result.<call_id>` never changes once written, so pinning only
        that would let this job's read set stay "valid" forever even
        after a correction supersedes the call it came from (the call
        itself is CONSUMED, a terminal status exempt from
        `InvalidationEngine`, so nothing ever retracts or changes its
        result fact). Transitively including the call's own dependencies
        is what makes a stale, already-dispatched COMPOSE job get
        correctly rejected (`K4`, `_apply_worker_result`'s existing
        `is_valid(job.read_set)` check) instead of silently producing a
        FINAL grounded in pre-correction data — see
        `_all_required_steps_done`'s docstring for the other half of this
        fix, found the same way."""
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
                read_keys.extend(entry.key for entry in call.read_set.entries)
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
