# Theme 05: Interruptible Real-Time Agents — Prototype Version Plan

Samsung PRISM Generative AI Hackathon, 3rd Edition.

This document transforms the architecture (prompt2), innovation analysis (prompt3), and implementation blueprint (theme05_implementation_blueprint) into a **staged prototype strategy** with independently runnable versions, optimized for the one-week implementation window.

**Core principle:** Every version is a working, submission-capable prototype. If Version N+1 fails, Version N is the fallback.

**Labels used throughout:**
- **[Spec]** — stated in the evaluation guide
- **[Inference]** — follows from the spec
- **[Decision]** — architectural/prioritization choice made here
- **[Implementation simplification]** — deliberate reduction from the full blueprint
- **[Open]** — depends on the unreleased kit

---

## 1. Subsystem Classification

Every major subsystem from the architecture is classified for hackathon viability.

### A. MUST HAVE — Without these, evaluation fails

| Subsystem | Reason |
|---|---|
| Single-writer coordination kernel (serialized step) | Core correctness mechanism; prevents all logical races between perception, reasoning, tools, and speech. Without it, every invariant is at risk. [Spec G§1, arch D1] |
| Event mailbox + batch ordering `(ts_us, class, seq)` | Deterministic event processing; without it, simultaneous events produce nondeterministic traces. [Spec G§4, arch D2] |
| FactStore with versioned facts + read sets + digest validity | The mechanism that detects stale work. Every scored action depends on this. [arch D3, D4] |
| DependencyIndex for invalidation | Cancellation within the grace period requires a lookup, not reasoning. [arch D8, invariant P3] |
| Tool catalog with dynamic manifest parsing + mutability | "Unseen tools" is a public-suite scenario class. Hardcoded tools fail hidden tests. [Spec G§3.2(4)] |
| CallLedger + EffectLedger + CommitGate | Zero duplicate state-changing calls is likely binary. Safety is 10% but cascades into TC and IR. [Spec G§5, invariant W1] |
| ResultRouter with stale/cancel/retained handling | Core of interruption recovery scoring. [arch §3.6] |
| FastResponder with templated ACK/PROGRESS/HOLD/FINAL | Response Latency (15%) + quality multiplier (0.8–1.2×). [Spec G§1] |
| SnapshotProjector (computed at emission, never cached) | Snapshots appear in 3 of 4 scoring categories (85% weight). [Spec G§5, invariant S3] |
| TurnManager + QuickDetector | Chunk-anchored cancellation; fast-path acknowledgments. [arch D8] |
| Task state machine (at least: idle→listening→understanding→executing→responding→completed + triage) | Governs the agent's behavior across the full scenario lifecycle. |
| Interpreter + Planner workers (LLM-backed) | Cannot complete tasks without language understanding and planning. |
| Emission gate with schema validation | Safety & Protocol requires well-formed JSON. [Spec G§5] |
| Harness adapter (ingress + codec + output writer) | The agent must communicate with the harness. |
| ClockPort (at least one implementation) | All timing decisions must use harness time. [invariant T1] |
| Deterministic ID generation | Replay identity; unique call_ids. [invariant C1] |
| Decision log (structured, per-step) | Traceability for debugging and demo. [arch D26] |

### B. SHOULD HAVE — High evaluation value, implement if time allows

| Subsystem | Reason |
|---|---|
| Transitive invalidation (provenance-closed, INNOV C5) | Highest IR impact; targets likely hidden-test class (chain correction). Worth ~1 day of effort. |
| Commit-last ordering + settle barrier (INNOV C9) | Highest safety impact for write scenarios. Small implementation. |
| Schema-complete read sets with absence entries (INNOV C6) | Catches "addition during search" scenarios. Small. |
| Claim-typed emission (INNOV C8) | Fixes defect L3 (speech claiming in-progress work that doesn't exist yet). Required for truthful speech about writes. |
| Triage with hold/release | Distinguishes backchannel from correction; preserves valid work during interruptions. |
| Goal suspension + return | "Goal switch and return" is a stated scenario class. |
| Rebinder (localized corrections without full replanning) | Reduces latency on slot corrections; preserves valid work. |
| Response frames (INNOV C2) | High demo value; structural grounding eliminates fabrication. |
| Basic perception (question-driven frame analysis) | Visual scenarios carry 1.5× weight in hidden set. |
| Composer with truth checks | Prevents fabricated details in final responses. |

### C. OPTIONAL — Nice to have, low risk/reward ratio in hackathon

| Subsystem | Reason |
|---|---|
| Inert-tail promotion (INNOV C1) | Benefits only under clock Model A; clock model unknown. |
| Event-anchored reaction scheduling (INNOV C3) | Medium complexity, mainly Model A benefit. |
| Criticality-aware worker scheduling (INNOV C16) | Minor; standard scheduling. |
| Reference-bound write arguments (INNOV C10) | Safety refinement; catches paraphrase duplicates. |
| Evidence leases (INNOV C7) | Important for visual scenarios but medium complexity. |
| Speculative interpretation on prefixes | Latency optimization; internal, no trace impact. |
| ASR worker | Only needed if audio scenarios lack transcripts. |
| Watch mode / frame coalescing | Edge case for visual scenarios. |
| Demand-driven perception at interpretation time (INNOV C13) | Small scheduling refinement. |

### D. CUT FOR HACKATHON — Too expensive relative to value

| Subsystem | Reason |
|---|---|
| Full adversarial schedule exploration (INNOV C17 full) | The explorer, minimizer, PCT, and mutation engine are ~2 days. Replace with targeted sweep tests. |
| Agreement-based perception confidence (INNOV C12) | Needs labeled frames; unmeasurable now. |
| Expected-value gating of speculative reads (INNOV C15) | Depends on kit Q6 (are extra calls penalized?). |
| Dual-channel interpretation cross-check (INNOV C14) | Low scoring relevance; diagnostic only. |
| Effect verification probes (INNOV C11) | Low and unmeasurable. |
| Full reconciliation with compensating tools | Rare scenario; a truthful INFORM covers 90% of the value. |
| Multiple ClockPort implementations (all 3 models) | Build for the most likely model (A or B); adapt when kit arrives. |
| Production-grade observability (metrics, telemetry, report) | Structured decision log is sufficient for debugging and demo. |
| import-linter enforcement | Good practice but doesn't affect evaluation. Enforce manually. |
| Elaborate scenario DSL with full assertion vocabulary | A simpler test harness that checks key invariants suffices. |

---

## 2. Implementation Scope Optimization

Aggressive reductions from the full blueprint:

| Original Design | Hackathon Version | Reason |
|---|---|---|
| 60+ source files across 8 packages | ~25–30 files across 5 packages | Many files can be merged. `model/` collapses to 2–3 files. `store/` to 3–4 files. |
| 3 ClockPort implementations (Models A, B, C) | 1 primary (likely Model B/stepped) + thin Model A adapter | Build for the most defensive model; adapt on kit arrival. |
| Response frame DSL with typed holes + operators | Simple template-based frames; holes are JSONPath lookups only | The full operator set (min, max, count, first-k) is overkill. |
| Advanced perception leases with frame-count + time bounds | Simple lease: expires after N seconds of harness time | Full lease semantics are medium complexity; a timeout suffices. |
| Full reconciliation + compensation tools | INFORM truthfully + CLARIFY | Compensation is rare; truthful reporting covers the scoring. |
| Extensive observability (metrics, telemetry, run_writer) | Decision log + action trace + simple latency tracking | The demo and debugging need traces, not production metrics. |
| Full ASR with auto/transcript_primary/audio_only modes | Single mode: prefer text, fall back to Whisper if no text | Simplify until kit reveals which mode is needed. |
| 50+ scenario tests with sweeps and PCT | ~15 hand-written scenario tests covering key failure classes | Focus on the scenarios that map to scoring criteria. |
| Adversarial schedule exploration + delta-debugging | Targeted timing sweeps on 3–5 critical scenarios | The full explorer is a luxury; hand-crafted adversarial timing tests cover most value. |
| `sim/` package with full discrete-event simulator | Simplified test harness that feeds events and checks outputs | The full simulator is the most complex non-core component. |
| Elaborate config system with YAML + policy registry | Simple Python config with defaults; policy overrides as constants | The config system doesn't affect scoring. |
| Separate Ingress async task | Inline event reading in the driver loop | One less concurrent task to manage. |

---

## 3. Version Definitions

### Version 0: Foundation + Deterministic Skeleton

#### PURPOSE
Prove that the event-driven kernel architecture works: events go in, the step engine processes them in order, facts change, actions come out. No LLM calls. All plans and interpretations are injected by test scripts.

#### INCLUDED CAPABILITIES
- Immutable data models (events, actions, facts, calls, effects, snapshots)
- Canonical JSON, value normalization, digest computation
- Deterministic ID generation
- FactStore with versioned facts, read sets, digest-based validity
- DependencyIndex (non-transitive: direct dependents only)
- SessionStore aggregate
- StoreTxn (mutation guard)
- ToolCatalog: manifest parsing, mutability detection, quarantine
- CallLedger, EffectLedger
- Kernel step engine: drain → order → apply → invalidate → decide → emit → log
- Batch ordering by `(ts_us, class, seq)`
- Reducers for: manifest, text_chunk, end_of_turn, interruption, tool_result
- ResultRouter: all 7 branches (unknown call_id, duplicate, malformed, cancel-requested, wrong goal, stale read set, consumed)
- CommitGate: conditions G1–G9 (no settle, no leases)
- Basic invalidation: changed fact → mark dependent calls INVALIDATED → emit CANCEL
- EmissionGate: schema validation, ordering (CANCEL → SPEAK → TOOL_CALL → CLARIFY → FINAL), read-set check at emission
- SnapshotProjector: compute from current committed facts at emission
- ClockPort: single implementation (stepped/Model B default)
- TimerWheel: harness-time timers, liveness rule
- OutputWriter: synchronous write to harness
- Codec: ICW format encode/decode
- Simplified simulator: feeds events from a YAML file, runs mock tools with deterministic latency, checks output actions
- Decision log: one structured record per step
- Basic TraceChecker: 5 key invariant checks (P1, P3, W1, S3, C1)

#### ARCHITECTURAL COMPONENTS
- `model/` (merged into 2–3 files: `types.py`, `events.py`, `actions.py`)
- `store/` (merged: `session.py`, `facts.py`, `catalog.py`, `ledgers.py`)
- `kernel/` (`step.py`, `ordering.py`, `reducers.py`, `invalidation.py`, `executor.py`, `binder.py`, `commit.py`, `results.py`, `emission.py`, `snapshot.py`, `timers.py`)
- `adapters/` (`clock.py`, `codec.py`, `output_writer.py`)
- `sim/` (`harness.py`, `checker.py`)
- `canonical.py`, `ids.py`, `config.py`, `errors.py`

#### EXCLUDED CAPABILITIES
- All LLM workers (interpreter, planner, composer, framer, vision, ASR)
- FastResponder / speech generation
- TurnManager / QuickDetector
- Task state machine
- Triage / interruption handling
- Goal management
- Clarification
- Perception / multimodal
- Retry logic
- Reconciliation
- Response frames

#### EVALUATION COVERAGE
| Category | Coverage |
|---|---|
| Task Completion | ✗ (no LLM) |
| Interruption Recovery | ~ (cancellation mechanics work with injected plans) |
| Response Latency | ✗ (no speech) |
| Safety & Protocol | ~ (CommitGate, schema validation, no duplicates) |

#### REQUIRED SCENARIOS (with injected plans)
1. A manifest is parsed; a read-only tool call is emitted with valid arguments; result is consumed; snapshot emitted.
2. A slot changes; dependent in-flight call is cancelled in the same step; new call emitted.
3. A write passes CommitGate; result consumed; effect confirmed; no duplicate allowed.
4. A result arrives for a cancelled call → `completed_after_cancel`, not consumed.
5. Unknown call_id → ignored, no crash.
6. Duplicate result delivery → ignored.
7. Malformed manifest → tools quarantined; valid tools usable.

#### REQUIRED INVARIANTS
T1, T2, T3, S1, S3, P1, P2, P3, P5, W1, W2, W3, C1, C2, C3, O1, K1, K2, K3, K4, K5

#### TEST GATE
- All 7 scenarios pass with injected plans
- TraceChecker reports 0 violations
- Replay identity: 10 runs of the same scenario produce identical decision logs
- Static check: no wall-clock reads in kernel/store modules

#### DEMO CAPABILITY
"Here is the coordination kernel processing events. Watch: a slot correction arrives, the stale search is cancelled in the same step, and no stale result is ever consumed. The commit gate blocks duplicate bookings."

#### FAILURE CONTAINMENT
V0 failing means the architecture is wrong. This must be rock-solid before proceeding.

#### GIT CHECKPOINT
Tag: `v0-skeleton`

---

### Version 1: Interruptible Text Agent

#### PURPOSE
A complete, working text-only agent that handles multi-turn conversations, slot corrections, and goal changes. This is the **first submission-capable version.**

#### INCLUDED CAPABILITIES
Everything in V0, plus:
- **TurnManager**: turn assembly, prefix digest
- **QuickDetector**: schema-aware extraction of HIGH-confidence values from chunks; correction/hold/abort/backchannel cue lexicons
- **Task state machine**: IDLE → LISTENING → UNDERSTANDING → PLANNING → EXECUTING → RESPONDING → COMPLETED → FAILED, plus TRIAGE and CLARIFYING
- **Interpreter worker**: LLM-backed turn interpretation → TurnInterpretation (act, intent, slot_deltas, commit_intent)
- **Planner worker**: LLM-backed plan generation → Plan with steps and bindings
- **Composer worker**: LLM-backed final response composition, with template fallback on timeout/failure
- **ModelGateway**: provider selection, structured output, deterministic settings, transport timeouts
- **interpret_apply**: convert interpretation → fact mutations (slots, intent, goals)
- **FastResponder**: templated ACK, PROGRESS, HOLD, CLARIFY, FINAL with typed claims
- **UtteranceBudget**: no speech during open turn, min gap, one ACK per turn, bounded HOLDs
- **Goal lifecycle**: ACTIVE → SUSPENDED → COMPLETED → FAILED → ABANDONED
- **Goal replacement**: suspend old goal, cancel its in-flight work, carry over session-scoped facts
- **Goal return**: reactivate suspended goal, reuse valid retained results
- **Triage**: hold mode during interruptions; release valid held work on backchannel; invalidate on correction
- **ClarificationManager**: ask when required parameter missing; void on correction
- **Retry policy**: reads retry up to 2x; writes retry only on known retryable error, once
- **Basic reconciliation**: INFORM truthfully when write outcome unknown; no compensation
- **Chunk-anchored cancellation**: QuickDetector HIGH value differs from in-flight call's slot → CANCEL
- **Watchdog**: wall-clock budget (105s); template FINAL on trigger
- **Truth checks on speech**: completion wording only with supporting claims; no narrating cancelled work
- **Scripted provider**: for testing without live models
- **Cassette record/replay**: for deterministic regression tests

#### ARCHITECTURAL COMPONENTS
All of V0, plus:
- `kernel/turns.py`, `kernel/detector.py`, `kernel/interpret_apply.py`, `kernel/task.py`
- `kernel/responder.py`, `kernel/clarify.py`, `kernel/retry.py`, `kernel/reconcile.py` (simplified)
- `kernel/scheduler.py` (basic job dispatch, no reaction slots)
- `workers/runner.py`, `workers/gateway.py`, `workers/interpreter.py`, `workers/planner.py`, `workers/composer.py`
- `workers/providers/` (anthropic + scripted)
- `workers/prompts/` (interpret.md, plan.md, compose.md)
- `observability/watchdog.py`
- `config/lexicons/en.yaml`, `config/templates/en.yaml`
- `entry.py` (setup + run_scenario)

#### EXCLUDED CAPABILITIES
- Transitive invalidation (C5)
- Settle barrier (C9)
- Schema-complete read sets / absence entries (C6)
- Claim-typed emission grades (C8 part b; part a atomic emission included)
- Response frames (C2)
- Rebinder (re-plan on every correction)
- Multimodal (vision, ASR, perception, evidence, leases)
- Speculative interpretation on prefixes
- Reaction slots / event-anchored scheduling
- Advanced reconciliation / compensation

#### EVALUATION COVERAGE
| Category | Coverage |
|---|---|
| Task Completion | ✓ (text scenarios) |
| Interruption Recovery | ✓ (slot corrections, goal changes, cancellation) |
| Response Latency | ✓ (fast-path ACK within step of EOT) |
| Safety & Protocol | ✓ (CommitGate, no duplicates, schema validation) |
| Multimodal | ✗ |

#### REQUIRED SCENARIOS
1. **N-01**: Simple read task (search flights → result → FINAL with snapshot)
2. **N-02**: Chained tool calls (search → get_seat_map → FINAL)
3. **N-03**: Clarification (missing date → CLARIFY → user answers → complete)
4. **N-04**: Successful write (search → "book it" → booking confirmed → FINAL)
5. **I-01**: Interrupt before planning (correction before interpreter returns)
6. **I-03**: Interrupt during tool execution (correction during search → cancel → re-search)
7. **I-07**: Repeated interruptions (3 corrections within one tool latency)
8. **I-08**: Goal replacement (flights → ticket)
9. **I-09**: Goal replacement then return
10. **I-13**: Abort ("never mind")
11. **S-01**: State-changing retry (booking error, retryable → one retry)
12. **S-02**: Write timeout (booking no result → INFORM)
13. **S-03**: Duplicate request (already booked → INFORM)
14. **R-02**: Duplicate result delivery → ignored
15. **R-06**: Simultaneous events (same-ts pairs handled deterministically)

#### REQUIRED INVARIANTS
All V0 invariants, plus: S2, S4, S5, P4, W4, W5, O2, O3, O4, O5

#### TEST GATE
- All 15 scenarios pass with live LLM (record mode), then pass in replay mode
- TraceChecker reports 0 violations on all scenarios
- Replay identity under Model B
- No false completion claims in any emitted utterance
- Watchdog fires and produces valid FINAL on slow-planner test

#### DEMO CAPABILITY
"A full interruptible text agent. Watch: the user asks for flights, then corrects the destination mid-search. The agent cancels the stale search in the same step, acknowledges 'Mumbai instead, searching now,' and completes with correct results. Then the user says 'forget flights, create a ticket' — the agent suspends the flight goal, handles the ticket, and when the user says 'back to flights,' it resumes with previously fetched results."

#### FAILURE CONTAINMENT
If V2 fails, V1 is the submission. It covers all text scenarios (50% of public suite) and all four scoring categories.

#### GIT CHECKPOINT
Tag: `v1-text-agent`
Branch: `release/v1`

---

### Version 2: Robust Recovery + Innovation Core

#### PURPOSE
Add the architectural innovations that most directly improve evaluation scores: transitive invalidation, settle barrier, schema-complete read sets, and claim-typed emission. These are the core technical differentiators for the hackathon presentation.

#### INCLUDED CAPABILITIES
Everything in V1, plus:
- **Transitive invalidation (INNOV C5)**: derived facts carry provenance read sets; retraction cascades through chains; downstream in-flight calls cancelled in the same step as the upstream correction
- **Settle barrier (INNOV C9)**: writes deferred until σ ms after end-of-turn with no new user event; timer liveness rule prevents deadlock under Model B
- **Schema-complete read sets (INNOV C6)**: read sets include absence entries for every tool parameter; setting a previously absent parameter invalidates the call
- **Claim-typed emission (INNOV C8)**: two-phase emission (validate batch, then write); claim grades (UNDERSTOOD, INTENDED, IN_PROGRESS, RESULT, EFFECT_DONE); templates keyed by grade
- **Rebinder**: localized corrections re-bind existing plan steps without calling the planner; escalate to planner only on structural change
- **Commit-last ordering**: writes deferred until all independent reads consumed
- **Enhanced triage**: hold/release with read-set validation; triage fallback on interpreter failure

#### ARCHITECTURAL COMPONENTS
All of V1, plus enhanced:
- `kernel/invalidation.py` (transitive retraction + unretraction)
- `kernel/binder.py` (absence entries)
- `kernel/commit.py` (settle timer, G10–G11)
- `kernel/executor.py` (rebinder, commit-last ordering, retained reuse on goal return)
- `kernel/emission.py` (two-phase, claim grades)
- `kernel/responder.py` (templates keyed by claim grade)

#### EXCLUDED CAPABILITIES
- Response frames (C2) — deferred to V3
- Multimodal (vision, ASR)
- Reaction slots / event-anchored scheduling
- Reference-bound write arguments (C10)
- Evidence leases (C7)
- Speculative interpretation

#### EVALUATION COVERAGE
| Category | Coverage |
|---|---|
| Task Completion | ✓ (improved: transitive chains correct, additions handled) |
| Interruption Recovery | ✓✓ (transitive cancellation, settle catches corrections) |
| Response Latency | ✓ (unchanged) |
| Safety & Protocol | ✓✓ (settle prevents write-during-correction, claim grades prevent false claims) |
| Multimodal | ✗ |

#### REQUIRED SCENARIOS
All V1 scenarios, plus:
- **I-04**: Interrupt during chain (transitive cancellation of downstream seat_map)
- **I-05**: Interrupt immediately before completion (correction at t-1, t, t+1 ms)
- **I-10**: Localized slot correction (only changed slot's dependents re-run)
- **I-11**: Backchannel interruption (no needless cancel on "mm-hmm")
- **I-12**: Optional constraint addition ("only direct flights" → search cancelled)
- **I-14**: Interruption during write settle window (correction at +150ms catches pre-emit)
- **I-15**: Interruption during write in flight (cancel emitted, reconciliation)
- **S-04**: Paraphrase duplicate detection
- **S-05**: Fabricated identifier blocked (if C10 included)
- **S-10**: Settle catches correction (ACK uses INTENDED grade for waiting write)
- **R-05**: Completion/cancellation race (same-ts result and correction)
- **T-05**: Timer liveness under Model B

#### REQUIRED INVARIANTS
All V1 invariants, plus: provenance-closed invariant (no non-retracted derived fact has invalid provenance)

#### TEST GATE
- All V1 + V2 scenarios pass
- TraceChecker with transitive retraction rule reports 0 violations
- Sweep test: I-14 with correction offset from -100 to +600 ms: zero 6pm bookings for offsets ≤ settle_ms
- Claim grade check: no ACK with IN_PROGRESS for a write that hasn't been emitted
- Timer liveness: T-05 passes (no deadlock when harness doesn't advance time)

#### DEMO CAPABILITY
All of V1, plus: "Watch the chain correction demo: search → seat_map in flight → user says 'Mumbai instead.' In the baseline, the seat_map for the old flight completes and might be spoken. In our system, transitive invalidation cancels both calls in the same step. And watch the settle barrier: 'book the 6pm one' then 200ms later 'wait, the 8pm one.' The baseline has to reconcile a completed booking. Our agent never booked the 6pm flight."

#### FAILURE CONTAINMENT
If V3 fails, V2 is the submission. V2 covers all text scenarios with the full innovation set. This is the **recommended minimum target for submission**.

#### GIT CHECKPOINT
Tag: `v2-robust-recovery`
Branch: `release/v2`

---

### Version 3: Multimodal Grounding + Response Frames

#### PURPOSE
Add visual scenario support (20% of public suite, 1.5× hidden-set weighting) and response frames for structural grounding. This significantly improves the hidden-set score where multimodal scenarios may carry 27–60% of the weighted total.

#### INCLUDED CAPABILITIES
Everything in V2, plus:
- **VisionAnalyzer worker**: frame analysis for a question → claims with confidence
- **PerceptionScheduler**: question-driven frame analysis (no continuous analysis); demand-driven trigger on visual cues
- **SlotResolver**: merge perception claims with user statements; open conflicts on disagreement
- **Evidence alignment**: AT_UTTERANCE (frame nearest to cue chunk) and CURRENT_STATE (latest frame) modes
- **Simple evidence leases (simplified C7)**: CURRENT_STATE claims expire after `lease_ms` harness time; checked at CommitGate and emission; renewal = one re-analysis
- **Conflict handling**: MEDIUM/HIGH claim vs. user value → CLARIFY; blocks dependent steps
- **Response frames (INNOV C2, simplified)**: model generates template with JSONPath holes bound to result fields; kernel validates and evaluates on result arrival; composer as fallback
- **Frame-accepting tool parameters**: detected from schema; frame ref bound instead of agent-side analysis when available
- **Basic ASR**: Whisper-based, single mode (prefer text, fall back to ASR if no text chunks)
- **Audio/text deduplication**: prefer text when both arrive for same capture window

#### ARCHITECTURAL COMPONENTS
All of V2, plus:
- `kernel/perception.py` (scheduler, resolver, alignment, leases, conflicts)
- `kernel/frames.py` (response frame validation + evaluation)
- `workers/vision.py`, `workers/asr.py`
- `workers/framer.py`, `workers/prompts/frame.md`, `workers/prompts/vision.md`
- `store/evidence.py` (observations, analyses, questions, conflicts)
- `store/blobs.py` (binary storage for PNG/WAV)

#### EXCLUDED CAPABILITIES
- Full evidence leases with frame-count bounds
- Watch mode (continuous re-analysis)
- Speculative interpretation / reaction slots / event-anchored scheduling
- Reference-bound write arguments (C10)
- Inert-tail promotion (C1)
- Agreement-based perception confidence (C12)

#### EVALUATION COVERAGE
| Category | Coverage |
|---|---|
| Task Completion | ✓✓ (text + visual + audio; response frames improve grounding) |
| Interruption Recovery | ✓✓ (includes perception cancellation on goal change) |
| Response Latency | ✓ (frames reduce result→FINAL latency) |
| Safety & Protocol | ✓✓ (leases prevent writes on stale evidence) |
| Multimodal | ✓ (visual lookup, conflicting evidence, deictic alignment, ASR) |

#### REQUIRED SCENARIOS
All V2 scenarios, plus:
- **N-05**: Visual lookup happy path (frames → vision → manual_lookup → FINAL)
- **N-06**: Audio-only request (WAV → ASR → synthetic chunks → task)
- **M-02**: Partial transcript with inert tail vs correction tail
- **M-03**: Stale frame (lease expiry → renewal before FINAL)
- **M-06**: Conflicting visual/text evidence (user says Tab A, frame shows Tab S9 → CLARIFY)
- **M-07**: Multimodal cancellation (vision job aborted on goal replacement)
- **M-08**: Deictic alignment ("this one" → frame nearest to cue chunk)
- **N-02 with frames**: Chained calls → FINAL rendered from response frame

#### REQUIRED INVARIANTS
All V2 invariants, plus: M1, M2, M3

#### TEST GATE
- All V2 + V3 scenarios pass
- Frame coverage: ≥50% of FINALs rendered from frames in applicable scenarios
- No stale-evidence grounding for writes
- Perception cancellation on goal change: vision job aborted, no claims from aborted job

#### DEMO CAPABILITY
All of V2, plus: "Now watch the visual scenario. The user says 'the light on my Tab A keeps blinking.' The agent analyzes the frame, detects a Tab S9 (HIGH confidence), and asks for clarification: 'The camera shows a Tab S9. Is that the device you mean?' Then the user corrects to 'the charger,' and the agent retargets perception seamlessly. And watch the response frame: while the last search runs, the agent prepares a template with typed holes. When results arrive, the FINAL renders instantly — every data value is structurally grounded."

#### FAILURE CONTAINMENT
If V4 fails, V3 is the submission. V3 covers all modalities and all scoring categories, with the full innovation set for text and basic multimodal support.

#### GIT CHECKPOINT
Tag: `v3-multimodal`
Branch: `release/v3`

---

### Version 4: Hardening + Adversarial Timing + Demo Polish

#### PURPOSE
Harden the system against adversarial timing, add remaining innovations for edge cases, optimize for the hidden test set, and polish the demo.

#### INCLUDED CAPABILITIES
Everything in V3, plus:
- **Reference-bound write arguments (INNOV C10)**: identifier parameters must bind to consumed read results or user-verbatim values; model-produced identifiers refused
- **Targeted timing sweeps**: I-03, I-04, I-05, I-14, R-05 with offset sweeps across critical events
- **Enhanced TraceChecker**: all 34 invariant checks from the blueprint
- **Inert-tail promotion (INNOV C1)**: promoted speculative interpretation when tail contains only inert content (politeness, fillers)
- **Kit integration**: codec mapping to real kit format, policy updates from kit answers, public suite validation
- **Demo video recording**: 5-minute demo covering chain correction, settle barrier, visual lease, and adversarial timing
- **Documentation**: README with reproducible setup, measurements.md with baseline vs. full-system comparison
- **Dockerfile**: final, builds and runs public suite

#### ARCHITECTURAL COMPONENTS
All of V3, plus enhanced:
- `kernel/binder.py` (identifier binding rule)
- `kernel/turns.py` (inert-tail promotion)
- `sim/checker.py` (full invariant set)
- `adapters/codec.py` (kit mapping)
- `entry.py` (final wiring)

#### EXCLUDED CAPABILITIES
- Full adversarial schedule exploration with PCT and delta debugging
- Event-anchored reaction scheduling (C3)
- Criticality-aware worker scheduling (C16)
- Agreement-based perception confidence (C12)
- Expected-value gating of speculative reads (C15)
- Full evidence lease with frame-count bounds

#### EVALUATION COVERAGE
| Category | Coverage |
|---|---|
| Task Completion | ✓✓ |
| Interruption Recovery | ✓✓ |
| Response Latency | ✓✓ (inert-tail promotion reduces post-EOT latency) |
| Safety & Protocol | ✓✓ (reference-bound arguments catch paraphrase duplicates) |
| Multimodal | ✓ |

#### REQUIRED SCENARIOS
All V3 scenarios, plus:
- **S-04**: Paraphrase duplicate detection (identifier-based fingerprint)
- **S-05**: Fabricated identifier blocked
- **T-02**: Adversarial sweeps on critical scenarios
- **M-02**: Inert-tail promotion ("please" → promoted, no extra interpret call)
- **All 9 public suite scenarios** (after kit integration)

#### REQUIRED INVARIANTS
All V0–V3 invariants (T1–T3, S1–S5, P1–P6, W1–W5, C1–C3, O1–O5, M1–M4), plus:
- **T4**: Monotonic progress under inert-tail promotion (promoted speculatives never regress state)
- **W6**: Reference-bound write uniqueness (no duplicate effect across paraphrase variations)
- **C4**: Timing sweep convergence (zero race defects across all simulated perturbation offsets)

#### TEST GATE
- All V3 + V4 scenarios pass
- Timing sweeps: 0 violations across all offsets
- Public suite: all 9 scenarios pass in kit harness
- Replay identity: 10 runs, identical traces
- Docker image builds, runs public suite from clean checkout

#### DEMO CAPABILITY
The full 5-minute demo narrative from INNOV §7.6:
1. Chain correction (transitive cancellation)
2. Write settling (correction 200ms after "book it")
3. Visual lease (indicator changes between lookup and response)
4. Adversarial timing verification (sweep results)
5. Results table (baseline vs. full system)

#### FAILURE CONTAINMENT
V3 is the fallback. V4 adds hardening and polish but no new scoring categories.

#### GIT CHECKPOINT
Tag: `v4-hardened` → then `PRISM_GENAI_HACKATHON_Y2026`
Branch: `release/v4`

---

## 4. Scenario Matrix

| Scenario Class | V0 | V1 | V2 | V3 | V4 | Final |
|---|---|---|---|---|---|---|
| Simple read task | ~ (injected) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Chained tool calls | ~ (injected) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Clarification | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Successful write | ~ (injected) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Slot correction during search | ~ (injected) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Correction during chain (transitive) | ✗ | ~ (non-transitive cancel) | ✓ | ✓ | ✓ | ✓ |
| Correction just before completion | ✗ | ~ | ✓ | ✓ | ✓ | ✓ |
| Repeated rapid interruptions | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Goal replacement | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Goal replacement + return | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Localized slot correction | ✗ | ~ | ✓ | ✓ | ✓ | ✓ |
| Backchannel interruption | ✗ | ~ | ✓ | ✓ | ✓ | ✓ |
| Addition during search | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ |
| Abort | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Write during correction (settle) | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ |
| Write timeout | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Duplicate state-changing call | ~ (injected) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Paraphrase duplicate | ✗ | ✗ | ~ | ~ | ✓ | ✓ |
| Unseen tool | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Visual lookup | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |
| Conflicting visual/text evidence | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |
| Deictic alignment | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |
| Stale frame / lease expiry | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |
| Audio-only request | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |
| Audio/text disagreement | ✗ | ✗ | ✗ | ~ | ~ | ✓ |
| Adversarial timing sweeps | ✗ | ✗ | ~ | ~ | ✓ | ✓ |
| Public suite (kit) | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ |

**✓** = supported | **~** = partial | **✗** = not implemented

**Fallback submission candidate:** V1 (first submission-capable) → V2 (recommended minimum) → V3 (target)

---

## 5. Risk-Based Prioritization

| Feature / Subsystem | Eval Value | Effort | Failure Risk | Version | Reason |
|---|---|---|---|---|---|
| Kernel step engine + facts + read sets | Critical | 1.5 days | Low (well-specified) | V0 | Everything depends on this |
| CommitGate + EffectLedger | Critical | 0.5 days | Low | V0 | Safety scoring is binary |
| ResultRouter (all branches) | Critical | 0.5 days | Low | V0 | Stale result handling is core IR |
| ToolCatalog + manifest parsing | Critical | 0.5 days | Low | V0 | Unseen tools is a scenario class |
| LLM Interpreter + Planner | Critical | 1.5 days | Medium (prompts) | V1 | Can't complete tasks without LLM |
| FastResponder + templates | High | 0.5 days | Low | V1 | 15% latency + quality multiplier |
| Task state machine + triage | High | 1 day | Medium | V1 | Governs interruption behavior |
| Goal suspension + return | High | 0.5 days | Low | V1 | Stated scenario class |
| Transitive invalidation (C5) | High | 1 day | Medium (tricky) | V2 | Highest IR improvement |
| Settle barrier (C9) | High | 0.5 days | Low | V2 | Highest safety improvement |
| Schema-complete read sets (C6) | Medium-High | 0.3 days | Low | V2 | Small; closes silent failure |
| Claim-typed emission (C8) | High | 0.5 days | Low | V2 | Fixes defect L3; truthfulness |
| Vision worker + perception | Medium-High | 1 day | Medium (model) | V3 | 1.5× multimodal weighting |
| Response frames (C2) | Medium-High | 1 day | Medium | V3 | Demo value + grounding |
| ASR worker | Medium | 0.5 days | Medium | V3 | Audio scenarios (30% public) |
| Evidence leases (simplified) | Medium | 0.5 days | Low | V3 | Prevents stale visual evidence |
| Ref-bound write args (C10) | Medium | 0.5 days | Low | V4 | Safety refinement |
| Inert-tail promotion (C1) | Low-Medium | 0.3 days | Low | V4 | Model A latency only |
| Kit integration | Critical | 1 day | HIGH | V4 | Depends on unreleased kit |
| **DANGEROUS:** Full adversarial explorer | Medium | 2+ days | HIGH | CUT | Could consume all remaining time |
| **DANGEROUS:** Full reconciliation | Low | 1+ days | HIGH | CUT | Rare scenario; INFORM suffices |
| **DANGEROUS:** Multiple clock models | Low | 1 day | Medium | CUT | Build for one; adapt |

---

## 6. Time Budget

### Day 1: V0 — Foundation (estimated: 10–12 hours)

| Task | Hours | Notes |
|---|---|---|
| Repository setup, pyproject.toml, Dockerfile skeleton, config | 1 | |
| Data models (types, events, actions) | 1.5 | Merged files, pydantic models |
| canonical.py, ids.py | 1 | |
| FactStore + DependencyIndex + SessionStore + StoreTxn | 2 | |
| ToolCatalog + manifest parsing | 1 | |
| CallLedger + EffectLedger | 1 | |
| Kernel step engine + ordering + reducers | 2 | |
| **Debugging buffer** | 1.5 | |

### Day 2: V0 Complete + V1 Start (estimated: 10–12 hours)

| Task | Hours | Notes |
|---|---|---|
| ResultRouter + CommitGate + EmissionGate + SnapshotProjector | 2 | |
| Invalidation engine (non-transitive) | 1 | |
| ClockPort + TimerWheel + OutputWriter + Codec | 1.5 | |
| Simple test harness (sim/harness.py, checker.py) | 1.5 | |
| V0 scenario tests (7 injected-plan scenarios) | 1 | |
| **V0 FREEZE + TAG** | 0.5 | `git tag v0-skeleton` |
| TurnManager + QuickDetector | 1.5 | |
| Task state machine (states + transitions) | 1.5 | |
| **Debugging buffer** | 1.5 | |

### Day 3: V1 — Text Agent (estimated: 10–12 hours)

| Task | Hours | Notes |
|---|---|---|
| ModelGateway + Anthropic provider + scripted provider | 1.5 | |
| Interpreter worker + interpret prompt + interpret_apply | 2 | |
| Planner worker + plan prompt | 1.5 | |
| Composer worker + compose prompt + template fallback | 1 | |
| FastResponder + UtteranceBudget + templates | 1.5 | |
| ClarificationManager | 0.5 | |
| Goal lifecycle + triage + carry-over | 1 | |
| **Debugging buffer** | 1.5 | |

### Day 4: V1 Complete + V2 Start (estimated: 10–12 hours)

| Task | Hours | Notes |
|---|---|---|
| Retry + basic reconciliation | 0.5 | |
| Watchdog | 0.5 | |
| entry.py (setup + run_scenario) | 1 | |
| V1 scenario tests (15 scenarios, live + replay) | 2 | |
| Fix failing scenarios and prompt tuning | 2 | |
| **V1 FREEZE + TAG** | 0.5 | `git tag v1-text-agent` |
| Transitive invalidation (C5): provenance on derived facts | 1.5 | |
| Transitive invalidation: retraction cascade + unretraction | 1.5 | |
| **Debugging buffer** | 1 | |

### Day 5: V2 — Robust Recovery (estimated: 10–12 hours)

| Task | Hours | Notes |
|---|---|---|
| Settle barrier (C9): timer, G10/G11, commit-last ordering | 1.5 | |
| Schema-complete read sets (C6): absence entries in binder | 1 | |
| Claim-typed emission (C8): two-phase + grades | 1.5 | |
| Rebinder: re-bind on localized corrections | 1 | |
| Enhanced triage: hold/release with read-set validation | 1 | |
| V2 scenario tests (12 additional scenarios) | 1.5 | |
| Fix failing scenarios | 1.5 | |
| **V2 FREEZE + TAG** | 0.5 | `git tag v2-robust-recovery` |
| **Debugging buffer** | 1 | |

### Day 6: V3 — Multimodal (estimated: 10–12 hours)

| Task | Hours | Notes |
|---|---|---|
| VisionAnalyzer worker + vision prompt | 1.5 | |
| PerceptionScheduler + SlotResolver + alignment | 2 | |
| Simple evidence leases | 0.5 | |
| Conflict handling (CLARIFY on disagreement) | 0.5 | |
| Response frames: framer worker + frame evaluation | 2 | |
| ASR worker (Whisper) + audio/text dedup | 1 | |
| V3 scenario tests (8 multimodal scenarios) | 1 | |
| Fix failing scenarios | 1 | |
| **V3 FREEZE + TAG** | 0.5 | `git tag v3-multimodal` |
| **Debugging buffer** | 0.5 | |

### Day 7: V4 — Hardening + Final (estimated: 10–12 hours)

| Task | Hours | Notes |
|---|---|---|
| Kit integration (codec mapping, policy updates) | 2 | If kit available |
| Reference-bound write arguments (C10) | 1 | |
| Inert-tail promotion (C1) | 0.5 | |
| Targeted timing sweeps (5 scenarios) | 1 | |
| Enhanced TraceChecker | 1 | |
| Full regression run, fix any failures | 2 | |
| Demo video (5 min) | 1 | |
| README + measurements.md + Dockerfile final | 1 | |
| **FINAL FREEZE + TAG** | 0.5 | `PRISM_GENAI_HACKATHON_Y2026` |

---

## 7. Minimum Viable Submission (MVP) & Definition of Done

### Minimum Viable Submission (MVP) Specification

The MVP is NOT the entire blueprint. The MVP is the **single most critical milestone** of the hackathon: the smallest, rock-solid, independently runnable prototype that completely solves the core Theme 05 challenge, demonstrates interruption handling and recovery, preserves deep architectural integrity, and guarantees a credible, high-scoring submission even if all post-MVP work is cut.

**MVP Version Identity:** **Version 1 (`v1-text-agent`)** built upon the verified **Version 0 (`v0-skeleton`)**.

The MVP must be the smallest realistically implementable prototype that:
1. **Is independently runnable**: Can be executed directly from CLI or test harness without stubbing out core runtime components.
2. **Demonstrates the core Theme 05 problem**: Asynchronous streaming dialogue and slow tool execution where user utterances and tool results arrive concurrently.
3. **Demonstrates interruption handling**: Sub-step detection of user corrections and instant cancellation of dependent in-flight work.
4. **Demonstrates recovery after interruption**: Graceful re-anchoring of goal state, discarding stale tool outputs, and replanning without data corruption.
5. **Demonstrates asynchronous slow-path work**: Decoupled background LLM worker calls executing concurrently while fast-path speech remains responsive.
6. **Demonstrates safe coordination/state consistency**: Strict single-writer kernel with write-ahead ledger preventing duplicate mutations or hallucinated side effects.
7. **Demonstrates the most important evaluation-relevant scenarios**: All 15 fundamental dialogue, interruption, retry, and concurrency scenarios (N-01..N-04, I-01, I-03, I-07..I-09, I-13, S-01..S-03, R-02, R-06).
8. **Has enough technical depth to be credible as a hackathon submission**: Full formal 7-phase kernel, versioned FactStore, SHA-256 digest validation, dynamic snapshot projection, and stepped-clock discipline (no toy shortcuts).
9. **Can be completed and stabilized within the available implementation time**: Fits cleanly into the 5-day budget (1.5 days V0 + 3.5 days V1), leaving 2 buffer days for V2–V4.
10. **Can serve as the fallback submission if all later versions fail**: Packaged and tagged (`v1-text-agent`, `release/v1`) as a frozen, battle-tested release candidate ready to be tagged `PRISM_GENAI_HACKATHON_Y2026`.

```
FULL BLUEPRINT
     │
     ▼
OPUS OPTIMIZATION
     │
     ├──────────────────────┐
     ▼                      ▼
    MVP                  POST-MVP
(V0 + V1)          (V2 → V3 → V4)
     │                      │
     ▼                      ▼
   FREEZE            Incremental Tags
     │
     └── GUARANTEED SUBMISSION FALLBACK
```

### MVP CORE
The absolute minimum functionality that MUST exist:
1. **Single-Writer Coordination Kernel**: Synchronous `Kernel.step()` engine executing the 7-phase pipeline with serialized state mutations.
2. **Versioned FactStore & ReadSet Digest Validation**: Facts tracked with integer versions; read sets validated against FactStore revisions using SHA-256 digests.
3. **DependencyIndex & Same-Step Invalidation**: Dependency mapping linking facts to active tool calls; same-step invalidation and cancellation (0-step grace period under Model B).
4. **TurnManager & QuickDetector**: Streaming text chunk buffer, prefix digest computation, and regex-based extraction of high-confidence slot values and interruption cues (`STOP`, `WAIT`, `NO`, `CHANGE`).
5. **Task State Machine & Triage**: States: `IDLE`, `LISTENING`, `UNDERSTANDING`, `PLANNING`, `EXECUTING`, `RESPONDING`, `CLARIFYING`, `TRIAGE`, `COMPLETED`, `FAILED`. Manages pause/resume during interruptions.
6. **FastResponder**: Fast-path templated acknowledgments (<500ms target), progress updates, holds, and final utterances.
7. **Asynchronous LLM Worker Pipeline**: `WorkerRunner` managing background `InterpreterWorker` (act, intent, slot deltas), `PlannerWorker` (multi-step tool plans), and `ComposerWorker` with template fallback.
8. **CommitGate & Write-Ahead EffectLedger (G1–G9)**: Prevents duplicate state-changing tool calls (Invariant W1). Writes recorded as `PENDING` before emission.
9. **7-Branch ResultRouter**: Systematically routes tool outputs; quarantines cancelled call results (`COMPLETED_AFTER_CANCEL`), ignores duplicates, and rejects stale read sets.
10. **Dynamic SnapshotProjector**: Generates read-only state projections from committed facts dynamically at action emission (Invariant S3).
11. **Virtual SteppedClock**: Enforces harness-time discipline (Invariant T1); zero wall-clock time reads in core kernel and store.
12. **ScenarioWatchdog**: 105s safety supervisor producing clean final fallback responses before harness timeout.

### MVP ARCHITECTURE
The minimum required repository subset:
- `config/`: `defaults.py`, `lexicons.py`, `templates.py`
- `src/prism_rt/`: `canonical.py`, `ids.py`, `config.py`, `errors.py`, `entry.py`
- `src/prism_rt/model/`: `types.py`, `events.py`, `actions.py`
- `src/prism_rt/store/`: `facts.py`, `catalog.py`, `ledgers.py`, `session.py`
- `src/prism_rt/adapters/`: `clock.py`, `output_writer.py`, `codec.py`
- `src/prism_rt/observability/`: `decision_log.py`, `watchdog.py`
- `src/prism_rt/kernel/`: `ordering.py`, `reducers.py`, `invalidation.py`, `turns.py`, `detector.py`, `task.py`, `binder.py`, `commit.py`, `results.py`, `snapshot.py`, `emission.py`, `responder.py`, `step.py`
- `src/prism_rt/workers/`: `runner.py`, `gateway.py`, `interpreter.py`, `planner.py`, `composer.py`
- `src/prism_rt/sim/`: `harness.py`, `checker.py`
- `tests/`: `conftest.py`, `test_v0.py`, `test_v1.py`

### MVP SCENARIOS
The MVP must pass all 15 core scenarios:
1. **N-01**: Simple read task (flights search → result → FINAL with snapshot).
2. **N-02**: Chained tool calls (search → get seat map → FINAL).
3. **N-03**: Clarification flow (missing date → CLARIFY → user responds → complete).
4. **N-04**: Successful write (search → "book it" → write confirmed → FINAL).
5. **I-01**: Interruption before planning (correction arrives before interpreter returns).
6. **I-03**: Interruption during tool execution (destination corrected mid-search → cancel → re-search).
7. **I-07**: Repeated rapid interruptions (3 rapid corrections during tool latency).
8. **I-08**: Goal replacement (user switches from flights to support ticket).
9. **I-09**: Goal replacement and return (switch to ticket, finish, return to flights reusing results).
10. **I-13**: User abort ("never mind" → cancel in-flight work → FINAL).
11. **S-01**: State-changing retry (booking error, retryable → one retry).
12. **S-02**: Write timeout (booking no result → truthful INFORM).
13. **S-03**: Duplicate request (user re-requests existing booking → INFORM).
14. **R-02**: Duplicate tool result delivery ignored.
15. **R-06**: Simultaneous events processed in deterministic order.

### MVP TEST GATE
Criteria that MUST pass before declaring MVP frozen:
- All 9 V0 foundation tests pass (`tests/test_v0.py`).
- All 15 V1 scenarios pass under both `ScriptedProvider` (deterministic replay) and live LLM gateway (`tests/test_v1.py`).
- `TraceChecker` reports 0 invariant violations across all runs (T1, P1, P3, W1, S3, C1).
- Zero false completion claims in emitted speech.
- Watchdog triggers clean template FINAL on slow planner.

### MVP DEMO
What can be demonstrated to judges using only the MVP:
> "Here is our Interruptible Real-Time Agent handling complex dialogue and slow asynchronous tools. The user asks for flights to Delhi. While the flight API is executing in the background, the user interrupts: 'Wait, Mumbai instead.' 
> Watch the decision log: within 0 milliseconds of harness time, the single-writer kernel detects the slot change via QuickDetector, marks the Delhi search INVALIDATED, emits an immediate CANCEL action to the harness, and responds with a fast-path acknowledgment: 'Mumbai instead, searching now.' 
> When the stale Delhi result arrives late, the ResultRouter routes it to completed_after_cancel and discards it—zero corrupted state. 
> Next, when the user says 'Book flight 101' twice, our write-ahead EffectLedger and CommitGate block the duplicate booking. 
> And when the user says 'Forget flights, open a support ticket,' the agent cleanly suspends the flight goal, completes the ticket, and seamlessly returns to flights with previously fetched results."

### MVP EXCLUSIONS
Deliberately excluded from MVP to guarantee on-time completion:
- Transitive invalidation (C5) — direct dependency invalidation only.
- Write settle barrier (C9) — writes execute upon EOT confirmation.
- Schema-complete absence read-sets (C6) — direct key read-sets only.
- Claim-typed emission grades (C8) — standard templated/composed utterances.
- Plan rebinder — full replanning on structural change.
- Multimodal perception / vision worker / evidence leases / ASR.
- Adversarial timing sweeps.

### MVP TIME BUDGET
- **Day 1: V0 Skeleton & Kernel**: 1.5 days (**100% COMPLETE & FROZEN with tag `v0-skeleton`**).
- **Day 2: V1 Conversational Engine**: 1 day (TurnManager, QuickDetector, TaskStateMachine, FastResponder).
- **Day 3: V1 LLM Workers & Integration**: 1.5 days (Gateway, Interpreter, Planner, Composer, Watchdog, Entry).
- **Day 4: V1 Scenario Testing & Stabilization**: 1 day (15 scenario tests, TraceChecker, replay validation).
- **Total MVP Effort**: ~5 days total.
- **Remaining Buffer**: 2 full days reserved for Post-MVP innovations (V2/V3/V4) and final packaging.

### MVP FALLBACK PROTOCOL
How the repository is frozen so this MVP can be submitted even if every subsequent version fails:
1. When all MVP tests pass, freeze the codebase:
   ```bash
   git add .
   git commit -m "feat(v1): complete minimum viable submission (MVP)"
   git tag v1-text-agent
   git branch release/v1
   ```
2. Create a feature branch for Post-MVP work:
   ```bash
   git checkout -b feature/v2-innovations
   ```
3. If Version 2, Version 3, or Version 4 encounters breaking bugs or time runs out with < 6 hours remaining:
   ```bash
   # Emergency one-command rollback to MVP:
   git checkout release/v1
   git tag PRISM_GENAI_HACKATHON_Y2026
   git push origin PRISM_GENAI_HACKATHON_Y2026
   ```
   This guarantees that the team ALWAYS has a working, high-scoring submission candidate ready.

### Comprehensive Feature Classification Matrix

| Feature / Subsystem | Category | Rationale | Target Milestone |
|---|---|---|---|
| Single-Writer Coordination Kernel (`Kernel.step`) | **MVP** | Core correctness mechanism preventing logical races | V0 (Done) |
| Deterministic Batch Ordering `(ts_us, class, seq)` | **MVP** | Eliminates race nondeterminism under simultaneous events | V0 (Done) |
| Versioned FactStore & ReadSet Digest Validation | **MVP** | Stale state detection mechanism required by all scored actions | V0 (Done) |
| DependencyIndex (Direct Dependents) | **MVP** | Fast cancellation lookup within same step (Invariant P3) | V0 (Done) |
| ToolCatalog with Manifest Parsing & Quarantine | **MVP** | Required to handle unseen tools and malformed definitions | V0 (Done) |
| CallLedger & EffectLedger (Write-Ahead) | **MVP** | Zero duplicate state-changing calls (Invariant W1) | V0 (Done) |
| 7-Branch ResultRouter | **MVP** | Handles cancelled, stale, duplicate, and wrong-goal results | V0 (Done) |
| SteppedClock (Model B Virtual Harness Time) | **MVP** | Enforces harness time; zero wall-clock reads (Invariant T1) | V0 (Done) |
| SnapshotProjector (Dynamic Emission Projection) | **MVP** | Required for task completion and recovery scoring (85% weight) | V0 (Done) |
| TurnManager (Chunk Buffering & EOT) | **MVP** | Ingestion of streaming user dialogue | V1 |
| QuickDetector (Chunk-Anchored Cue/Slot Extraction)| **MVP** | Sub-step interruption detection and cancellation | V1 |
| TaskStateMachine & Triage Mode | **MVP** | Orchestrates dialogue states, holds, and resumptions | V1 |
| FastResponder (Templated ACK/PROGRESS/HOLD) | **MVP** | Response latency score (15%) + quality multiplier | V1 |
| Interpreter & Planner LLM Workers | **MVP** | Required to complete tasks dynamically | V1 |
| Composer Worker with Template Fallback | **MVP** | Response quality and timeout prevention | V1 |
| Scripted Provider Mock | **MVP** | Deterministic regression testing and replay identity (C1) | V1 |
| ScenarioWatchdog | **MVP** | Prevents scenario timeout under slow planning | V1 |
| Transitive Invalidation (Provenance Closure, C5) | **POST-MVP** | Enhances recovery scoring on chained tool cancellations | V2 |
| Settle Barrier (Write Delay Timer, C9) | **POST-MVP** | Prevents write-during-correction races | V2 |
| Schema-Complete Read Sets (Absence Tracking, C6) | **POST-MVP** | Catches parameter additions during background execution | V2 |
| Claim-Typed Emission & Grades (C8) | **POST-MVP** | Prevents truthful speech violations on pending writes | V2 |
| Plan Rebinder (Localized Corrections) | **POST-MVP** | Lowers latency on slot modifications without replanning | V2 |
| PerceptionScheduler & Frame Grounding | **POST-MVP** | Multimodal visual scenarios (1.5× weight in hidden set) | V3 |
| Visual Evidence Leases | **POST-MVP** | Prevents actions based on stale camera frames | V3 |
| Reference-Bound Write Arguments (C10) | **POST-MVP** | Blocks paraphrase duplicate write calls | V4 |
| Inert-Tail Promotion (C1) | **POST-MVP** | Latency optimization under Model A | V4 |
| Targeted Timing Sweeps | **POST-MVP** | Proves stability under millisecond jitter | V4 |
| Kit Codec Mapping & Docker Packaging | **POST-MVP** | Official evaluation harness integration | V4 |
| Full Adversarial PCT Exploration (C17 Full) | **CUT** | Too complex; 2+ days effort with high risk of dead ends | Never |
| Compensating Tool Reconciliation | **CUT** | Rare edge case; truthful INFORM covers 90% of scoring value | Never |
| Multiple Clock Model Implementations | **CUT** | Model B suffices; adapt on kit arrival if necessary | Never |
| Full Production Observability (Prometheus/Metrics)| **CUT** | Structured JSONL decision log is sufficient and simpler | Never |
| Production ASR Pipeline | **CUT** | Prefer text transcripts; simple Whisper mock fallback | Never |
| Dual-Channel Interpretation Cross-Check (C14) | **CUT** | Low scoring relevance; purely diagnostic | Never |

### MVP-First Implementation Protocol

```
PHASE 1: MVP Core Implementation
  └── V0 Skeleton (COMPLETE) + V1 Turn/Detector/Task/Responder
        │
PHASE 2: MVP Worker Integration
  └── V1 Workers (Interpreter, Planner, Composer), Gateway, Entry
        │
PHASE 3: MVP Deterministic Testing
  └── 15 Scenarios verified with ScriptedProvider + TraceChecker
        │
PHASE 4: MVP Hardening & Watchdog
  └── Live LLM validation + Watchdog timeout fallback verification
        │
PHASE 5: FREEZE MVP (CRITICAL CHECKPOINT)
  └── git tag v1-text-agent && git branch release/v1
        │
        ▼  <--- (ONLY PROCEED IF ALL ACCEPTANCE CRITERIA PASS)
PHASE 6: Post-MVP 1 (V2 - Transitive Invalidation & Settle Barrier)
        │
PHASE 7: Post-MVP 2 (V3 - Multimodal Grounding & Response Frames)
        │
PHASE 8: Post-MVP 3 (V4 - Hardening, Timing Sweeps & Kit Integration)
```

---

### MVP DoD (V1 — minimum submission)

- [ ] Runnable: `python -m prism_rt.entry` starts the agent
- [ ] Reproducible: same scenario produces same outputs in replay mode
- [ ] Dockerized: `docker build . && docker run` works
- [ ] Traceable: decision log shows why each action was taken
- [ ] Testable: 15 scenario tests pass
- [ ] Handles interruptions: slot corrections cancel stale work and update snapshots
- [ ] Handles goal changes: goal suspension, return, context preservation
- [ ] Safe: no duplicate state-changing calls; CommitGate enforces all conditions
- [ ] Demonstrable: can run a live demo showing interruption recovery

### FINAL DoD (V3 or V4 — target submission)

- [ ] All MVP DoD items
- [ ] Transitive invalidation: chain corrections cancel downstream in the same step
- [ ] Settle barrier: writes wait for quiet interval; corrections during settle caught
- [ ] Schema-complete read sets: additions invalidate in-flight searches
- [ ] Claim-typed emission: no false completion claims; epistemic grades match ledger
- [ ] Multimodal: visual lookup, deictic alignment, conflict resolution, lease-checked evidence
- [ ] Response frames: FINALs rendered from structural templates (≥50% coverage)
- [ ] ASR: audio-only scenarios handled
- [ ] Public suite: all 9 scenarios pass (if kit available)
- [ ] Timing sweeps: 0 violations across all offsets on critical scenarios
- [ ] Docker image builds from clean checkout
- [ ] README gives reproducible commands
- [ ] Demo video (≤5 min) covers chain correction, settle, visual lease, timing verification
- [ ] Tagged `PRISM_GENAI_HACKATHON_Y2026`

---

## 8. Version Independence Strategy

### Mechanism: Git Tags + Feature Flags

**Primary:** Git tags on known-good commits.

```
git tag v0-skeleton      # after V0 passes all tests
git tag v1-text-agent    # after V1 passes all tests
git tag v2-robust-recovery # after V2 passes all tests
git tag v3-multimodal    # after V3 passes all tests
git tag v4-hardened      # after V4 passes all tests
git tag PRISM_GENAI_HACKATHON_Y2026  # final submission
```

**Secondary:** Feature flags in `config.py` for innovations that can be toggled:

```python
class Config:
    # V2 innovations (can be disabled to revert to V1 behavior)
    transitive_invalidation: bool = True
    settle_ms: int = 300        # 0 = disabled
    absence_entries: bool = True
    claim_grades: bool = True   # False = all claims are UNDERSTOOD
    
    # V3 features (can be disabled to revert to V2 behavior)
    vision_enabled: bool = True
    asr_enabled: bool = True
    response_frames: bool = True
    evidence_leases: bool = True
    
    # V4 features
    identifier_binding: bool = True
    inert_tail_promotion: bool = True
```

**Rollback strategy:**

1. **"Version 3 broke, revert to Version 2":**
   ```
   git checkout v2-robust-recovery
   git tag PRISM_GENAI_HACKATHON_Y2026
   ```

2. **"V3 mostly works but one feature is buggy":**
   ```python
   # In config: disable the problematic feature
   Config(evidence_leases=False)
   ```
   Then re-tag.

3. **Implementation rule:** Each version's new code must be additive. Never modify V1 interfaces that V1 tests depend on. Add new methods, new code paths, new config options — but don't break the existing path.

---

## 9. Summary and Recommendations

### Recommended Version Sequence
V0 (skeleton) → **V1 (text agent)** → **V2 (robust recovery)** → V3 (multimodal) → V4 (hardened)

### Minimum Viable Submission (MVP) Scope
**Version 1 (Interruptible Text Agent)** — the smallest complete, submission-capable prototype serving as the guaranteed fallback submission.

### Recommended Target Scope (Post-MVP)
**Version 2 (Robust Recovery & Innovations)** — covers all text scenarios with the full innovation set (transitive invalidation, settle barrier, absence tracking, claim grades). If multimodal doesn't work, V2 still scores well on 50% of the public suite and the full innovation narrative applies.

### Features to Cut
- Full adversarial schedule exploration (C17 full — too large)
- Agreement-based perception confidence (C12 — unmeasurable)
- Expected-value gating of speculative reads (C15 — depends on kit Q6)
- Dual-channel interpretation cross-check (C14 — low value)
- Effect verification probes (C11 — low value)
- Full reconciliation with compensation tools
- Multiple clock model implementations
- Production-grade observability infrastructure
- import-linter enforcement
- Full scenario DSL with assertion vocabulary

### Features to Defer
- Event-anchored reaction scheduling (C3) — Model A benefit only
- Criticality-aware worker scheduling (C16) — minor
- Full evidence leases with frame-count bounds — simplified version in V3
- Watch mode / continuous frame analysis
- Speculative interpretation on transcript prefixes — internal optimization

### Features That Must Not Be Cut
- Single-writer kernel with serialized step
- Read-set / digest-based validity checking
- DependencyIndex for fast invalidation
- CommitGate with all conditions (no duplicates, commit intent, closed floor)
- EffectLedger (PENDING before emission)
- ResultRouter with stale/cancel/retained handling
- SnapshotProjector (computed at emission)
- FastResponder with typed claims and truth checks
- QuickDetector with chunk-anchored cancellation
- Schema validation on emission
- Harness-time ClockPort (no wall-clock correctness decisions)
- Decision log per step

### First Version That Is Realistically Submission-Capable
**V1** — a complete text agent that handles interruptions, goal changes, slot corrections, and writes safely. Covers all text scenarios (50% of public suite) and all four scoring categories.

### Final Version Target
**V3** — full multimodal support with response frames. V4 is polish and hardening.

### Biggest Implementation Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **LLM prompt quality** — interpreter/planner produce wrong act/intent/slots | Task Completion fails | Start with simple scenarios; iterate prompts; use scripted provider for regression; validate all outputs against schema |
| **Transitive invalidation bugs** — over-retraction or missed retraction | IR score drops or valid work lost | Comprehensive chain-correction tests; V1 works without transitive (non-transitive fallback) |
| **Kit format mismatch** — real harness has different event/action schema | Agent can't communicate with harness | Codec abstraction layer; all internal code uses ICW; kit mapping is configuration |
| **Clock model surprise** — real harness uses Model A with tight grace periods | Cancellation too slow | Build for Model B (worst case for liveness); chunk-anchored cancellation doesn't need model speed |
| **Model latency** — API calls too slow under 120s cap | Scenario timeout | Watchdog at 105s; template fallback for composer; bounded retries |
| **Multimodal complexity** — vision model unreliable, perception pipeline fragile | V3 fails | V2 is the fallback; vision is isolated in workers; feature flag to disable |
| **Time pressure** — any version takes longer than estimated | Later versions cut | Each version is independently runnable; tag and submit the latest passing version |

### Exact Handoff Instructions for Sonnet

Sonnet must treat this document as the single authoritative engineering blueprint and follow these exact rules of engagement:

1. **Strict Sequential Execution**:
   - Begin at **Day 1: Version 0 (Foundation Skeleton)**. Implement canonical serialization, ID generation, types/events/actions, SessionStore/FactStore/CallLedger, the 7-phase Kernel step engine, adapters, and simulation harness. Write `tests/test_v0.py` and ensure all 7 foundational scenarios pass with zero invariant violations. Freeze with tag `v0-skeleton`.
   - Advance to **Day 2–3: Version 1 (Interruptible Text Agent)**: Implement turn manager, quick detector, task state machine, LLM workers (interpreter, planner, composer), fast responder, and watchdog. Pass all 15 V1 scenarios. Freeze with tag `v1-text-agent` and create branch `release/v1`. This is your guaranteed hackathon submission fallback.
   - Advance to **Day 4–5: Version 2 (Robust Recovery & Innovations)**: Implement transitive invalidation (C5), settle barrier (C9), schema-complete read sets with absence tracking (C6), claim-typed emission with claim grades (C8), and plan rebinder. Pass all V2 scenarios. Freeze with tag `v2-robust-recovery` and create branch `release/v2`. This is the recommended minimum target.
   - Advance to **Day 6: Version 3 (Multimodal Grounding)**: Implement perception scheduler, vision analyzer worker, ASR worker, evidence store with leases, and response frame generator. Pass all multimodal scenarios. Freeze with tag `v3-multimodal` and create branch `release/v3`.
   - Finalize on **Day 7: Version 4 (Hardening & Kit Integration)**: Implement reference-bound write arguments (C10), inert-tail promotion (C1), kit codec adapter mapping, timing sweep harness, Docker container, and full invariant suite. Tag: `v4-hardened` and `PRISM_GENAI_HACKATHON_Y2026`.
2. **Zero Architectural Deviation**:
   - Never introduce asynchronous mutations directly into the `FactStore`. All mutations MUST go through `StoreTxn` inside the synchronous `Kernel.step()` execution.
   - Never read `time.time()`, `time.monotonic()`, or `datetime.now()` for causal or state-machine decisions in core modules. All timing must flow from `ClockPort.now()`.
   - Never emit a state-changing tool call without a `PENDING` entry in `EffectLedger` and passing all active `CommitGate` conditions.
   - Never emit an action whose `ReadSet` is stale at the moment of emission.
3. **Additive-Only Evolution**:
   - When moving from Version N to Version N+1, write new code additively. Never modify or break interfaces that Version N tests depend on.
   - Guard all version-specific innovations behind `Config` feature flags. If an innovation introduces instability, turning off its feature flag must immediately restore passing behavior.
4. **Mock First, Live Second**:
   - For all worker tests and scenario suites, implement deterministic scripted providers first. Verify all state transitions, cancellations, and invariant checks under 100% deterministic conditions.
   - Run live model tests only after deterministic tests pass cleanly.
5. **Continuous Invariant Verification**:
   - Run `TraceChecker` on every execution trace. Any violation of invariants T1–T3, S1–S5, P1–P6, W1–W6, C1–C4, O1–O5, M1–M4 constitutes an immediate test failure.
6. **Hard Deadline & Rollback Rule**:
   - If work on Version N+1 is incomplete or fails regression tests with less than 6 hours remaining before submission deadline, STOP work immediately. Check out the latest stable release branch (`git checkout release/vN`), run the test gate, build the Docker container, tag `PRISM_GENAI_HACKATHON_Y2026`, and submit.

---

## 10. Implementation Contract for Sonnet

This section is the concrete technical contract for Sonnet. Sonnet should be able to take this document and build the codebase directly without making major architectural decisions or guessing contracts.

### Repository Structure Target

```
prism-theme05/
├── README.md
├── pyproject.toml
├── Dockerfile
├── config/
│   ├── defaults.py          # All policy defaults as Python constants
│   ├── lexicons.py          # Cue lexicons, inert words
│   └── templates.py         # Utterance templates by kind and grade
├── src/prism_rt/
│   ├── __init__.py
│   ├── entry.py             # setup() + run_scenario()
│   ├── config.py            # Config dataclass with feature flags
│   ├── ids.py               # Deterministic ID generation
│   ├── canonical.py         # Canonical JSON, digests, normalization
│   ├── errors.py            # Typed exceptions
│   ├── model/
│   │   ├── __init__.py
│   │   ├── types.py         # All enums + small records (Fact, ReadSetEntry, Provenance, etc.)
│   │   ├── events.py        # IngressEvent, InternalEvent, Envelope, payloads
│   │   └── actions.py       # Action, SpeakBody, ToolCallBody, CancelBody, Snapshot
│   ├── store/
│   │   ├── __init__.py
│   │   ├── session.py       # SessionStore aggregate + StoreTxn
│   │   ├── facts.py         # FactStore + DependencyIndex
│   │   ├── catalog.py       # ToolCatalog, manifest parser
│   │   └── ledgers.py       # CallLedger, EffectLedger, GoalRegistry, PlanStore,
│   │                        # TurnLog, OutputLedger, EvidenceStore, JobTable, TimerWheel
│   ├── kernel/
│   │   ├── __init__.py
│   │   ├── step.py          # Kernel object + 7-phase step
│   │   ├── ordering.py      # Batch ordering
│   │   ├── reducers.py      # Event → state-change functions
│   │   ├── invalidation.py  # Dependency marking + transitive retraction (V2)
│   │   ├── turns.py         # TurnManager + prefix digest + inert-tail (V4)
│   │   ├── detector.py      # QuickDetector
│   │   ├── interpret_apply.py # Interpretation → fact mutations
│   │   ├── task.py          # Task state machine + goal ops + triage
│   │   ├── executor.py      # PlanExecutor + Rebinder (V2) + commit-last (V2)
│   │   ├── binder.py        # ArgBinder + fingerprint + absence (V2) + identifier (V4)
│   │   ├── commit.py        # CommitGate G1-G12 + settle (V2)
│   │   ├── results.py       # ResultRouter
│   │   ├── retry.py         # RetryPolicy
│   │   ├── reconcile.py     # ReconcilePolicy (simplified)
│   │   ├── clarify.py       # ClarificationManager
│   │   ├── responder.py     # FastResponder + UtteranceBudget + claim grades (V2)
│   │   ├── perception.py    # PerceptionScheduler + SlotResolver + leases (V3)
│   │   ├── frames.py        # Response frame validation + evaluation (V3)
│   │   ├── snapshot.py      # SnapshotProjector
│   │   ├── emission.py      # EmissionGate + two-phase (V2) + monitors
│   │   ├── scheduler.py     # JobScheduler (basic dispatch)
│   │   └── timers.py        # TimerWheel + liveness
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── runner.py        # WorkerRunner (submit/abort/results via mailbox)
│   │   ├── gateway.py       # ModelGateway + providers
│   │   ├── interpreter.py   # Interpreter worker
│   │   ├── planner.py       # Planner worker
│   │   ├── composer.py      # Composer worker + template fallback
│   │   ├── framer.py        # Response frame generator (V3)
│   │   ├── vision.py        # VisionAnalyzer worker (V3)
│   │   ├── asr.py           # SpeechRecognizer worker (V3)
│   │   └── prompts/         # Prompt templates
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── clock.py         # ClockPort interface + Model B impl
│   │   ├── codec.py         # ICW ↔ wire format
│   │   ├── ingress.py       # Event reader (inline in driver or separate task)
│   │   └── output_writer.py # Synchronous write to harness
│   ├── observability/
│   │   ├── __init__.py
│   │   ├── decision_log.py  # Structured record per step
│   │   └── watchdog.py      # Wall-clock budget
│   └── sim/
│       ├── __init__.py
│       ├── harness.py       # Simplified test harness
│       └── checker.py       # Invariant checker
├── tests/
│   ├── scenarios/           # YAML scenario files
│   ├── test_v0.py           # V0 acceptance tests
│   ├── test_v1.py           # V1 scenario tests
│   ├── test_v2.py           # V2 scenario tests
│   ├── test_v3.py           # V3 scenario tests
│   └── test_v4.py           # V4 hardening tests
├── scenarios/               # Scenario YAML files
└── docs/
    ├── architecture.md      # Copy of prompt2 (for submission)
    ├── innovations.md       # Copy of prompt3
    └── measurements.md      # Results table
```

### Core Files and Modules

| Module Path | Core Responsibility | Key Exports |
|---|---|---|
| `canonical.py` | Canonical JSON formatting, value normalization, cryptographic SHA-256 digests | `to_canonical_json`, `normalize_value`, `compute_digest`, `compute_read_set_digest` |
| `ids.py` | Deterministic counter- and seed-based ID generation for reproducible replays | `IdGenerator`, `DEFAULT_ID_GEN` |
| `config.py` | Central configuration dataclass containing timing policies, limits, and version feature toggles | `Config`, `DEFAULT_CONFIG` |
| `errors.py` | Typed hierarchy of exceptions for invariant violations, gate rejections, and schema errors | `PrismError`, `InvariantViolationError`, `CommitGateRejected`, `StaleReadSetViolation` |
| `model/types.py` | Foundational system enums, provenance records, read-set descriptors, and change sets | `EventClass`, `ActionType`, `FactStatus`, `ClaimGrade`, `ToolMutability`, `CallStatus`, `EffectStatus`, `GoalStatus`, `TaskState`, `Provenance`, `ReadSetEntry`, `ReadSet`, `ChangeSet`, `ValidityResult` |
| `model/events.py` | Event envelopes, ordering tuples, and typed ingress event payloads | `Envelope`, `ManifestPayload`, `TextChunkPayload`, `EndOfTurnPayload`, `InterruptionPayload`, `ToolResultPayload`, `TimerFiredPayload` |
| `model/actions.py` | Outbound action definitions, body schemas, snapshot representations, and write results | `Action`, `SpeakBody`, `ToolCallBody`, `CancelBody`, `FinalBody`, `Snapshot`, `WriteResult` |
| `store/session.py` | Aggregate session container holding all store sub-components; creates and commits transactions | `SessionStore`, `StoreTxn` |
| `store/facts.py` | Versioned fact key-value store, read-set digest validity verification, and dependency indexing | `Fact`, `FactStore`, `DependencyIndex` |
| `store/catalog.py` | Tool catalog parsing manifests, classifying mutability, and quarantining malformed tools | `ToolCatalog`, `ToolManifest`, `ToolParameter` |
| `store/ledgers.py` | CallLedger tracking tool calls, EffectLedger tracking state writes, GoalRegistry, and TimerWheel | `CallRecord`, `CallLedger`, `EffectRecord`, `EffectLedger`, `GoalRecord`, `TimerWheel` |
| `kernel/ordering.py` | Deterministic batch sorting by `(ts_us, event_class, seq)` | `order_batch` |
| `kernel/reducers.py` | Pure state transition reducers mapping incoming event envelopes to store mutations | `reduce_manifest`, `reduce_chunk`, `reduce_eot`, `reduce_interruption`, `reduce_tool_result`, `reduce_timer` |
| `kernel/invalidation.py` | Computes direct and transitive dependencies of retracted/updated facts and marks calls invalidated | `InvalidationEngine` |
| `kernel/turns.py` | Turn buffer manager, chunk accumulation, prefix digest computation, and inert-tail detection | `TurnManager` |
| `kernel/detector.py` | Fast regex/lexicon scanner detecting high-confidence slot values, corrections, holds, and cues | `QuickDetector`, `DetectionResult` |
| `kernel/task.py` | Task coordination state machine governing states, goals, triage, and clarification modes | `TaskStateMachine`, `GoalLifecycle` |
| `kernel/binder.py` | Binds abstract plan step parameters to concrete values from FactStore and constructs read sets | `ArgBinder`, `BindingResult` |
| `kernel/commit.py` | Evaluates safety conditions G1–G12, enforces settle barrier, and performs ledger write-ahead | `CommitGate`, `CommitGateResult` |
| `kernel/results.py` | Evaluates tool results against 7 routing branches (unknown, duplicate, malformed, cancel, stale, etc.) | `ResultRouter`, `RouteOutcome` |
| `kernel/snapshot.py` | Computes dynamic projections of committed facts at the moment of emission | `SnapshotProjector` |
| `kernel/emission.py` | Validates action schemas, enforces strict emission order, and re-validates read sets at emission | `EmissionGate` |
| `kernel/responder.py` | FastResponder generating templated acknowledgments, progress updates, holds, and final utterances | `FastResponder`, `UtteranceBudget` |
| `kernel/step.py` | Synchronous 7-phase step coordination engine running all kernel phases | `Kernel`, `StepReport` |
| `workers/runner.py` | Background asynchronous worker runner submitting jobs and aborting cancelled tasks | `WorkerRunner`, `Job` |
| `workers/gateway.py` | Unified LLM provider interface managing timeouts, structured schemas, and fallback providers | `ModelGateway`, `ScriptedProvider` |
| `workers/interpreter.py` | Language understanding worker producing acts, intents, and slot deltas | `InterpreterWorker`, `TurnInterpretation` |
| `workers/planner.py` | Plan generation worker converting intents and slots into execution plans | `PlannerWorker`, `Plan`, `PlanStep` |
| `workers/composer.py` | Response composition worker with grounding and fallback to fast templates | `ComposerWorker` |
| `adapters/clock.py` | ClockPort protocol and SteppedClock implementation enforcing harness time | `ClockPort`, `SteppedClock` |
| `adapters/output_writer.py` | OutputWriter protocol and synchronous buffer writer sending actions to harness | `OutputWriter`, `BufferOutputWriter` |
| `adapters/codec.py` | Wire format encoder/decoder mapping between harness JSON/ICW lines and internal types | `HarnessCodec` |
| `observability/decision_log.py` | Structured per-step decision record logger providing auditability | `DecisionLogger`, `DecisionRecord` |
| `observability/watchdog.py` | Wall-clock safety supervisor ensuring hard timeouts trigger graceful final responses | `ScenarioWatchdog` |
| `sim/harness.py` | Deterministic simulation test harness feeding events, running mock tools, and gathering traces | `SimHarness` |
| `sim/checker.py` | Formal trace checker validating invariants T1–T3, S1–S5, P1–P6, W1–W6, C1–C4, O1–O5, M1–M4 | `TraceChecker` |
| `entry.py` | Runtime initialization, harness wiring, and scenario execution entrypoint | `setup`, `run_scenario`, `Runtime` |

### Core Interfaces

```python
# --- entry.py ---
class Runtime:
    async def run_scenario(self, io: HarnessIO, meta: ScenarioMeta) -> RunSummary: ...

async def setup(config_dir: str) -> Runtime: ...

# --- adapters/clock.py ---
class ClockPort(Protocol):
    def now(self) -> int: ...           # harness time in µs
    def wake_at(self, ts_us: int) -> None: ...  # hint to resume kernel
    def busy(self, flag: bool) -> None: ...     # Model B busy signaling

# --- adapters/output_writer.py ---
class OutputWriter(Protocol):
    def write(self, action: Action) -> WriteResult: ...  # synchronous write to harness

# --- store/session.py ---
class StoreTxn:
    def set_fact(self, key: str, value: Any, provenance: Provenance, 
                 status: FactStatus = FactStatus.COMMITTED,
                 derivation_read_set: ReadSet | None = None) -> None: ...
    def retract_fact(self, key: str) -> None: ...
    def commit(self) -> ChangeSet: ...

class SessionStore:
    @classmethod
    def new(cls, config: Config) -> SessionStore: ...
    def begin_txn(self, step_no: int) -> StoreTxn: ...
    @property
    def facts(self) -> FactStore: ...
    @property
    def catalog(self) -> ToolCatalog: ...
    @property
    def call_ledger(self) -> CallLedger: ...
    @property
    def effect_ledger(self) -> EffectLedger: ...

# --- store/facts.py ---
class FactStore:
    def get(self, key: str) -> Fact | None: ...
    def is_valid(self, read_set: ReadSet) -> ValidityResult: ...
    def revision(self) -> int: ...
    def snapshot_committed(self) -> dict[str, Any]: ...

class DependencyIndex:
    def register(self, dep_id: str, read_set: ReadSet) -> None: ...
    def unregister(self, dep_id: str) -> None: ...
    def dependents(self, changed_keys: set[str]) -> set[str]: ...
    def transitive_dependents(self, changed_keys: set[str], fact_store: FactStore) -> set[str]: ...

# --- kernel/commit.py ---
class CommitGate:
    def evaluate(self, candidate: ToolCallBody, read_set: ReadSet, 
                 store: SessionStore, now_us: int) -> CommitGateResult: ...

# --- kernel/results.py ---
class ResultRouter:
    def route(self, result: ToolResultPayload, store: SessionStore, 
              now_us: int) -> RouteOutcome: ...

# --- kernel/emission.py ---
class EmissionGate:
    def validate_and_order(self, candidates: list[Action], store: SessionStore, 
                           now_us: int) -> list[Action]: ...

# --- kernel/step.py ---
class Kernel:
    def step(self, batch: list[Envelope]) -> StepReport: ...
    # step is synchronous; never awaits

# --- workers/runner.py ---
class WorkerRunner(Protocol):
    def submit(self, job: Job) -> None: ...
    def abort(self, job_id: str) -> None: ...
```

### Core Data Models

```python
# --- model/types.py ---
class EventClass(IntEnum):
    INTERRUPTION = 0
    USER_SPEECH = 1
    TOOL_RESULT = 2
    TIMER = 3
    PERCEPTION = 4
    WORKER_RESULT = 5
    MANIFEST = 6

class ActionType(str, Enum):
    CANCEL = "cancel"
    SPEAK = "speak"
    TOOL_CALL = "tool_call"
    CLARIFY = "clarify"
    FINAL = "final"

class FactStatus(str, Enum):
    COMMITTED = "committed"
    RETRACTED = "retracted"

class ClaimGrade(str, Enum):
    UNDERSTOOD = "understood"
    INTENDED = "intended"
    IN_PROGRESS = "in_progress"
    RESULT = "result"
    EFFECT_DONE = "effect_done"

class ToolMutability(str, Enum):
    READ_ONLY = "read_only"
    STATE_CHANGING = "state_changing"

class CallStatus(str, Enum):
    READY = "ready"
    IN_FLIGHT = "in_flight"
    COMPLETED = "completed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"

class EffectStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"

@dataclass(frozen=True)
class Provenance:
    source: str
    event_id: str | None = None
    step_no: int = 0
    ts_us: int = 0

@dataclass(frozen=True)
class ReadSetEntry:
    key: str
    version: int
    absent: bool = False

@dataclass(frozen=True)
class ReadSet:
    entries: dict[str, ReadSetEntry] = field(default_factory=dict)
    digest: str = ""

@dataclass(frozen=True)
class ChangeSet:
    step_no: int
    added_or_updated: dict[str, Any]
    retracted: set[str]
    new_revision: int

@dataclass(frozen=True)
class ValidityResult:
    is_valid: bool
    stale_keys: list[str] = field(default_factory=list)
    missing_keys: list[str] = field(default_factory=list)

# --- model/events.py ---
@dataclass(frozen=True)
class Envelope:
    ts_us: int
    event_class: EventClass
    seq: int
    event_id: str
    payload_type: str
    payload: Any

    @property
    def ordering_key(self) -> tuple[int, int, int]:
        return (self.ts_us, int(self.event_class), self.seq)

@dataclass(frozen=True)
class ManifestPayload:
    tools: list[dict[str, Any]]

@dataclass(frozen=True)
class TextChunkPayload:
    turn_id: str
    text: str
    chunk_index: int = 0
    is_final: bool = False

@dataclass(frozen=True)
class EndOfTurnPayload:
    turn_id: str

@dataclass(frozen=True)
class InterruptionPayload:
    turn_id: str
    kind: str = "speech_start"

@dataclass(frozen=True)
class ToolResultPayload:
    call_id: str
    tool_name: str
    result: Any
    is_error: bool = False
    error_code: str | None = None
    error_message: str | None = None

@dataclass(frozen=True)
class TimerFiredPayload:
    timer_id: str
    scheduled_ts_us: int
    timer_type: str = "generic"

# --- model/actions.py ---
@dataclass(frozen=True)
class SpeakBody:
    text: str
    kind: str           # ack, progress, hold, final
    claim_grade: ClaimGrade | None = None
    supporting_claim_ids: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class ToolCallBody:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    mutability: ToolMutability = ToolMutability.READ_ONLY
    idempotency_key: str | None = None

@dataclass(frozen=True)
class CancelBody:
    target_call_id: str
    reason: str

@dataclass(frozen=True)
class FinalBody:
    text: str
    summary: str
    task_completed: bool

@dataclass(frozen=True)
class Snapshot:
    facts: dict[str, Any]
    active_goal: str | None
    pending_effects: list[str]
    revision: int
    digest: str

@dataclass(frozen=True)
class Action:
    action_type: ActionType
    action_id: str
    ts_us: int
    body: SpeakBody | ToolCallBody | CancelBody | FinalBody
    read_set: ReadSet
    snapshot: Snapshot | None = None
    decision_rule_id: str = ""

@dataclass(frozen=True)
class WriteResult:
    action_id: str
    accepted: bool
    written_ts_us: int
```

### Execution Order

1. **V0 (Day 1):** `canonical.py` → `ids.py` → `config.py` → `errors.py` → `model/types.py` → `model/events.py` → `model/actions.py` → `store/facts.py` → `store/catalog.py` → `store/ledgers.py` → `store/session.py` → `kernel/ordering.py` → `kernel/reducers.py` → `kernel/invalidation.py` → `kernel/binder.py` → `kernel/commit.py` → `kernel/results.py` → `kernel/snapshot.py` → `kernel/emission.py` → `kernel/timers.py` → `kernel/step.py` → `adapters/clock.py` → `adapters/output_writer.py` → `adapters/codec.py` → `sim/harness.py` → `sim/checker.py` → `tests/test_v0.py`
2. **V1 (Day 2–3):** `kernel/turns.py` → `kernel/detector.py` → `kernel/task.py` → `kernel/interpret_apply.py` → `kernel/responder.py` → `kernel/clarify.py` → `kernel/retry.py` → `kernel/reconcile.py` → `kernel/scheduler.py` → `workers/runner.py` → `workers/gateway.py` → `workers/interpreter.py` → `workers/planner.py` → `workers/composer.py` → `observability/decision_log.py` → `observability/watchdog.py` → `entry.py` → `tests/test_v1.py`
3. **V2 (Day 4–5):** enhance `kernel/invalidation.py` (transitive graph search) → enhance `kernel/binder.py` (absence entries) → enhance `kernel/commit.py` (settle timer + liveness) → enhance `kernel/emission.py` (two-phase commit + claim grades) → enhance `kernel/executor.py` (rebinder + commit-last) → enhance `kernel/responder.py` (grade-keyed utterance templates) → `tests/test_v2.py`
4. **V3 (Day 6):** `kernel/perception.py` → `kernel/frames.py` → `workers/vision.py` → `workers/asr.py` → `workers/framer.py` → enhance `store/ledgers.py` (EvidenceStore + leases) → `tests/test_v3.py`
5. **V4 (Day 7):** enhance `kernel/binder.py` (reference-bound write arguments) → enhance `kernel/turns.py` (inert-tail promotion) → enhance `adapters/codec.py` (kit wire mapping) → timing sweep tests in `tests/test_v4.py` → Dockerfile & submission bundle

### Dependency Order and Import Hierarchy

To guarantee ZERO circular dependencies, the codebase is structured into 7 strictly layered tiers. A module in Layer N may ONLY import from Layer N-1 or below:

```
Layer 0: Core Primitives
  ├── canonical.py
  ├── ids.py
  ├── config.py
  └── errors.py
         ▲
Layer 1: Domain Models
  ├── model/types.py
  ├── model/events.py
  └── model/actions.py
         ▲
Layer 2: Storage Layer
  ├── store/facts.py
  ├── store/catalog.py
  ├── store/ledgers.py
  └── store/session.py
         ▲
Layer 3: Harness Adapters
  ├── adapters/clock.py
  ├── adapters/output_writer.py
  └── adapters/codec.py
         ▲
Layer 4: Coordination Kernel
  ├── kernel/ordering.py
  ├── kernel/reducers.py
  ├── kernel/invalidation.py
  ├── kernel/turns.py
  ├── kernel/detector.py
  ├── kernel/binder.py
  ├── kernel/commit.py
  ├── kernel/results.py
  ├── kernel/snapshot.py
  ├── kernel/emission.py
  ├── kernel/responder.py
  ├── kernel/task.py
  └── kernel/step.py
         ▲
Layer 5: Asynchronous Workers & Observability
  ├── workers/runner.py
  ├── workers/gateway.py
  ├── workers/interpreter.py
  ├── workers/planner.py
  ├── workers/composer.py
  ├── observability/decision_log.py
  └── observability/watchdog.py
         ▲
Layer 6: System Orchestration, Simulators & Test Suites
  ├── entry.py
  ├── sim/harness.py
  ├── sim/checker.py
  └── tests/
```

### Feature Flags and Configuration

All runtime behaviors, policy timeouts, and version-specific innovations are controlled through a single frozen dataclass:

```python
@dataclass(frozen=True)
class Config:
    # Clock & Harness Model
    clock_model: Literal["A", "B", "C"] = "B"
    settle_ms: int = 300
    timer_granularity_us: int = 10_000
    watchdog_timeout_ms: int = 105_000

    # Policy Thresholds & Retries
    max_read_retries: int = 2
    max_write_retries: int = 1
    max_clarification_attempts: int = 2
    max_consecutive_holds: int = 3
    min_speech_gap_ms: int = 400

    # V0 Foundation (Always Active)
    enforce_strict_commit_gate: bool = True
    enforce_read_set_at_emission: bool = True
    enforce_invalidation_grace_period: bool = True

    # V2 Innovation Feature Flags
    transitive_invalidation: bool = False
    settle_barrier_enabled: bool = False
    absence_read_sets: bool = False
    claim_grades_enabled: bool = False
    rebinder_enabled: bool = False
    commit_last_ordering: bool = False

    # V3 Multimodal Feature Flags
    vision_enabled: bool = False
    asr_enabled: bool = False
    response_frames_enabled: bool = False

    # V4 Hardening Feature Flags
    inert_tail_promotion: bool = False
    strict_identifier_binding: bool = False

    # Observability & Tracing
    log_decisions: bool = True
    record_trace: bool = True
```

### Test Strategy and Verification Harness

1. **Test Categories**:
   - `tests/test_v0.py`: Foundational mechanics using injected plans (7 core scenarios + replay determinism + AST static check for zero wall-clock time in core).
   - `tests/test_v1.py`: Multi-turn conversational scenarios with scripted/live LLMs (15 scenarios covering read, chained, clarify, write, interrupt, goal replace/return).
   - `tests/test_v2.py`: Innovation verification (transitive invalidation chains, settle barrier offset tests, absence invalidation, claim grade template matching).
   - `tests/test_v3.py`: Multimodal scenarios (visual search, conflicting visual/text evidence, deictic pointing, evidence lease expiration).
   - `tests/test_v4.py`: Adversarial timing sweep permutations (I-03, I-04, I-05 sweeps at -10ms, 0ms, +10ms offsets) and official kit harness integration.
2. **Invariant Checker (`TraceChecker`) Assertions**:
   - `assert_T1(trace)`: Every timestamp in action or decision equals current harness clock. Zero calls to `time.time()`.
   - `assert_P1(trace)`: Every emitted action has a valid read set matching FactStore state at the emission step.
   - `assert_P3(trace)`: Downstream in-flight calls are cancelled within the same step (grace period = 0 under Model B) as the upstream fact mutation.
   - `assert_W1(trace)`: Zero duplicate state-changing calls for the same intent/parameters across the entire session trace.
   - `assert_S3(trace)`: Emitted snapshot digest equals SHA-256 digest of committed facts at emission step.
   - `assert_C1(traces)`: 10 repeated runs of identical event inputs produce byte-identical decision logs and emitted actions.
3. **Mocking and Fixtures**:
   - Use `SimHarness` with `SteppedClock` for fully deterministic step-by-step event feeding.
   - Use `ScriptedProvider` returning canned JSON responses for LLM workers to isolate testing from external network or latency variance.

### What NOT to Implement

- No `sim/explorer.py`, `sim/minimize.py`, `sim/report.py` (full adversarial PCT explorer)
- No `observability/metrics.py`, `observability/telemetry.py`, `observability/run_writer.py` (production observability)
- No `tools/*.py` (CLI exploration tools)
- No multiple clock models (build stepped Model B default; adapt if needed)
- No `workers/providers/openai_compat_provider.py` unless requested
- No `.importlinter` enforcement (enforce manually via dependency order)
- No `model/frames.py` separate from `kernel/frames.py` (merged)
- No `model/trace.py` (decision log suffices)
- No `store/holds.py`, `store/blobs.py` as separate files (merged into `ledgers.py`)

### What Must Never Be Simplified Away

- **Read-set validity check at emission** — Core correctness mechanism
- **CommitGate conditions G1–G12** — Safety scoring is binary; duplicate writes are fatal
- **EffectLedger write-ahead** — PENDING before emission; CONFIRMED/FAILED on result
- **Snapshot as projection at emission** — Snapshots appear in 85% of scoring weight
- **Batch ordering by (ts_us, class, seq)** — Strict determinism requirement
- **QuickDetector HIGH-confidence-only rule** — False cues destroy task completion
- **Truth checks on speech** — Completion wording only with supporting claims
- **Harness clock only** — Zero wall-clock reads in core kernel and store modules

### What Can Be Stubbed

- **Reconciliation**: Stub as "INFORM truthfully, no compensation"
- **Evidence leases**: Stub as "always valid" in V1/V2; simple timeout in V3
- **Perception scheduling**: Stub as "analyze first available frame for open question"
- **ASR deduplication**: Stub as "prefer text always"
- **Response frames**: Stub as "always use composer" in V1/V2
- **Triage fallback**: Stub as "treat as BACKCHANNEL if detector finds nothing"

### What Can Use Deterministic Mocks

- **Worker outputs in tests**: Scripted provider returning fixed `TurnInterpretation`/`Plan`/`Composition`
- **Tool results in tests**: Mock tools with deterministic latency and fixed responses
- **Vision claims in tests**: Scripted `VisionAnalyzer` returning fixed claims
- **ASR in tests**: Scripted ASR returning fixed text segments

### What Should Be Real

- **FactStore + DependencyIndex**: The core data structures must be real
- **Kernel step engine**: Must execute all 7 phases in order
- **CommitGate**: All condition checks must be real
- **ResultRouter**: All 7 routing branches must be real
- **EmissionGate**: Schema validation and emission ordering must be real
- **LLM calls in live mode**: Real API calls to Anthropic/OpenAI

### What Should Be Deferred

- Kit-specific codec mapping → Phase 11 equivalent (V4)
- Clock model selection → Default to Model B; adapt when kit arrives
- Policy registry YAML → Use Python constants in `config/defaults.py`; convert to YAML later if needed
- Measurement table population → Fill after tests pass

### Version Checkpoints

```
Day 1 end: V0 tests pass → git tag v0-skeleton
Day 3 end: V1 tests pass → git tag v1-text-agent; git branch release/v1
Day 5 end: V2 tests pass → git tag v2-robust-recovery; git branch release/v2
Day 6 end: V3 tests pass → git tag v3-multimodal; git branch release/v3
Day 7 end: V4 tests pass → git tag v4-hardened; git tag PRISM_GENAI_HACKATHON_Y2026
```

### Freeze Criteria

A version is frozen when:
1. All its listed scenario tests pass with live LLM (or scripted mocks)
2. All its listed scenario tests pass in replay mode (100% deterministic)
3. `TraceChecker` reports 0 invariant violations
4. No scenario produces a crash, timeout, or malformed output
5. The decision log is complete (every emitted action has an associated rule ID)

### Rollback Strategy

If Version N+1 breaks or destabilizes before the deadline:

```bash
# Example: If V3 is unstable, immediately rollback to V2:
git checkout release/v2
# Validate test suite passes cleanly
pytest -v tests/test_v2.py
# Tag and push final submission
git tag PRISM_GENAI_HACKATHON_Y2026
git push origin PRISM_GENAI_HACKATHON_Y2026
```

Alternatively, soft rollback using `Config` feature flags:
```python
# Disable broken V3 features while retaining all V2 innovations:
Config(vision_enabled=False, asr_enabled=False, response_frames_enabled=False)
```
