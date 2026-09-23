# Janus — Initial Feature Plan vs. What's Actually Implemented

This document has two parts: **what was originally proposed** (the full innovation catalog and the staged version plan, before any trimming) and **what the codebase actually has today**. It exists so the gap between "designed" and "built" is visible in one place, rather than scattered across `docs/prompt 3.txt`, `docs/prototype_version_plan.md`, `docs/post_v4_implementation_plan.md`, and `currentStatus.md`.

Source documents for Part 1: `docs/prompt 3.txt` (innovation analysis) and `docs/prototype_version_plan.md` (original staged plan) — both pre-implementation, written before any code existed. Source for Part 2: `currentStatus.md` and this session's own work, verified against the actual repository, not assumed from the plan.

---

## Part 1 — What Was Originally Proposed

### 1.1 The problem: 17 limitations found in the baseline architecture

Before proposing anything new, the analysis reviewed the baseline single-writer/read-set/commit-gate design against adversarial timing and found 17 weaknesses (`docs/prompt 3.txt` §1), summarized here by theme:

| Theme | Limitations |
|---|---|
| Stale work surviving | L1 (transitive invalidation lag), L2 (staleness by omission — an unbound optional parameter never invalidates), L10 (perception claims never expire) |
| Defects (must-fix regardless) | L3 (same-step claim ordering — SPEAK emitted before the TOOL_CALL it claims), L4 (timer liveness deadlock under a stepped clock), L15 (speech claims in-progress work before it's actually in flight) |
| Latency | L6 (speculative interpretation promotion fails on any trailing filler word), L8 (final response waits for a model call after the last result), L9 (triage holds all valid work during any interruption, even a backchannel), L12 (perception only triggers on lexical cues), L16 (no priority between speculative and critical jobs), L17 (visible speculative reads disabled by default) |
| Safety | L5 (a correction just after end-of-turn races an already-emitted write), L13 (fingerprints computed over unnormalized strings — paraphrases evade duplicate detection, fabricated IDs pass schema validation), L14 (writes aren't ordered relative to pending reads) |
| Multimodal | L7 (correction detector misses indirect phrasing — "the later one"), L11 (uncalibrated confidence labels from the vision model) |

### 1.2 The 17 candidate innovations (C1–C17)

| ID | Candidate | Addresses | Complexity | Decision |
|---|---|---|---|---|
| C1 | Inert-tail promotion of speculative interpretation | L6, L7 | S | **Select** (M1) |
| C2 | Prepared response frames with typed holes | L8, L3 | M | **Select** (M1) |
| C3 | Event-anchored reaction scheduling | L6, L8, L16 | M | **Select** (M1) |
| C4 | Planner bypass for single-tool intents | new-goal latency | S | Reject |
| C5 | Provenance-closed invalidation + value-based early cutoff | L1 | M | **Select** (M2) |
| C6 | Schema-complete read sets | L2 | S | **Select** (M2) |
| C7 | Evidence leases | L10, L11 | M | **Select** (M2) |
| C8 | Claim-typed atomic emission | L3, L15 | S | **Select** (M2, part a mandatory) |
| C9 | Commit-last ordering + settled commit barrier | L5, L14, L4 | S–M | **Select** (M3, liveness mandatory) |
| C10 | Reference-bound write arguments | L13 | S–M | **Select** (M3) |
| C11 | Effect verification probes | unknown write outcomes | M | Reject |
| C12 | Agreement-based perception confidence | L11 | M | Defer (needs labeled data) |
| C13 | Demand-driven perception at interpretation time | L12 | S | Adopt as support (folded into C3) |
| C14 | Dual-channel interpretation cross-check | L7 | S | Adopt as diagnostic only |
| C15 | Expected-value gating of speculative reads | L17 | M | Defer (depends on unreleased kit rules) |
| C16 | Criticality-aware worker scheduling | L16 | S | Adopt as support (folded into C3) |
| C17 | Adversarial schedule exploration + shared invariant monitors | all timing-dependent findings | L | **Select** (M4) |

**The technical identity, in one line: "Prepare early. Check at the point of use. Commit late."** The 9 selected candidates group into four milestones, each depending on the one before it:

| Milestone | Candidates | What it does |
|---|---|---|
| M1 — Prepared reactions | C1, C2, C3 (+C13, C16 as support) | Move model work *before* the event it reacts to |
| M2 — Complete validity conditions | C5, C6, C7, C8 | Make "is this still valid" exact — transitive, negative (absence), temporal, epistemic |
| M3 — Settled commits | C9, C10 | Move irreversible effects after everything that could still change them |
| M4 — Adversarial exploration | C17 | Verify the above hold across interleavings, not just one lucky ordering |

**Priority order under the deadline** (`docs/prompt 3.txt` §5.4, highest scoring-relevance-per-effort first): (1) C8a atomic emission + C9c timer liveness — defect fixes; (2) C17 fake harness/sweeps/monitors; (3) C5 transitive invalidation; (4) C9a/b commit-last + settle barrier; (5) C6 schema-complete read sets; (6) C8b claim grades; (7) C2 response frames; (8) C7 evidence leases; (9) C1, C3; (10) C10 reference-bound arguments; (11) C17 randomized schedules.

### 1.3 The original staged version plan (V0–V4)

`docs/prototype_version_plan.md` classified every subsystem before writing any code:

| Class | Meaning | Examples |
|---|---|---|
| **A. MUST HAVE** | Evaluation fails without it | Single-writer kernel, FactStore + read sets, DependencyIndex, CommitGate/EffectLedger, ResultRouter, FastResponder, SnapshotProjector, TurnManager/QuickDetector, task state machine, Interpreter/Planner workers, EmissionGate, ClockPort, decision log |
| **B. SHOULD HAVE** | High value, implement if time allows | Transitive invalidation (C5), commit-last + settle barrier (C9), schema-complete read sets (C6), claim-typed emission (C8), triage hold/release, goal suspend/return, rebinder, response frames (C2), basic perception, composer truth checks |
| **C. OPTIONAL** | Nice to have | Inert-tail promotion (C1), event-anchored scheduling (C3), criticality scheduling (C16), reference-bound args (C10), evidence leases (C7), speculative interpretation, ASR worker, watch mode, demand-driven perception (C13) |
| **D. CUT FOR HACKATHON** | Too expensive relative to value | Full adversarial exploration (C17, full), agreement-based confidence (C12), EV-gated speculation (C15), dual-channel cross-check (C14), effect verification probes (C11), full reconciliation/compensation tools, multiple `ClockPort` models, production-grade observability (Prometheus/telemetry), import-linter enforcement, an elaborate scenario DSL |

Staged as five versions, each required to be independently runnable/submittable before the next began:

| Version | Purpose | Adds |
|---|---|---|
| **V0** | Prove the kernel works — no LLM, plans injected by test scripts | Kernel step engine, FactStore, CommitGate G1–G9, ResultRouter, EmissionGate, basic (non-transitive) invalidation, 5-check TraceChecker |
| **V1** | First submission-capable version — a complete text agent | TurnManager/QuickDetector, task state machine, Interpreter/Planner/Composer workers, FastResponder, goal lifecycle + suspend/return, triage, clarification, retry, watchdog. **Explicitly excluded**: C5, C6, C8b, C2, rebinder, all multimodal, speculative interpretation |
| **V2** | The core technical differentiators | Transitive invalidation (C5), settle barrier (C9), schema-complete read sets (C6), claim-typed emission (C8), rebinder, commit-last ordering |
| **V3** | Visual scenario support (1.5× hidden-set weight) | VisionAnalyzer, PerceptionScheduler, SlotResolver, evidence alignment + simplified leases (C7), conflict handling, response frames (C2, simplified to JSONPath holes), basic ASR |
| **V4** | Harden against adversarial timing, polish for the hidden set | Reference-bound write args (C10), targeted timing sweeps, full 34-check TraceChecker, inert-tail promotion (C1) |

**Original scope reductions applied up front** (`docs/prototype_version_plan.md` §2) — the hackathon version was deliberately smaller than the full blueprint in specific, named ways: ~25–30 files instead of 60+; one `ClockPort` model (B) instead of three; response frames as plain JSONPath-hole templates instead of the full typed-operator DSL; a flat-timeout perception lease instead of frame-count + time bounds; INFORM+CLARIFY instead of full reconciliation/compensation; a decision log instead of production observability/telemetry; ~15 hand-written scenario tests instead of 50+ with PCT sweeps; targeted timing sweeps instead of full adversarial schedule exploration; a simplified test harness instead of a full discrete-event simulator; plain Python config constants instead of a YAML + policy registry.

---

## Part 2 — What's Actually Implemented

Verified against the repository as of this session (`git log`, `currentStatus.md`, direct code reads) — not assumed from the plan above. 190/190 tests passing, Docker-verified on Python 3.11.

### 2.1 The staged versions — all five frozen and tagged

| Version | Tag / branch | Status |
|---|---|---|
| V0 | `v0-skeleton` | Frozen — deterministic kernel skeleton, 8/8 tests |
| V1 | `v1-text-agent` / `release/v1` | Frozen — first submission-capable MVP, 15/15 tests |
| V2 | `v2-robust-recovery` / `release/v2` | Frozen — all 5 V2 innovation flags now default **on**, 13/13 tests |
| V3 | `v3-multimodal` / `release/v3` | Frozen — multimodal grounding behind `Config.vision_enabled`, 6/6 tests |
| V4 | `v4-hardened` / `release/v4` | Frozen — C10, timing sweeps, Docker verified, 18/18 tests |

Every version's own scenario/invariant requirements from the original plan were met before moving on (§3's per-version TEST GATE sections) — none of V0–V4 was skipped or partially built.

### 2.2 Beyond V4 — six additional phases the original plan didn't specify

The original plan stopped at V4. After V4 froze, the two official guideline documents were read in full for the first time and found the real async entry point had never once been executed — this triggered a full re-sequencing (`docs/post_v4_implementation_plan.md`), which drove six more phases:

| Phase | Tag | What it added |
|---|---|---|
| A — Integration Surface | `v5-integration` | A genuinely async `entry.py`, tolerant wire-format codec, `audio_clip` ingestion, watchdog salvage |
| 2 — Audio/ASR | `v6-audio` | `kernel/audio.py` (`AsrScheduler`), `workers/asr.py`, `Config.audio_mode` |
| 3 — Live Multimodal Provider | `v7-live-multimodal` | Real image/audio bytes (`MediaPart`/`blob_resolver`) to the live Gemini API |
| 4 — Chunk-Anchored Interruption | `v8-chunk-anchor` | Cancel on a HIGH-confidence correction mid-utterance, not just at end-of-turn |
| 5 — Task-Completion Depth | `v9-task-completion` | C2 response frames (full typed-hole set: `field`/`count`/`min`/`max`/`first_k`/`exists` — beyond the original plan's JSONPath-only simplification) and C9 commit-last ordering |
| 6 — Response Latency | `v10-response-latency` | Speculative interpretation coalescing/promotion, content-bearing ACK |

Plus three rounds of independent-review-driven bugfixes (not phases, no release branches): `v10-1-correction-race-fix`, `v10-2-failure-honesty-fix`, `v10-3-write-lineage-fix` — each found by a fresh model given full repo access and told to hunt for real bugs, each independently verified by direct reproduction before shipping (see `reviews/index.md`).

### 2.3 The innovation catalog (C1–C17) — corrected against the actual code

`docs/post_v4_implementation_plan.md`'s own tracking table was checked directly against the repository this session and found to understate progress in one place (C2); corrected here:

| ID | Innovation | Status |
|---|---|---|
| C1 | Inert-tail promotion | ✅ **Built** (this session, `kernel/turns.py._inert_tail`) — TTFS(EOT) on a trailing "please": 100ms → 0; CS-30 now checked offline |
| C2 | Response frames with typed holes | ✅ **Built** (Phase 5, `kernel/frames.py.FrameScheduler`) — full typed-hole operator set, beyond the original plan's JSONPath-only simplification |
| C3 | Event-anchored reaction scheduling | ✗ Deliberately not built — its purpose is already met by the same-step kernel (kernel-decided reactions) plus C1/C2/speculation (model work); the remaining value applies only under clock Model A |
| C5 | Provenance-closed invalidation | ◐ Transitive invalidation built (V2); value-based early cutoff not |
| C6 | Schema-complete read sets | ✅ **Built** (V2's absence entries, extended to full schema completeness this session — see §2.5) |
| C7 | Evidence leases | ✅ Built (V3, simplified to a flat timeout as originally planned) |
| C8 | Claim-typed atomic emission | ◐ Claim grades built (V2); overlay validation (CS-06) not |
| C9 | Commit-last ordering + settle barrier | ✅ Built (settle barrier V2, commit-last ordering Phase 5) |
| C10 | Reference-bound write arguments | ✅ Built (V4) |
| C4, C11 | Planner bypass; effect verification probes | Correctly rejected in the original analysis, never revisited |
| C12, C14–C16 | Confidence labels, dual-channel, EV-gating, criticality scheduling | Deferred as originally planned (C16 not needed: no job-concurrency cap exists to prioritize); C13 realized via V3's "demand" trigger |
| C17 | Adversarial schedule exploration | ✅ **Built, bounded** (this session, `sim/explorer.py` + `sim/minimize.py`) — found 3 real bugs on its first run; 9,142 schedules clean after the fixes |

**Net (re-audited against the code): 7 of the 10 selected mechanisms fully built (C1, C2, C6, C7, C9, C10, C17), 2 partial (C5, C8), 1 deliberately not built (C3).** All four layers of the original thesis — M1 prepared reactions, M2 complete validity, M3 settled commits, M4 adversarial exploration — now exist and are exercised by tests.

### 2.4 Independent review rounds — not in the original plan at all

Four rounds of fresh-model code review (`reviews/index.md`), each verifying claims by direct reproduction rather than trusting the reviewer's own report:

| Round | Reviewer | Result |
|---|---|---|
| 1 | Claude Opus 4.6 (Thinking) | Correction-race stale-COMPOSE bug — fixed |
| 2 | Fresh Claude Sonnet | Retry-exhaustion false-completion-claim + S-07 livelock — fixed |
| 3 | Sonnet, continued on Gemini 3.1 Pro | Both stated findings disproven, but the same investigation surfaced an unreported real bug (`CommitGate` G6 blocking a corrected write) — fixed |
| (Nemotron, V4-era) | Nemotron-3-Ultra | Superseded/stale before it finished; not counted as a fourth round |

**Standing lesson, four rounds in: "all tests pass" has never meant zero bugs, only zero known ones — a review's self-report is a lead, never a fact.**

### 2.5 This session's three tasks — none in the original plan, all found by re-auditing the codebase against the original design docs

1. **C6, completed** — the original plan's "small, closes a silent failure" scope (priority #5) turned out to be only partially realized: V2's absence entries only covered fact keys the Planner *explicitly declared*, and no live prompt ever taught a model that mechanism existed. Extended to schema-complete: every optional tool-schema parameter a plan step doesn't bind is now tracked automatically, closing the L2 "omission escape" bug class the original analysis named C6 for. 8 new tests.
2. **A full CS-01–CS-34 safety-check audit** (the full 34-check `TraceChecker` V4 originally scoped, still only ~30% covered) — 17 checks confirmed sound, 2 real-but-out-of-scope gaps named (CS-06 overlay validation, CS-25's speak-hedging half), and **one real bug found and fixed: CS-28**, the "floor rule" from `docs/prompt 2.txt` — nothing checked floor state before SPEAK/CLARIFY/FINAL, so the agent could talk over the user mid-utterance. Reproduced with two ordinary scenarios, fixed, 6 new tests.
3. **T-04, a reusable metrics module** (`observability/metrics.py`) — the original plan explicitly *cut* "production-grade observability" as too expensive; what §10.3 actually asked for is smaller (a deterministic offline aggregator over the existing trace, not live telemetry) and had never been built. Implemented `ttfs`, `tool_latency`, `interruption_to_cancellation_latency`, `end_to_end_latency`; cross-validated against an already-published number in `docs/measurements.md` (300,000µs/0µs) via independent reproduction. 24 new tests.

4. **Architecture re-anchoring (M4 + C1)** — built the bounded adversarial explorer the original thesis's M4 layer calls for. Its first run (126 of 471 schedules failing) found three real bugs: the spec's TRIAGE hold was never built (a FINAL for a value the user had just corrected, or a write for a corrected value, could go out after the correction's end-of-turn but before it was interpreted); a rapid follow-up turn silently erased the first request; a speculative correction could be dropped and never interpreted. All fixed with regression tests; afterwards 9,142 schedules run clean, while disabling only the hold brings back 704 failures. Also built C1 inert-tail promotion and the CS-30 check it enables. 39 new tests.

### 2.6 What's still genuinely absent (honest, not silently skipped)

**Cut, and still cut** (matches the original plan's own D-classification — nothing here has come back):
- The *full* PCT/mutation-engine form of adversarial exploration (T-03). A bounded, deterministic explorer + 1-minimal shrinker now exists (`sim/explorer.py`, `sim/minimize.py`) and has already found real bugs; the heavyweight engine remains cut as originally planned
- Multiple `ClockPort` models (only Model B was ever built, as originally planned)
- Production-grade observability — Prometheus, live telemetry spans (T-04's `observability/metrics.py` is a different thing: offline trace aggregation, not live export)
- A YAML scenario DSL (tests stayed Python-native)

**Deferred and still open**, beyond what §2.3/2.5 already covers:
- Kit wire-format integration — genuinely blocked, the evaluation kit is still unreleased
- ~24 of the 34 blueprint `TraceChecker` invariant checks (10 have real coverage: 9 offline + CS-10 enforced live)
- 5 of the original 14 scenario-coverage gaps (R-01, R-09, S-09, P-05, T-03)
- PROGRESS/HOLD utterance kinds, `RETURN_TO_GOAL` result reuse, a few small V3 perception simplifications (single question/conflict tracking, flat leases instead of frame-count bounds)
- Phase 8 (quality-multiplier polish: no raw schema names in speech, phrasing variation, hedged MEDIUM claims) and Phase 0 (deck, demo video, team name) — not started, the latter needs the user

### 2.7 Verification status

**229/229 tests passing** (from 60 across V0–V4 alone, growing with every phase and bugfix round since); `TraceChecker` reports 0 violations on every test including the static no-wall-clock scan; replay identity (byte-identical decision logs across repeated runs) is checked on every version's own test file; Docker-verified on real Python 3.11 after every session's changes (dev environment here is Python 3.14, so this is the only real cross-version compatibility check that's actually been run). No final submission tag (`PRISM_GENAI_HACKATHON_Y2026`) has been applied — that's a deliberate, user-confirmed action, not yet taken.
