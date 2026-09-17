"""Reducers: event -> state mutation, dispatched by `Envelope.payload_type`.

V0 handled `manifest` and `tool_result`. V1 adds `text_chunk`,
`end_of_turn`, `interruption`, and `worker_result` — turn assembly and
consuming Interpreter/Planner/Composer proposals.
"""

from __future__ import annotations

import dataclasses

from prism_rt.kernel.interpret_apply import active_goal_id, apply_interpretation
from prism_rt.kernel.proposals import parse_compose, parse_interpretation, parse_plan
from prism_rt.kernel.results import ResultRouter
from prism_rt.kernel.turns import TurnManager
from prism_rt.model.events import (
    Envelope,
    InterruptionPayload,
    ManifestPayload,
    TextChunkPayload,
    ToolResultPayload,
    WorkerResultPayload,
)
from prism_rt.model.types import (
    FactStatus,
    GoalStatus,
    JobKind,
    JobStatus,
    Provenance,
    StepKind,
    TaskState,
    ToolMutability,
)
from prism_rt.store.session import StoreTxn

_RESULT_ROUTER = ResultRouter()
_TURN_MANAGER = TurnManager()


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


def _apply_tool_result(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    payload: ToolResultPayload = env.payload
    _RESULT_ROUTER.route(payload, txn.store, now_us, step_no=step_no, event_id=env.event_id)


def _apply_text_chunk(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    payload: TextChunkPayload = env.payload
    gid = active_goal_id(txn.store)
    _TURN_MANAGER.on_chunk(payload.text, env.event_id, now_us, txn, active_goal_id=gid)


def _apply_end_of_turn(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    turn = _TURN_MANAGER.on_eot(now_us, txn)
    if turn is None or not turn.chunks:
        return
    txn.facts.set(
        "session.pending_interpretation_turn",
        turn.turn_id,
        FactStatus.COMMITTED,
        Provenance(source="user", event_id=env.event_id, turn_id=turn.turn_id, step_no=step_no, ts_us=now_us),
        rule="reducers.eot",
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


_HANDLERS = {
    "manifest": _apply_manifest,
    "tool_result": _apply_tool_result,
    "text_chunk": _apply_text_chunk,
    "end_of_turn": _apply_end_of_turn,
    "interruption": _apply_interruption,
    "worker_result": _apply_worker_result,
}


def apply(env: Envelope, txn: StoreTxn, now_us: int, step_no: int) -> None:
    handler = _HANDLERS.get(env.payload_type)
    if handler is None:
        return
    handler(env, txn, now_us, step_no)
