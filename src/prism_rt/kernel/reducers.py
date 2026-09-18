"""Reducers: event -> state mutation, dispatched by `Envelope.payload_type`.

V0 handled `manifest` and `tool_result`. V1 adds `text_chunk`,
`end_of_turn`, `interruption`, and `worker_result` — turn assembly and
consuming Interpreter/Planner/Composer proposals.
"""

from __future__ import annotations

import dataclasses

from config.templates import WATCHDOG_FALLBACK
from prism_rt.kernel.audio import AsrScheduler
from prism_rt.kernel.interpret_apply import active_goal_id, apply_interpretation
from prism_rt.kernel.perception import PerceptionScheduler
from prism_rt.kernel.proposals import parse_asr, parse_compose, parse_interpretation, parse_perception, parse_plan
from prism_rt.kernel.results import ResultRouter
from prism_rt.kernel.turns import TurnManager
from prism_rt.model.events import (
    AudioClipPayload,
    Envelope,
    InterruptionPayload,
    ManifestPayload,
    TextChunkPayload,
    ToolResultPayload,
    VideoFramePayload,
    WorkerResultPayload,
)
from prism_rt.model.types import (
    CallStatus,
    FactStatus,
    GoalStatus,
    JobKind,
    JobStatus,
    Observation,
    Provenance,
    StepKind,
    TaskState,
    ToolMutability,
)
from prism_rt.store.session import StoreTxn

_RESULT_ROUTER = ResultRouter()
_TURN_MANAGER = TurnManager()
_PERCEPTION = PerceptionScheduler()
_ASR = AsrScheduler()


def _apply_manifest(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    payload: ManifestPayload = env.payload
    txn.catalog.parse_manifest(payload.tools)
    txn.facts.set(
        "catalog.version",
        txn.catalog.version,
        FactStatus.COMMITTED,
        Provenance(source="system", event_id=env.event_id, step_no=step_no, ts_us=now_us),
        rule="reducers.manifest",
    )


def _traverse_path(value, path: str):
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.lstrip("-").isdigit():
            index = int(part)
            value = value[index] if -len(value) <= index < len(value) else None
        else:
            value = None
        if value is None:
            break
    return value


def _write_derived_facts(txn: StoreTxn, call_id: str, now_us: int, step_no: int, event_id: str) -> None:
    """V2: map a consumed result through its step's `output_map` into
    `derived.<gid>.<name>` facts, tagged with the call's own read set as
    `derivation_read_set` — this is what makes a downstream call that binds
    to the derived value (instead of the raw `result.<call_id>`) cancel
    transitively when the upstream slot changes (`kernel/invalidation.py`).
    """
    if not txn.store.config.transitive_invalidation:
        return
    call = txn.store.call_ledger.get(call_id)
    if call is None:
        return
    plan = txn.store.plans.current(call.goal_id)
    if plan is None:
        return
    step = next((s for s in plan.steps if s.step_key == call.step_key), None)
    if step is None or not step.output_map:
        return
    result_fact = txn.facts.get(f"result.{call.call_id}")
    if result_fact is None:
        return
    for path, derived_name in step.output_map.items():
        value = _traverse_path(result_fact.value, path)
        if value is None:
            continue
        key = derived_name.replace("$G", call.goal_id)
        txn.facts.set(
            key,
            value,
            FactStatus.DERIVED,
            Provenance(
                source="tool",
                event_id=event_id,
                call_id=call.call_id,
                step_no=step_no,
                ts_us=now_us,
                derivation_read_set=call.read_set,
            ),
            rule="derived.from_output_map",
        )


def _apply_tool_result(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    payload: ToolResultPayload = env.payload
    outcome = _RESULT_ROUTER.route(payload, txn.store, now_us, step_no=step_no, event_id=env.event_id)
    # outcome.branch == "consumed" covers both the ok and error sub-cases
    # (see ResultRouter branch 7); only the ok one actually wrote a result.
    if outcome.branch == "consumed" and outcome.new_status == CallStatus.CONSUMED and outcome.call_id is not None:
        _write_derived_facts(txn, outcome.call_id, now_us, step_no, env.event_id)


def _apply_text_chunk(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    payload: TextChunkPayload = env.payload
    gid = active_goal_id(txn.store)
    _TURN_MANAGER.on_chunk(payload.text, env.event_id, now_us, txn, active_goal_id=gid)


def _apply_end_of_turn(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    turn = _TURN_MANAGER.on_eot(now_us, txn)
    _TURN_MANAGER.request_interpretation(txn, turn, now_us, step_no, event_id=env.event_id)


def _apply_video_frame(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    """V3: a frame is always stored as an Observation, never analyzed on
    arrival (§10.4) — relevance is decided later, only by an open question
    (`kernel/perception.py`). Storing unconditionally (not gated on
    `vision_enabled`) matches §10.3 ("Evidence is always stored") and costs
    V0-V2 nothing since they never send this event."""
    payload: VideoFramePayload = env.payload
    obs_id = txn.store.ids.next("obs")
    seq = len(txn.store.evidence.observations_by_modality("frame")) + 1
    txn.evidence.add_observation(
        Observation(obs_id=obs_id, frame_id=payload.frame_id, capture_ts_us=now_us, arrival_step=step_no, modality_seq=seq, modality="frame")
    )


def _apply_audio_clip(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    """Phase A (`docs/post_v4_implementation_plan.md`): store-only, same as
    `_apply_video_frame` — an audio clip is always recorded as an
    Observation on arrival, analyzed by nothing yet. ASR (Phase 2) is what
    turns this into transcribed content; until then this exists purely so
    a harness sending audio_clip events degrades to "observed, ignored"
    rather than the event being unparseable (30% of scored scenarios are
    audio per guidelines/Theme_5_Guide.md §4)."""
    payload: AudioClipPayload = env.payload
    obs_id = txn.store.ids.next("obs")
    seq = len(txn.store.evidence.observations_by_modality("audio")) + 1
    txn.evidence.add_observation(
        Observation(obs_id=obs_id, frame_id=payload.clip_id, capture_ts_us=now_us, arrival_step=step_no, modality_seq=seq, modality="audio")
    )


def _apply_watchdog(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    """§8.9 salvage. The *trigger* (real wall-clock elapsed) is excluded
    from determinism tests by design (`ScenarioWatchdog` is the one
    permitted wall-clock reader, outside `kernel/`/`store/`) — but the
    *salvage behavior* itself is a deterministic reducer, directly
    testable by sending this envelope like any other. Sets
    `session.watchdog_fired` (checked by `CommitGate` to block all future
    writes), cancels every in-flight READ (same-step CANCEL via the
    ordinary InvalidationEngine mechanism, exactly like
    `interpret_apply._abandon_goal`), and forces a truthful, templated
    FINAL for the active goal if one isn't already on its way — `_final`
    reports `task_completed=False` for this case (`kernel/responder.py`),
    since the whole point is never claiming to have finished."""
    txn.facts.set(
        "session.watchdog_fired",
        True,
        FactStatus.COMMITTED,
        Provenance(source="system", event_id=env.event_id, step_no=step_no, ts_us=now_us),
        rule="reducers.watchdog",
    )

    for call in txn.store.call_ledger.non_terminal():
        if call.kind != StepKind.READ:
            continue
        if call.status == CallStatus.IN_FLIGHT:
            txn.store.call_ledger.set_status(call.call_id, CallStatus.INVALIDATED)
            txn.store.dep_index.unregister(call.call_id)
        elif call.status == CallStatus.PROPOSED:
            txn.store.call_ledger.set_status(call.call_id, CallStatus.DISCARDED)

    gid = active_goal_id(txn.store)
    if gid is None:
        return
    goal = txn.store.goals.get(gid)
    if goal is None or goal.status != GoalStatus.ACTIVE:
        return
    existing_text = txn.facts.get(f"compose.{gid}.text")
    if existing_text is not None and existing_text.status != FactStatus.RETRACTED:
        return  # a FINAL is already on its way; don't clobber it
    txn.store.goals.update(gid, task_state=TaskState.RESPONDING)
    txn.facts.set(
        f"compose.{gid}.text",
        WATCHDOG_FALLBACK,
        FactStatus.COMMITTED,
        # source="watchdog" (not "system") is the signal `_final` reads to
        # know this particular FINAL is the salvage fallback, not a real
        # completion — `rule=` below is decision-log-only and isn't
        # retrievable from the fact itself.
        Provenance(source="watchdog", event_id=env.event_id, step_no=step_no, ts_us=now_us),
        rule="reducers.watchdog_final",
    )


def _apply_interruption(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    payload: InterruptionPayload = env.payload
    del payload  # reason isn't consulted in V1
    gid = active_goal_id(txn.store)
    _TURN_MANAGER.on_interruption(now_us, txn, active_goal_id=gid)


def _fix_step_kind(step, catalog):
    """`step.kind` is copied from the catalog at plan acceptance
    (`docs/prompt 2.txt` §4.3) — the model's own claim is provisional only,
    never trusted for mutability (W5: unknown -> WRITE, the safe default)."""
    spec = catalog.get(step.tool)
    kind = StepKind.READ if spec is not None and spec.mutability == ToolMutability.READ_ONLY else StepKind.WRITE
    return dataclasses.replace(step, kind=kind, requires_commit_intent=(kind == StepKind.WRITE))


def _apply_worker_result(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    payload: WorkerResultPayload = env.payload
    job = txn.store.jobs.get(payload.job_id)
    if job is None or job.status != JobStatus.RUNNING:
        return  # unknown or already-settled job (duplicate/late delivery): ignore

    txn.store.jobs.set_status(payload.job_id, JobStatus.DONE)

    if payload.status != "ok":
        return  # worker failure: dropped; TaskStateMachine will re-request next step
    if not txn.facts.is_valid(job.read_set).is_valid:
        return  # stale proposal: dropped, never applied (K4)

    kind = JobKind(payload.kind)
    proposal = payload.proposal or {}

    if kind == JobKind.INTERPRET:
        try:
            interp = parse_interpretation(proposal, turn_id=job.turn_id or "", input_digest="")
        except (KeyError, ValueError):
            return
        apply_interpretation(interp, txn, now_us, step_no, event_id=env.event_id)

    elif kind == JobKind.PLAN:
        if job.goal_id is None:
            return
        goal = txn.store.goals.get(job.goal_id)
        if goal is None or goal.status != GoalStatus.ACTIVE:
            return
        try:
            plan_rev = txn.store.plans.next_rev(job.goal_id)
            plan = parse_plan(proposal, goal_id=job.goal_id, plan_rev=plan_rev)
        except (KeyError, ValueError):
            return
        fixed_steps = tuple(_fix_step_kind(step, txn.store.catalog) for step in plan.steps)
        plan = dataclasses.replace(plan, steps=fixed_steps)
        txn.store.plans.create(plan)
        txn.store.goals.update(job.goal_id, task_state=TaskState.EXECUTING)

    elif kind == JobKind.COMPOSE:
        if job.goal_id is None:
            return
        text, _claims = parse_compose(proposal)
        txn.facts.set(
            f"compose.{job.goal_id}.text",
            text,
            FactStatus.COMMITTED,
            Provenance(source="system", event_id=env.event_id, step_no=step_no, ts_us=now_us),
            rule="reducers.compose_result",
        )

    elif kind == JobKind.VISION:
        try:
            claims = parse_perception(proposal)
        except (KeyError, ValueError):
            return
        _PERCEPTION.on_perception_result(txn, job, claims, now_us, step_no, event_id=env.event_id)

    elif kind == JobKind.ASR:
        try:
            segments, end_of_utterance = parse_asr(proposal)
        except (KeyError, ValueError):
            return
        _ASR.on_asr_result(txn, job, segments, end_of_utterance, now_us, step_no, event_id=env.event_id)


_HANDLERS = {
    "manifest": _apply_manifest,
    "tool_result": _apply_tool_result,
    "text_chunk": _apply_text_chunk,
    "video_frame": _apply_video_frame,
    "audio_clip": _apply_audio_clip,
    "end_of_turn": _apply_end_of_turn,
    "interruption": _apply_interruption,
    "worker_result": _apply_worker_result,
    "watchdog": _apply_watchdog,
}


def apply(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    handler = _HANDLERS.get(env.payload_type)
    if handler is None:
        return
    handler(env, txn, now_us, step_no)
