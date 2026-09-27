"""Central, frozen configuration: timing policies, limits, and feature flags.

Every V(N+1) behavior change is guarded by a flag here defaulting to the
V(N) value, so disabling a flag restores prior-version behavior (see
`docs/sonnet_implementation_plan.md` §14 and §0 guiding principle 4).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Clock (`docs/prompt 2.txt` §13.2, `adapters/clock.py`). "A" (coupled,
    # the default): entry.py's real driver derives now_us() from real
    # elapsed wall time on top of the last harness timestamp, so a
    # step-scheduled timer (e.g. G10's settle wake) is honoured even with
    # no further external events (D1, docs/original_design_audit.md P0.1).
    # "B" (stepped): time only ever moves via an explicit advance_to — what
    # sim/harness.py always uses (deterministic replay), and an option for
    # entry.py too when exact virtual timestamps matter more than real
    # elapsed time.
    clock_model: str = "A"
    settle_ms: int = 300  # 0 = disabled
    # Day 2 (docs/fdb_v3_day2_plan.md WP1): when the latest closed user
    # turn's last word looks unfinished (config/lexicons.py's
    # TRAILING_CONNECTIVES, or an inert token, or a HOLD_CUES phrase),
    # G10 waits settle_ms_incomplete instead of settle_ms. Gated by
    # incomplete_turn_settle_enabled (default off) so it never changes
    # any existing test/behavior; only meaningful with
    # settle_barrier_enabled=True.
    incomplete_turn_settle_enabled: bool = False
    settle_ms_incomplete: int = 2500
    # Day 2 WP2 (docs/fdb_v3_day2_plan.md): the Interpreter/Planner see
    # every action a turn asks for, not just the first (found live: 0 of
    # 34 FDB-v3 multi-action recordings got their full tool set before
    # this). See workers/interpreter.py's own docstring at the
    # multi_action_block for exactly what changes.
    multi_action_enabled: bool = False
    watchdog_timeout_ms: int = 105_000
    # P0.4 (call deadlines, docs/original_design_audit.md D4, blueprint
    # §5.9's own defaults): an IN_FLIGHT call past this many ms with no
    # result is treated as dropped -- reads retry (bounded by
    # max_read_retries); writes get an honest, ambiguous-outcome INFORM +
    # CLARIFY (effect UNKNOWN, no auto-retry) instead of silently hanging
    # until the scenario watchdog (105_000ms default, well above both).
    call_deadline_read_ms: int = 6_000
    call_deadline_write_ms: int = 12_000

    # Policies
    max_read_retries: int = 2
    # A real, pre-existing conflation found and fixed 2026-09-27 (S2
    # validation, win_plan §6.2): `kernel/reducers.py._release_dropped_
    # interpret` used to reuse `max_read_retries` to gate give-up on a
    # failed/stale INTERPRET *job* -- a live LLM call, which can fail
    # transiently, is not the same thing as a tool call against FDB's own
    # deterministic mock APIs (which never fail transiently, the actual
    # reason FDB's profile sets max_read_retries=0). With that shared
    # field, FDB's profile meant a single transient INTERPRET failure
    # permanently abandoned the turn with zero recovery -- confirmed live
    # on housing_13 (reproduced twice; a standalone same-input API call
    # succeeded cleanly, ruling out a persistent content/schema issue) --
    # and neither of S2's own new safety nets (never_silent_unclear_
    # enabled, turn_stall_salvage_ms) could catch it, since both need
    # something INTERPRET itself never produced. Decoupled here; the FDB
    # profile deliberately does NOT zero this one out.
    max_interpret_retries: int = 2
    max_write_retries: int = 1
    max_clarification_attempts: int = 2
    max_consecutive_holds: int = 3
    min_speech_gap_ms: int = 400
    # CS-28 / `docs/prompt 2.txt` line 370: "No SPEAK, CLARIFY, or FINAL is
    # emitted while the floor region is USER_TURN_OPEN (policy
    # speak_during_open_turn, default OFF). Talking over the user harms
    # naturalness and usually refers to soon-to-change information."
    # `False` (the doc's own default) enforces the floor rule; `kernel/
    # responder.py.FastResponder.decide` is the sole source of SPEAK/
    # CLARIFY/FINAL actions, so gating there is sufficient — CANCEL/
    # TOOL_CALL emission is untouched (the floor rule names only the three
    # speaking action types).
    speak_during_open_turn: bool = False
    # TRIAGE hold (`docs/prompt 2.txt` §8.3, transitions 23-31): once a
    # user turn spoken during an active goal has closed but isn't
    # interpreted yet, hold every emission except CANCEL -- speech in
    # `FastResponder`, new tool calls in `CommitGate` (G3) -- until it is.
    # On by default (the spec's behavior); `False` exists so
    # `sim/explorer.py` can demonstrate what it prevents.
    triage_hold_enabled: bool = True
    # C1 inert-tail promotion (`docs/prompt 3.txt` C1): extends Phase 6's
    # exact-digest promotion to a turn whose only words after the cached
    # speculative prefix are inert (`config/lexicons.py` INERT_TOKENS).
    # Only has any effect when speculative_interpretation_enabled is on.
    inert_tail_promotion: bool = True

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
    frame_gap_ms: int = 3000  # docs/prompt 2.txt §10 freshness: a CURRENT_STATE claim older than this since the last frame asks for a fresh view before a WRITE (M-05)
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
    # AUTO mode (`docs/prompt 2.txt` line 909, default 800): ASR output is
    # held this long after the clip's capture; if the harness's own text
    # transcript covers that window, the ASR output is discarded.
    asr_dedupe_window_ms: int = 800

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

    # Phase 6 (docs/prompt 2.txt §11.3): speculative interpretation
    # coalescing. While a turn is open, at most one INTERPRET job runs
    # speculatively per turn (a growing prefix does not spawn a second
    # job — it just makes the completed one stale, dispatching a fresh
    # one against the latest prefix). At EOT, if the running or completed
    # job's prefix digest equals the final digest, its result is applied
    # in the same step as EOT — zero additional model latency — instead
    # of dispatching a non-speculative job and waiting for it.
    speculative_interpretation_enabled: bool = False

    # Day 2 S2 (docs-personal/private-docs/win_plan_2026-09-27.md §6.2,
    # "quick-win pack"). Each Q-item is its own flag, additive and off by
    # default, matching every prior version's own rule.
    #
    # Q1: a slot value that's really a spelled-out identifier ("X-Y-Z-8-8")
    # gets joined back into one token (XYZ88) before being written --
    # `kernel/interpret_apply.py._canonicalize_spoken_id`. A value with any
    # multi-character token (e.g. "FL-DEN-8AM") is left untouched -- that's
    # a structured code, not a spelled-out one.
    normalize_spoken_ids: bool = False
    # Q2: extra Interpreter-prompt guidance (workers/interpreter.py) --
    # copy values verbatim, never invent a year, only the final corrected
    # value after a self-correction, no unstated optional params -- plus
    # explicit guidance against misclassifying a disfluent-but-real first
    # utterance as BACKCHANNEL/SMALLTALK/UNCLEAR (the confirmed root cause
    # of the housing_11/housing_13 silent stalls, found by reading their
    # saved decision logs: interpretation resolved to a no-op act with no
    # active goal to attach to, and NOTHING spoke -- see
    # never_silent_unclear_enabled below for the safety-net half of this
    # same fix).
    strict_value_rules_enabled: bool = False
    # Q6a: the confirmed fix for the housing_11/housing_13 class of silent
    # stall -- when interpretation resolves to a no-op act (BACKCHANNEL/
    # SMALLTALK/UNCLEAR, or -- found in the same live validation pass --
    # SLOT_UPDATE/ADDITION/ANSWER_CLARIFICATION/CONFIRM/DENY) and there is
    # no active goal at all for it to attach to, that almost certainly
    # means real content was thrown away, not that the user said nothing
    # worth a response (a standalone repro of housing_13's own transcript
    # returned `slot_update` on 2 of 4 identical live calls -- real
    # response variance, not a one-off). `kernel/interpret_apply.py._flag_
    # unclear_no_goal` (called from both code paths) flags the turn;
    # `kernel/responder.py.FastResponder._unclear_no_goal` speaks an
    # honest re-ask once, per turn. Kept flagged (not unconditional like
    # the write-honesty fixes) since a genuine "hi"/pure smalltalk turn
    # with no goal would also trigger it -- fine for a task-oriented
    # benchmark, a judgment call for a general assistant.
    never_silent_unclear_enabled: bool = False
    # How long the Q6a re-ask waits after the unclear turn was interpreted,
    # and it is dropped if the user has started another turn by then. Found
    # live (2026-09-27): a hesitant opener ("Um so uh...") followed by a
    # pause longer than the end-of-turn silence gets closed as its own
    # turn, reads as UNCLEAR, and an immediate re-ask talks over the user
    # who is about to state the real request. 0 = speak immediately.
    unclear_reask_delay_ms: int = 0
    # Q6b: an honest safety-net salvage independent of the whole-scenario
    # ScenarioWatchdog (which is a single budget from scenario start, per
    # `observability/watchdog.py` -- wrong shape for "this turn is taking
    # too long", and never fires at all when no goal is active, exactly
    # like Q6a's own gap). If this many ms have elapsed since the last EOT
    # and the active goal still hasn't produced FINAL text,
    # `kernel/task.py.TaskStateMachine` forces one through the same
    # salvage path `reducers._apply_watchdog` uses (WATCHDOG_FALLBACK
    # wording, task_completed=False) rather than let the scenario run out
    # the full ScenarioWatchdog budget in silence. 0 = disabled.
    turn_stall_salvage_ms: int = 0
    # Q5: when a required slot is missing and CLARIFYING is about to speak
    # a generic "what should X be" question, dispatch one narrow EXTRACT
    # job first (one parameter, the transcript, that parameter's own
    # schema) -- the user may already have said it in words the broader
    # INTERPRET pass didn't bind to this exact parameter name. Only tried
    # once per (goal, target); a null/no-value result falls through to
    # the ordinary clarify.
    clarify_reextract_enabled: bool = False
    # Q8: a deterministic post-lint (`observability/speechlint.py`) over
    # every SPEAK/CLARIFY/FINAL body right before it's written --
    # humanizes a leaked raw schema/parameter identifier (M-09's own
    # known bug class: "what should indicator_state be?") and flags an
    # ACK that claims something is already done before any call has run.
    # Reused, not duplicated, by `scripts/fdb_v3/run_text_replay.py`'s own
    # report (Q11) to lint a whole run's transcripts after the fact too.
    speechlint_enabled: bool = False
    # Q7: the Interpreter names every requested action and its key values
    # in one future-tense sentence (`TurnInterpretation.ack_phrase`,
    # already parsed in `kernel/proposals.py` but never consumed before
    # this); `kernel/responder.py.FastResponder._ack_plan_dispatch` speaks
    # it in place of the generic ACK once a plan actually dispatches,
    # falling back to the existing content-ack/generic chain when the
    # model didn't provide one. Also gates the prompt guidance that tells
    # the model `ack_phrase` exists at all (`workers/interpreter.py`).
    echo_ack_enabled: bool = False

    # Day 2 (docs/fdb_v3_implementation_plan.md §5.2): G4 (commit_intent)
    # applies to every WRITE-kind call, including a tool whose mutability
    # was never declared and therefore defaults to STATE_CHANGING (the
    # catalog's own safe default, W5 -- see store/catalog.py). FDB-v3
    # declares no mutability for any of its 12 tools, so every one of
    # them -- including plain reads like search_flights -- needs an
    # explicit Interpreter-set commit_intent=true or the call sits
    # PROPOSED forever with no clarify, no retry, total silence (found
    # live, reproduced directly: travel_21's search_flights call, 2026-
    # 09-25 session, see currentStatus.md's FDB-v3 Day 1 section).
    # Requiring commit_intent for "search for flights" would incorrectly
    # block a read the user plainly asked for -- the plan's own stated
    # decision: "an accepted interpretation of a completed, settled turn
    # counts as commit intent for undeclared tools." When on, G4 is
    # skipped for a call whose tool has `mutability_source == "DEFAULTED"`
    # -- G3 (floor closed) and, if enabled, G10/G11 (settle) still gate
    # it exactly as before; this flag only removes the *additional*
    # explicit-commit_intent requirement for tools nobody ever declared
    # mutability for. A tool that *does* declare mutability (DECLARED)
    # is completely unaffected either way.
    g4_exempt_undeclared_mutability: bool = False

    # Observability
    log_decisions: bool = True
    record_trace: bool = True

    # Kernel performance target (telemetry only; never a correctness gate)
    step_budget_ms: float = 5.0


DEFAULT_CONFIG = Config()
