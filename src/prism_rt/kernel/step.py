"""Kernel: the single-writer step engine.

Every event batch goes through exactly these phases, in this order, with no
awaiting anywhere inside (K1): ORDER, APPLY, INVALIDATE, DECIDE, EMIT,
COMMIT, DISPATCH, LOG. `docs/prompt 2.txt` §5.2,
`docs/sonnet_implementation_plan.md` §3.2.

DECIDE runs its components in the architecture's fixed order (§5.2):
cancellation, TaskStateMachine, PlanExecutor, CommitGate, FastResponder —
each sees the post-invalidation state and nothing decided later in the
same step. TaskStateMachine allocates job_ids and writes JobRecords
directly (so FastResponder, running right after it, can see "a PLAN job
just started" and ACK in the same step); DISPATCH only has to make the
actual `runner.submit()` calls, which is why it stays a thin phase 7 even
though V1 has real worker jobs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from prism_rt.adapters.clock import ClockPort
from prism_rt.adapters.output_writer import OutputWriter
from prism_rt.config import Config
from prism_rt.kernel.commit import CommitGate, GateRejection
from prism_rt.kernel.emission import EmissionGate, EmitReport
from prism_rt.kernel.executor import PlanExecutor
from prism_rt.kernel.invalidation import InvalidationEngine, InvalidationReport
from prism_rt.kernel.ordering import order_batch
from prism_rt.kernel.audio import AsrScheduler
from prism_rt.kernel.frames import FrameScheduler
from prism_rt.kernel.perception import PerceptionScheduler
from prism_rt.kernel.reducers import apply as apply_reducer
from prism_rt.kernel.responder import FastResponder
from prism_rt.kernel.task import TaskStateMachine
from prism_rt.model.events import Envelope
from prism_rt.model.types import ChangeSet
from prism_rt.observability.decision_log import DecisionLogger
from prism_rt.store.session import SessionStore


@dataclass(frozen=True)
class StepReport:
    step_no: int
    now_us: int
    batch: tuple[Envelope, ...]
    change_set: ChangeSet
    invalidation: InvalidationReport
    emit_report: EmitReport
    next_wake_us: int | None = None  # V2: earliest time a driver must call step() again (settle liveness)
    # P1 (docs/original_design_audit.md): every PROPOSED call CommitGate
    # blocked this step, and why (unblocks CS-15 offline).
    gate_rejections: tuple[GateRejection, ...] = ()


class Kernel:
    def __init__(
        self,
        config: Config,
        store: SessionStore,
        clock: ClockPort,
        writer: OutputWriter,
        runner: object | None = None,
        log: DecisionLogger | None = None,
    ) -> None:
        """`runner` is duck-typed to `workers.runner.WorkerRunner`'s shape
        (`.submit(job_id, kind, view, *, dispatched_us)`) — not imported
        here, since kernel must not import workers (job dispatch happens
        purely by calling whatever was handed in); `docs/prompt 2.txt`
        §21.1."""
        self.config = config
        self.store = store
        self.clock = clock
        self.runner = runner
        self.log = log

        self._emission_gate = EmissionGate(writer)
        self._invalidation_engine = InvalidationEngine()
        self._commit_gate = CommitGate()
        self._task_state_machine = TaskStateMachine()
        self._perception_scheduler = PerceptionScheduler()
        self._asr_scheduler = AsrScheduler()
        self._frame_scheduler = FrameScheduler()
        self._plan_executor = PlanExecutor()
        self._fast_responder = FastResponder()
        self._step_no = 0

    def step(
        self,
        batch: list[Envelope],
        *,
        inject: Callable[["object", int, int], None] | None = None,
    ) -> StepReport:
        """`inject`, if given, runs at the end of the APPLY phase, inside
        this step's own StoreTxn — before INVALIDATE looks at what changed.
        This is the V0 stand-in for real interpretation/planning reducers:
        `docs/sonnet_implementation_plan.md` §4 VERSION 0 says "test scripts
        inject plans and interpretations directly via StoreTxn", and doing
        it here (rather than in a separate txn before/after step()) is what
        makes same-step cancellation (P3) observable without a real
        Interpreter/Planner. V1 replaces the *need* for this with real
        reducers; the parameter stays available for future direct-injection
        tests either way."""
        self._step_no += 1
        step_no = self._step_no
        now_us = self.clock.now_us()

        # Phase 1: ORDER
        ordered = order_batch(batch)

        if self.log is not None:
            self.log.begin(step_no=step_no, now_us=now_us, batch=ordered)

        with self.store.begin_txn(step_no) as txn:
            # Phase 2: APPLY
            for env in ordered:
                apply_reducer(env, txn, now_us, step_no)
            if inject is not None:
                inject(txn, now_us, step_no)

            # V3: lease expiry is clock-driven, not event-driven — it must
            # run every step (a no-op when config.vision_enabled is False)
            # so a stale CURRENT_STATE claim retracts and cascades through
            # the ordinary INVALIDATE phase below, same step, same as any
            # other fact change (P3).
            self._perception_scheduler.expire_leases(txn, now_us, step_no)
            # Same clock-driven shape: a held ASR transcript whose dedupe
            # window has elapsed is released even if no event arrived.
            self._asr_scheduler.release_due(txn, now_us, step_no)

            # Phase 3: INVALIDATE
            changed_keys = txn.changed_keys()
            invalidation = self._invalidation_engine.invalidate(changed_keys, self.store, txn=txn)

            # Phase 4: DECIDE — fixed order: cancellation, TaskStateMachine,
            # PerceptionScheduler, PlanExecutor, CommitGate, FastResponder.
            intended = []
            intended.extend(self._invalidation_engine.cancellation_actions(invalidation, self.store))
            # P0.4: a call sitting IN_FLIGHT past its deadline with no
            # result is treated as dropped (D4) -- an ordinary CANCEL,
            # same channel as the line above, not a direct status write.
            intended.extend(self._plan_executor.expire_deadlines(self.store, now_us))
            dispatch_requests = list(self._task_state_machine.decide(self.store, now_us, step_no))
            dispatch_requests.extend(self._perception_scheduler.decide(self.store, now_us, step_no))
            dispatch_requests.extend(self._asr_scheduler.decide(self.store, now_us, step_no))
            dispatch_requests.extend(self._frame_scheduler.decide(self.store, now_us, step_no))
            self._plan_executor.propose_ready_calls(self.store, now_us, step_no)
            admitted_actions, admitted_call_ids, gate_rejections = self._commit_gate.scan_and_admit(self.store, now_us)
            intended.extend(admitted_actions)
            intended.extend(self._fast_responder.decide(self.store, now_us, step_no, skip_call_ids=admitted_call_ids))

            # Phase 5: EMIT
            emit_report = self._emission_gate.emit(intended, self.store, now_us, self.store.ids)

            # Phase 6: COMMIT
            change_set = txn.commit()

        # Phase 7: DISPATCH — submit jobs TaskStateMachine requested this
        # step. The JobRecord already exists (written during DECIDE); this
        # only makes the actual (unguarded) runner call.
        if self.runner is not None:
            for req in dispatch_requests:
                self.runner.submit(req.job_id, req.kind, req.view, dispatched_us=now_us)

        # Phase 8: LOG
        if self.log is not None:
            self.log.end(
                step_no=step_no,
                now_us=now_us,
                change_set=change_set,
                invalidation=invalidation,
                emit_report=emit_report,
            )

        return StepReport(
            step_no=step_no,
            now_us=now_us,
            batch=tuple(ordered),
            change_set=change_set,
            invalidation=invalidation,
            emit_report=emit_report,
            next_wake_us=self.store.timers.next_due_us(),
            gate_rejections=tuple(gate_rejections),
        )
