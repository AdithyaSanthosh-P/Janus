# currentStatus.md — Agent Operational Memory

## What We Are Building

**Project name: Janus** (`pyproject.toml` distribution name `janus`; the Python package import path stays `prism_rt` — renaming that across the whole codebase wasn't worth the risk this close to the deadline, so `import prism_rt` is correct and intentional, not a leftover).

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
| **Current version** | V2 FROZEN — robust recovery + innovation core |
| **MVP status** | DONE at V1; V2 strictly improves on it |
| **Git initialized** | YES |
| **Latest Git tag** | `v2-robust-recovery` (branch `release/v2`); `v1-text-agent`/`release/v1` and `v0-skeleton` still intact |
| **Submission fallback** | `v2-robust-recovery` / `release/v2` — transitive invalidation, settle barrier, rebinder, absence read-sets, claim grades, reconciliation, all on by default |

### Implemented
V0 + V1 (see git history / `v0-skeleton`, `v1-text-agent` tags) plus full V2 per `docs/sonnet_implementation_plan.md` §4 VERSION 2 / §5 Phase 16. All five V2 flags now default `True` in `Config` (`config.py`) — construct `Config(flag=False)` to get V1 behavior back for comparison:
- **Transitive invalidation** (`transitive_invalidation`): consuming a result now writes `derived.<gid>.<name>` facts from the plan step's `output_map`, tagged with the call's own read set as `Provenance.derivation_read_set`. `kernel/invalidation.py`'s fixpoint loop (max 8 rounds) retracts a stale derived fact and cascades to whatever read *it*, so a 2- or 3-step chain (search → derived flight → seat map / price → book) cancels every downstream call in the same step a slot changes. `PlanExecutor._bind`'s `STEP_OUTPUT` binding prefers the derived fact over the raw `result.<call_id>` when this is on.
- **Settle barrier** (`settle_barrier_enabled`): `CommitGate` gains G10 (settle_ms since `session.last_eot_ts`) and G11 (no newer turn open) for writes — catches "book the 6pm one" → 200ms → "wait, the 8pm one" before the first booking ever goes out. New `store/ledgers.py` `TimerWheel` + `StepReport.next_wake_us` are a pure liveness hint (T-05) — no correctness depends on a timer firing; a blocked call is just re-evaluated every step like always.
- **Rebinder** (`rebinder_enabled`): `interpret_apply._can_rebind` — a slot change stays in `EXECUTING` instead of forcing a full replan when every affected step is FACT-bound to it and none has `structure_depends_on` including it (§8.4). Zero Planner round-trip for a purely local correction.
- **Absence read-sets** (`absence_read_sets`): `PlanStep.absence_keys` — a step can declare an optional fact it assumes is unset; `PlanExecutor._bind` adds an `ABSENT` read-set entry for it, so adding that fact later ("only direct flights") invalidates the call through the ordinary mechanism with no new machinery.
- **Claim grades** (`claim_grades_enabled`) + **reconciliation** (unconditional, not gated — a safety property): `FinalBody`/`SpeakBody.claim_grade` (UNDERSTOOD/INTENDED/RESULT/EFFECT_DONE); an INTENDED-graded utterance while a write sits behind G10; and `FastResponder._reconcile_completed_after_cancel` truthfully reports a write that completed (or whose outcome is unknown) despite being cancelled (W4, I-15).

### Tested / Verified
`tests/test_v0.py` (8) + `tests/test_v1.py` (15) + `tests/test_v2.py` (13: I-04, I-04-chain, I-05, I-10 through I-15, S-04, S-10, R-05, T-05, plus 5x replay identity) — **36/36 passing**, `TraceChecker` 0 violations everywhere including the static no-wall-clock scan.

Run with: `cd /home/adi/Desktop/Hackathons/Prism && source .venv/bin/activate && python -m pytest tests/ -v`. `tests/conftest.py`'s shared `config` fixture pins all five V2 flags back to `False` for `test_v0.py`/`test_v1.py` (they were written and timed against V1 semantics); `test_v2.py` has its own `v2_config(**overrides)` helper that starts every flag `False` and enables only what each scenario means to exercise — deliberately decoupled from whatever `DEFAULT_CONFIG` becomes later.

Also verifiable interactively: `PYTHONPATH=src:. python demo/run_v0_demo.py` (V0-era raw trace) and `demo/run_v1_demo.py` (real spoken ACK/CANCEL/FINAL) — neither updated yet to show V2's rebinder/settle/reconcile behavior specifically (optional, not blocking).

**Five more real kernel bugs found and fixed while writing the V2 tests** (on top of the three found during V1 — eight total across the project, all from writing tests against the real kernel, none from design review):
1. `PlanExecutor` treated `CONSUMED` as "done forever" for a step_key regardless of whether the facts it read had since changed — contradicting the architecture's own §3.5 carry-over rule ("a step carries over only while its resolved arguments are unchanged"). A corrected step never re-ran; everything downstream silently bound to pre-correction data. Fixed: both `propose_ready_calls` and `_step_done` now check the consumed call's read-set validity, not just its status.
2. The same bug's sibling: a `PROPOSED`-but-not-yet-admitted call (e.g. blocked behind settle) could also go stale before ever being emitted, and was left blocked forever failing G2 for the wrong reason. Fixed identically — replace it if its read set has gone stale, mark the old one `DISCARDED`.
3. `CANCEL_REQUESTED` calls blocked their own replacement's proposal until the stale call's late result formally resolved it (~800ms later in one repro) — defeating same-step cancellation's entire purpose. Fixed: `CANCEL_REQUESTED` now gets an immediate fresh attempt, uncounted against retry limits.
4. Calls never included `goal.active`/`catalog.version` in their read set (architecture spec §4.4 calls for this explicitly) — so ABORT never invalidated in-flight calls. Fixed the read set; `_abandon_goal` also directly invalidates (since `goal.active` must stay pointing at the abandoned goal so FastResponder can still speak the final "cancelled" message), and `InvalidationEngine.cancellation_actions` now scans the ledger for any `INVALIDATED` call rather than only what its own fact-diff pass found this step.
5. `TraceChecker`'s own S3 and W1 checks had false positives (compared a snapshot against *final* state instead of state-as-of-that-step; flagged legitimate write retries as duplicates). Both fixed to check the actual invariant.

(V1's three bugs are still documented in git history on the V1 freeze commit if needed — not re-listed here to keep this file from growing unbounded; see `git log --oneline` around the `v1-text-agent` tag.)

### Failing / Broken
Nothing currently failing.

### In Progress
V3 not yet started.

### Deferred (per sonnet_implementation_plan.md §1.2, plus scope trims documented in module docstrings)
- `kernel/perception.py`, `workers/vision.py`/`asr.py`/`framer.py`, response frames, evidence store/blobs → V3
- Chunk-anchored speculative cancellation (QuickDetector value extraction is built but unwired — cues only, used for backchannel detection) → not planned before V3/V4, low scoring value relative to effort
- Result reuse on `RETURN_TO_GOAL` (still forces a fresh PLANNING pass even when a retained result could be reused — the rebinder only covers in-place slot corrections on the *current* goal, not goal-switch-back) → possible V3/V4 polish, not currently planned
- PROGRESS/HOLD utterance kinds → not needed by any scenario tested so far, not implemented
- Automatic worker-job abort on staleness (jobs are safely *rejected* via read-set validity on late delivery, just not proactively cancelled — correct, just leaves a little wasted compute) → optimization, not correctness
- Literal two-phase emission (validate-all-then-write-all) — the existing per-action validate-then-write loop already gives the same guarantee for this system (no action's validation depends on another action emitted earlier in the same batch), so this would be a structural no-op; documented in `kernel/responder.py` rather than built

### Cut
- `sim/explorer.py`, `sim/minimize.py` — adversarial PCT, delta-debugging
- Multiple clock models — build Model B only
- Production observability (Prometheus, telemetry spans)
- YAML scenario format (`docs/sonnet_implementation_plan.md` §6.2) — all tests are Python-native (`SimHarness.send()`/`drain()`) instead; the YAML format's `scripted_responses` section only makes sense once a real kit wire format exists to target

### Known Risks
- Evaluation kit unreleased — `adapters/codec.py`'s wire schema is a provisional guess, isolated behind that one module so it's cheap to replace; `entry.py`'s `HarnessIO` contract is likewise a guess
- Dev/test environment is Python 3.14 (no 3.10–3.12 interpreter available locally); Docker target is 3.11 — code avoids 3.11+-only syntax deliberately but this is unverified on the actual target versions
- No live LLM validation has been run (no API key in this environment) — `AnthropicProvider`/`AsyncWorkerRunner` are real but untested against a live model
- V3 (multimodal) needs a real vision-capable API key and test fixture images — the first version with a genuine external dependency this project doesn't already have covered
- Deadline: 25 Sep 2026

### Next Task
**Begin V3 (Multimodal Grounding)** — follow `docs/sonnet_implementation_plan.md` §4 VERSION 3, §5 Phase 17:
1. `kernel/perception.py` — simplified perception scheduler
2. `store/ledgers.py` — extend with `EvidenceStore` (observations, questions, conflicts)
3. `workers/vision.py` — vision analyzer (real LLM vision call; `ScriptedProvider`-driven in tests exactly like the other workers)
4. `kernel/reducers.py` — handle `video_frame` events
5. `tests/test_v3.py` — 5 multimodal scenarios (N-05, M-03, M-06, M-07, M-08); run, stabilize, freeze `v3-multimodal` + `release/v3` branch

Before starting: confirm whether a vision-capable API key is available for at least manual/optional live validation (V3 is explicitly the first version with a real external dependency — see Known Risks). If not available, V3 can still be built and tested entirely against `ScriptedProvider` (matching how every prior version's tests work), same as the plan's own "Mock first, live second" principle — just flag that live vision has never actually been exercised.

Optional, not blocking V3: update the demo scripts to show V2's rebinder/settle-barrier/reconcile behavior specifically (current demos predate V2).

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
Implement V3 (Multimodal Grounding). Follow `docs/sonnet_implementation_plan.md` §4 VERSION 3, §5 Phase 17, on top of the frozen `v2-robust-recovery` tag / `release/v2` branch. Do not modify V0/V1/V2 files' public interfaces — this is the first version touching genuinely new event types (`video_frame`) rather than extending existing kernel modules, so the blast radius on existing code should be naturally small; keep it that way.

### Files to read first
1. This file (`currentStatus.md`)
2. `docs/sonnet_implementation_plan.md` — §4 VERSION 3 section, §5 Phase 17
3. `docs/prompt 2.txt` §10 (Multimodal architecture) for the AT_UTTERANCE/CURRENT_STATE question modes and confidence-band rules (HIGH/MEDIUM/LOW) this version needs to implement
4. `src/prism_rt/model/events.py` for the existing `VIDEO_FRAME`... actually check first whether a video_frame payload type exists yet in `EVENT_CLASS_BY_PAYLOAD_TYPE`/`PAYLOAD_TYPES` — V0/V1/V2 never needed one

### Current blockers
None — V2 frozen and passing (36/36 tests), ready to begin V3. V3's only real blocker is external: whether a vision-capable API key is available (see Known Risks) — but per "Mock first, live second," this doesn't need to block starting, only live validation.

### Commands to run before modifying anything
```bash
cd /home/adi/Desktop/Hackathons/Prism
git status                        # confirm working tree is clean at v2-robust-recovery
git log --oneline -12
source .venv/bin/activate && python -m pytest tests/ -v   # confirm all 36 tests still pass
```

### Important context
- Git repo initialized, V2 frozen at tag `v2-robust-recovery` / branch `release/v2`; `v1-text-agent`/`release/v1` and `v0-skeleton` still intact as successively older emergency fallbacks.
- `src/`, `tests/`, `config/`, `pyproject.toml`, `demo/` all exist — see them before assuming anything is missing.
- Python target: 3.11 in Docker, must also run on 3.10 and 3.12. Dev/test venv here runs 3.14 (no 3.10–3.12 interpreter was available locally) — keep avoiding 3.11+-only syntax (`ExceptionGroup`, `TaskGroup`, `Self`) since it hasn't been verified against the real target versions.
- The evaluation kit is unreleased. `adapters/codec.py` has a provisional wire schema, isolated behind that one module; `entry.py`'s `HarnessIO` is likewise a guess.
- All timing via `ClockPort.now_us()`. Zero `time.time()`/`datetime.now()`/etc. in `kernel/` or `store/` — enforced by `TraceChecker.check_static_no_wallclock`, run as part of every test.
- `TraceChecker` (`sim/checker.py`) has had real false-positive bugs before (see the V2 bug list above) — if a V3 test trips a checker violation, seriously consider whether the checker's assumption is wrong before assuming the kernel is.
- `DEFAULT_CONFIG` now has all five V2 flags `True`. If V3 needs its own comparably-scoped test isolation, follow the same pattern `tests/test_v2.py` set: a local `v3_config(**overrides)` helper (or similar) that starts flags at an explicit known baseline rather than trusting whatever the dataclass defaults happen to be by the time V3 lands — this is what kept `test_v0.py`/`test_v1.py` stable through the V2 default flip with zero changes to their own logic.
- Writing tests against the *real* kernel (not mocks) found 8 genuine bugs across V1+V2 that design review alone missed — keep doing that for V3 rather than trusting the implementation once it "looks right."

---

## Update Rule

**THIS FILE MUST BE UPDATED AFTER MEANINGFUL WORK.**

Update after: implementing a feature, fixing a bug, completing tests, changing version, freezing a version, discovering a blocker, changing priorities, making an architectural decision.

| Field | Value |
|---|---|
| **Last updated** | 2026-09-17 22:10 IST |
| **Current agent/task** | Implemented and froze V2 (Robust Recovery + Innovation Core) |
| **Latest meaningful change** | Built full V2 (transitive invalidation, settle barrier + timer liveness, rebinder, absence read-sets, claim grades, reconciliation), wrote and passed all 12 `tests/test_v2.py` scenarios + replay identity (36/36 with V0+V1), found and fixed 5 more real kernel bugs in the process (8 total across the project), flipped `DEFAULT_CONFIG`'s V2 flags to `True` and fixed the resulting test-isolation gaps, tagged `v2-robust-recovery` + branch `release/v2` |
