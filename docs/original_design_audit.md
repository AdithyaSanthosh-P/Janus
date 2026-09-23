# Janus: current-state audit and plan to finish the original design

**Date:** 2026-09-23. **Type:** audit and plan only. No code was changed while producing it.

**How it was checked.** The code was compared against `docs/prompt 3.txt` (C1–C17), the blueprint's Definition of Done (`docs/theme05_implementation_blueprint.md` §14, with §10–§13), and the other original documents. The suite passes on the working tree it was audited on: **246 passed** (run during the audit). Where this report says "reproduced", a script was run against the real kernel or the real `entry.py`; the scripts are in Appendix A. Everything else comes from reading the code.

---

## Headline: 4 real defects the status notes didn't list (3 reproduced, 1 by reading)

| # | Defect | How it is known | Default config? |
|---|---|---|---|
| **D1** | **The production driver never releases a settled write.** In `entry.py` the clock only moves when the harness sends an event, and nothing reads `next_wake_us`. With `settle_ms=300`, "Book flight AI-1" produced "On it — book flight now." and then no booking at all. After 3 s the watchdog salvaged it with `task_completed=False`. With `settle_ms=0` the booking goes out. This is exactly L4; C9(c) timer liveness doesn't exist in production. | Reproduced against `Runtime.run_scenario` (A.1) | Yes |
| **D2** | **C5 transitive invalidation does nothing on live plans.** Derived facts only come from `output_map`. The live Planner schema lists `output_map` as a bare `{"type":"object"}` and never explains it. A `step_output` binding without it reads `result.<call_id>`, which is `COMMITTED`, has no provenance, and is never retracted. The I-04 chain was run twice. With `output_map`, the old seat map is cancelled and re-run for BOM-2. Without it, `get_seat_map(PNQ-1)` is consumed and never re-run, and the FINAL goes out with a Mumbai snapshot grounded in a Pune seat map. Neither the TraceChecker nor the explorer oracles flag it. | Reproduced (A.2) | Yes |
| **D3** | **A failed write produces a false completion claim, then hangs.** On a non-retryable booking error the effect becomes `UNKNOWN`, but PlanExecutor retries anyway. G5 blocks the retry, and `_inform_blocked_writes` answers a G5 block by saying **"That's already been taken care of."** The retry then sits `PROPOSED` forever and the goal stays `ACTIVE/EXECUTING`. The blueprint's S-01 says no retry and report `effect_failed`. | Reproduced (A.3) | Yes |
| **D4** | **No call deadlines exist** (blueprint §5.9). If a tool result is dropped, the call stays `IN_FLIGHT` forever and only the 105 s watchdog ends it. The existing `test_s02_write_timeout_informs_truthfully` only asserts the effect stays `PENDING`; it never checks that anything is said to the user. | Read the code | Yes |

**About the shipping config:** `setup()` uses `DEFAULT_CONFIG`, and in it `vision_enabled`, `asr_enabled`, `speculative_interpretation_enabled` (which gates C1 too), `frame_rendering_enabled` (C2), `commit_last_ordering` (C9a) and `reference_bound_identifiers` (C10) are **all False**. The Theme guide says 50% of kit scenarios are audio or visual. As shipped today, the production agent would store audio clips and video frames but never transcribe or analyse them.

**Corrections to the hypotheses the audit started from:**
- It's 246/246 now, not 166.
- C1 and C2 **are** built, but both are partial and both are off by default. Only C3 is fully missing.
- C9 is not "substantially implemented" in production, because D1 means its liveness part is missing there.
- The rest held up: CS-06 is unbuilt, CS-25 is half done, CS-29 and CS-31 are off by default, and C17 is bounded.

---

## Step 1: What exists

| Component | Files | Status | Tests | Main limitations |
|---|---|---|---|---|
| Single-writer step engine (8 phases) | `kernel/step.py`, `store/session.py` (MutationGuard) | Complete | all tests; CS-01/02 by reading | — |
| Versioned fact store, read sets, dependency index | `store/facts.py` | Complete | whole suite | "Unretraction" only happens implicitly: re-`set` with an equal digest |
| Invalidation, transitive fixpoint | `kernel/invalidation.py` | Partial | I-04 x2 (`test_v2`) | Only works through `output_map` derived facts (D2); stops silently after 8 rounds |
| Plan executor / binder | `kernel/executor.py` | Mostly complete | V1/V2/V4/C6/phase 5 tests | STEP_OUTPUT has no provenance (D2); retry ignores `retryable` (D3); no deadlines (D4) |
| CommitGate G1–G11 plus the TRIAGE clause | `kernel/commit.py` | Complete as gate logic | S-01/03/04, I-14, triage tests | G10 depends on time moving forward (D1); no lease check when a call is admitted |
| Result router (7 branches) | `kernel/results.py` | Complete | R-02/03/04/05/08 | Non-retryable → `UNKNOWN` + retry (D3); result facts carry no provenance |
| EmissionGate | `kernel/emission.py` | Partial | whole suite | One action at a time, not two-phase; no overlay; every speech action has an empty read set, so CS-04 only really applies to tool calls |
| FastResponder | `kernel/responder.py`, `config/templates.py` | Partial | V1/V2, CS-28, triage | Templates aren't keyed by grade; false INFORM (D3); leaks raw parameter names ("flight_id"); no PROGRESS/HOLD; never checks Composer text |
| Turns, speculation, C1 | `kernel/turns.py`, `kernel/detector.py`, `config/lexicons.py` | Partial | phase 4/6, C1 (28) | Off by default; see C1 below |
| Task machine, triage (stateless) | `kernel/task.py`, `kernel/interpret_apply.py` | Complete for its scope | triage (11), regression tests | I-06 unsupported |
| Response frames (C2) | `kernel/frames.py`, `workers/frame.py` | Partial | phase 5 (7) | Off by default; see C2 below |
| Perception / leases (C7, C13) | `kernel/perception.py`, `store/ledgers.py` EvidenceStore | Partial | V3, phase 7, multimodal races | Off by default; see C7 below |
| ASR | `kernel/audio.py`, `workers/asr.py` | Built | audio (5), races | Off by default |
| Clock | `adapters/clock.py` | **Model B stub only** | — | No Model A, no `request_advance`, no liveness fire |
| Real async driver | `entry.py` | Built, has a liveness bug | integration (13) | D1; worker results are stamped with frozen time, so latency on this path can't be measured |
| Simulator | `sim/harness.py` | Built | everything | Time runs freely while workers compute (Model A-like). **Production runs a frozen clock.** Tests therefore exercise a different time model than the product runs. |
| Trace checker | `sim/checker.py` | 11 offline CS checks, P1, P-04, static wall-clock scan, replay | fault-injection tests | No overlay, stale-consumption or omission checks; no import-rule or mutable-globals checks |
| Explorer / minimizer (C17) | `sim/explorer.py`, `sim/minimize.py`, `tests/explore_scenarios*.py` | Bounded | triage, multimodal races | See C17 below |
| Metrics | `observability/metrics.py` | Partial | 24 tests | No event-anchored ratio, state-update timing, evidence-anchored cancel latency, or §6.2 counters |
| Providers | `workers/gateway.py` | Built | live-reliability (7) | No record/replay cassette, so live runs can't be replayed |

Package boundaries hold. `kernel`/`store`/`model` never import `workers`, and `workers` never imports `store`/`kernel`. This was checked by grep; no automated test enforces it.

---

## Step 2: Each original mechanism

### C1: Inert-tail promotion (PARTIAL)
1. **Original requirement:** at end of turn, compute the tail after the last completed speculative interpretation. Promote if the tail has no value, cue or negation and only inert words. Otherwise run a shorter re-interpretation conditioned on the tail. A commit-bearing turn needs two consecutive prefix interpretations with identical act, intent and slots. Measure the promotion disagreement rate.
2. **What the code does:** `_inert_tail` and `is_inert_tail` implement the lexical, cue, value and clarifying guards. It only promotes from a *completed* cached result. CS-30 is checked offline.
3. **Still missing:**
   - The tail-conditioned re-interpretation; today it falls back to a full re-interpretation.
   - Handling of an in-flight pre-tail job.
   - The two-consecutive-identical guard. The code refuses commit-bearing turns outright, which is stricter and safe, but not the spec.
   - The offline promotion-disagreement check.
   - **It's off by default**, because it requires `speculative_interpretation_enabled`.
4. **Existing tests:** `test_c1_inert_tail.py` (28), `test_phase6.py`, M-02.
5. **Missing tests:** promotion disagreement against the full interpretation; T-06 under Model A; the tail-conditioned path; the in-flight job case.
6. **Depends on:** Phase 6 speculation; Model A simulator for the latency claim.
7. **Files:** `kernel/turns.py`, `kernel/task.py`, `workers/interpreter.py`, `sim/checker.py`, `config.py`.
8. **Risks:** a promoted wrong interpretation ends up driving a write; mitigated by keeping the commit guard.
9. **Acceptance:** zero promotion disagreements on recorded runs; TTFS(EOT) ≤ 1 ms under Model A for exact and inert-tail promotion; fewer post-EOT INTERPRET jobs.

### C2: Prepared response frames (PARTIAL)
1. **Original requirement:**
   - Dispatch a FRAME job when the last call is emitted, using the fixed operator set.
   - At acceptance, validate the frame against the output schema or the shape of an earlier result from the same tool.
   - Force the error branch on errors ("a failed write cannot be reported as done").
   - Include goal facts and constraints in the frame's read set.
   - Composer stays as the fallback.
   - Report frame coverage.
2. **What the code does:** dispatch, all 6 operators, `success`/`empty` selection, deterministic rendering, read-set drop, and Composer fallback all work. **Off by default.**
3. **Still missing:**
   - Validation at acceptance: holes only fail at render time.
   - The error branch is never selected.
   - CS-32 literal-text validation, so static template text can contain invented entities.
   - No fallback to an earlier result's shape when the tool has no output schema.
   - Perception-claim holes.
   - Frame coverage and result→FINAL metrics.
4. **Existing tests:** `test_phase5.py` (7).
5. **Missing tests:** N-02 frame vs fallback on a chain; a literal-text violation; the error branch on a failed write; frame coverage.
6. **Depends on:** C8 (grades per branch), P0.2 (the failure path), C7 (holes over claims).
7. **Files:** `kernel/frames.py`, `kernel/proposals.py`, `kernel/executor.py` (`_fail_goal`), `sim/checker.py`, `observability/metrics.py`.
8. **Risks:** the literal-text check could reject natural wording, which pushes more responses to the Composer fallback. That costs latency, not correctness.
9. **Acceptance:** every entity or number in a frame-rendered FINAL is a hole value (CS-32 check); a failed write always renders the error branch or honest-failure text; coverage is reported.

### C3: Event-anchored reaction scheduling, with C13 and C16 (NOT IMPLEMENTED; C13 PARTIAL, C16 NOT IMPLEMENTED)
1. **Original requirement:**
   - A JobScheduler reaction-slot table, one slot per anticipated event: end of an open turn, the result of each in-flight call, the answer to a pending clarification, an open visual question, and the ACK phrase.
   - Slots filled in order of imminence and criticality (C16's three priority classes).
   - The event step only validates and selects.
   - Measured by the event-anchored ratio.
2. **What the code does:**
   - Two slot-like mechanisms exist independently: speculative interpretation and FRAME dispatch.
   - C13 fires only when an interpretation is accepted, not before end of turn.
   - Neither runner caps concurrency, so C16 is moot today.
   - `post_v4_implementation_plan.md` calls C3 "deliberately not built" under Model B. That is a later scope decision, not the original design.
3. **Still missing:** everything in the requirement above.
4. **Existing tests:** none.
5. **Missing tests:** slot invalidation and refill after triage; event-anchored ratio; per-trigger latency and variance over 20 Model A runs; speculative waste.
6. **Depends on:** Model A simulator; C1, C2, C7/C13; C8 for ACK grades.
7. **Files:** new `kernel/scheduler.py`; `kernel/task.py`, `kernel/frames.py`, `kernel/perception.py`, `workers/runner.py` (the concurrency cap), `observability/metrics.py`.
8. **Risks:** more speculative calls hitting live rate limits; waste. Neither can be measured until a Model A clock exists.
9. **Acceptance:** event-anchored ratio reported and higher than with C3 off under Model A; no prepared reaction takes effect with an invalid read set; waste bounded to one job per slot.

### C5: Provenance-closed invalidation and early cutoff (PARTIAL)
1. **Original requirement:** every derived fact stores the read set of the call that produced it, and "ResultRouter records provenance". Retract to a fixpoint in the same step. Early cutoff: when re-derivation produces an equal digest, completed downstream results become valid again. Metrics: transitive cancel delay, stale consumptions, re-executed steps.
2. **What the code does:**
   - The fixpoint works, but only for `output_map` derived facts.
   - Early cutoff happens *implicitly*: re-setting a retracted fact with an equal digest makes old read sets valid again. It's untested and unmeasured.
3. **Still missing:**
   - Provenance on step outputs (D2), which makes C5 inert on live plans.
   - The 8-round cap should be asserted, not silently cut off.
   - Cutoff tests and all three metrics.
4. **Existing tests:** `test_i04_*` (both use `output_map`).
5. **Missing tests:** D2 regression; cutoff when the search returns the same flight; reversion reuse; I-04 offset sweep; flag-off vs flag-on explorer comparison.
6. **Depends on:** nothing; everything else leans on it.
7. **Files:** `kernel/results.py` or `kernel/reducers.py`, `kernel/executor.py._bind`, `kernel/invalidation.py`, `workers/planner.py`, metrics, explorer.
8. **Risks:**
   - Re-deriving with an equal digest onto a fact that *isn't* retracted is a no-op, so its old provenance would be kept (the D4 no-op rule in `FactStore.set`). This already bit `set_grounded_compose_text`; retract before re-setting.
   - Many read sets change shape.
9. **Acceptance:** the D2 repro cancels `get_seat_map(PNQ-1)` in the correction step and the FINAL is grounded in BOM-2; zero stale consumptions under exploration; a cutoff re-uses work.

### C6: Schema-complete read sets (PARTIAL, nearly done)
1. **Original requirement:** every tool parameter, bound or not, is in the call's read set. Goal-level constraints that map to no parameter go into the read sets of frames and of selection steps. Policy `absence_sensitivity`: only user-sourced facts break an absence entry. Metric: omission escapes.
2. **What the code does:** `_schema_complete_absence_keys` covers every unbound optional parameter. Frames and Composer inherit the call's read set.
3. **Still missing:**
   - Goal-level constraints that aren't parameters. A constraint added after a frame or Composer job was dispatched doesn't invalidate it.
   - `absence_sensitivity` is approximated by excluding the current question's targets rather than filtering by source.
   - The omission-escape metric and oracle.
4. **Existing tests:** `test_c6_*` (8), I-12.
5. **Missing tests:** "only morning flights" with no matching parameter, added mid-frame; a perception-sourced optional parameter doesn't invalidate completed work (source-based); omission-escape count = 0 under exploration.
6. **Depends on:** C5 (P0.3) for selection-step read sets.
7. **Files:** `kernel/frames.py`, `kernel/task.py._build_compose_request`, `kernel/executor.py`, `model/types.py` (ReadSetEntry sources), `store/facts.py.is_valid` (absence sensitivity), explorer.
8. **Risks:** changing `is_valid` for absence entries is global; gate it on a per-entry field only.
9. **Acceptance:** zero omission escapes; a late constraint drops the frame or Composer text.

### C7: Evidence leases (PARTIAL, simplified)
1. **Original requirement:**
   - Leases are per mode. AT_UTTERANCE claims never expire. CURRENT_STATE claims expire after `lease_frames` newer frames **or** `lease_ms` **since capture**.
   - Leases are checked **only at points of use**: the gate, emission of a statement that relies on the claim, and binding.
   - Renewal: one job on the newest frame. Agreement renews the lease and raises confidence one band; HIGH disagreement cascades; MEDIUM disagreement opens a conflict.
   - Reads may proceed on an expired lease with hedged wording; writes and unhedged statements may not.
   - No watch mode.
2. **What the code does:**
   - No watch mode, and AT_UTTERANCE never expires; both correct.
   - Expiry is a flat timeout from **analysis time**. It is checked **eagerly every step** and retracts the claim, which cascades.
   - M-05 adds a frame-gap check, but only when a write call is first created.
3. **Still missing:**
   - Lease measured from capture time.
   - The `lease_frames` bound, the only one that works under frozen Model B time.
   - Point-of-use semantics: eager retraction cancels reads the spec allows to proceed.
   - Agreement raising confidence; MEDIUM disagreement opening a conflict.
   - Hedged wording (CS-25's speech half).
   - A lease check when the gate admits a write.
   - The stale-evidence-grounding metric.
4. **Existing tests:** M-03, M-05, M-06, M-09, the multimodal races.
5. **Missing tests:** M-03 agree and disagree variants; frame-count expiry with time frozen; MEDIUM hedge; stale-evidence groundings = 0.
6. **Depends on:** C8 (hedge grade), C5 (cascade), the Model B simulator mode.
7. **Files:** `kernel/perception.py`, `kernel/commit.py`, `kernel/responder.py`, `config/templates.py`, `model/types.py` (lease on claim), `config.py`.
8. **Risks:** the multimodal race fixes depend on today's retraction path, so re-run all 17 multimodal-race tests after changing it.
9. **Acceptance:** no write and no unhedged statement relies on an expired CURRENT_STATE lease (CS-25, online); reads hedge.

### C8: Claim-typed atomic emission (PARTIAL; part (a) missing)
1. **Original requirement:**
   - (a) Two-phase emission: validate the whole batch against the ledger state after the batch; claims may reference same-batch actions; re-render at a lower grade or drop.
   - (b) The full grade ladder, including IN_PROGRESS, EFFECT_FAILED and EFFECT_UNKNOWN. Templates keyed by grade. Lexicon checks for free-form model text.
2. **What the code does:**
   - Grades UNDERSTOOD, INTENDED, RESULT and EFFECT_DONE are set on ACK, INTENDED and FINAL.
   - IN_PROGRESS is defined but never emitted; EFFECT_FAILED and EFFECT_UNKNOWN don't exist.
   - Two-phase emission is documented as a "structural no-op", which is true only because no speech ever references a same-batch call.
3. **Still missing:**
   - Overlay validation (CS-06).
   - Grade-keyed templates.
   - Any check on Composer text. `composer.py`'s docstring claims a truth-check fallback in `responder.py`, but none exists.
   - The failure grades.
   - D3 is a live instance of the false claim this mechanism exists to prevent.
4. **Existing tests:** S-10.
5. **Missing tests:** P-05; injected gate rejections (mutation); false claims = 0 under exploration.
6. **Depends on:** nothing; C7, C9(d) and C2 depend on it.
7. **Files:** `model/types.py`, `model/actions.py`, `kernel/emission.py`, `kernel/responder.py`, `config/templates.py`, `workers/composer.py`, `kernel/proposals.py`, `sim/checker.py`.
8. **Risks:** re-rendering could change action order; keep CANCEL first.
9. **Acceptance:** every emitted claim is supported by the ledger after the batch (online monitor); zero false claims under mutation.

### C9: Commit-last, settle barrier and timer liveness (PARTIAL)
1. **Original requirement:** (a) commit-last, with consecutive writes forming one commit window; (b) settle σ plus leases plus no pending interpretation; (c) timer liveness, with a `LIVENESS_FIRE` so σ degrades to 0 instead of deadlocking; (d) INTENDED speech for waiting writes.
2. **What the code does:**
   - (a) is built but off by default, and has no commit window.
   - (b) works: G10/G11 and the TRIAGE clause.
   - (c) exists only as a hint that the simulator honours; **the production driver ignores it (D1)**.
   - (d) works, but the wording "On it — book flight now." reads as in-progress.
3. **Still missing:** (c) in production; a Model B simulator mode with no idle advance; the commit window; a lease check at admission; DoD 9's settle/speech test; the σ × offset sweep.
4. **Existing tests:** I-14 (with its sweep), N-02b, T-05. T-05 assumes a driver that honours `next_wake_us`.
5. **Missing tests:** the real async entry point releasing a write after settle; Model B with no advance → `liveness_fires ≥ 1`; speech latency identical for settle 0 and 300; the −500…+1000 ms × σ∈{0,150,300,600} sweep.
6. **Depends on:** P0.1; C7 for leases.
7. **Files:** `adapters/clock.py`, `entry.py`, `sim/harness.py`, `store/ledgers.py` (TimerWheel), `kernel/commit.py`, `kernel/reducers.py`, `kernel/responder.py`.
8. **Risks:** a liveness fire landing while a correction is in flight. The TRIAGE clause of G3 still blocks that; keep it ahead of G10.
9. **Acceptance:** the D1 repro emits `book_flight`; I-14 still never emits the 6 pm booking; speech latency unchanged by σ.

### C10: Reference-bound write arguments (PARTIAL; off by default)
1. **Original requirement:**
   - Identifier-like parameters are recognised by enum, format, name pattern, **or by having appeared in consumed results**.
   - Such an argument must bind to a consumed result field or to a literal **the user stated verbatim**.
   - Fingerprints use the bound values.
   - A value read from a frame qualifies only as a HIGH claim with a valid lease.
2. **What the code does:** the first three recognition criteria; accepts `step_output`, tool-sourced and user-sourced facts; checks literals by **substring**.
3. **Still missing:**
   - The fourth recognition criterion.
   - "User-sourced" slot values are really Interpreter (model) output, and they're accepted without any verbatim check.
   - Substring matching lets "AI" match any sentence containing "AI".
   - Perception identifiers are always refused. That's stricter than the spec; fine, but it should be documented.
   - Default off.
4. **Existing tests:** `test_c10_*` (3), S-04.
5. **Missing tests:** an identifier hallucinated by the Interpreter is refused; a substring false-accept; a fourth-criterion parameter; the clarify path through to resolution.
6. **Depends on:** C5 (a retracted source blocks the write).
7. **Files:** `kernel/executor.py`, `config.py`, `workers/planner.py`.
8. **Risks:** turning it on could stall live plans that write literals; test on the live demo first.
9. **Acceptance:** every identifier argument of a write is referenced (CS-29 offline check); on by default with the full suite green.

### C17: Adversarial exploration (PARTIAL, bounded)
1. **Original requirement:**
   - A simulator with pluggable Model A and Model B semantics.
   - Sweeps, PCT reordering of ties (d=2), and scenario mutation (insert/move/duplicate a correction, drop or duplicate a result, move EOT, add a backchannel).
   - ddmin over events and choice points, with failures saved as regression tests.
   - Online monitors in EmissionGate.
   - Budget: 500 PCT + 200 mutation schedules per family.
2. **What the code does:**
   - A seeded sampler over gaps, per-tool and per-job-kind latencies, plus targeted "result next to user event" schedules.
   - 11 oracles; 1-minimal shrinking over those knobs.
   - 19 scenarios; 17,336 schedules clean; 14 safeguards shown to matter by mutation.
3. **Still missing:**
   - Clock modes, PCT, and scenario mutation.
   - Per-call latency choices.
   - Event-level ddmin and saved regressions.
   - Online monitors.
   - Oracles for stale consumption, omission escape, false claims and stale evidence.
   - **It never explores configs with C2, C9a or C10 turned on.** Its oracles missed D2 and D3, so "17,336 clean" overstates what's covered.
4. **Existing tests:** `test_triage_hold.py`, `test_multimodal_races.py`, `tests/mutations.py`.
5. **Missing tests:** T-03 at full budget; mutation families; the D2 and D3 shapes as scenarios.
6. **Depends on:** P0.1 (clock semantics); the C5/C6/C8 oracles.
7. **Files:** `sim/explorer.py`, `sim/minimize.py`, `sim/harness.py`, `kernel/emission.py` (monitors), `tests/explore_scenarios*.py`.
8. **Risks:** runtime cost; put the full budget in a separate nightly script, not the per-commit suite.
9. **Acceptance:** zero violations over 500 + 200 schedules per family under both clock modes, with every counterexample kept as a test.

---

## Step 3: The blueprint, phase by phase (§13)

| Phase | Verdict | Notes |
|---|---|---|
| 0 Tooling | Partial | No Makefile, `.importlinter`, or static import / mutable-global tests. Flat `Config` instead of YAML policies is a legitimate change. |
| 1 Models / canonical form / IDs | Complete | Dataclasses instead of pydantic is a legitimate change. |
| 2 Store | Complete | S-06 quarantine is covered. |
| 3 Event engine / clock / timers / simulator | **Partial** | Only a Model B-style `SteppedClock`. No Model A, no `request_advance`, no liveness fire. Simulator semantics ≠ production (D1). YAML scenario DSL cut in favour of Python builders; legitimate. |
| 4 Tool engine / emission | Partial | Binder inlined into the executor (legitimate). Missing: `retry.py` deadlines (D4), `monitors.py`, two-phase emission. |
| 5 Conversation / fast path | Mostly complete | No PROGRESS/HOLD utterances, no re-prompt, no I-06. |
| 6 Complete validity / settled commits | **Partial** | D1, D2; cutoff and retained-result reuse untested or missing (I-09 reuse); claim grades partial. |
| 7 Model-backed workers | Partial | Live Gemini/Anthropic work. No cassette record/replay, so live runs can't be replayed. |
| 8 Prepared reactions | Partial | Frames and promotion built but off by default. No reaction slots, no speculative planning. |
| 9 Multimodal | Substantially built | Off by default. Leases simplified. Cue- and plan-triggered questions missing. |
| 10 Exploration / metrics | Partial | Bounded explorer; metrics subset; no report tooling. |
| 11 Kit integration | Blocked | The tolerant codec is the right stopgap. |

---

## Step 4: Definition-of-Done matrix

| # | Requirement | Status | Evidence | Remaining |
|---|---|---|---|---|
| 1 | All §12.3/§12.4 tests pass under Model A and B (advance on and off), strict monitors on | **Not met** | 246 pass, in one simulator mode only | Clock modes, strict monitors, and the missing scenarios (I-06, R-01, R-09, S-02 proper, S-09, P-02 fuzz, P-05) |
| 2 | T-02 sweeps + T-03 (500 PCT + 200 mutation) with zero violations | Partial | Sweeps for I-03/I-14/R-05 and C1; bounded explorer | PCT, mutation, I-04/I-05/M-03 sweeps, both clocks |
| 3 | Public suite in the kit harness | Blocked | — | Kit not released |
| 4 | Replay identity everywhere | Met in the simulator | `check_replay` in several files; explorer determinism | Real driver unchecked (wall-clock watchdog) |
| 5a | Zero duplicate write fingerprints | Met in tests | CS-13 checker; explorer double-effect oracle | Re-verify with C10 on |
| 5b | Zero schema-invalid actions | Partial | Strict `encode()`, P-04 check | No schema validation of emitted actions |
| 5c | Zero false claims | **Fails** | D3 reproduced | P0.2 + C8 |
| 5d | Zero stale consumptions | **Fails** | D2 reproduced | P0.3 + oracle |
| 5e | Zero omission escapes | Unmeasured | C6 tests | Oracle + goal-level constraints |
| 5f | Zero stale-evidence write groundings | Partial | M-05 | Lease at admission; metric |
| 6 | Static checks: imports, wall clock, mutable globals, no fixture tool names | Partial | Wall-clock scan only; boundaries hold by grep | Write the other three checks |
| 7 | Model A: TTFS(EOT) ≤ 1 ms; cancel p95 targets | Not measurable | No Model A clock | Clock + C1/C3 |
| 8 | Model B: evidence-anchored cancel = 0 for I-03/04/07/12 | Unmeasured | Only signal-anchored cancel latency exists | Per-call cause instrumentation |
| 9 | Speech latency identical at settle 0 and 300 | Untested | — | One test |
| 10 | Setup < 300 s; each scenario < 120 s | Partial | Watchdog at 105 s | D1 makes every settled write hit the watchdog |
| 11 | Docker from a clean checkout; reproducible README | Partial | Dockerfile runs the tests | README says 149 tests; no agent/kit run command |
| 12 | §6.3 ablation table | **Not met** | Only a flag-by-flag table | P12 |
| 13 | Final tag with all referenced docs | Not done | — | Needs user confirmation; deck and video missing |

---

## Step 5: Work packages, in dependency order

**Before any of these:** at audit time the working tree held 38 changed or untracked files (M4, C1, the multimodal fixes), all newer than tag `v10-3`. Committing and tagging them is the user's call; doing it first gives a fallback that includes them.

### P0.1: Production clock and timer liveness (C9c). Size M. Needed for DoD.
- *Objective:* the real driver advances or liveness-fires timers, so σ never deadlocks.
- *Files:* `adapters/clock.py`, `entry.py`, `store/ledgers.py` (TimerWheel), `model/events.py` (timer envelope), `kernel/reducers.py`, `kernel/commit.py` (G10 honours a liveness fire), `sim/harness.py` (a mode where the harness doesn't advance idle time), `observability/metrics.py`.
- *Prerequisites:* none.
- *Steps:*
  1. Give `ClockPort` a `model`.
  2. Model B: when idle (no queued events, no running jobs, a timer due), fire the earliest timer as `liveness_fire`, logged as `TIMER.LIVENESS`, and let G10 treat it as elapsed.
  3. Model A: a `CoupledClock` using harness time plus wall offset, with `wall_elapsed` allowed only in the adapter.
  4. Stamp worker results with correct time.
  5. Add the matching simulator mode.
- *Tests:* turn repro A.1 into a pytest-asyncio test on `run_scenario`; T-05 with no idle advance (`liveness_fires ≥ 1`, write proceeds); I-14 unchanged; replay identity in the simulator.
- *Invariants:* CS-20, CS-21, CS-15 (the liveness exception is logged), CS-22.
- *Acceptance:* the D1 script emits `book_flight` before the watchdog; all 246 tests still pass.
- *Demo:* run repro A.1 before and after.
- *Could break:* a liveness fire while the correction's INTERPRET is running. That's safe only because the TRIAGE clause blocks first, so test that ordering explicitly.

### P0.2: Honest write failure (S-01/S-02 wording, D3). Size S. Needed.
- *Files:* `kernel/results.py` (record `retryable` on the call; non-retryable → effect FAILED per S-01), `kernel/executor.py` (never retry a non-retryable write; route to `_fail_goal`), `kernel/responder.py` (the "already done" message only when the blocking effect is CONFIRMED; UNKNOWN → an uncertainty message), `config/templates.py`.
- *Prerequisites:* none.
- *Tests:* S-01 with `retryable` false and absent; an explorer oracle: "an already-done message was spoken with no CONFIRMED effect".
- *Invariants:* CS-06 (spirit), W4, `task_completed` truthfulness.
- *Acceptance:* repro A.3 produces honest failure text, `task_completed=False`, and no stuck `PROPOSED` call.
- *Demo:* run repro A.3 before and after.
- *Could break:* `test_s01_write_retryable_error_retries_once` must stay green.

### P0.3: Provenance on step outputs (C5 core, D2). Size S–M. Needed.
- *Files:* `kernel/reducers.py._apply_tool_result`, `kernel/executor.py._bind`, `workers/planner.py`.
- *Steps:*
  1. On consumption, write a stable `stepout.<gid>.<step_key>` fact (`DERIVED`, `derivation_read_set=call.read_set`).
  2. STEP_OUTPUT bindings read it via the path and add its key to their read set.
  3. Treat a RETRACTED result as missing.
  4. When re-deriving from a new call, retract first so the new provenance is stored.
  5. Keep the `output_map` path working.
- *Tests:* repro A.2 as a regression; a cutoff case (same flight → seat map reused); the explorer chain scenario without `output_map`.
- *Invariants:* CS-07, CS-08, P3, CS-04.
- *Acceptance:* the stale seat map is cancelled in the correction step and FINAL is grounded in BOM-2.
- *Demo:* run repro A.2 before and after.
- *Could break:* COMPOSE/FRAME read-set shapes, and the correction-race regression; re-run all of them.

### P0.4: Call deadlines and the retry table (§5.9, D4). Size M. Needed.
- *Files:* `kernel/executor.py` (or a new `kernel/retry.py`), `store/ledgers.py`, `kernel/results.py` (superseded original), `kernel/responder.py`, `config.py`.
- *Prerequisites:* P0.1 (deadlines are timers).
- *Tests:*
  - S-02 done properly: a dropped booking result leads to effect UNKNOWN, no re-issue, INFORM + CLARIFY, then "yes try again" gives exactly one retry.
  - R-09.
  - A dropped read gets retried.
- *Acceptance:* no hang ends only at the watchdog when a result is dropped.
- *Could break:* R-04's slow deliver-anyway result. Deadlines must not count cancelled calls.

### P1: Verification base (C17 part 1). Size M. Needed.
- Simulator modes: Model A, Model B with idle advance, Model B without.
- Oracles, both offline and in the explorer: stale consumption (provenance closure at consumption), omission escape, false claim, stale-evidence write grounding.
- `StepReport` instrumentation: gate rejections (unblocks CS-15) and the per-call invalidation cause (unblocks evidence-anchored latency).
- Online monitor hook in EmissionGate behind `strict_monitors`.
- Explore configs with C2, C9a and C10 on.
- *Acceptance:* the new oracles fire when P0.2 or P0.3 is reverted (mutation proof).

### P2: Finish C8. Size M. Needed.
- Full grade ladder; claims referencing calls and effects; two-phase overlay validation in `emission.py` (cancellations first, same-batch references allowed, re-render or drop); grade-keyed templates (INTENDED "I'll book…", IN_PROGRESS "Booking…", EFFECT_DONE "Booked"); a lexicon check on Composer text (completion verbs need EFFECT_DONE); CS-06 online and offline.
- *Tests:* P-05, gate-rejection mutation, false claims = 0 under exploration.

### P3: Finish C5. Size S–M. Needed.
- Explicit cutoff tests; assert the fixpoint bound; metrics for transitive cancel delay, stale consumptions and re-executed steps.
- I-04/I-05 offset sweeps.
- Explorer flag-off vs flag-on comparison showing L1 found off and absent on.

### P4: Finish C6. Size S–M. Needed.
- Goal constraint set (a `goal.<gid>.slot_names` fact) in frame and Composer read sets.
- Source-filtered absence entries for `absence_sensitivity`.
- Omission-escape metric.

### P5: Finish C9 and C10. Size M. Needed.
- C9: commit window; lease check at admission; the DoD 9 test; the σ × offset sweep.
- C10: fourth recognition criterion; verbatim token match for literals **and** for user-sourced identifier facts; tests.
- Then decide which of the two defaults to flip on.

### P6: Harden C7. Size M. Needed. Prerequisites: P2, P3.
- Lease measured from capture time; `lease_frames`; point-of-use checks (gate, relying emission, binding) replacing eager retraction; renewal agreement or disagreement handling; hedged MEDIUM wording (CS-25 in full); stale-evidence metric.
- M-03 agree/disagree variants.
- Re-run all multimodal-race tests.

### P7: Finish C2. Size M. Needed. Prerequisites: P2, P0.2.
- Validation at acceptance against the output schema or an earlier result's shape; error branch wired to honest failure; CS-32 literal-text check; coverage and result→FINAL metrics; N-02 frame vs fallback.

### P8: Finish C1. Size S–M. Needed. Prerequisite: the Model A simulator from P1.
- Tail-conditioned re-interpretation; the in-flight pre-tail case; the spec's two-identical commit guard; promotion-disagreement check; T-06 under Model A.

### P9: C3 with C13 and C16. Size M–L. Needed (DoD 12 lists "C3 disabled" as a baseline). Prerequisites: P1, P7, P8, P6.
- A `kernel/scheduler.py` reaction-slot table that subsumes the existing speculative-interpretation and FRAME dispatch.
- Pre-EOT perception slot (C13); clarification-answer slot.
- Three priority classes plus a concurrency cap in `AsyncWorkerRunner` (C16).
- Event-anchored ratio metric; variance over 20 Model A runs.

### P10: Full C17. Size L. Needed.
- Per-call and per-job latencies; PCT (d=2, ties within 50 ms); mutation families; event-level ddmin; saved regressions loaded by a test.
- A nightly script with 500 + 200 schedules per family, run under both clocks.

### P11: Remaining scenarios. Size M–L. Needed.
- I-06, R-01, S-09 (a `modify_booking` tool, or INFORM + CLARIFY), P-02 Hypothesis fuzz, I-09 reuse of retained results, N-03 re-prompt, the M-01 clarify variant, the I-11 signal-anchor variant.
- Static tests: imports, mutable globals, no fixture tool names.

### P12: Measurement and ablation (§6.3). Size M. Needed.
- Baseline vs full system vs leave-one-out, under Model A and B, for every §6.2 metric.
- Decide the shipping config (vision, ASR, C1, C2, C9a, C10), backed by a full green suite **in that config**.

### P13: Kit integration. Size L. Blocked until the kit ships.
- Codec mapping and clock-model selection; public suite; re-run T-02/T-03 with the kit's latencies.

### P14: Final hardening. Size S. Needed.
- README (test count, agent/kit run command); Docker; setup under 300 s; tidy the docs; tag only after user confirmation.

---

## A. Architecture as it stands

```
Harness queues ──► entry.py (async driver) [D1: time frozen between events, no liveness]
                     │ ClockPort: SteppedClock only (no Model A)
                     ▼
            Kernel.step (sync, 8 phases) ── store/ (facts+read sets, dep index, call/effect ledgers,
                     │                                 catalog, evidence, TimerWheel=hint only)
   APPLY reducers ── results router [no provenance on results: D2] · turns/detector (chunk anchor, C1*)
   INVALIDATE ────── same-step cancel ✓ · transitive via output_map only (C5◑)
   DECIDE ────────── TaskSM (spec interp*) · Perception(C7◑,C13◑)* · ASR* · Frames(C2◑)* ·
                     PlanExecutor (C6◑, C9a*, C10*, no deadlines D4, retry bug D3) ·
                     CommitGate G1–G11+TRIAGE ✓ · FastResponder (floor ✓, grades◑, false INFORM D3)
   EMIT ──────────── one action at a time, CANCEL first ✓, no overlay (C8a ✗), snapshot projection ✓
   DISPATCH ──────── workers (Interpreter/Planner/Composer/Frame/Vision/ASR) via runner, no priorities (C16 ✗)
Verification: SimHarness (Model-A-like free time) · TraceChecker (11 CS) · bounded explorer (C17◑)
* = built but OFF in DEFAULT_CONFIG
```

## B. Mechanism matrix

| Mechanism | Status | Missing (short) | Tests | Priority |
|---|---|---|---|---|
| C1 | Partial, off | Tail-conditioned re-interpretation, in-flight job case, commit guard, disagreement check | 28 + phase 6 | P8 |
| C2 | Partial, off | Validation at acceptance, error branch, CS-32, coverage metric | 7 | P7 |
| C3 (+C13◑, C16✗) | Not implemented | Everything | 0 | P9 |
| C5 | Partial | **Step-output provenance (D2)**, explicit cutoff, metrics | 2 | **P0.3**, P3 |
| C6 | Partial (nearly) | Goal-level constraints, source-based sensitivity, omission metric | 8 | P4 |
| C7 | Partial (simplified) | Capture-time lease, `lease_frames`, point-of-use checks, renewal logic, hedging | ~12 | P6 |
| C8 | Partial (a missing) | Overlay, full ladder, grade-keyed templates, Composer text check | 1 | **P0.2**, P2 |
| C9 | Partial | **Production liveness (D1)**, commit window, admission lease, DoD 9 | ~13 | **P0.1**, P5 |
| C10 | Partial, off | Verbatim check for user-sourced values, 4th criterion, token match | 4 | P5 |
| C17 | Partial (bounded) | Clock modes, PCT, mutation, ddmin, regressions, monitors, oracles | 28 | P1, P10 |

## C. Definition-of-Done matrix

See Step 4 above.

## D. Dependency graph

```
P0.1 clock/liveness ──► P0.4 deadlines ──► P11 (S-02, R-09)
        │
        └──► P1 verification base (sim modes, oracles, instrumentation, monitor hook)
                 │
P0.2 honest failure ─┐   ├──► P2 C8 ──┬──► P6 C7 ──┐
P0.3 step provenance ┴──►├──► P3 C5   ├──► P7 C2 ──┤
                          ├──► P4 C6   └──► P5 C9/C10┤
                          └──► P8 C1 (needs Model A sim) ──► P9 C3/C13/C16 ◄┘
                                                              │
                          P10 full C17 ◄── all mechanisms ────┘
                          P12 ablation ◄── P10 ;  P13 kit (blocked) ;  P14 final
```

## E. Execution order

(Commit and tag the current tree, if the user agrees) → **P0.1 → P0.2 → P0.3 → P0.4** → P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8 → P9 → P10 → P11 → P12 → (P13 once the kit ships) → P14.

**On the deadline:** the full list is several weeks of work, and the deadline is 25 Sep 2026. P0.1–P0.3 plus the shipping-config decision plus P14 is realistic by then. Everything after that is "finishing the design", not something that fits before the deadline.

## F. Top 10 risks, all from the code

1. **D1:** the production driver's frozen clock means every settled write reaches only the watchdog. The 246 tests exercise a time model the product doesn't run.
2. **Shipping defaults:** ASR and vision are off in `setup()`, so the 50% of scenarios that are audio or visual get no analysis. C1, C2, C9a and C10 are off too.
3. **D2:** C5 does nothing for live chained plans, so stale downstream results get consumed.
4. **D3:** a false "already taken care of" claim plus a livelock on an ordinary fault the kit explicitly injects.
5. **D4:** a dropped result hangs until 105 s, close to the 120 s cap.
6. Composer's free text is never checked, and grades don't constrain wording (CS-06 missing). The model can claim completion freely.
7. The explorer's oracles missed D2 and D3, and it never explored C2, C9a or C10 turned on. Its 17,336 clean schedules overstate coverage.
8. Deadline pressure, 38 uncommitted files at audit time, and an unread V2 rules PDF plus an AI-disclosure form in `guidelines/mail/`, with no deck or video yet.
9. C7 expires leases by analysis time and never by frame count. Under a frozen Model B clock they never expire; meanwhile eager retraction cancels reads the spec lets proceed.
10. C10, once on, accepts any Interpreter-extracted identifier and matches literals by substring. A fabricated identifier can still pass.

## G. First task

**READY FOR IMPLEMENTATION: P0.1 — Production clock and timer liveness (C9c).**

Why this goes first:
- **It's the largest verified failure on the production path.** On default settings every write that goes through the settle barrier ends in the watchdog, unless the harness happens to send another event.
- **It's what the original design requires.** C9(c) is marked "mandatory" in `prompt 3.txt`, and CS-20 depends on it.
- **Other packages depend on it.** P0.4's deadlines are timers and need the same liveness. The Model A/B simulator modes in P1 extend the same clock work, and those modes are what make DoD 1, 7, 8, 9 and the C1/C3 latency claims measurable.
- **It's contained.** It touches the clock adapter, the driver, the timer wheel and one G10 clause. The single-writer kernel stays as it is.

P0.2 and P0.3 are both small and independent of it, so they can follow on the same day.

---

## Appendix A: Repro scripts

Run from the repo root with `source .venv/bin/activate && PYTHONPATH=src:. python <script>`. Each is a ready-made starting point for its work package's regression test.

### A.1 D1: settled write never released on the real entry point (P0.1)

```python
import asyncio, sys, time
sys.path[:0] = ["tests"]
from conftest import BOOK_FLIGHT_TOOL
from prism_rt.config import Config
from prism_rt.entry import setup
from prism_rt.workers.gateway import ScriptedProvider

p = ScriptedProvider()
p.register("interpret", "Book flight AI-1", {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True})
p.register("plan", "book_flight", {"steps": [{"local_id": "s1", "tool": "book_flight", "kind": "write", "bindings": {"flight_id": {"type": "fact", "key": "slot.$G.flight_id"}}, "after": []}]})
p.register("compose", "", {"text": "Booked.", "claims": []})

async def main(settle_ms):
    rt = setup(Config(settle_ms=settle_ms, watchdog_timeout_ms=3000, log_decisions=False), provider=p)
    ev, act = asyncio.Queue(), asyncio.Queue()
    for e in [{"ts_us": 0, "type": "manifest", "payload": {"tools": [BOOK_FLIGHT_TOOL]}},
              {"ts_us": 100_000, "type": "text_chunk", "payload": {"text": "Book flight AI-1"}},
              {"ts_us": 150_000, "type": "end_of_turn", "payload": {}}]:
        await ev.put(e)
    # Harness now waits for the agent (never sends another event, no sentinel).
    t0 = time.monotonic()
    summary = await rt.run_scenario(ev, act)
    out = []
    while not act.empty(): out.append(act.get_nowait())
    print(f"settle_ms={settle_ms} wall={time.monotonic()-t0:.1f}s watchdog_fired={summary.watchdog_fired}")
    for a in out:
        print("  ", a)
asyncio.run(main(300)); asyncio.run(main(0))
```

Observed: with `settle_ms=300`, only ACK, "On it — book flight now." and the watchdog salvage FINAL (`task_completed=False`); no `book_flight` call. With `settle_ms=0`, the `book_flight` call is emitted.

### A.2 D2: stale chained result consumed without `output_map` (P0.3)

```python
import sys
sys.path[:0] = ["tests"]
from conftest import GET_SEAT_MAP_TOOL, SEARCH_FLIGHTS_TOOL, FAST_WORKER_LATENCY, chunk_event, drain, eot_event, interruption_event, manifest_event
from prism_rt.config import Config
from prism_rt.model.types import ActionType
from prism_rt.sim.harness import SimHarness, MockToolRegistry
from prism_rt.workers.gateway import ScriptedProvider

class ArgMock(MockToolRegistry):
    def on_call(self, call_id, tool_name, args, ts_us):
        super().on_call(call_id, tool_name, args, ts_us)
        due, st, resp, err = self._scheduled[call_id]
        if tool_name == "search_flights":
            resp = {"flight_id": {"Pune": "PNQ-1", "Mumbai": "BOM-2"}[args["destination"]]}
        if tool_name == "get_seat_map":
            resp = {"seats_for": args["flight_id"]}
        self._scheduled[call_id] = (due, st, resp, err)

def run(output_map):
    p = ScriptedProvider()
    p.register("interpret", "Find flights to Pune", {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]})
    p.register("interpret", "actually Mumbai", {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]})
    s1 = {"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}
    if output_map: s1["output_map"] = {"flight_id": "derived.$G.selected_flight"}
    p.register("plan", "goal_intent: search_flights", {"steps": [s1, {"local_id": "s2", "tool": "get_seat_map", "kind": "read", "bindings": {"flight_id": {"type": "step_output", "step_key": "s1", "path": "flight_id"}}, "after": ["s1"]}]})
    p.register("compose", "seats", {"text": "Seat map ready.", "claims": []})
    h = SimHarness(Config(), seed=1, provider=p, worker_latency_us=FAST_WORKER_LATENCY)
    h.mock_tools = ArgMock({"search_flights": {"latency_ms": 100}, "get_seat_map": {"latency_ms": 800}})
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, GET_SEAT_MAP_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")]); h.send(150_000, [eot_event()])
    drain(h, 400_000, stop_on_final=False)
    h.send(h.clock.now_us()+10_000, [interruption_event()])
    h.send(h.clock.now_us()+5_000, [chunk_event("actually Mumbai")])
    h.send(h.clock.now_us()+5_000, [eot_event()])
    drain(h, h.clock.now_us()+3_000_000)
    print(f"--- output_map={output_map}")
    for c in h.store.call_ledger.all():
        print(" ", c.call_id, c.tool, c.args, c.status.name)
    for a in h.run_log.emitted_actions:
        if a.action_type == ActionType.FINAL:
            print("  FINAL snapshot:", a.snapshot)
run(True); run(False)
```

Observed: with `output_map`, `get_seat_map(PNQ-1)` is `COMPLETED_AFTER_CANCEL` and a new `get_seat_map(BOM-2)` is consumed. Without it, `get_seat_map(PNQ-1)` is `CONSUMED`, never re-run, and the FINAL carries a Mumbai snapshot.

### A.3 D3: false "already taken care of" and livelock after a non-retryable write error (P0.2)

```python
import sys
sys.path[:0] = ["tests"]
from conftest import BOOK_FLIGHT_TOOL, FAST_WORKER_LATENCY, chunk_event, drain, eot_event, manifest_event
from prism_rt.config import Config
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider
p = ScriptedProvider()
p.register("interpret", "Book flight AI-1", {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True})
p.register("plan", "book_flight", {"steps": [{"local_id": "s1", "tool": "book_flight", "kind": "write", "bindings": {"flight_id": {"type": "fact", "key": "slot.$G.flight_id"}}, "after": []}]})
p.register("compose", "", {"text": "Booked.", "claims": []})
h = SimHarness(Config(), seed=1, provider=p, worker_latency_us=FAST_WORKER_LATENCY,
               tools={"book_flight": {"latency_ms": 50, "error": {"code": "E", "retryable": False}}})
h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])]); h.send(100_000, [chunk_event("Book flight AI-1")]); h.send(150_000, [eot_event()])
drain(h, 8_000_000, stop_on_final=False)
for a in h.run_log.emitted_actions: print(a.ts_us, a.action_type.value, getattr(a.body, "text", None) or getattr(a.body, "tool_name", ""))
for c in h.store.call_ledger.all(): print(c.call_id, c.status.value, c.attempt)
print("goal:", [(g.status.value, g.task_state.value) for g in h.store.goals.all()])
```

Observed: speech "That's already been taken care of." at 500 ms; `c-0001 failed`, `c-0002 proposed` (stuck); goal still `active/executing` after 8 simulated seconds.
