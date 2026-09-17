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
| **Total** | | **60** | **60/60 passing** |

Reproduce: `source .venv/bin/activate && python -m pytest tests/ -v`. Every test also runs `TraceChecker` (P1/P3/W1/S3 invariants, plus the static no-wall-clock scan over `kernel/` and `store/`); 0 violations across all 60 tests. Full-suite wall time: **0.26s** on this machine's dev environment (Python 3.14), **0.40s** inside the V4 Docker image (Python 3.11, `docker run --rm janus`) — both cold, no cache warm-up between runs.

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

## Known gaps in this data

- No timing/latency comparison against the real evaluation kit exists — it isn't released yet, and `adapters/codec.py`'s wire format is a documented guess (see `currentStatus.md`).
- No live-model latency numbers: `ScriptedProvider` (used by every test above) resolves in the same process with no network round-trip. `GeminiProvider` (`demo/run_v1_live_demo.py`) has been verified to work correctly against the real Gemini API but its latency was not benchmarked here — that number would be dominated by network/API latency, not kernel behavior.
- The per-step latency benchmark above is one scenario shape (single-goal, single read tool, one correction). It was not repeated across every scenario category (e.g. multi-step chains, multimodal claim acceptance) — the P95/P99 margin under `step_budget_ms` is wide enough that this wasn't judged necessary, but it is a real limit of what's measured here, not implied to generalize without basis.
