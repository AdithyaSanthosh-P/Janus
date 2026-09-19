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
| **Total** | | **117** | **117/117 passing** |

Reproduce: `source .venv/bin/activate && python -m pytest tests/ -v`. Every test also runs `TraceChecker` — now 9 checks (CS-05, CS-08, CS-09, CS-13, CS-14, CS-17, CS-27, CS-33, plus the P1 structural check and the static no-wall-clock scan over `kernel/` and `store/`). The 4 CS checks added during bugfix round 3 (CS-05/09/14/33) have since been audited against the blueprint text by Phase 7 (see `sim/checker.py`'s module docstring for per-check findings — most hold up, CS-14 covers only half its stated invariant). CS-27 is new this Phase 7 session and, unlike the round-3 checks, ships with a fault-injection test (`tests/test_checker_cs27.py`) that proves it actually fires, not just that it produces zero violations on the existing suite. 0 violations across all 117 tests — notably, `TraceChecker` did **not** catch any of the three independent-review-round bugs on its own; all three were found by a fresh model reading the code and running real reproductions, not by this project's own test suite. Full-suite wall time: **~0.6s** on this machine's dev environment (Python 3.14), verified passing (117/117) inside the Docker image (Python 3.11, `docker run --rm janus`).

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

## Known gaps in this data

- No timing/latency comparison against the real evaluation kit exists — it isn't released yet, and `adapters/codec.py`'s wire format is a documented guess (see `currentStatus.md`).
- No live-model latency numbers: `ScriptedProvider` (used by every test above) resolves in the same process with no network round-trip. `GeminiProvider` (`demo/run_v1_live_demo.py`) has been verified to work correctly against the real Gemini API but its latency was not benchmarked here — that number would be dominated by network/API latency, not kernel behavior.
- The per-step latency benchmark above is one scenario shape (single-goal, single read tool, one correction). It was not repeated across every scenario category (e.g. multi-step chains, multimodal claim acceptance) — the P95/P99 margin under `step_budget_ms` is wide enough that this wasn't judged necessary, but it is a real limit of what's measured here, not implied to generalize without basis.
