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
| **Current version** | V4 FROZEN — hardening + packaging |
| **MVP status** | DONE at V1; V2, V3, V4 strictly improve on it |
| **Git initialized** | YES |
| **Latest Git tag** | `v4-hardened` (branch `release/v4`); `v3-multimodal`/`release/v3`, `v2-robust-recovery`/`release/v2`, `v1-text-agent`/`release/v1`, `v0-skeleton` still intact |
| **Submission fallback** | `v4-hardened` / `release/v4` — everything in V3 plus reference-bound write identifiers (gated behind `reference_bound_identifiers`, default `False`), a verified Docker image, README, measurements.md, and timing-sweep tests |

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

V4 (Hardening + Packaging) per `docs/sonnet_implementation_plan.md` §4 VERSION 4 and `docs/prototype_version_plan.md`'s V4 section (see Deferred below for the V4 items intentionally *not* built):
- **C10 — reference-bound write identifiers** (`Config.reference_bound_identifiers`, default `False`): `docs/theme05_implementation_blueprint.md` §5.5. `PlanExecutor._bind` (`kernel/executor.py`) now refuses to bind a WRITE tool's identifier-like parameter (schema `enum`, `format: uuid`, or a name matching `*_id`/`*_code`/`*_number`/`*_ref`/`*_reference`) to a model-invented literal. Accepted origins: a consumed read result (`step_output`), a tool-derived fact (`provenance.source == "tool"`), a user-stated fact (`provenance.source == "user"`), or a literal matching verbatim text in a committed turn. A perception-sourced fact (V3) is deliberately *not* accepted — booking against a vision guess is exactly what this rule exists to prevent. A refused binding reuses the existing missing-slot clarify path (`_ask_for`) rather than a new gate.
- **Targeted timing sweeps** (`tests/test_v4.py`): I-03, I-14, and R-05 re-run at 5 nearby event-timing offsets each (5ms increments, +-10ms total spread) instead of one hand-picked delay, to prove the same invariants hold under small timing perturbation. R-05's sweep specifically checks what that scenario actually guarantees — deterministic, structurally clean resolution of the completion/cancellation race — not that either destination always "wins" it, since nothing in V0/V1 reopens an already-composed goal (a wrong assumption in the first draft of this sweep, caught by the test itself failing until the assertion was corrected to match the real invariant).
- **Dockerfile**: `python:3.11-slim`, installs the package + dev deps, runs the full suite by default. **Verified locally**: `docker build -t janus .` succeeds and `docker run --rm janus` passes all 60 tests on real Python 3.11 — the first actual verification of the 3.10-3.12 compatibility claim (dev environment here only has Python 3.14).
- **README.md**: reproducible setup, version/tag table, known limitations.
- **docs/measurements.md**: real numbers only (test coverage table; per-step kernel latency measured via `time.perf_counter()` around 44,200 `SimHarness` calls — mean 9.2us, p95 17.9us, well under the 5ms `step_budget_ms` telemetry target; an internal flag-off/flag-on comparison table in place of a true baseline, since the hidden eval kit that would provide one is unreleased).

### Tested / Verified
`tests/test_v0.py` (8) + `tests/test_v1.py` (15) + `tests/test_v2.py` (13) + `tests/test_v3.py` (6) + `tests/test_v4.py` (18: I-03/I-14/R-05 sweeps x5 each, C10 x3) — **60/60 passing**, `TraceChecker` 0 violations everywhere including the static no-wall-clock scan. Also verified on real Python 3.11 inside Docker (not just this machine's Python 3.14): 60/60 passing there too.

Run with: `cd /home/adi/Desktop/Hackathons/Prism && source .venv/bin/activate && python -m pytest tests/ -v`, or `docker build -t janus . && docker run --rm janus`. `tests/conftest.py`'s shared `config` fixture pins all five V2 flags back to `False` for `test_v0.py`/`test_v1.py`; `test_v2.py`'s `v2_config`, `test_v3.py`'s `v3_config`, and `test_v4.py`'s `v4_config` each start every V2-V4 flag at an explicit known baseline (V3's own default flips `vision_enabled` to `True`) and enable only what each scenario means to exercise.

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
Nothing in progress. All four planned versions (V0-V4) are built, tested, and frozen. Remaining work is optional polish (demo video, PPT, deferred V4 items below) or genuinely blocked (kit integration).

### Deferred (per sonnet_implementation_plan.md §1.2, plus scope trims documented in module docstrings)
- V4 items from `docs/prototype_version_plan.md`'s (higher-ranked) V4 section, deliberately not built this pass, in order of how much new machinery each would need:
  - **Kit wire-format integration** (codec mapping to the real kit, policy updates from kit answers, public suite validation) — genuinely blocked: the kit is unreleased. Nothing to build against yet.
  - **Inert-tail promotion (C1)** — needs a pre-EOT speculative-interpretation subsystem (prefix-digest-based INTERPRET dispatch before end-of-turn) that doesn't exist anywhere in this codebase yet; V1's "Known Limitations" already scoped this out and V2's rebinder only covers post-EOT corrections. A real new subsystem, not a targeted addition — judged too large relative to its scoring value for this pass.
  - **Enhanced TraceChecker (all 34 invariant checks from the blueprint)** — only P1, P3, S3, W1, plus the static no-wall-clock scan and replay identity (C1) are implemented; the blueprint's full invariant catalog is much larger. Every check that *is* implemented has caught real bugs (see the V1/V2 bug lists); the untried ones are an unquantified risk, not a known-safe gap.
  - **Demo video recording, PPT preparation** — outside what an agent session can produce; the demo scripts (`demo/run_v1_demo.py`, `demo/run_v1_live_demo.py`) are ready to record from.
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
- ~~Dev/test environment is Python 3.14...unverified on the actual target versions~~ — **resolved in V4**: `docker build -t janus . && docker run --rm janus` verified 60/60 tests passing on real Python 3.11.
- Live LLM validation: `GeminiProvider` (`workers/gateway.py`) has been verified against the real Gemini API for the text-only INTERPRET/PLAN/COMPOSE path (`demo/run_v1_live_demo.py`) — but only text, since `Provider.complete_json` carries no image parameter. `workers/vision.py` has never been called against a real vision model; `AnthropicProvider`/`AsyncWorkerRunner` also remain untested against a live model.
- V3 shipped without ever exercising real pixel-grounded vision (see above) — every V3 test is `ScriptedProvider`-driven, matching how every prior version's tests work, per the plan's own "Mock first, live second" principle. A true live vision path would need a second, image-aware `Provider` method — not built.
- V4 shipped without the kit wire-format integration, inert-tail promotion (C1), or the full 34-check `TraceChecker` — see Deferred above for why each was cut. None of these block a safe submission (V4's own 60 tests + all prior tags still pass); they're scoring upside left on the table, not correctness gaps in what shipped.
- `PRISM_GENAI_HACKATHON_Y2026` (the final submission tag) has **not** been applied yet — tagging it is a deliberate, explicitly confirmed action per the project's own versioning rule, not something to do automatically on freezing V4. `v4-hardened` is ready to be that tag whenever the user says so.
- Deadline: 25 Sep 2026

### Next Task
**All four planned versions (V0-V4) are complete.** What's left is optional and explicitly deferred (see above), or requires a decision only the team can make:
1. **Submission**: when ready, tag the judged commit `PRISM_GENAI_HACKATHON_Y2026` (currently `v4-hardened` on `main`/`release/v4` is the candidate) — confirm with the team first, this is a one-way "this is final" action.
2. Optional polish, roughly in order of value for effort: demo video recording (5 min, covering chain correction / settle barrier / visual lease / adversarial timing per `docs/prototype_version_plan.md`'s V4 demo script), PPT prep, updating `demo/run_v1_demo.py`/`run_v1_live_demo.py` to show V2/V3 behavior specifically (current demos predate both).
3. If there's still real time before the deadline and more scoring depth is wanted: pick one deferred V4 item (kit integration is blocked; inert-tail promotion (C1) and the full 34-check TraceChecker are both real, bounded pieces of work — see Deferred above for what each needs).

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
There is no mandatory next implementation task — V0 through V4 are all built, tested, and frozen. Read the updated Next Task section above and confirm with the team which optional item (if any) is wanted: submission tagging, demo recording, or a specific deferred V4 item. Do not pick a deferred item and start building it without checking — `PRISM_GENAI_HACKATHON_Y2026` in particular must never be tagged without explicit team confirmation that it's final.

### Files to read first
1. This file (`currentStatus.md`)
2. `README.md` for the outward-facing project summary and version table
3. `docs/measurements.md` for what's actually been measured so far
4. If picking up a deferred item: its entry in the Deferred section above names exactly what it needs

### Current blockers
None. `v4-hardened` on `main`/`release/v4` is a complete, tested, Docker-verified submission candidate.

### Commands to run before modifying anything
```bash
cd /home/adi/Desktop/Hackathons/Prism
git status                        # confirm working tree is clean at v4-hardened
git log --oneline -12
source .venv/bin/activate && python -m pytest tests/ -v   # confirm all 60 tests still pass
docker build -t janus . && docker run --rm janus          # confirm the Docker image still builds/passes
```

### Important context
- Git repo initialized, V4 frozen at tag `v4-hardened` / branch `release/v4`; `v3-multimodal`/`release/v3`, `v2-robust-recovery`/`release/v2`, `v1-text-agent`/`release/v1`, `v0-skeleton` still intact as successively older emergency fallbacks.
- `src/`, `tests/`, `config/`, `pyproject.toml`, `demo/`, `Dockerfile`, `README.md`, `docs/measurements.md` all exist — see them before assuming anything is missing.
- Python 3.10-3.12 compatibility is now actually verified (not just assumed) via the Docker build on 3.11 — see Known Risks for what's still unverified (live vision, the full 34-check TraceChecker, kit integration).
- The evaluation kit is unreleased. `adapters/codec.py` has a provisional wire schema, isolated behind that one module; `entry.py`'s `HarnessIO` is likewise a guess.
- All timing via `ClockPort.now_us()`. Zero `time.time()`/`datetime.now()`/etc. in `kernel/` or `store/` — enforced by `TraceChecker.check_static_no_wallclock`, run as part of every test.
- `TraceChecker` (`sim/checker.py`) has had real false-positive bugs before (see the V2 bug list above) — if a test trips a checker violation, seriously consider whether the checker's assumption is wrong before assuming the kernel is. The V4 timing sweeps also caught a *test*-authoring bug this way (the first draft of the R-05 sweep asserted an invariant the scenario doesn't actually guarantee) — the same "verify the assumption, not just the kernel" discipline applies to test code too.
- `DEFAULT_CONFIG` has all five V2 flags `True`; `vision_enabled` (V3) and `reference_bound_identifiers` (V4) both default `False` (deliberately — each version's own tests opt in explicitly via their `vN_config` helper, unlike V2's flags which flipped the global default once frozen). Whether to flip either to `True` by default is an open, low-stakes decision — nothing currently depends on it either way.
- Writing tests against the *real* kernel (not mocks) found 8 genuine bugs across V1+V2, a real schema gap (Gemini's live `bindings` call) while wiring the live provider, and a test-authoring bug (R-05 sweep, above) — keep doing that rather than trusting an implementation once it "looks right."

---

## Update Rule

**THIS FILE MUST BE UPDATED AFTER MEANINGFUL WORK.**

Update after: implementing a feature, fixing a bug, completing tests, changing version, freezing a version, discovering a blocker, changing priorities, making an architectural decision.

| Field | Value |
|---|---|
| **Last updated** | 2026-09-18 |
| **Current agent/task** | Implemented and froze V4 (Hardening + Packaging) — the last planned version |
| **Latest meaningful change** | Added C10 (reference-bound write identifiers, `kernel/executor.py`, gated behind `Config.reference_bound_identifiers`); `tests/test_v4.py` with +-10ms timing sweeps on I-03/I-14/R-05 (5 offsets each) plus 3 direct C10 tests, 18/18 passing (60/60 with V0-V3); wrote and verified a `Dockerfile` (`docker build && docker run` passes all 60 tests on real Python 3.11 — the first actual check of the declared 3.10-3.12 compatibility, previously only assumed); wrote `README.md` and `docs/measurements.md` (real per-step latency numbers measured via `time.perf_counter()`, not estimated). Deliberately deferred kit integration (blocked, kit unreleased), inert-tail promotion (C1, needs a new pre-EOT speculative subsystem), and the full 34-check TraceChecker — documented as scope cuts, not silent gaps. Tagged `v4-hardened` + branch `release/v4`. Did **not** tag `PRISM_GENAI_HACKATHON_Y2026` — that's a explicitly confirmed final-submission action. |
