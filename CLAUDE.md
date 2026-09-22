# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

This project is called **Janus** (`pyproject.toml` distribution name `janus`; the Python package import path is `prism_rt` — a naming mismatch left in place deliberately, not a leftover to "fix"). It's a hackathon entry for the Samsung PRISM GenAI Hackathon, Theme 05 (deadline 25 Sep 2026). V0 through V4 are frozen (git tags `v0-skeleton`, `v1-text-agent`/`release/v1`, `v2-robust-recovery`/`release/v2`, `v3-multimodal`/`release/v3`, `v4-hardened`/`release/v4`), plus six post-V4 phases: **Phase A (Integration Surface)** fixing the real async entry point and hardening the wire-format layer (tag `v5-integration`/`release/v5`) — written after the two official guideline documents were finally read in full and found the project's actual entry point had never once been executed — **Phase 2 (Audio/ASR)** (tag `v6-audio`/`release/v6`), unlocking the 30% of scored scenarios that are audio, **Phase 3 (Live Multimodal Provider)** (tag `v7-live-multimodal`/`release/v7`), sending real image/audio bytes (`MediaPart`/`blob_resolver`) to the live Gemini API instead of by-reference IDs, **Phase 4 (Chunk-Anchored Interruption Cancellation)** (tag `v8-chunk-anchor`/`release/v8`), cancelling in-flight work the instant a HIGH-confidence correcting value is heard mid-utterance instead of waiting for end-of-turn, **Phase 5 (Task-Completion Depth)** (tag `v9-task-completion`/`release/v9`), adding C2 response frames (a deterministic, kernel-rendered FINAL grounded in the real tool result, no COMPOSE round-trip) and C9 commit-last ordering (a write waits for unrelated reads to settle first), and **Phase 6 (Response Latency)** (tag `v10-response-latency`/`release/v10`), adding speculative interpretation (an INTERPRET job runs on the transcript prefix while the user is still speaking, applied at EOT with zero additional model latency when the digest matches — measured 300ms→0ms on a T-06-shaped scenario) and content-bearing ACK. On top of that, three rounds of independent-review-driven bugfixes (none a phase — no release branches): `v10-1-correction-race-fix` (a correction landing while a goal was finishing up could let a stale, pre-correction COMPOSE result produce a false-completion FINAL), `v10-2-failure-honesty-fix` (a tool failing past its retry limit — an ordinary scenario — produced a FINAL admitting failure in its own text while still claiming `task_completed=True`; a plan referencing an unknown tool silently livelocked forever instead of failing honestly), and `v10-3-write-lineage-fix` (a corrected WRITE whose original had already been cancelled but still confirmed on the provider side could get stuck `PROPOSED` forever, since `CommitGate` G6 blocks any future write to a plan-step lineage once any effect confirms for it, regardless of fingerprint — fixed with the safe default `docs/prompt 2.txt` §8.8 itself calls for, an honest failure naming what already went through). All three found by fresh model sessions given full repo access and asked to verify claims and hunt for bugs — the third round's own two stated findings were themselves independently disproven before the real bug it led to was found, a pattern significant enough that every review round since has been logged in `reviews/index.md` rather than trusted at face value. See `currentStatus.md`'s Implemented section for the mechanisms, and `reviews/opus/`/`reviews/sonnet/`/`reviews/gemini/` for the investigation artifacts. `docs/post_v4_implementation_plan.md` is the authoritative sequencing for everything after V4; read it before `currentStatus.md`'s own version-strategy framing below, which predates it. The final `PRISM_GENAI_HACKATHON_Y2026` submission tag needs explicit team confirmation, not autonomous action. Every future session should treat `currentStatus.md` as the live handoff record and update it after meaningful work (it says so explicitly — follow that rule).

**Read `currentStatus.md` first, every session.** It names the read order for the other docs, the current implementation state, and the next task. Do not re-derive that from scratch — it is kept up to date for exactly this purpose.

## What is being built

A full-duplex, interruptible real-time conversational agent: it must stay responsive while reasoning (fast path), run tool calls and LLM reasoning concurrently (slow path), and recover cleanly when the user interrupts, corrects a slot, or switches goals mid-task — without duplicate side effects or false completion claims. Scored on Task Completion (40%), Interruption Recovery (35%), Response Latency (15%), Safety & Protocol (10%), against an evaluation kit that is not yet released (defaults must be safe/reversible via policy settings, see below).

## Source-of-truth hierarchy

When documents disagree, higher wins:
1. `guidelines/Theme_5_Guide.md` — official spec, scoring, interface contract
2. `guidelines/Samsung_PRISM_Y2026_GenAI_Hackathon_3rd_Edition.md` — submission rules, deadlines
3. `docs/prompt 2.txt` (architecture) + `docs/prompt1.txt` (problem analysis) — architectural invariants
4. `docs/prototype_version_plan.md` (V0-V4 scope) + `docs/post_v4_implementation_plan.md` (Phase A-8 scope, everything after V4) — version/phase scope: what to build vs. defer; between these two, the one covering the work at hand wins
5. `docs/sonnet_implementation_plan.md` — **the executable coding plan**: repo layout, file-by-file build order, interfaces, test strategy
6. `docs/theme05_implementation_blueprint.md` — detailed reference for data models (190KB — look up sections, don't read cover-to-cover)
7. `currentStatus.md` — current project state only; never overrides the above

`docs/sonnet_implementation_plan.md` is the primary implementation guide during coding. The other `docs/` files are ground truth for *why* the architecture is shaped this way, not sequencing.

## Architecture (from `docs/prompt 2.txt` and the implementation plan)

The system is a **single-writer coordination kernel**, not an LLM-loop-calls-tools design. No model ever mutates state or emits directly to the harness — models are workers that return proposals.

- **One synchronous kernel step** (`kernel/step.py`) owns all session state mutation. Everything else (LLM interpretation/planning/composition, vision, ASR) runs as async **workers** outside the kernel and returns proposals; the kernel never awaits inside a step.
- **Seven-phase step**: drain mailbox → order batch by `(ts, class, seq)` → apply reducers → invalidate (mark dependents of changed facts) → decide (fixed component order) → emit (EmissionGate, synchronous) → log. Target ≤5ms wall time per step.
- **Optimistic concurrency via read sets.** Every proposal, call, and pending utterance records the `(key, ver, digest)` facts it depended on. A read set is valid iff every fact still exists, isn't retracted, and its digest is unchanged — this is what lets a value *revert* (Pune→Mumbai→Pune) without re-running work, while still invalidating genuinely stale work. There is no global generation counter; `goal.active` acts as the coarse generation, individual fact versions as the fine one.
- **Same-step cancellation.** When a fact changes, `InvalidationEngine` walks the dependency index and marks affected calls/jobs/proposals in the same step; CANCEL is emitted before SPEAK/TOOL_CALL/CLARIFY/FINAL (fixed emission order).
- **READ vs WRITE tools are architecturally different.** Reads may be speculated, retried, run in parallel. Writes pass a single `CommitGate` (conditions G1–G9, see Appendix B of the implementation plan) requiring closed floor, committed user intent, no duplicate fingerprint/lineage, and are recorded in an effect ledger *before* emission — this is what prevents double-booking.
- **Harness-time only.** All timing goes through `ClockPort.now_us()`. `time.time`, `time.sleep`, `datetime.now`, `asyncio.sleep` (outside `ClockPort`), and `loop.time` are **banned in `kernel/`, `store/`, and worker/tool-facing code** — enforced by a static check. The only wall-clock reader is the budget watchdog (105s default) and test/telemetry code.
- **Unknowns live in policy, not structure.** The eval kit's wire format and many behavioral questions are unresolved; every such unknown is a named policy setting (`PolicyRegistry`, section 20 of `prompt 2.txt`) with a stated safe default, not a structural fork.

### Planned package boundaries (do not violate)

`kernel` must not import `workers` (dispatches jobs by kind through `JobScheduler` instead); `workers` must not import `store` or `kernel`. Full boundary table is in `prompt 2.txt` §21.1. Planned source layout (`src/prism_rt/`): `model/` (immutable records, imports nothing), `store/` (FactStore, DependencyIndex, ledgers, catalog), `kernel/` (step engine, reducers, invalidation, task state machine, commit gate, result router, responder), `workers/` (Interpreter, Planner, Composer, ModelGateway), `adapters/` (clock, codec, output writer), `observability/` (decision log, watchdog), `sim/` (deterministic harness, trace checker). Full file-by-file spec with interfaces is in `docs/sonnet_implementation_plan.md` §1–2.

## Versioning strategy

Build in strict stages; each version must be independently runnable/submittable before starting the next (`docs/prototype_version_plan.md`, `docs/sonnet_implementation_plan.md` §4, §11):

- **V0** — deterministic kernel skeleton, no LLM, plans injected by test scripts. Freeze as git tag `v0-skeleton`.
- **V1** — interruptible text agent with real LLM workers (Interpreter/Planner/Composer via `ModelGateway`). Tag `v1-text-agent`, branch `release/v1`. This is the submission-capable MVP.
- **V2** — robustness innovations (transitive invalidation, settle barrier, absence read sets, claim grades, rebinder), each behind a `Config` feature flag defaulting OFF until enabled. Tag `v2-robust-recovery`, branch `release/v2`.
- **V3** — multimodal grounding (`kernel/perception.py` PerceptionScheduler, `store/ledgers.py` EvidenceStore, `workers/vision.py`, `video_frame` events, perception claims/conflicts/leases), entirely behind `Config.vision_enabled` (default OFF). Tag `v3-multimodal`, branch `release/v3`.
- **V4** — hardening: reference-bound write identifiers (C10, `Config.reference_bound_identifiers`, default OFF), targeted timing sweeps, a verified Dockerfile (Python 3.11), README, measurements.md. Tag `v4-hardened`, branch `release/v4`. Deliberately deferred: kit wire-format integration (kit unreleased), inert-tail promotion (C1), the full 34-check TraceChecker — see `currentStatus.md`'s Deferred section.

Rules: never break a frozen version's interfaces (guard new behavior behind `Config` flags); the highest frozen version/tag is always the emergency-submission fallback; final submission needs git tag `PRISM_GENAI_HACKATHON_Y2026` on the judged commit. Stop conditions (when to halt a version and fall back) are in `docs/sonnet_implementation_plan.md` §13.

## Commands

Python project (`pyproject.toml`, deps: pydantic, PyYAML, pytest, pytest-asyncio) targeting **Python 3.10–3.12** — do not use 3.11+-only features (`ExceptionGroup`, `TaskGroup`, `Self`) since the Docker target is 3.11 but must also run on 3.10/3.12. Dev venv on this machine runs 3.14 (no 3.10–3.12 interpreter was available), so target-version compatibility is unverified beyond static syntax avoidance.

```bash
source .venv/bin/activate                     # venv already has all deps installed
python -m pytest tests/ -v                    # full suite (129 tests: V0-V4 + post-V4 Phase A + Phase 2 + Phase 3 + Phase 4 + Phase 5 + Phase 6 + 3 bugfix rounds + Phase 7 in progress + live-reliability fixes)
python -m pytest tests/test_v3.py -v           # single version's scenario tests
PYTHONPATH=src:. python demo/run_v1_demo.py    # interactive demo with real spoken output (scripted)
PYTHONPATH=src:. python demo/run_v1_live_demo.py  # same demo against the real Gemini API (GEMINI_API_KEY in .env)
docker build .                                 # V4 scope — no Dockerfile yet
```

Note `pyproject.toml`'s `pythonpath = ["src", "."]` — both the `prism_rt` package under `src/` and the top-level `config/` package need to be importable; scripts run outside pytest need `PYTHONPATH=src:.` set the same way.

Tests are deterministic by construction: `SteppedClock` (no real sleeps), `ScriptedProvider` (no live LLM calls), `MockToolRegistry`/`ScriptedRunner` (mock tool and worker-job results, both withheld until a configured latency passes so nothing ever resolves in the same step it was dispatched), and a seeded `IdGenerator`. `TraceChecker` (`sim/checker.py`) runs after every test against the decision log; replay identity (multiple runs of the same input → byte-identical decision log) is a required check, not optional. Never claim untested work is verified — use "NOT VERIFIED" until tests actually pass. Note: `TraceChecker` itself has had real false-positive bugs (see `currentStatus.md`) — if a check fails, verify whether the checker's assumption is wrong before assuming the kernel is.

## Session hygiene

- Inspect the repo before writing code — check what already exists and continue from it rather than assuming `currentStatus.md` is current.
- Complete and freeze one version before starting the next; do not expand scope into anything marked DEFERRED or CUT in `currentStatus.md` without being told to.
- Update `currentStatus.md` after meaningful work (new feature, bug fix, version freeze, blocker, architectural decision) — see the update rule at the bottom of that file for the exact fields to fill in.
