"""Kernel: the single-writer step engine.

Every event batch goes through exactly these phases, in this order, with no
awaiting anywhere inside (K1): ORDER, APPLY, INVALIDATE, DECIDE, EMIT,
COMMIT, DISPATCH, LOG. `docs/prompt 2.txt` §5.2,
`docs/sonnet_implementation_plan.md` §3.2.

V0 has no worker jobs, so DISPATCH is a no-op; it exists as a phase now so
V1 (`workers/`, `JobScheduler`) slots in without changing this file's
control flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from prism_rt.adapters.clock import ClockPort
from prism_rt.adapters.output_writer import OutputWriter
from prism_rt.config import Config
from prism_rt.kernel.commit import CommitGate
from prism_rt.kernel.emission import EmissionGate, EmitReport
from prism_rt.kernel.invalidation import InvalidationEngine, InvalidationReport
from prism_rt.kernel.ordering import order_batch
from prism_rt.kernel.reducers import apply as apply_reducer
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
        self.config = config
        self.store = store
        self.clock = clock
        self.runner = runner  # V1: WorkerRunner — unused in V0
        self.log = log

        self._emission_gate = EmissionGate(writer)
        self._invalidation_engine = InvalidationEngine()
        self._commit_gate = CommitGate()
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

            # Phase 3: INVALIDATE
            changed_keys = txn.changed_keys()
            invalidation = self._invalidation_engine.invalidate(changed_keys, self.store)

            # Phase 4: DECIDE (fixed component order: cancellation, then admission)
            intended = []
            intended.extend(self._invalidation_engine.cancellation_actions(invalidation, self.store))
            intended.extend(self._commit_gate.scan_and_admit(self.store, now_us))

            # Phase 5: EMIT
            emit_report = self._emission_gate.emit(intended, self.store, now_us, self.store.ids)

            # Phase 6: COMMIT
            change_set = txn.commit()

        # Phase 7: DISPATCH — no worker jobs in V0.

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
        )
