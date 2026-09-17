# currentStatus.md — Agent Operational Memory

## What We Are Building

Samsung PRISM Theme 05: **Interruptible Real-Time Agents** — a full-duplex conversational agent scored on Task Completion (40%), Interruption Recovery (35%), Response Latency (15%), Safety & Protocol (10%). Deadline: **25 Sep 2026**.

Architecture: single-writer kernel processing timestamped events in deterministic batches, with async LLM workers, versioned fact store, read-set validation, and same-step cancellation.

---

## Read Order

| # | File | Purpose | When to Read |
|---|---|---|---|
| 1 | `currentStatus.md` | **Current project state + handoff** | Always read FIRST |
| 2 | `guidelines/Theme_5_Guide.md` | Official evaluation spec & scoring | Before any design decisions |
| 3 | `guidelines/Samsung_PRISM_Y2026_GenAI_Hackathon_3rd_Edition.md` | Hackathon rules, all themes, submission format | For submission/format questions |
| 4 | `docs/prompt1.txt` | Pre-architecture problem analysis | When understanding WHY the architecture exists |
| 5 | `docs/prompt 2.txt` | System architecture (single-writer kernel, invariants) | For architectural decisions |
| 6 | `docs/prompt 3.txt` | Innovation analysis (17 weaknesses + fixes) | When implementing V2 innovations |
| 7 | `docs/prototype_version_plan.md` | Staged V0→V4 strategy, subsystem classification, time budgets | For version scope/priority decisions |
| 8 | `docs/theme05_implementation_blueprint.md` | Full data models, interfaces, file specs (190KB) | Reference during coding — look up specific sections, do NOT read cover-to-cover |
| 9 | `docs/sonnet_implementation_plan.md` | **Executable coding plan** — repo structure, coding order, tests, interfaces | Primary guide during implementation |

---

## Source of Truth Hierarchy

When documents conflict, higher rank wins:

1. **`guidelines/Theme_5_Guide.md`** — official spec, scoring, interface contract
2. **`guidelines/Samsung_PRISM_Y2026_GenAI_Hackathon_3rd_Edition.md`** — submission rules, deadlines
3. **`docs/prompt 2.txt`** (architecture) + **`docs/prompt1.txt`** (analysis) — architectural invariants
4. **`docs/prototype_version_plan.md`** — version scope, what to build vs defer
5. **`docs/sonnet_implementation_plan.md`** — coding plan, file structure, interfaces
6. **`docs/theme05_implementation_blueprint.md`** — detailed reference for data models
7. **`currentStatus.md`** — current project state only; does NOT override any of the above

---

## Current State

| Field | Value |
|---|---|
| **Current version** | V0 FROZEN — deterministic kernel skeleton |
| **MVP status** | NOT STARTED (MVP is V1) |
| **Git initialized** | YES |
| **Latest Git tag** | `v0-skeleton` |
| **Submission fallback** | `v0-skeleton` — no LLM, no turns, but the full kernel loop runs and is tested |

### Implemented
Full V0 per `docs/sonnet_implementation_plan.md` §4/§5 Phases 1–9, under `src/prism_rt/`:
- **Core primitives**: `canonical.py` (canonical JSON + digest + ABSENT), `ids.py` (deterministic per-session `IdGenerator`), `errors.py`, `config.py` (`Config`/`DEFAULT_CONFIG` with all V0–V3 feature flags).
- **Domain models** (`model/`): `types.py` (all enums + `Fact`/`ReadSet`/`CallRecord`/`EffectRecord`/`GoalRecord`/`Snapshot`), `events.py` (`Envelope` + payloads + `EVENT_CLASS_BY_PAYLOAD_TYPE`), `actions.py` (`Action`/`IntendedAction` + bodies).
- **Storage** (`store/`): `facts.py` (`FactStore` + `DependencyIndex`, digest-based validity, no-op-safe reversion), `catalog.py` (`ToolCatalog` — per-tool quarantine, mutability defaulting), `ledgers.py` (`CallLedger`, `EffectLedger`), `session.py` (`SessionStore` + `StoreTxn`/`MutationGuard` — the single write gate, K2/K3).
- **Adapters** (`adapters/`): `clock.py` (`SteppedClock`, Model B only), `codec.py` (`HarnessCodec` — provisional wire schema, isolated behind this one module), `output_writer.py` (`BufferOutputWriter`).
- **Kernel** (`kernel/`): `ordering.py`, `reducers.py` (manifest, tool_result), `invalidation.py` (`InvalidationEngine`, non-transitive), `commit.py` (`CommitGate` G1–G9), `results.py` (`ResultRouter`, 7 branches), `snapshot.py` (`SnapshotProjector`), `emission.py` (`EmissionGate`, CANCEL→SPEAK→TOOL_CALL→CLARIFY→FINAL order), `step.py` (`Kernel` — the 7-phase step engine, with a test-only `inject` hook into the step's own txn for V0 plan injection).
- **Observability**: `decision_log.py` (`DecisionLogger`, JSONL).
- **Sim**: `harness.py` (`SimHarness`, `MockToolRegistry`), `checker.py` (`TraceChecker` — T1 static AST scan, P1/P3/W1/S3 trace checks, C1 replay-identity).

### Tested / Verified
`tests/test_v0.py` — 8 tests (7 acceptance scenarios + replay identity), all passing:
happy-path read→consume→snapshot; same-step slot-correction cancel + re-issue (P3); write CommitGate admission + duplicate-fingerprint block + effect CONFIRMED (G1–G9); late result after cancel → `COMPLETED_AFTER_CANCEL`, not consumed (W4); unknown call_id ignored; duplicate result delivery ignored; malformed manifest → bad tool quarantined, good tool usable; 10x replay produces byte-identical decision logs (C1). `TraceChecker` reports 0 violations on every test, including the static no-wall-clock scan over `kernel/` and `store/`.

Run with: `cd /home/adi/Desktop/Hackathons/Prism && source .venv/bin/activate && python -m pytest tests/ -v` (venv has pydantic/PyYAML/pytest/pytest-asyncio installed; dev machine runs Python 3.14 — code itself avoids 3.11+-only syntax so it stays valid for the 3.10–3.12 Docker target, but this has not been verified by actually running on 3.10–3.12).

### Failing / Broken
Nothing currently failing.

### In Progress
V1 not yet started.

### Deferred (per sonnet_implementation_plan.md §1.2)
- `kernel/perception.py` → V3
- `workers/vision.py`, `workers/asr.py`, `workers/framer.py` → V3
- Response frames → V3
- Evidence store, blobs → V3
- Turn management, task state machine, LLM workers, FastResponder → V1 (not V0 scope)

### Cut
- `sim/explorer.py`, `sim/minimize.py` — adversarial PCT, delta-debugging
- Multiple clock models — build Model B only
- Production observability (Prometheus, telemetry spans)

### Known Risks
- Evaluation kit unreleased — `adapters/codec.py`'s wire schema is a provisional guess, isolated behind that one module so it's cheap to replace
- Dev/test environment is Python 3.14 (no 3.10–3.12 interpreter available locally); Docker target is 3.11 — code avoids 3.11+-only syntax deliberately but this is unverified on the actual target versions
- Deadline: 25 Sep 2026

### Next Task
**Begin V1 implementation** — follow `docs/sonnet_implementation_plan.md` §5, Phases 10–15:
1. Turn management: `kernel/turns.py` (`TurnManager`), `kernel/detector.py` (`QuickDetector`), populate `config/lexicons.py`
2. Task state machine: `kernel/task.py` (`TaskStateMachine` + goal lifecycle + triage), extend `store/ledgers.py` with `GoalRegistry`/`PlanStore`/`TurnLog`
3. LLM workers: `workers/gateway.py` (`ModelGateway` + `ScriptedProvider`), `workers/runner.py`, `workers/interpreter.py`, `workers/planner.py`, `workers/composer.py`
4. `kernel/interpret_apply.py`, `kernel/executor.py` (`PlanExecutor`), `kernel/responder.py` (`FastResponder`), populate `config/templates.py`
5. Integration: extend `kernel/step.py` decide phase and `kernel/reducers.py` for text_chunk/end_of_turn/interruption/worker_result, `observability/watchdog.py`, `entry.py`
6. `tests/scenarios/*.yaml` (15 files) + `tests/test_v1.py`, run, stabilize, freeze `v1-text-agent` + `release/v1` branch

Note: V0's `Kernel.step(batch, inject=...)` hook was a deliberate, documented V0-only mechanism for injecting plans directly into a step's txn (see `kernel/step.py` docstring) — V1 should route real plan/interpretation creation through reducers driven by worker results instead of relying on `inject`.

---

## Agent Instructions

1. **Read `currentStatus.md` first.** Then read `sonnet_implementation_plan.md` for the task at hand.
2. **Inspect the repo before writing code.** Check what exists. Continue from existing work.
3. **One version at a time.** Complete V(N) and freeze before starting V(N+1).
4. **Never break a frozen version.** After `git tag vN-*`, those files and tests must keep passing.
5. **Do not expand scope.** If it's marked DEFERRED or CUT, leave it unless explicitly told otherwise.
6. **Run tests after changes.** All existing tests must pass before committing.
7. **Deterministic tests only.** Use `SteppedClock` + `ScriptedProvider` + `MockToolRegistry`. No real sleeps, no live LLM calls in tests.
8. **Do not claim untested work is verified.** Use NOT VERIFIED until tests pass.
9. **Preserve architectural invariants:** single-writer kernel, no wall-clock in core, read-set validation at emission, same-step cancellation.
10. **Always maintain a working fallback.** The highest frozen version tag is the emergency submission.
11. **Update `currentStatus.md` after meaningful work.** See update rule below.

Full details: `docs/sonnet_implementation_plan.md` (§3 invariants, §4 version plans, §13 stop conditions).

---

## For the Next Agent

### What to do next
Implement V1 (Interruptible Text Agent / MVP). Follow `docs/sonnet_implementation_plan.md` §5 Phases 10–15 in exact order, on top of the frozen `v0-skeleton` tag. Do not modify V0 files' public interfaces — extend `kernel/step.py`'s decide phase and `kernel/reducers.py`'s dispatch table additively.

### Files to read first
1. This file (`currentStatus.md`)
2. `docs/sonnet_implementation_plan.md` — §4 VERSION 1 section, §5 Phases 10–15
3. The V0 source under `src/prism_rt/` (especially `kernel/step.py`, `model/types.py`) to see the actual interfaces already built, which may differ in small ways from the plan's pseudocode (documented as deviations in module docstrings, e.g. `HarnessCodec.decode` takes `seq` explicitly, `Kernel.step` takes an `inject` test hook)

### Current blockers
None — V0 frozen and passing, ready to begin V1.

### Commands to run before modifying anything
```bash
cd /home/adi/Desktop/Hackathons/Prism
git status                        # confirm working tree is clean at v0-skeleton
git log --oneline -5
source .venv/bin/activate && python -m pytest tests/ -v   # confirm V0's 8 tests still pass
```

### Important context
- Git repo initialized, V0 frozen at tag `v0-skeleton`.
- `src/`, `tests/`, `config/`, `pyproject.toml` all exist — see them before assuming anything is missing.
- Python target: 3.11 in Docker, must also run on 3.10 and 3.12. Dev/test venv here runs 3.14 (no 3.10–3.12 interpreter was available locally) — keep avoiding 3.11+-only syntax (`ExceptionGroup`, `TaskGroup`, `Self`) since it hasn't been verified against the real target versions.
- The evaluation kit is unreleased. `adapters/codec.py` has a provisional wire schema, isolated behind that one module.
- All timing via `ClockPort.now_us()`. Zero `time.time()`/`datetime.now()`/etc. in `kernel/` or `store/` — enforced by `TraceChecker.check_static_no_wallclock`, run as part of every V0 test.

---

## Update Rule

**THIS FILE MUST BE UPDATED AFTER MEANINGFUL WORK.**

Update after: implementing a feature, fixing a bug, completing tests, changing version, freezing a version, discovering a blocker, changing priorities, making an architectural decision.

| Field | Value |
|---|---|
| **Last updated** | 2026-09-17 19:15 IST |
| **Current agent/task** | Implemented and froze V0 (Foundation + Deterministic Skeleton) |
| **Latest meaningful change** | Built the full V0 kernel (`src/prism_rt/`: core primitives, domain models, storage, adapters, kernel step engine, observability, sim harness + trace checker), wrote and passed all 8 `tests/test_v0.py` tests, tagged `v0-skeleton` |
