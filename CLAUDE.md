# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

This is a **pre-code** hackathon repository (Samsung PRISM GenAI Hackathon, Theme 05, deadline 25 Sep 2026). As of now the repo contains only planning documents (`docs/`, `guidelines/`) and `currentStatus.md` — no `src/`, `tests/`, or `pyproject.toml` exist yet, and git has no commits. Every future session should treat `currentStatus.md` as the live handoff record and update it after meaningful work (it says so explicitly — follow that rule).

**Read `currentStatus.md` first, every session.** It names the read order for the other docs, the current implementation state, and the next task. Do not re-derive that from scratch — it is kept up to date for exactly this purpose.

## What is being built

A full-duplex, interruptible real-time conversational agent: it must stay responsive while reasoning (fast path), run tool calls and LLM reasoning concurrently (slow path), and recover cleanly when the user interrupts, corrects a slot, or switches goals mid-task — without duplicate side effects or false completion claims. Scored on Task Completion (40%), Interruption Recovery (35%), Response Latency (15%), Safety & Protocol (10%), against an evaluation kit that is not yet released (defaults must be safe/reversible via policy settings, see below).

## Source-of-truth hierarchy

When documents disagree, higher wins:
1. `guidelines/Theme_5_Guide.md` — official spec, scoring, interface contract
2. `guidelines/Samsung_PRISM_Y2026_GenAI_Hackathon_3rd_Edition.md` — submission rules, deadlines
3. `docs/prompt 2.txt` (architecture) + `docs/prompt1.txt` (problem analysis) — architectural invariants
4. `docs/prototype_version_plan.md` — version scope: what to build vs. defer
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
- **V2** — robustness innovations (transitive invalidation, settle barrier, absence read sets, claim grades, rebinder), each behind a `Config` feature flag defaulting OFF until enabled. Tag `v2-robust-recovery`.
- **V3** — multimodal (vision, evidence store); **V4** — hardening/adversarial timing sweeps + Docker.

Rules: never break a frozen version's interfaces (guard new behavior behind `Config` flags); the highest frozen version/tag is always the emergency-submission fallback; final submission needs git tag `PRISM_GENAI_HACKATHON_Y2026` on the judged commit. Stop conditions (when to halt a version and fall back) are in `docs/sonnet_implementation_plan.md` §13.

## Commands (once the toolchain exists — see `docs/sonnet_implementation_plan.md` §12 for build order)

Per the plan, this will be a Python project (`pyproject.toml`, deps: pydantic, pyyaml, pytest, pytest-asyncio) targeting **Python 3.10–3.12** — do not use 3.11+-only features (`ExceptionGroup`, `TaskGroup`, `Self`) since the Docker target is 3.11 but must also run on 3.10/3.12.

```bash
pytest                          # full suite
pytest tests/test_v0.py -v      # single version's scenario tests
docker build .                  # must build and run all tests from a clean checkout
```

Tests are deterministic by construction: `SteppedClock` (no real sleeps), `ScriptedProvider` (no live LLM calls), `MockToolRegistry`, and a seeded `IdGenerator`. `TraceChecker` runs after every test against the decision log; replay identity (two runs of the same input → byte-identical decision log) is a required check, not optional. Never claim untested work is verified — use "NOT VERIFIED" until tests actually pass.

## Session hygiene

- Inspect the repo before writing code — check what already exists and continue from it rather than assuming `currentStatus.md` is current.
- Complete and freeze one version before starting the next; do not expand scope into anything marked DEFERRED or CUT in `currentStatus.md` without being told to.
- Update `currentStatus.md` after meaningful work (new feature, bug fix, version freeze, blocker, architectural decision) — see the update rule at the bottom of that file for the exact fields to fill in.
