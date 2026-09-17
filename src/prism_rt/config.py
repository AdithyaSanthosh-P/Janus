"""Central, frozen configuration: timing policies, limits, and feature flags.

Every V(N+1) behavior change is guarded by a flag here defaulting to the
V(N) value, so disabling a flag restores prior-version behavior (see
`docs/sonnet_implementation_plan.md` §14 and §0 guiding principle 4).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Clock
    clock_model: str = "B"
    settle_ms: int = 300  # 0 = disabled
    watchdog_timeout_ms: int = 105_000

    # Policies
    max_read_retries: int = 2
    max_write_retries: int = 1
    max_clarification_attempts: int = 2
    max_consecutive_holds: int = 3
    min_speech_gap_ms: int = 400

    # V0 Foundation (always active)
    enforce_commit_gate: bool = True
    enforce_read_set_at_emission: bool = True

    # V2 Innovation feature flags
    transitive_invalidation: bool = False  # V2: True
    settle_barrier_enabled: bool = False  # V2: True
    absence_read_sets: bool = False  # V2: True
    claim_grades_enabled: bool = False  # V2: True
    rebinder_enabled: bool = False  # V2: True

    # V3 Multimodal feature flags
    vision_enabled: bool = False  # V3: True
    asr_enabled: bool = False  # not implemented

    # Observability
    log_decisions: bool = True
    record_trace: bool = True

    # Kernel performance target (telemetry only; never a correctness gate)
    step_budget_ms: float = 5.0


DEFAULT_CONFIG = Config()
