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
| **Current version** | V1 FROZEN — interruptible text agent (MVP) |
| **MVP status** | DONE — V1 is the first submission-capable version |
| **Git initialized** | YES |
| **Latest Git tag** | `v1-text-agent` (branch `release/v1`); `v0-skeleton` still intact |
| **Submission fallback** | `v1-text-agent` / `release/v1` — full text agent with real LLM-worker interface (ScriptedProvider-driven in tests), turns, goals, interruption recovery |

### Implemented
V0 (see git history / `v0-skeleton` tag) plus full V1 per `docs/sonnet_implementation_plan.md` §4 VERSION 1 / §5 Phases 10–15:
- **Turn management**: `kernel/turns.py` (`TurnManager` — chunk buffering, prefix-digest fact so a job dispatched mid-turn is naturally invalidated by a later chunk), `kernel/detector.py` (`QuickDetector`, cue-detection only — chunk-anchored speculative cancellation is V2 scope), `config/lexicons.py`.
- **Goal/task state**: `kernel/task.py` (`TaskStateMachine` — the sole INTERPRET/PLAN/COMPOSE dispatcher, idempotent, called every step; allocates job_id and writes `JobRecord` itself during DECIDE so `FastResponder` can ACK in the same step), `kernel/interpret_apply.py` (interpretation → goal/fact mutations; slot deltas go through ordinary `FactStore.set`, so the unmodified V0 `InvalidationEngine` cancels dependents with no new mechanism). New store types: `GoalRegistry`, `PlanStore`, `TurnLog`, `JobTable` (`store/ledgers.py`), all wired into `SessionStore`/`StoreTxn`.
- **LLM workers** (`workers/`): `gateway.py` (`ModelGateway` + `ScriptedProvider`, used by every test; optional untested-here `AnthropicProvider`), `interpreter.py`/`planner.py`/`composer.py` (prompt-building only — response parsing is kernel-side in `kernel/proposals.py` per the package boundary), `runner.py` (`WorkerRunner` protocol, `ScriptedRunner` mirrors `MockToolRegistry`'s latency-then-deliver pattern, `AsyncWorkerRunner` for optional live use).
- **Execution**: `kernel/executor.py` (`PlanExecutor` — creates PROPOSED calls from ready plan steps via dependency+binding resolution with `$G` goal-id substitution; handles read/write retries; does **not** duplicate CommitGate's admission logic, reuses the unmodified V0 `scan_and_admit`).
- **Speech**: `kernel/responder.py` (`FastResponder` — ACK, CLARIFY, INFORM (blocked duplicate writes), FINAL; PROGRESS/HOLD are real architecture concepts not needed by V1's scenarios and are **not implemented**, not silently dropped), `config/templates.py`.
- **Integration**: `kernel/step.py` DECIDE phase now runs cancellation → TaskStateMachine → PlanExecutor → CommitGate → FastResponder (the architecture's fixed order); DISPATCH phase submits jobs TaskStateMachine requested. `kernel/reducers.py` handles `text_chunk`/`end_of_turn`/`interruption`/`worker_result`. `observability/watchdog.py` (the one permitted wall-clock read), `entry.py` (Runtime/run_scenario composition root for a real harness transport — unverified, no real kit IO contract exists yet).
- **Sim**: `sim/harness.py` now polls `ScriptedRunner` results the same way it polls `MockToolRegistry` results — a `WORKER_RESULT` can never land in the same step it was dispatched.

### Tested / Verified
`tests/test_v0.py` (8 tests) + `tests/test_v1.py` (15 tests: N-01..N-04, I-01, I-03, I-07, I-08/I-09, I-13, S-01..S-03, R-02, R-06, plus 5x replay identity) — **23/23 passing**, `TraceChecker` reports 0 violations on every test including the static no-wall-clock scan.

Run with: `cd /home/adi/Desktop/Hackathons/Prism && source .venv/bin/activate && python -m pytest tests/ -v` (venv has pydantic/PyYAML/pytest/pytest-asyncio; dev machine runs Python 3.14 — code avoids 3.11+-only syntax for the 3.10–3.12 Docker target but this is unverified on the actual target versions).

Also verifiable interactively: `PYTHONPATH=src:. python demo/run_v0_demo.py` (V0-era terminal trace of same-step cancellation; still valid, not yet updated to show V1's real ACK/CLARIFY/FINAL speech — see Next Task).

**Three real kernel bugs found and fixed while writing the V1 tests** (design review alone would not have caught these):
1. `PlanExecutor` treated a `CANCEL_REQUESTED` call as "still in progress" — a correction's replacement call didn't get proposed until the stale call's late result arrived, defeating same-step cancellation's purpose. Fixed: `CANCEL_REQUESTED` now gets an immediate fresh attempt.
2. Calls never included `goal.active`/`catalog.version` in their read set (architecture spec calls for this, §4.4) — so ABORT never invalidated in-flight calls. Fixed the read set; `_abandon_goal` also directly invalidates (goal.active must stay pointing at the abandoned goal so FastResponder can still find it to speak the final message), and `InvalidationEngine.cancellation_actions` now scans the ledger for any `INVALIDATED` call rather than only its own fact-diff's list.
3. `TraceChecker`'s own S3 and W1 checks had false positives (compared last snapshot against *final* state rather than state-as-of-that-step; flagged legitimate write retries as duplicates). Both fixed to check the real invariant.

### Failing / Broken
Nothing currently failing.

### In Progress
V2 not yet started.

### Deferred (per sonnet_implementation_plan.md §1.2, plus V1-specific scope trims — see module docstrings for each)
- `kernel/perception.py`, `workers/vision.py`/`asr.py`/`framer.py`, response frames, evidence store/blobs → V3
- Transitive invalidation, settle barrier, claim-typed emission, rebinder (localized re-binding instead of full replan) → V2
- Chunk-anchored speculative cancellation (QuickDetector value extraction is built but unwired — cues only) → V2
- Result reuse on `RETURN_TO_GOAL` (V1 always forces a fresh PLANNING pass, even when a retained result could be reused) → V2 rebinder territory
- PROGRESS/HOLD utterance kinds → not needed by V1 scenarios, not implemented
- Automatic worker-job abort on staleness (jobs are safely *rejected* via read-set validity on late delivery, just not proactively cancelled — acceptable correctness-wise, leaves some wasted compute) → optimization, not correctness

### Cut
- `sim/explorer.py`, `sim/minimize.py` — adversarial PCT, delta-debugging
- Multiple clock models — build Model B only
- Production observability (Prometheus, telemetry spans)
- YAML scenario format (`docs/sonnet_implementation_plan.md` §6.2) — V0/V1 tests are Python-native (`SimHarness.send()`/`drain()`) instead; the YAML format's `scripted_responses` section only makes sense once a real kit wire format exists to target

### Known Risks
- Evaluation kit unreleased — `adapters/codec.py`'s wire schema is a provisional guess, isolated behind that one module so it's cheap to replace; `entry.py`'s `HarnessIO` contract is likewise a guess
- Dev/test environment is Python 3.14 (no 3.10–3.12 interpreter available locally); Docker target is 3.11 — code avoids 3.11+-only syntax deliberately but this is unverified on the actual target versions
- No live LLM validation has been run (no API key in this environment) — `AnthropicProvider`/`AsyncWorkerRunner` are real but untested against a live model
- Deadline: 25 Sep 2026

### Next Task
**Begin V2 (Robust Recovery + Innovation Core)** — follow `docs/sonnet_implementation_plan.md` §4 VERSION 2, §5 Phase 16:
1. `kernel/invalidation.py` — transitive retraction (provenance graph walk, fixpoint loop)
2. `store/facts.py` — `derivation_read_set` support on derived facts
3. `kernel/commit.py` — settle barrier (G10, G11), timer liveness rule
4. `kernel/emission.py` — two-phase emission, claim grades
5. `kernel/executor.py` — rebinder (localized correction → re-bind without a full replan; also would let `RETURN_TO_GOAL` reuse retained results, closing the gap noted above)
6. `kernel/responder.py` — templates keyed by claim grade
7. `config.py` — flip V2 feature flags to `True`
8. `tests/test_v2.py` — 12 scenarios (I-04, I-05, I-10 through I-15, S-04, S-10, R-05, T-05); run, stabilize, freeze `v2-robust-recovery` + `release/v2` branch

Optional, not blocking V2: update `demo/run_v0_demo.py` (or add a `demo/run_v1_demo.py`) to show V1's actual spoken ACK/CLARIFY/FINAL output — the V0 demo still only shows the V0-era raw trace.

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
Implement V2 (Robust Recovery + Innovation Core). Follow `docs/sonnet_implementation_plan.md` §4 VERSION 2, §5 Phase 16, on top of the frozen `v1-text-agent` tag / `release/v1` branch. Do not modify V0/V1 files' public interfaces — every V2 change is guarded by a `Config` flag defaulting to the V1 value (guiding principle 4).

### Files to read first
1. This file (`currentStatus.md`)
2. `docs/sonnet_implementation_plan.md` — §4 VERSION 2 section, §5 Phase 16
3. The V1 source under `src/prism_rt/kernel/` (`invalidation.py`, `commit.py`, `emission.py`, `executor.py`, `responder.py` — all four get extended, none replaced) and `model/types.py` for the existing `CallRecord`/`Fact`/`ReadSet` shapes V2 builds on (e.g. `derivation_read_set` already has a field slot on `Provenance`, just unused so far)

### Current blockers
None — V1 frozen and passing (23/23 tests), ready to begin V2.

### Commands to run before modifying anything
```bash
cd /home/adi/Desktop/Hackathons/Prism
git status                        # confirm working tree is clean at v1-text-agent
git log --oneline -8
source .venv/bin/activate && python -m pytest tests/ -v   # confirm all 23 tests still pass
```

### Important context
- Git repo initialized, V1 frozen at tag `v1-text-agent` / branch `release/v1`; `v0-skeleton` still intact as the emergency fallback one level further back.
- `src/`, `tests/`, `config/`, `pyproject.toml`, `demo/` all exist — see them before assuming anything is missing.
- Python target: 3.11 in Docker, must also run on 3.10 and 3.12. Dev/test venv here runs 3.14 (no 3.10–3.12 interpreter was available locally) — keep avoiding 3.11+-only syntax (`ExceptionGroup`, `TaskGroup`, `Self`) since it hasn't been verified against the real target versions.
- The evaluation kit is unreleased. `adapters/codec.py` has a provisional wire schema, isolated behind that one module; `entry.py`'s `HarnessIO` is likewise a guess.
- All timing via `ClockPort.now_us()`. Zero `time.time()`/`datetime.now()`/etc. in `kernel/` or `store/` — enforced by `TraceChecker.check_static_no_wallclock`, run as part of every test.
- `TraceChecker` (`sim/checker.py`) itself had two false-positive bugs found and fixed during V1 (see "Tested / Verified" above) — if a V2 test trips a checker violation, seriously consider whether the checker's assumption is wrong before assuming the kernel is.
- V1 has three known, documented scope gaps that V2's rebinder is expected to close: no chunk-anchored speculative cancellation, no rebind-in-place (every correction forces a full replan), no retained-result reuse on `RETURN_TO_GOAL`.

---

## Update Rule

**THIS FILE MUST BE UPDATED AFTER MEANINGFUL WORK.**

Update after: implementing a feature, fixing a bug, completing tests, changing version, freezing a version, discovering a blocker, changing priorities, making an architectural decision.

| Field | Value |
|---|---|
| **Last updated** | 2026-09-17 20:40 IST |
| **Current agent/task** | Implemented and froze V1 (Interruptible Text Agent / MVP) |
| **Latest meaningful change** | Built full V1 (turns, goals, task state machine, LLM workers, executor, responder, integration), wrote and passed all 15 `tests/test_v1.py` scenarios (23/23 with V0), fixed 3 real kernel bugs + 2 checker false-positives found in the process, tagged `v1-text-agent` + branch `release/v1` |
