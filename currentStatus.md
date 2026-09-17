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
| **Current version** | PRE-V0 — no code exists |
| **MVP status** | NOT STARTED |
| **Git initialized** | NO |
| **Latest Git tag** | NONE |
| **Submission fallback** | NONE — no runnable version exists |

### Implemented
Nothing. Repository contains only `docs/` and `guidelines/`.

### Tested / Verified
Nothing.

### Failing / Broken
N/A — no code to fail.

### In Progress
- Implementation planning complete (`docs/sonnet_implementation_plan.md`)

### Deferred (per sonnet_implementation_plan.md §1.2)
- `kernel/perception.py` → V3
- `workers/vision.py`, `workers/asr.py`, `workers/framer.py` → V3
- Response frames → V3
- Evidence store, blobs → V3

### Cut
- `sim/explorer.py`, `sim/minimize.py` — adversarial PCT, delta-debugging
- Multiple clock models — build Model B only
- Production observability (Prometheus, telemetry spans)

### Known Risks
- Evaluation kit unreleased — wire format assumptions may be wrong
- 8 days to deadline (25 Sep 2026)
- No code exists yet

### Next Task
**Begin V0 implementation** — follow `docs/sonnet_implementation_plan.md` §5, Phases 1–9:
1. `git init` + `pyproject.toml` + directory scaffold
2. Core primitives: `canonical.py`, `ids.py`, `errors.py`, `config.py`
3. Domain models: `model/types.py`, `model/events.py`, `model/actions.py`
4. Storage: `store/facts.py`, `store/catalog.py`, `store/ledgers.py`, `store/session.py`
5. Adapters: `clock.py`, `codec.py`, `output_writer.py`
6. Kernel: `ordering.py`, `reducers.py`, `invalidation.py`, `commit.py`, `results.py`, `snapshot.py`, `emission.py`, `step.py`
7. Observability: `decision_log.py`
8. Sim: `harness.py`, `checker.py`
9. Tests: 7 injected-plan scenarios → freeze `v0-skeleton`

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
Implement V0 (Foundation + Deterministic Skeleton). Follow `docs/sonnet_implementation_plan.md` §5 Phases 1–9 in exact order.

### Files to read first
1. This file (`currentStatus.md`)
2. `docs/sonnet_implementation_plan.md` — §1 (repo structure), §2 (data models), §4 V0 section, §5 Phases 1–9

### Current blockers
None — ready to begin implementation.

### Commands to run before modifying anything
```bash
cd /home/adi/Desktop/Hackathons/Prism
ls -la              # confirm: only docs/ and guidelines/ exist
git status          # confirm: no git repo yet
```

### Important context
- No git repo initialized yet. First step is `git init`.
- No `src/`, `tests/`, `config/`, `pyproject.toml` exist yet.
- Python target: 3.11 in Docker, must also run on 3.10 and 3.12.
- Do NOT use Python 3.11+ features (`ExceptionGroup`, `TaskGroup`, `Self`).
- The evaluation kit is unreleased. Wire format is assumed. Build the codec adapter to be swappable.
- All timing via `ClockPort.now_us()`. Zero `time.time()` in `kernel/` or `store/`.

---

## Update Rule

**THIS FILE MUST BE UPDATED AFTER MEANINGFUL WORK.**

Update after: implementing a feature, fixing a bug, completing tests, changing version, freezing a version, discovering a blocker, changing priorities, making an architectural decision.

| Field | Value |
|---|---|
| **Last updated** | 2026-09-17 13:45 IST |
| **Current agent/task** | Initial setup — created implementation plan + currentStatus.md |
| **Latest meaningful change** | Created `docs/sonnet_implementation_plan.md` and `currentStatus.md` |
