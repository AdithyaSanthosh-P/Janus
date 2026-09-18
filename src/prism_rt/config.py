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

    # V2 Innovation feature flags — on by default now that V2 is frozen
    # (v2-robust-recovery); construct Config(...=False) to get V1 behavior
    # back for a specific test or comparison.
    transitive_invalidation: bool = True
    settle_barrier_enabled: bool = True
    absence_read_sets: bool = True
    claim_grades_enabled: bool = True
    rebinder_enabled: bool = True

    # V3 Multimodal feature flags. vision_enabled is the master gate: every
    # perception code path (kernel/perception.py, the video_frame ->
    # Observation reducer excepted, since storing evidence is always safe —
    # see its own docstring) is a no-op while this is False, so V0-V2 tests
    # (which never set it) are structurally unaffected by this version.
    vision_enabled: bool = False  # V3: True
    evidence_align_window_ms: int = 1000  # AT_UTTERANCE: how far past anchor_ts to wait for a frame
    evidence_lease_ms: int = 4000  # CURRENT_STATE claim staleness timeout (simplified: flat timeout, not frame-count based)
    # Phase 2 (docs/post_v4_implementation_plan.md): ASR. asr_enabled is
    # the master gate, matching vision_enabled's pattern — every AsrScheduler
    # code path is a no-op when it's False. audio_mode: "transcript_primary"
    # (never analyze — text chunks carry all content), "audio_only" (always
    # transcribe, release immediately, no dedupe wait), "auto" (transcribe,
    # but discard the result if a real text_chunk already opened/extended
    # the turn by the time it resolves — a simplified stand-in for
    # docs/prompt 2.txt §9.3's precise dedupe-window/timer scheme; see
    # kernel/audio.py's module docstring for the exact trade-off).
    asr_enabled: bool = False
    audio_mode: str = "auto"  # "transcript_primary" | "audio_only" | "auto"
    asr_closes_turn: bool = False  # audio_only + end_of_utterance: also synthesize end_of_turn

    # V4 Hardening feature flags
    reference_bound_identifiers: bool = False  # V4: C10 — see kernel/executor.py

    # Phase 5 (docs/post_v4_implementation_plan.md): C9 commit-last
    # ordering. A WRITE step, even once its own `after` dependencies are
    # satisfied, waits for every *unrelated* READ step in the same plan
    # (one it doesn't structurally depend on) to also resolve first — an
    # irreversible action gets the maximum chance to be caught by a
    # correction arriving on a sibling read. `commit_last_wait_cap_ms`
    # bounds that wait so one hung/slow unrelated read can't block a write
    # forever (`docs/theme05_implementation_blueprint.md` N-02b).
    commit_last_ordering: bool = False
    commit_last_wait_cap_ms: int = 3000
    # Phase 5: C2 response frames — a FRAME job runs once the plan's last
    # required call is emitted, writing a template with typed holes ahead
    # of the result; when the result lands, the kernel renders FINAL from
    # the frame in the same step, no COMPOSE round-trip. Composer remains
    # the fallback whenever no valid frame is available.
    frame_rendering_enabled: bool = False

    # Observability
    log_decisions: bool = True
    record_trace: bool = True

    # Kernel performance target (telemetry only; never a correctness gate)
    step_budget_ms: float = 5.0


DEFAULT_CONFIG = Config()
