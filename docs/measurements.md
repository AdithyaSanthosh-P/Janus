# Measurements

All numbers on this page were produced by actually running this repository's own test suite and a small benchmark script against `SimHarness` — none are estimated or asserted from design intent. Where the project's guiding rule ("never claim untested work is verified") would be violated by a number we don't actually have (e.g. scoring against the hidden evaluation kit, which is unreleased), that's stated explicitly rather than approximated.

## Test coverage by version

| Version | Scenarios | Count | Status |
|---|---|---|---|
| V0 | Deterministic kernel skeleton (no LLM) | 8 | 8/8 passing |
| V1 | Interruptible text agent (N-01..N-04, I-01/I-03/I-07/I-08/I-09/I-13, S-01/S-02/S-03, R-02/R-06) + replay identity | 15 | 15/15 passing |
| V2 | Robust recovery (I-04, I-04-chain, I-05, I-10..I-15, S-04, S-10, R-05, T-05) + replay identity | 13 | 13/13 passing |
| V3 | Multimodal (N-05, M-03, M-06, M-07, M-08) + replay identity | 6 | 6/6 passing |
| V4 | Timing sweeps (I-03 x5, I-14 x5, R-05 x5) + C10 (x3) | 18 | 18/18 passing |
| Phase A | Integration surface: format-variant dialects, watchdog salvage (T-07), real async entry point | 13 | 13/13 passing |
| Phase 2 | Audio/ASR (N-06, M-01, M-10) + replay identity | 5 | 5/5 passing |
| Phase 3 | Live multimodal blob-resolution plumbing (deterministic; live-API verification done separately, see below) | 4 | 4/4 passing |
| Phase 4 | Chunk-anchored cancellation, I-02, revert-safety, no-active-goal inertness + replay identity | 5 | 5/5 passing |
| Phase 5 | C9 commit-last ordering (N-02b) + C2 response frames + replay identity | 11 | 11/11 passing |
| Phase 6 | Speculative interpretation coalescing/promotion + content-bearing ACK + replay identity | 8 | 8/8 passing |
| Bugfix round 1 | Correction-race / stale-COMPOSE regression (found by independent review, `reviews/opus/`) | 1 | 1/1 passing |
| Bugfix round 2 | Retry-exhaustion false-completion-claim + S-07 livelock (found by a follow-up independent review, `reviews/sonnet/`) | 3 | 3/3 passing |
| Bugfix round 3 | Write-lineage livelock — `CommitGate` G6 permanently blocking a corrected write (found while independently verifying, and disproving, a third review's own two stated findings; `reviews/gemini/`) | 3 | 3/3 passing |
| Phase 7 (in progress) | CS-27 checker check + fault-injection test, plus R-03/S-08 scenario coverage | 4 | 4/4 passing |
| Live-model reliability fixes | Retry/backoff on transient provider errors + prompt-tool-context regression tests (found by direct live reproduction against the real Gemini API, not a review round) | 7 | 7/7 passing |
| Phase 7, session 2 | R-04 delayed-cancellation scenario coverage, plus CS-10 live-guard fault-injection (a real enforcement gap the blueprint claims exists but didn't, until this session — see `sim/checker.py`'s docstring) | 5 | 5/5 passing |
| Live multimodal orchestration fix | `visual_candidates` prompt guidance + pending-visual-target PLAN binding guidance (found by directly running `demo/run_live_multimodal_demo.py` against the real Gemini API — a live vision question came back with an empty claim list and an unrelated tool answer, not from quota, from two real prompt-completeness gaps; confirmed fixed live, first run after) | 5 | 5/5 passing |
| Phase 7, session 3 | R-07, M-09, M-04 scenarios + N-05-slow-VISION regression, and dropped VISION/ASR job liveness (three real bugs: VISION re-analysis loop, dropped jobs stuck forever, slow-VISION spurious CLARIFY) | 7 | 7/7 passing |
| Phase 7, session 4 | M-05 (stale evidence before a write → fresh-view request) x3, P-04 snapshot validity (offline check fault-injection x3, extra-slot snapshot filtering, wrong-type-slot livelock) x5 | 8 | 8/8 passing |
| Phase 7, session 5 | CS-03 event-ordering checker check + fault injection | 3 | 3/3 passing |
| C6 | Schema-complete read sets (tool calls + FRAME inheritance), each gap verified to fail pre-fix | 8 | 8/8 passing |
| CS-28 | Floor rule: no SPEAK/CLARIFY/FINAL while the user holds the floor (found by the CS audit) | 6 | 6/6 passing |
| T-04 | `observability/metrics.py` — TTFS, tool latency, cancel latency, end-to-end latency; cross-checked against the Phase 6 number below | 24 | 24/24 passing |
| M4 / C17 | Explorer-found regressions (TRIAGE hold, lost rapid follow-up, stuck speculative correction, interpreter give-up, watchdog exemption) + bounded exploration + explorer self-tests | 11 | 11/11 passing |
| C1 | Inert-tail promotion: lexicon, promotion/refusal cases, 9-point timing sweep, CS-30 fault injection | 28 | 28/28 passing |
| Multimodal races | Vision (V1–V4) + ASR (A1–A4b) explorer regressions: 12 fix-and-mutation cases (clean with the fix, violating with only it removed), 4 outcome checks, bounded exploration | 17 | 17/17 passing |
| **Total** | | **246** | **246/246 passing** |

Reproduce: `source .venv/bin/activate && python -m pytest tests/ -v`. Every test also runs `TraceChecker` — 9 offline checks (CS-05, CS-08, CS-09, CS-13, CS-14, CS-17, CS-27, CS-33, plus the P1 structural check and the static no-wall-clock scan over `kernel/` and `store/`). The 4 CS checks added during bugfix round 3 (CS-05/09/14/33) have since been audited against the blueprint text by Phase 7 (see `sim/checker.py`'s module docstring for per-check findings — most hold up, CS-14 covers only half its stated invariant). CS-27 shipped with a fault-injection test (`tests/test_checker_cs27.py`) that proves it actually fires, not just that it produces zero violations on the existing suite. Phase 7's second session found CS-10 ("terminal call statuses never change") had no live enforcement at all despite the blueprint claiming it did — fixed directly in `store/ledgers.py` (not an offline `TraceChecker` rule, since there's no per-step call-status history retained to check a transition-and-overwrite after the fact) with its own fault-injection test, `tests/test_cs10_terminal_call_guard.py`. CS-15/CS-16/CS-24/CS-26/CS-30 are investigated and explicitly deferred with documented reasons, not silently skipped. 0 violations across all 149 tests (plus a new P-04 snapshot-validity check, with its own fault-injection tests) — notably, `TraceChecker` did **not** catch any of the three independent-review-round bugs on its own; all three were found by a fresh model reading the code and running real reproductions, not by this project's own test suite. Full-suite wall time: **~0.8s** on this machine's dev environment (Python 3.14), **1.20s** re-verified passing (149/149) inside the Docker image (Python 3.11, `docker run --rm janus`).

## Per-step kernel latency

Measured with `time.perf_counter()` wrapped around 44,200 `SimHarness.send()`/`.advance()` calls (each one drives exactly one `Kernel.step()`), across 200 full runs of an I-03-style scenario (new goal → search dispatched → interrupted mid-flight → corrected → re-dispatched → composed → FINAL). This measures wall time in Python around the harness call (envelope construction + the kernel step itself), not an isolated, instrumented measurement of `Kernel.step()` alone — no timer lives inside `kernel/` itself, by design (`docs/prompt 2.txt` §5: no wall-clock in kernel/store code).

| Metric | Value |
|---|---|
| Steps measured | 44,200 |
| Mean | 9.2 µs |
| p50 | 5.8 µs |
| p95 | 17.9 µs |
| p99 | 82.3 µs |
| Max | 2,760.8 µs |

`config.step_budget_ms` (telemetry target, never a correctness gate) is 5.0 ms = 5,000 µs. Every percentile up to p99 is roughly two orders of magnitude under that target; the single max outlier (2.76 ms) is still under budget and consistent with an interpreter-level pause (GC, scheduling) rather than kernel work — the same scenario's other 44,199 steps were all under 600 µs.

## Baseline vs. full-system comparison

The hackathon's hidden evaluation kit — the actual scoring harness this project is ultimately measured against — is unreleased (`currentStatus.md`'s Known Risks). Without it, there is no real "baseline" to score against beyond this project's own test suite, so the comparison below is internal: the same scenario, with a version's own `Config` flags toggled off (reproducing the prior version's behavior) vs. on.

| Comparison | Flag(s) | Effect (from the scenario the flag exists for) |
|---|---|---|
| V1 vs V2 rebinder | `rebinder_enabled` | `test_i10_localized_correction_rebinds_without_replanning`: a purely-local slot correction rebinds the existing plan step in the same step the correction lands, with **zero** additional Planner round-trips — vs. a full replan (one PLAN job + its dispatch latency) with the flag off. |
| V1 vs V2 transitive invalidation | `transitive_invalidation` | `test_i04_transitive_cancel_of_downstream_chain`: a 2-3 step chained plan (search → derived value → dependent call) cancels every downstream call in the **same step** the upstream slot changes — vs. only the directly-dependent call cancelling, leaving later steps to bind stale data, with the flag off. |
| V1 vs V2 settle barrier | `settle_barrier_enabled` | `test_i14_correction_within_settle_window_blocks_the_original_booking`: a write is never emitted for a value the user corrected within `settle_ms` (300ms default) of speaking it — vs. the write going out immediately and needing post-hoc reconciliation, with the flag off. |
| V0-V2 vs V3 multimodal | `vision_enabled` | `test_m06_conflicting_visual_text`: a vision-perceived value that disagrees with what the user said opens a conflict and blocks the step with a specific CLARIFY — vs. the slot binding simply being absent (a generic "what should X be" clarify, or nothing at all if unrelated), with the flag off. |
| V0-V3 vs V4 identifier binding | `reference_bound_identifiers` | `test_c10_model_literal_identifier_refused`: a WRITE call with a model-invented identifier argument is refused and the plan blocks pending a resolving read/clarification — vs. the call being admitted and potentially acting on a fabricated identifier, with the flag off. |
| Phase 5 vs Phase 6 speculative interpretation | `speculative_interpretation_enabled` | Real benchmark (`SimHarness`, INTERPRET latency 300ms, chunk spacing 150ms — the same numbers `docs/theme05_implementation_blueprint.md`'s T-06 scenario uses), measuring TTFS(EOT): time from the `end_of_turn` event to the first substantive SPEAK/FINAL/CLARIFY action. **Flag off: 300,000 µs** (the full INTERPRET round-trip happens only after EOT). **Flag on: 0 µs** (the speculative job resolved and was promoted while the turn was still open, so EOT itself triggers the plan dispatch and ACK in the same step) — a **100% reduction**, meeting T-06's acceptance bar ("TTFS(EOT) ≤ 1 ms when... promotion"). This specific number depends on the turn lasting long enough for the speculative job to resolve before EOT (here: 4 × 150ms ≥ 300ms); a turn shorter than the model's own latency falls back to the flag-off number exactly (nothing is lost, nothing double-counted — see `tests/test_phase6.py`'s in-flight-waiting and stale-cache-fallback tests for those paths). |

## Reusable metrics module (T-04)

`src/prism_rt/observability/metrics.py` (`docs/theme05_implementation_blueprint.md` §10.3, T-04) turns the ad-hoc, one-off TTFS calculation used for the Phase 6 row above into a tested, reusable function over a run's own `StepReport` trace (`list[kernel.step.StepReport]`, the same `reports`/`store` calling convention `sim/checker.py.TraceChecker` already established) — no wall clock, no kernel/store mutation, pure aggregation.

**Cross-check, not just self-consistency**: `tests/test_metrics.py::test_ttfs_matches_the_documented_speculative_interpretation_numbers` reproduces the exact scenario this page's Phase 6 row describes by hand ("INTERPRET latency 300ms, chunk spacing 150ms") through `metrics.ttfs()` and asserts it returns the identical **300,000 µs / 0 µs** already published above — an independent implementation reproducing a previously hand-computed number exactly, not a number invented to match.

Implemented: `ttfs` (all three trigger types), `tool_latency` (per tool + status), `interruption_to_cancellation_latency` (signal-anchored latency, plus the blueprint's own explicit safety count of calls invalidated-but-never-cancelled — must be 0, and is, on every existing test), `end_to_end_latency` (both of §10.3's definitions). Deliberately not implemented, with reasons documented in the module's own docstring: evidence-anchored cancel latency and cancel-delay-for-transitive-dependents (the trace doesn't preserve which specific fact-changing envelope invalidated which specific call, only aggregate per-step sets — the same class of limitation `sim/checker.py` already documents for CS-15/CS-26); event-anchored ratio and state-update timing (look derivable, not attempted this session); the full "Others" (INNOV §6.2) counter grab-bag (stale consumptions, omission escapes, etc. — each needs its own bespoke definition, deliberately out of a "small reusable module"'s scope).

`tests/test_metrics.py` (24 tests): empty-trace handling for every function, percentile-math correctness on known datasets, substantive-action classification, TTFS windowing edge cases (nearest-action selection, per-trigger-type windowing, miss cases, zero-latency same-step promotion), tool-latency grouping and malformed-trace handling (a `tool_result` for a call never dispatched), cancel-latency signal-anchoring and the invalidated-never-cancelled safety count, end-to-end latency across single and multiple turns, non-mutation and reproducibility, plus three full `SimHarness` scenarios (normal, interruption/cancellation, multi-turn) and the measurements cross-check above. 190/190 passing across the full suite, Docker-verified on Python 3.11.

## Adversarial schedule exploration (M4 / C17)

`src/prism_rt/sim/explorer.py` re-runs 10 race scenarios (`tests/explore_scenarios.py`: correction vs. in-flight read, write vs. correction, cancel vs. write result, correction through a chained plan, slow tool vs. a new utterance, backchannel, retry vs. late success, manifest update mid-plan, speculative correction, inert tail + correction) under perturbed schedules — seeded random draws over tool/worker latencies and user-event gaps, plus *targeted* schedules that set a tool's latency so its result lands one step before, at, or after each later user event, each crossed with a slow (400ms) interpreter. Every run is checked by 9 oracles re-derived from state (not by calling kernel helpers): the full `TraceChecker`, the floor rule, the TRIAGE hold, FINAL-vs-consumed-call grounding, invalidated-never-cancelled, at most one confirmed write per lineage, liveness at a horizon that outlasts the slowest perturbed chain, W4 reconciliation, and a per-scenario semantic check. `sim/minimize.py` shrinks any failure back toward baseline one knob at a time until only the deviations the failure needs remain.

| Run | Schedules | Failing | Violation kinds |
|---|---|---|---|
| First run, before this session's fixes (9 scenarios) | 471 | 126 | triage, stale-final (FINAL says Pune after "actually Mumbai"), liveness (lost first turn; stuck speculative correction) |
| After the fixes — 3 seeds × 300 random + targeted | **9,142** | **0** | — |
| Same explorer, `triage_hold_enabled=False` (every other fix still in place) | 3,122 | **704** | triage 704, **stale-final 256**, **superseded-write 76** |

The last row is the evidence for what the TRIAGE hold buys: without it, 256 of 3,122 adversarial schedules (8.2%) give the user a stale answer after they corrected it, and 76 (2.4%) book a flight the user had already corrected away from. Minimized, the most common failure is a single deviation from baseline ("search latency 400ms → 220ms"): the result lands mid-correction and was released in the step the correction's end-of-turn closed the floor. Exploration is deterministic (same seed → same schedules → same findings) and fast: ~9,000 kernel runs in ~2 minutes; the suite runs a bounded version (`tests/test_triage_hold.py::test_bounded_exploration_of_every_scenario_is_clean`) on every test run.

## C1 inert-tail promotion

Same T-06 shape as the Phase 6 row above (INTERPRET 300ms), with the user adding "please" after the speculative interpretation of "Find flights to Pune" has finished. **With C1: TTFS(EOT) = 0 µs** (promoted across the inert tail, same step as EOT). **Without C1: 100,000 µs** here — the remaining latency of the re-dispatched interpretation; approaches the full 300ms model round-trip when the tail arrives right before EOT. Measured with `observability/metrics.ttfs` (`tests/test_c1_inert_tail.py`, which also sweeps 9 tail/EOT offsets and holds TTFS(EOT)=0 and the correct destination across all of them).

### Multimodal extension (vision + ASR)

9 more scenarios (`tests/explore_scenarios_mm.py`): V1 redirect to another frame, V2 new frame vs. old result, V3 vision failure vs. redirect, V4 slow vision + watchdog; A1 spoken request vs. typed correction, A2 earlier vs. newer speech with a transient failure, A3 ASR vs. end-of-turn, A4 persistent / A4b malformed transcription then a typed request. Before fixes: 4 of 8 baselines failed and 123 of 556 schedules failed on the first bounded run; 12 real bugs followed (see `currentStatus.md`), one of them in the text path (a composed answer grounded in pre-correction results).

**Reproduce: `PYTHONPATH=src:.:tests python demo/run_exploration.py`** (deterministic, ~5 min; `--quick` for a smaller run).

| All safeguards on | Scenarios | Schedules | Failing |
|---|---|---|---|
| Text/tool | 10 | 9,142 | **0** |
| Multimodal | 9 | 8,194 | **0** |

Each safeguard disabled *alone*, via a test-local patch (`tests/mutations.py`; production untouched), against the scenarios it protects:

| Safeguard disabled | Schedules | Failing | What comes back |
|---|---|---|---|
| ASR capture order + AUTO dedupe window | 912 | 629 | lost/duplicated/out-of-order utterances, floor stuck open |
| Malformed transcript counts as a failure | 453 | 453 | agent silent forever |
| TRIAGE hold (text) | 916 | 265 | stale answers (86), superseded writes (98) |
| Deferred end-of-turn | 453 | 208 | spoken request never answered |
| TRIAGE hold (multimodal) | 927 | 161 | answers from superseded evidence (147) |
| Vision job identity *(pre-existing)* | 921 | 142 | stale evidence, spurious clarifications |
| Retarget clears a pending clarify | 453 | 86 | clarification asked after the user already redirected |
| RESPONDING re-checks step validity | 936 | 74 | ungrounded / lost answers |
| Retarget retracts old evidence | 936 | 67 | answer from the frame the user redirected away from |
| Compose-text grounding | 463 | 24 | FINAL speaks words composed from pre-correction results |
| Untranscribed speech is pending user content | 459 | 14 | answer given over speech still in ASR |
| Watchdog exemption only for the salvage FINAL | 475 | 6 | genuine answer skips the hold after a watchdog |
| Perception stops after the goal ends | 468 | 5 | paid vision calls continue forever |
| Frame tie-break by arrival | 921 | 2 | rarest — needs every gap compressed onto one timestamp; pinned by a deterministic test |

Bounded, not exhaustive: latency knobs are per worker *kind*, event order is always preserved, and the value grids are fixed. A clean run means no violation in the explored space, not a proof.

Current suite: **246/246 passing**, Docker-verified on Python 3.11.

## FDB-v3: current results (30 Sep 2026)

FDB-v3's own runner and evaluators at the pinned commit `3e799c45`, unmodified. "Judged" means FDB's `--use-llm` with `gpt-4o`, the organizers' evaluation setting.

| Run | Strict Pass@1 | Tool sel. | Arg. acc. | Resp. quality | Self-correction (Pass@1) |
|---|---|---|---|---|---|
| Text replay, all 100, Gemini 3.6 Flash (thinking 0), judged | **0.770** | 0.959 | 0.807 | 0.840 | **0.824** |
| Voice over LiveKit, all 100, cloud RTX 3090, native path, judged (29-30 Sep, before the fixes below) | 0.420 | 0.844 | 0.611 | 0.597 | 0.412 |
| Voice, 32-recording rerun of that run's hardest recordings, after the GPU fix, exact match | 11/32 (was 8/32) | | | | |

By domain (text replay): finance 1.00, travel 0.95, e-commerce 0.83, housing 0.35. By number of tool calls: 1 call 0.82, 2 calls 0.72, 3 calls 0.63.

What the full voice run's decision logs showed, each fixed with a regression test (details in `currentStatus.md`):

- **Speech-to-text fell back from GPU to CPU** in every job process started after ~75 recordings (decode 0.18 s → ~12 s per segment; the agent heard users ~29 s late). Pass rate 47 % before the switch, 24 % after. Now logged with GPU memory, and can be made fatal (`JANUS_REQUIRE_GPU=1`); `repro_inner.sh` records `gpu_mem.csv`.
- **Two kernel liveness bugs**: a clarifying question set late in a step was never dispatched when no other event arrived; the stall salvage could fire while the user was still speaking. Both invisible to the fixed-tick simulator; now covered by event-driven tests (`tests/test_clarify_wake.py`).
- **Whisper artefacts**: silence hallucinations ("you", "Hmm.") became turns; spoken codes ("F A S T nine nine") were not joined into identifiers.
- **8 recordings lost to the benchmark client aborting (SIGABRT) after the stream**; the agent's decision logs show correct tool calls in 6 of them.

Hosted transcription (`JANUS_STT=openai`, `gpt-4o-mini-transcribe-2025-12-15`) heard every name and code local Whisper got wrong on the checked recordings ("Chicago", "Milan", "BOB12", "123ABC", "P88990011"); a voice run with it has not been scored yet.

## FDB-v3 published baselines (docs/fdb_v3_implementation_plan.md Day 1)

From the FDB-v3 paper (arXiv 2604.04847, Lin/Chen/Chen/Lee, NTU + NVIDIA), Tables 2/4/5/6 — quoted exactly, not re-derived. All six baselines are voice-native systems run over real audio through LiveKit; Janus's own Day-1 numbers below are a **text replay** (no voice yet, see `currentStatus.md`'s FDB-v3 Day 1 section) and are not directly comparable on latency/turn-taking, only on tool-selection/Pass@1 as a rough sanity check of the reasoning core.

| System | Tool Sel F1 | Arg Acc | Resp Qual | Pass@1 | Take-turn | Latency | Interrupt | Filler |
|---|---|---|---|---|---|---|---|---|
| GPT-Realtime | 0.876 | 0.680 | 0.792 | **0.600** | 96.0% | 6.89s | 13.5% | 16.9% |
| Gemini Live 2.5 | 0.786 | 0.593 | 0.554 | 0.490 | 92.0% | 7.26s | 14.1% | 8.9% |
| Gemini Live 3.1 | 0.817 | 0.588 | 0.718 | 0.540 | 78.0% | **4.25s** | 19.2% | 31.7% |
| Grok | 0.797 | 0.542 | 0.617 | 0.430 | 94.0% | 6.65s | 25.5% | 44.3% |
| Ultravox | 0.794 | 0.513 | 0.510 | 0.410 | 96.0% | 8.40s | 47.9% | 88.0% |
| Cascaded (Whisper→GPT-4o→TTS) | 0.803 | 0.562 | 0.600 | 0.450 | **100.0%** | 10.12s | 33.0% | 26.9% |

Pass@1 by difficulty (Table 4): GPT-Realtime Easy/Medium/Hard = 0.750/0.588/0.433; every system degrades with difficulty, Grok steepest (0.583→0.200).

Pass@1 by domain (Table 5): Finance is easiest for every system (GPT-Realtime 0.960); Housing is hardest for every system (GPT-Realtime 0.308, Grok 0.115) — "multi-entity order handling and complex constraint reasoning" per the paper.

Mean latency breakdown in seconds (Table 6, First Word / Tool Call / Task Completion): GPT-Realtime 6.36/3.89/6.89; Gemini Live 3.1 3.95/2.21/4.25 (fastest); Cascaded 8.78/3.15/10.12 (slowest, "sequential Whisper→LLM→TTS chain creates an irreducible bottleneck").

Two qualitative findings worth carrying into Day 2's own design: (1) **Gemini Live 3.1 is "the silent worker"** — fastest when it responds, but 22% of scenarios get no speech at all despite 86% of those silent cases having actually executed the right tool calls; a disconnect between reasoning and speech generation the paper calls out as its own failure mode, distinct from Janus's own "no honest failure on a stalled call" gap found this session (see `currentStatus.md`) but a useful cross-check that "acts correctly but says nothing" is a known failure shape in this benchmark, not unique to Janus. (2) **self-correction is the hardest category for every system** (GPT-Realtime leads at only 0.588; Cascaded scores just 0.176, "the downstream LLM has no opportunity for state rollback") — this is exactly the class of correction/rollback Janus's own architecture (read-set invalidation, same-step cancellation) was built for, so it is the single highest-leverage place Janus's design should differentiate once voice is wired up.

Pass@1 by disfluency (Table 3): self-correction is the hardest category for every system: GPT-Realtime 0.588, Gemini Live 2.5 0.471, Gemini Live 3.1 0.353, Ultravox 0.353, Grok 0.294, Cascaded 0.176.

**Historical (25 Sep, superseded by the section above) — Janus's own Day-1 numbers, for rough sanity-check context (not apples-to-apples — see caveat above):** 20-scenario subset (gemini-3.5-flash-lite, post bare-fact-key fix, exact-match scoring): strict Pass@1 40.0% — inside the published baselines' range (0.410–0.600), comparable to Grok/Ultravox/Gemini-2.5. Full 100-scenario run: strict Pass@1 21.0% — below the published range, understood to be suppressed by the G4/commit-intent gap diagnosed the same session (`currentStatus.md`'s FDB-v3 Day 1 section) rather than a real reasoning-quality ceiling; re-measure after Day 2's commit-intent policy lands. All argument-accuracy numbers are exact-match (no `OPENAI_API_KEY` yet) where the paper's are presumably judge-scored, so Janus's true numbers are likely higher than quoted here.

## Known gaps in this data

- No timing/latency comparison against the real evaluation kit exists — it isn't released yet, and `adapters/codec.py`'s wire format is a documented guess (see `currentStatus.md`).
- No live-model latency numbers: `ScriptedProvider` (used by every test above) resolves in the same process with no network round-trip. `GeminiProvider` (`demo/run_v1_live_demo.py`) has been verified to work correctly against the real Gemini API but its latency was not benchmarked here — that number would be dominated by network/API latency, not kernel behavior.
- The per-step latency benchmark above is one scenario shape (single-goal, single read tool, one correction). It was not repeated across every scenario category (e.g. multi-step chains, multimodal claim acceptance) — the P95/P99 margin under `step_budget_ms` is wide enough that this wasn't judged necessary, but it is a real limit of what's measured here, not implied to generalize without basis.
