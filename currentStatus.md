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
| **Current version** | V3 FROZEN — multimodal grounding |
| **MVP status** | DONE at V1; V2 and V3 strictly improve on it |
| **Git initialized** | YES |
| **Latest Git tag** | `v3-multimodal` (branch `release/v3`); `v2-robust-recovery`/`release/v2`, `v1-text-agent`/`release/v1`, `v0-skeleton` still intact |
| **Submission fallback** | `v3-multimodal` / `release/v3` — everything in V2 plus vision-grounded questions, claims, conflicts, and lease renewal, gated behind `vision_enabled` (default `False`, so V0-V2 behavior is bit-for-bit unchanged when it's off) |

### Implemented
V0 + V1 (see git history / `v0-skeleton`, `v1-text-agent` tags) plus full V2 per `docs/sonnet_implementation_plan.md` §4 VERSION 2 / §5 Phase 16. All five V2 flags now default `True` in `Config` (`config.py`) — construct `Config(flag=False)` to get V1 behavior back for comparison:
- **Transitive invalidation** (`transitive_invalidation`): consuming a result now writes `derived.<gid>.<name>` facts from the plan step's `output_map`, tagged with the call's own read set as `Provenance.derivation_read_set`. `kernel/invalidation.py`'s fixpoint loop (max 8 rounds) retracts a stale derived fact and cascades to whatever read *it*, so a 2- or 3-step chain (search → derived flight → seat map / price → book) cancels every downstream call in the same step a slot changes. `PlanExecutor._bind`'s `STEP_OUTPUT` binding prefers the derived fact over the raw `result.<call_id>` when this is on.
- **Settle barrier** (`settle_barrier_enabled`): `CommitGate` gains G10 (settle_ms since `session.last_eot_ts`) and G11 (no newer turn open) for writes — catches "book the 6pm one" → 200ms → "wait, the 8pm one" before the first booking ever goes out. New `store/ledgers.py` `TimerWheel` + `StepReport.next_wake_us` are a pure liveness hint (T-05) — no correctness depends on a timer firing; a blocked call is just re-evaluated every step like always.
- **Rebinder** (`rebinder_enabled`): `interpret_apply._can_rebind` — a slot change stays in `EXECUTING` instead of forcing a full replan when every affected step is FACT-bound to it and none has `structure_depends_on` including it (§8.4). Zero Planner round-trip for a purely local correction.
- **Absence read-sets** (`absence_read_sets`): `PlanStep.absence_keys` — a step can declare an optional fact it assumes is unset; `PlanExecutor._bind` adds an `ABSENT` read-set entry for it, so adding that fact later ("only direct flights") invalidates the call through the ordinary mechanism with no new machinery.
- **Claim grades** (`claim_grades_enabled`) + **reconciliation** (unconditional, not gated — a safety property): `FinalBody`/`SpeakBody.claim_grade` (UNDERSTOOD/INTENDED/RESULT/EFFECT_DONE); an INTENDED-graded utterance while a write sits behind G10; and `FastResponder._reconcile_completed_after_cancel` truthfully reports a write that completed (or whose outcome is unknown) despite being cancelled (W4, I-15).

V3 (multimodal grounding) per `docs/sonnet_implementation_plan.md` §4 VERSION 3 / §5 Phase 17, gated entirely behind `Config.vision_enabled` (default `False`) plus two new flat-timeout knobs (`evidence_align_window_ms`, `evidence_lease_ms`) — every new code path is a documented no-op when the flag is off, so V0-V2 are structurally unaffected (proved by all 36 V0-V2 tests passing unchanged):
- **New module `kernel/perception.py`** (`PerceptionScheduler`): the whole V3 lifecycle in one place — `create_or_retarget_question` (called from `kernel/interpret_apply.py` when an accepted interpretation's `visual_reference` is set — the only question-creation trigger implemented; QuickDetector-cue and plan-declared triggers from the full spec are deliberately not wired, see the module's own docstring), `decide` (frame alignment + VISION job dispatch, called from `kernel/step.py`'s DECIDE phase alongside `TaskStateMachine`), `on_perception_result` (claim acceptance — HIGH promotes to a `slot.<goal>.<name>` fact when no conflicting user value exists, MEDIUM/LOW never do), and `expire_leases` (a flat-timeout lease check run every step, gated no-op when vision is off, that retracts a stale `CURRENT_STATE` claim through the *ordinary* fact-retraction/invalidation path — no new cancellation machinery needed).
- **`store/ledgers.py` `EvidenceStore`**: observations (frames, always stored, analyzed only on demand), one active `Question` tracked per goal, one open `Conflict` tracked per `(goal, name)` — simplified from the full spec's per-question/multi-conflict bookkeeping (see `model/types.py`'s V3 section docstring for the exact list of cuts).
- **Conflicts** (`docs/prompt 2.txt` §9.1): a HIGH/MEDIUM perception claim that disagrees with an existing *user-sourced* slot value opens a `Conflict` instead of overwriting it; `kernel/executor.py`'s `PlanExecutor._bind` blocks any step bound to that slot (reusing the exact same "missing key → clarify" path V1 already had for unfilled slots — no new gate); `kernel/responder.py`'s `_clarify` recognizes an open conflict and names both candidate values instead of the generic template. A later user statement on that slot name voids the conflict (`interpret_apply._apply_slot_deltas`).
- **`$Q` plan-binding substitution**: `PlanStep.bindings` may reference `claim.$Q.<name>` the same way `$G` already substitutes the goal id — `PlanExecutor._bind` resolves it to the goal's current question id. Used specifically for `CURRENT_STATE` targets (so a lease-expiry retraction is recognizable by its `claim.` key prefix and triggers a silent re-fetch rather than an `_ask_for` clarify — see the scope-trim note in `executor.py`); `AT_UTTERANCE` targets bind through the ordinary `slot.$G.<name>` path instead, since that's what makes the conflict-blocking check apply.
- **`workers/vision.py`** (`VisionAnalyzer`): same `ScriptedProvider`-mocked worker pattern as Interpreter/Planner/Composer. The frame is passed by reference (`obs_id`) only — the existing `Provider.complete_json(kind, prompt, schema)` protocol is text-only (see `workers/gateway.py`'s `GeminiProvider`, added earlier this session for the same reason), so no test or live path here actually sends pixel data; a real image-grounded call would need a second, image-aware `Provider` method, out of scope.
- **New event `video_frame`** (`model/events.py`, `adapters/codec.py`): always stored as an `Observation` on arrival, matching §10.3 ("evidence is always stored"); relevance is decided later, only by an open question.

### Tested / Verified
`tests/test_v0.py` (8) + `tests/test_v1.py` (15) + `tests/test_v2.py` (13) + `tests/test_v3.py` (6: N-05, M-06, M-07, M-08, M-03, plus replay identity) — **42/42 passing**, `TraceChecker` 0 violations everywhere including the static no-wall-clock scan (which now also covers `kernel/perception.py`).

Run with: `cd /home/adi/Desktop/Hackathons/Prism && source .venv/bin/activate && python -m pytest tests/ -v`. `tests/conftest.py`'s shared `config` fixture pins all five V2 flags back to `False` for `test_v0.py`/`test_v1.py`; `test_v2.py`'s `v2_config(**overrides)` and `test_v3.py`'s `v3_config(**overrides)` each start every flag `False` (V3's defaults `vision_enabled=True`) and enable only what each scenario means to exercise.

Also verifiable interactively: `PYTHONPATH=src:. python demo/run_v0_demo.py` (V0-era raw trace), `demo/run_v1_demo.py` (real spoken ACK/CANCEL/FINAL), and `demo/run_v1_live_demo.py` (same scenario against the real Gemini API) — none updated to show V2's rebinder/settle/reconcile or V3's multimodal behavior specifically (optional, not blocking).

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
V4 not yet started.

### Deferred (per sonnet_implementation_plan.md §1.2, plus scope trims documented in module docstrings)
- V3 scope trims (see `model/types.py`'s V3 section docstring and `kernel/perception.py`'s module docstring for the full list): QuickDetector-cue and plan-declared question triggers (only the Interpreter "demand" trigger is wired); frame-count-based leases (flat timeout only); coalescing/watch-mode re-analysis of a newer frame while a job is in flight; multiple simultaneous open questions/conflicts per goal (one each, tracked directly); ASR (`workers/asr.py`, audio_mode handling) — not implemented at all; live pixel-grounded vision calls (the `Provider` protocol is text-only; `workers/vision.py` passes frames by reference only, same limitation `GeminiProvider` already has)
- Chunk-anchored speculative cancellation (QuickDetector value extraction is built but unwired — cues only, used for backchannel detection, and now also `visual_reference` per-chunk detection which V3 doesn't use either — see `kernel/perception.py`) → not planned before V4, low scoring value relative to effort
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
- Live LLM validation: `GeminiProvider` (`workers/gateway.py`) has been verified against the real Gemini API for the text-only INTERPRET/PLAN/COMPOSE path (`demo/run_v1_live_demo.py`) — but only text, since `Provider.complete_json` carries no image parameter. `workers/vision.py` has never been called against a real vision model; `AnthropicProvider`/`AsyncWorkerRunner` also remain untested against a live model.
- V3 shipped without ever exercising real pixel-grounded vision (see above) — every V3 test is `ScriptedProvider`-driven, matching how every prior version's tests work, per the plan's own "Mock first, live second" principle. A true live vision path would need a second, image-aware `Provider` method — not built.
- Deadline: 25 Sep 2026

### Next Task
**Begin V4 (Hardening + Packaging)** — follow `docs/sonnet_implementation_plan.md` §4 VERSION 4, §5 Phase 18:
1. `Dockerfile` — build and run all tests in the Docker target (Python 3.11), the first real verification of the 3.10–3.12 compatibility claim
2. `README.md` — reproducible setup
3. Targeted timing sweeps (`tests/test_v4.py`) on I-03, I-14, R-05 (±10ms offsets) — and worth adding equivalents for V3's lease-expiry/renewal timing (`test_m03_stale_frame_renewal`'s hand-tuned latencies), since that's the newest timing-sensitive logic
4. Full regression run across all versions; demo recording; PPT prep
5. Final freeze: `git tag v4-hardened`, `git tag PRISM_GENAI_HACKATHON_Y2026`

Optional, not blocking V4: update the demo scripts to show V2's rebinder/settle-barrier/reconcile and V3's multimodal behavior specifically (current demos predate both).

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
Implement V4 (Hardening + Packaging). Follow `docs/sonnet_implementation_plan.md` §4 VERSION 4, §5 Phase 18, on top of the frozen `v3-multimodal` tag / `release/v3` branch. Do not modify V0/V1/V2/V3 files' public interfaces or `Config` flag defaults — V4 is packaging/verification, not new kernel behavior; the only source changes expected are `Dockerfile`, `README.md`, and new timing-sweep tests.

### Files to read first
1. This file (`currentStatus.md`)
2. `docs/sonnet_implementation_plan.md` — §4 VERSION 4 section, §5 Phase 18
3. `docs/prototype_version_plan.md` for the original V4 scope/time-budget framing
4. `pyproject.toml` for the declared Python version range (`>=3.10,<3.13`) that the Dockerfile needs to target and actually verify for the first time

### Current blockers
None — V3 frozen and passing (42/42 tests), ready to begin V4. Nothing external is required to start (Docker build/test run, README, timing sweeps are all self-contained); live vision validation remains an open, non-blocking risk (see Known Risks).

### Commands to run before modifying anything
```bash
cd /home/adi/Desktop/Hackathons/Prism
git status                        # confirm working tree is clean at v3-multimodal
git log --oneline -12
source .venv/bin/activate && python -m pytest tests/ -v   # confirm all 42 tests still pass
```

### Important context
- Git repo initialized, V3 frozen at tag `v3-multimodal` / branch `release/v3`; `v2-robust-recovery`/`release/v2`, `v1-text-agent`/`release/v1`, `v0-skeleton` still intact as successively older emergency fallbacks.
- `src/`, `tests/`, `config/`, `pyproject.toml`, `demo/` all exist — see them before assuming anything is missing.
- Python target: 3.11 in Docker, must also run on 3.10 and 3.12. Dev/test venv here runs 3.14 (no 3.10–3.12 interpreter was available locally) — keep avoiding 3.11+-only syntax (`ExceptionGroup`, `TaskGroup`, `Self`) since it hasn't been verified against the real target versions. **The Docker build is V4's first chance to actually verify this claim** — treat any failure there as a real bug, not a fluke.
- The evaluation kit is unreleased. `adapters/codec.py` has a provisional wire schema, isolated behind that one module; `entry.py`'s `HarnessIO` is likewise a guess.
- All timing via `ClockPort.now_us()`. Zero `time.time()`/`datetime.now()`/etc. in `kernel/` or `store/` — enforced by `TraceChecker.check_static_no_wallclock`, run as part of every test.
- `TraceChecker` (`sim/checker.py`) has had real false-positive bugs before (see the V2 bug list above) — if a V4 test trips a checker violation, seriously consider whether the checker's assumption is wrong before assuming the kernel is.
- `DEFAULT_CONFIG` has all five V2 flags `True`; `vision_enabled` (V3) defaults `False` (deliberately — V3's tests each opt in via `v3_config`, unlike V2's flags which flipped the global default once frozen). Decide, if it matters for V4, whether to flip `vision_enabled` to `True` by default the way V2's flags were — not done yet, no test currently depends on it either way.
- Writing tests against the *real* kernel (not mocks) found 8 genuine bugs across V1+V2, and a real schema gap (Gemini's live `bindings` call) while wiring the live provider — keep doing that for V4's timing sweeps rather than trusting the implementation once it "looks right."

---

## Update Rule

**THIS FILE MUST BE UPDATED AFTER MEANINGFUL WORK.**

Update after: implementing a feature, fixing a bug, completing tests, changing version, freezing a version, discovering a blocker, changing priorities, making an architectural decision.

| Field | Value |
|---|---|
| **Last updated** | 2026-09-17 |
| **Current agent/task** | Wired a live Gemini provider into the V1 gateway, then implemented and froze V3 (Multimodal Grounding) |
| **Latest meaningful change** | Added `GeminiProvider` (`workers/gateway.py`, stdlib `urllib`, text-only) + `demo/run_v1_live_demo.py`, verified live against the real Gemini API (found and fixed a real schema gap: `PLAN_SCHEMA`'s `bindings` field had no shape description, so a live model couldn't produce valid ones). Then built full V3 (`kernel/perception.py` PerceptionScheduler, `store/ledgers.py` EvidenceStore, `workers/vision.py`, `video_frame` event + reducer, conflict detection/CLARIFY, `$Q` plan-binding substitution, lease expiry/renewal), all gated behind `Config.vision_enabled` (default `False`). Wrote and passed all 5 `tests/test_v3.py` scenarios (N-05, M-06, M-07, M-08, M-03) + replay identity (42/42 with V0-V2), zero regressions. Tagged `v3-multimodal` + branch `release/v3`. |
