"""KernelContext: the bundle of per-step context V1's decide-phase
components need (store, time, config, logger).

V0's components took explicit (store, now_us) params — fine for two
arguments across a handful of methods. V1 adds enough methods across enough
components (TaskStateMachine alone has eleven) that threading store/now_us/
step_no/config individually everywhere would be worse than one small
context object. V0's own signatures are left untouched (only step.py's
internal call sites change) so the frozen v0-skeleton interfaces don't move.
"""

from __future__ import annotations

from dataclasses import dataclass

from prism_rt.config import Config
from prism_rt.observability.decision_log import DecisionLogger
from prism_rt.store.session import SessionStore


@dataclass
class KernelContext:
    store: SessionStore
    now_us: int
    step_no: int
    config: Config
    log: DecisionLogger | None = None

    def note(self, message: str) -> None:
        if self.log is not None:
            self.log.note(message)
