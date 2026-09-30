"""TaskStateMachine: the sole dispatcher of INTERPRET/PLAN/COMPOSE jobs.

Called once per kernel step, in the DECIDE phase, after cancellation and
before PlanExecutor/CommitGate/FastResponder (the fixed order in
`docs/prompt 2.txt` §5.2). It is a pure function of current state —
idempotent, safe to call every step — because it always checks
`store.jobs` for an already-running job of the kind it would otherwise
request before requesting another.

There is no explicit TRIAGE *task state*. Interruptions are handled
uniformly: whatever the Interpreter's committed act says (SLOT_UPDATE,
NEW_GOAL, ABORT, RETURN_TO_GOAL, ...) is applied via
`kernel/interpret_apply.py` regardless of what task_state the goal was in
when the turn started. TRIAGE's *hold mode* (`docs/prompt 2.txt` §8.3) is
realized without a state: while `interpret_apply.interpretation_busy` is
true for an active goal, `FastResponder` emits nothing and `CommitGate`
admits no new call (G3), and turns are interpreted strictly in order
(`enqueue_interpretation`). Held work isn't stored anywhere -- the kernel
re-derives everything from state each step, so whatever is still valid
once the interpretation lands is emitted then, and whatever it
invalidated never is.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.templates import WATCHDOG_FALLBACK
from prism_rt.kernel.action_plans import active_actions_view, is_compiled
from prism_rt.kernel.replies import last_finished_goal
from prism_rt.kernel.interpret_apply import active_goal_id, grounded_compose_text, merged_turns, user_content_pending
from prism_rt.kernel.replies import salvage_text
from prism_rt.model.types import (
    CallStatus,
    FactStatus,
    FloorState,
    GoalStatus,
    JobKind,
    JobRecord,
    Provenance,
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
            running = store.jobs.running_by_kind_turn(JobKind.INTERPRET, turn_id)
            if store.config.merge_split_turns_enabled:
                # A job built before this turn absorbed earlier ones is stale
                # and will be dropped -- don't wait for it to come back.
                running = [j for j in running if store.facts.is_valid(j.read_set).is_valid]
            if not running:
                requests.append(self._build_interpret_request(store, turn_id))
        elif store.config.speculative_interpretation_enabled:
            requests.extend(self._speculative_interpret(store))

        gid = active_goal_id(store)
        if gid is not None:
            goal = store.goals.get(gid)
            if goal is not None and goal.status == GoalStatus.ACTIVE:
                if self._maybe_salvage_stalled_turn(store, gid, now_us, step_no):
                    return requests
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
                        if grounded_compose_text(store, gid) is None:
                            requests.append(self._build_compose_request(store, gid))
                elif goal.task_state == TaskState.CLARIFYING:
                    target = store.facts.get(f"goal.{gid}.clarify_target")
                    if target is None or target.status == FactStatus.RETRACTED:
                        # S3: a compiled plan already binds the answered
                        # slot -- rebind it, never replan (kernel/
                        # action_plans.py.is_compiled).
                        store.goals.update(gid, task_state=TaskState.EXECUTING if is_compiled(store, gid) else TaskState.PLANNING)
                    elif (
                        store.config.clarify_reextract_enabled
                        and isinstance(target.value, str)
                        and target.value.startswith(f"slot.{gid}.")
                    ):
                        # Q5 (win_plan §6.2): a plain missing-slot target
                        # (not a "retry:<lineage>" write-confirmation
                        # question, which isn't a slot at all) gets one
                        # narrow re-extraction attempt before the generic
                        # clarify is ever spoken -- FastResponder's own
                        # _clarify still gates on this same fact, so
                        # nothing speaks while this is in flight.
                        requests.extend(self._maybe_reextract(store, gid, target.value))
                elif goal.task_state == TaskState.RESPONDING and self._consumed_step_went_stale(store, gid):
                    # RESPONDING means "every step consumed *and still
                    # valid*" (`_all_required_steps_done`) -- but that was
                    # only ever checked on the way in. A fact a consumed step
                    # read can change afterwards without a replan: a
                    # perception retarget or lease expiry retracts a
                    # perception slot (text corrections go through
                    # interpret_apply, which forces PLANNING). Without this,
                    # the goal re-composed from the stale result instead of
                    # re-running it -- found by `sim/explorer.py` (V1/V2: a
                    # FINAL built on the first frame's lookup after the user
                    # redirected to the second). PlanExecutor, next in this
                    # same DECIDE phase, re-executes the stale step.
                    store.goals.update(gid, task_state=TaskState.EXECUTING)
                elif goal.task_state == TaskState.RESPONDING:
                    pending_text = grounded_compose_text(store, gid)
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

    def _maybe_salvage_stalled_turn(self, store, goal_id: str, now_us: int, step_no: int) -> bool:
        """Q6b (win_plan §6.2): an honest safety-net salvage, independent
        of `observability/watchdog.py`'s ScenarioWatchdog -- that one is a
        single whole-scenario wall-clock budget (105s default), the wrong
        shape for "this one turn is taking too long", and (like Q6a's own
        gap) never fires at all while no goal is active. If
        `turn_stall_salvage_ms` have elapsed since the last EOT and this
        goal still has no FINAL text, force one through the exact same
        salvage path `reducers._apply_watchdog` uses (WATCHDOG_FALLBACK
        wording, `source="watchdog"` so `_final` correctly reports
        task_completed=False) instead of running out the full
        ScenarioWatchdog budget in silence. Returns True iff it salvaged
        this step -- the caller skips the rest of this goal's normal
        dispatch, matching `_apply_watchdog`'s own "don't clobber a FINAL
        already on its way" rule (checked here too, via `grounded_compose_
        text` rather than the raw fact, so an already-invalid stale text
        doesn't block a fresh salvage)."""
        ms = store.config.turn_stall_salvage_ms
        if not ms:
            return False
        last_eot = store.facts.get("session.last_eot_ts")
        if last_eot is None or last_eot.status == FactStatus.RETRACTED:
            return False
        # Measured from the user's latest words, not just the latest end of
        # turn, and never while they are still talking or their latest turn
        # is still being interpreted. Found in the 30 Sep voice rerun: a
        # request spoken in pieces (one end of turn, then 15 s of speech with
        # none) was salvaged mid-sentence -- "I couldn't finish this in
        # time" -- and its first action was lost.
        last_user_us = last_eot.value
        turns = store.turn_log.all()
        if turns and turns[-1].chunks:
            last_user_us = max(last_user_us, turns[-1].chunks[-1].ts_us)
        due_us = last_user_us + ms * 1000
        store.timers.schedule(f"turn_stall_salvage:{goal_id}", due_us)
        if now_us < due_us:
            return False
        if store.floor_state == FloorState.USER_TURN_OPEN or user_content_pending(store):
            return False
        if grounded_compose_text(store, goal_id) is not None:
            return False
        text = WATCHDOG_FALLBACK
        if store.config.conversational_replies_enabled:
            # Live demo (28 Sep): after "what should the date be?" the user
            # simply hadn't answered yet -- the salvage ended the task under
            # them with a status that named nothing. Waiting on the user's
            # answer to a question we asked is not a stall; otherwise say
            # what actually ran.
            goal = store.goals.get(goal_id)
            target = store.facts.get(f"goal.{goal_id}.clarify_target")
            asked = store.facts.get(f"clarify.{goal_id}.asked")
            if (
                goal is not None and goal.task_state == TaskState.CLARIFYING
                and target is not None and target.status != FactStatus.RETRACTED
                and asked is not None and asked.status != FactStatus.RETRACTED and asked.value == target.value
            ):
                return False
            text = salvage_text(store, goal_id)
        store.goals.update(goal_id, task_state=TaskState.RESPONDING)
        store.facts.set(
            f"compose.{goal_id}.text",
            text,
            FactStatus.COMMITTED,
            Provenance(source="watchdog", step_no=step_no, ts_us=now_us),
            rule="task.turn_stall_salvage",
        )
        return True

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

    def _consumed_step_went_stale(self, store, goal_id: str) -> bool:
        """A step whose consumed result no longer matches its inputs --
        unless the goal's final text is system-authored (watchdog salvage,
        honest failure), which is not grounded in results and must never be
        reopened."""
        text = store.facts.get(f"compose.{goal_id}.text")
        if text is not None and text.status != FactStatus.RETRACTED and text.provenance.derivation_read_set is None:
            return False
        plan = store.plans.current(goal_id)
        if plan is None:
            return False
        for step in plan.steps:
            latest = store.call_ledger.latest_by_step(goal_id, step.step_key)
            if latest is not None and latest.status == CallStatus.CONSUMED and not store.facts.is_valid(latest.read_set).is_valid:
                return True
        return False

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
        if store.config.merge_split_turns_enabled:
            # Config.merge_split_turns_enabled: earlier turns folded into this
            # one are part of what was said. The key is in every INTERPRET read
            # set, so a job built before a merge (its value was absent) goes
            # stale the moment the merge happens.
            read_keys.append(f"turn.{turn_id}.merged")
            earlier = [store.turn_log.get(t) for t in merged_turns(store, turn_id)]
            prefix = " ".join(" ".join(c.text for c in t.chunks) for t in earlier if t is not None)
            if prefix:
                transcript = f"{prefix} {transcript}"
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
        # Day 1 (docs/fdb_v3_implementation_plan.md §4.1, §9): the block
        # above only ever showed *required* parameter names -- a live
        # model had no way to know a tool also accepts an optional
        # parameter, what type any parameter is, or what it means (e.g.
        # "3-letter currency code" for `to_currency`), so it either
        # omitted optional args entirely or guessed at formatting the
        # judge then had to tolerate. `params` carries everything
        # `fdb_manifest.py`'s introspection already put on each tool's
        # `params_schema.properties` -- purely additive, `required_params`
        # itself is untouched so existing callers/tests keep matching.
        def _param_summary(schema: dict) -> dict:
            required = set((schema or {}).get("required", []))
            out: dict = {}
            for name, prop in ((schema or {}).get("properties") or {}).items():
                if not isinstance(prop, dict):
                    continue
                info: dict = {"required": name in required}
                if "type" in prop:
                    info["type"] = prop["type"]
                if "description" in prop:
                    info["description"] = prop["description"]
                if "default" in prop:
                    info["default"] = prop["default"]
                out[name] = info
            return out

        tool_summary = [
            {
                "name": tool.name,
                "required_params": (tool.params_schema or {}).get("required", []),
                "params": _param_summary(tool.params_schema),
            }
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
            "multi_action_enabled": store.config.multi_action_enabled,
            "strict_value_rules_enabled": store.config.strict_value_rules_enabled,
            "echo_ack_enabled": store.config.echo_ack_enabled,
        }
        # S3: only added when on, so every existing prompt (and every
        # ScriptedProvider test's matched substring) is unchanged when off.
        # `active_actions` values are already in the read set: they are the
        # goal's own slot.<gid>.a<i>.* facts, listed via `active_slots`.
        if store.config.action_plans_enabled:
            view["action_plans_enabled"] = True
            view["active_actions"] = active_actions_view(store, gid)
        if store.config.fill_unstated_required_enabled:
            view["fill_unstated_required_enabled"] = True
        if store.config.conversational_replies_enabled:
            view["conversational_replies_enabled"] = True
            # With no task running, show the one that just finished, so "make
            # it a thousand rupees" is read as a correction to it (redone by
            # kernel/interpret_apply.py._redo_last_goal) rather than a status
            # question. `session.last_finished_goal` is in the read set: a
            # job built before that task finished goes stale.
            if gid is None and store.config.action_plans_enabled:
                read_keys.append("session.last_finished_goal")
                last = last_finished_goal(store)
                last_view = active_actions_view(store, last.goal_id) if last is not None else []
                if last_view:
                    view["last_task"] = last_view
        # Extension (camera-grounded troubleshooting): tell the model a frame
        # exists, so a visible parameter the user never said aloud is looked
        # at instead of asked for. Only when true, so no other prompt changes.
        if store.config.vision_enabled and store.evidence.observations_by_modality("frame"):
            view["camera_available"] = True
        # With declared write tools a booking needs the user's explicit go-ahead
        # (CommitGate G4), so the model must be told what commit_intent means.
        if not store.config.g4_exempt_undeclared_mutability:
            view["commit_intent_guidance"] = True
        read_set = store.facts.build_read_set(sorted(set(read_keys)))
        return self._make_request(store, JobKind.INTERPRET, view, gid, turn_id, read_set)

    def _build_plan_request(self, store, goal_id: str) -> DispatchRequest:
        intent_fact = store.facts.get(f"goal.{goal_id}.intent")
        intent = intent_fact.value if intent_fact is not None and intent_fact.status != FactStatus.RETRACTED else None
        facts = self._goal_slots(store, goal_id)
        read_keys = ["goal.active", f"goal.{goal_id}.intent", "catalog.version"]
        read_keys.extend(f"slot.{goal_id}.{name}" for name in facts)

        # Day 2 WP2 (docs/fdb_v3_day2_plan.md): every action the turn
        # asked for, so the Planner emits one step per action instead of
        # just one (found live: 0 of 34 multi-action FDB-v3 recordings
        # got their full tool set before this). In the read set like
        # `intent` -- a correction that changes the action list mid-turn
        # must invalidate an in-flight PLAN job the same way a slot
        # change already does.
        requested_actions: list[str] = []
        if store.config.multi_action_enabled:
            actions_fact = store.facts.get(f"goal.{goal_id}.actions")
            if actions_fact is not None and actions_fact.status != FactStatus.RETRACTED:
                requested_actions = list(actions_fact.value or [])
                read_keys.append(f"goal.{goal_id}.actions")

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
            "requested_actions": requested_actions,
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
        # Day 2 WP3 (docs/fdb_v3_day2_plan.md): found live -- COMPOSE saw
        # only raw result facts, never which tools actually ran, so it
        # could (and did) write "your flight has been successfully
        # confirmed" when only search_flights executed (book_flight
        # never got planned/run). `executed_calls` names exactly what
        # ran; `not_done` (only meaningful with multi_action_enabled)
        # names what the turn asked for that never got a call at all --
        # the prompt below is told to report only the former and admit
        # the latter honestly.
        executed_calls: list[dict] = []
        executed_tools: set[str] = set()
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
                executed_calls.append({"tool": step.tool, "args": dict(call.args)})
                executed_tools.add(step.tool)
                if step.kind == StepKind.WRITE:
                    effect = store.effect_ledger.by_fingerprint(call.fingerprint)
                    if effect is not None:
                        effects[step.step_key] = effect.status.value

        not_done: list[str] = []
        if plan is not None and plan.origin == "compiled":
            # S3: per step, not per tool name -- the same tool asked for
            # twice with only one call made is still one action not done.
            for step in plan.steps:
                call = store.call_ledger.latest_by_step(goal_id, step.step_key)
                if call is None or call.status != CallStatus.CONSUMED:
                    not_done.append(step.tool)
        elif store.config.multi_action_enabled:
            actions_fact = store.facts.get(f"goal.{goal_id}.actions")
            if actions_fact is not None and actions_fact.status != FactStatus.RETRACTED:
                not_done = [t for t in (actions_fact.value or []) if t not in executed_tools]
                read_keys.append(f"goal.{goal_id}.actions")

        view = {
            "facts": facts,
            "effects": effects,
            "open_questions": [],
            "executed_calls": executed_calls,
            "not_done": not_done,
        }
        read_set = store.facts.build_read_set(sorted(set(read_keys)))
        return self._make_request(store, JobKind.COMPOSE, view, goal_id, None, read_set)

    def _make_request(
        self,
        store,
        kind: JobKind,
        view: dict,
        goal_id: str | None,
        turn_id: str | None,
        read_set: ReadSet,
        *,
        target: str | None = None,
    ) -> DispatchRequest:
        job_id = store.ids.next("job")
        store.jobs.create(
            JobRecord(job_id=job_id, kind=kind, goal_id=goal_id, turn_id=turn_id, read_set=read_set, target=target)
        )
        return DispatchRequest(job_id=job_id, kind=kind, view=view, goal_id=goal_id, turn_id=turn_id, read_set=read_set)

    def _build_extract_request(self, store, goal_id: str, target: str, param_name: str) -> DispatchRequest:
        """Q5 (win_plan §6.2): the whole conversation so far, not just the
        latest turn -- the value may have been stated earlier, under
        different phrasing than whatever the broader INTERPRET pass bound
        it to (or failed to bind at all). `goal.{goal_id}.clarify_target`
        is in the read set so a stale/already-resolved clarify (the user
        answered some other way while this was in flight) is rejected the
        same way every other job kind's stale result already is (K4)."""
        transcript = " ".join(chunk.text for turn in store.turn_log.all() for chunk in turn.chunks)
        param_schema: dict = {}
        for tool in store.catalog.usable_tools():
            props = (tool.params_schema or {}).get("properties") or {}
            if param_name in props and isinstance(props[param_name], dict):
                param_schema = props[param_name]
                break
        view = {
            "transcript": transcript,
            "param_name": param_name,
            "param_description": param_schema.get("description"),
        }
        read_keys = ["goal.active", f"goal.{goal_id}.clarify_target", "catalog.version"]
        read_set = store.facts.build_read_set(sorted(set(read_keys)))
        return self._make_request(store, JobKind.EXTRACT, view, goal_id, None, read_set, target=target)

    def _maybe_reextract(self, store, goal_id: str, target: str) -> list[DispatchRequest]:
        tried = store.facts.get(f"reextract.{goal_id}.{target}")
        if tried is not None and tried.status != FactStatus.RETRACTED:
            return []  # already attempted (result applied or genuinely null) for this exact target
        if store.jobs.running_by_kind_goal(JobKind.EXTRACT, goal_id):
            return []
        param_name = target.rsplit(".", 1)[-1]
        return [self._build_extract_request(store, goal_id, target, param_name)]
