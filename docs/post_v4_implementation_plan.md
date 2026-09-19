# Post-V4 Implementation Plan — finishing the original design

**Status:** written 2026-09-18, after V4 froze. **Audience:** the implementing session (Sonnet). **Authority:** this sits below `docs/sonnet_implementation_plan.md` in the source-of-truth hierarchy but supersedes its V4-and-beyond sequencing, because it is written against two things that plan did not have: the official `guidelines/Theme_5_Guide.md` scoring framework and the actual submission rubric in `guidelines/Samsung_PRISM_Y2026_GenAI_Hackathon_3rd_Edition.md`.

---

## 1. Why this document exists

V0–V4 are built, tested (60/60), Docker-verified and frozen. The question asked was: does the project match the *original* design, and what does it take to get there. The short answer is that the **kernel** matches the original design closely, but the **surface area** does not — and a review against the two official guideline documents (which had not been read during the V0–V4 build) surfaced several gaps that are worth more than everything currently on the deferred list.

Two findings dominate and reorder every previous priority:

1. **30% of the evaluation scenarios are audio.** The Theme 5 guide states the public suite (9 scenarios) and hidden set (~60 scenarios) are both *"50% text, 30% audio, 20% visual."* This project has **zero** audio support — and worse, an `audio_clip` event currently raises `CodecError` (§4, F1/F2), which in `entry.py` is unguarded and would abort the scenario.
2. **The submission is judged on a different rubric than the kit's scoring framework**, and it has hard, stated disqualification conditions (deck, demo video, release tag, naming) that the repo does not yet satisfy.

Everything below is ordered by those findings, not by the order the architecture documents happen to list features in.

---

## 2. The two scoring systems (both matter, differently)

**A. Submission rubric** — decides the top-15 shortlist on 25 Sep (`Samsung_PRISM...md`, "Evaluation weighting"):

| Criterion | Weight | Where we stand |
|---|---|---|
| Working prototype & functionality | 30% | Strong kernel, but audio (30% of scenarios) unsupported; kit never run |
| Technical depth & feasibility | 25% | **Our strongest asset** — single-writer kernel, read-set invalidation, same-step cancellation, settle barrier, claim grades. Needs to be *articulated*, not just built |
| Innovation & originality | 20% | 2 of 9 selected innovations fully built (see §3.1) |
| Relevance to theme | 15% | Full-duplex + interruption is on-theme; the audio hole undercuts it |
| Presentation & documentation | 10% | README/measurements good; **no deck, no demo video, no architecture diagram** |

**B. Kit scenario scoring** — the Theme 5 guide's framework, applied per scenario by the (unreleased) kit; feeds criterion A.1:

| Category | Weight | Main lever we have not pulled |
|---|---|---|
| Task Completion | 40% | Response frames (C2) for grounded finals; commit-last ordering |
| Interruption Recovery | 35% | Chunk-anchored cancellation (cancel mid-utterance, not at EOT) |
| Response Latency | 15% | Speculative interpretation; content-bearing ACK |
| Safety & Protocol | 10% | Full 34-check invariant suite |

Plus a **0.80×–1.20× quality multiplier** on "transcript naturalness, truthfulness, relevance", and a **1.5× multiplier on multimodal scenarios** in the hidden set.

**Implication:** Interruption Recovery (35%) is worth more than twice Response Latency (15%). The previously-approved V5 plan targeted latency first. This plan reorders accordingly.

---

## 3. Original design vs. what exists

### 3.1 Innovation catalog (`docs/prompt 3.txt` §C1–C17; "Select" = the 9 chosen)

| ID | Innovation | Verdict in the original analysis | Built? |
|---|---|---|---|
| C1 | Inert-tail promotion of speculative interpretation | Select (M1) | ✗ none |
| C2 | Prepared response frames with typed holes | Select (M1) | ✗ none |
| C3 | Event-anchored reaction scheduling | Select (M1) | ✗ none |
| C5 | Provenance-closed invalidation + value-based early cutoff | Select (M2) | ◑ transitive invalidation built (V2); value-based cutoff not |
| C6 | Schema-complete read sets | Select (M2) | ◑ absence entries built (V2); read set still only covers bound params |
| C7 | Evidence leases | Select (M2) | ● built (V3, simplified to flat timeout) |
| C8 | Claim-typed atomic emission | Select (M2) | ◑ claim grades built (V2); overlay validation (CS-06) not |
| C9 | Commit-last ordering + settled commit barrier | Select (M3) | Built — settle barrier (V2), commit-last ordering (Phase 5) |
| C10 | Reference-bound write arguments | Select (M3) | ● built (V4) |
| C4, C11 | Planner bypass; effect verification probes | Reject | — (correctly skipped) |
| C12–C17 | Agreement confidence, demand-driven perception, dual-channel cross-check, EV-gated speculation, criticality scheduling, adversarial exploration | Defer / support / cut | ✗ (C13 partially: V3's demand trigger *is* C13) |

**Score: 2 of 9 fully, 4 partially, 3 not started.** The three untouched ones (C1, C2, C3) are all in "M1" — the milestone the original analysis ranked *first*.

### 3.2 Scenario coverage (`docs/theme05_implementation_blueprint.md` catalog — 64 scenarios)

Covered by a dedicated test (~36): N-01…N-05, I-01, I-03…I-05, I-07…I-15, R-02, R-05, R-06, R-08, S-01…S-06, S-10, M-03, M-06…M-08, T-01, T-05, T-08, plus partial T-02 (V4 sweeps).

**Not covered (27):**

| Group | IDs | Note |
|---|---|---|
| Audio | **N-06** (audio-only request), **M-01** (audio/transcript disagreement), **M-10** (audio self-repair) | Nothing exists. 30% of the hidden set. |
| Protocol robustness | **P-01** (malformed worker output), **P-02** (malformed input), P-03, P-04, P-05 | P-02 is the crash risk in §4/F2 |
| Result resilience | R-01, R-03, R-04, R-07, R-09 | Mostly handled by existing generic code, but untested |
| Tools | **S-07** (unseen tool), S-08 (manifest update), S-09 (compensation) | S-07 is *explicitly named* in the Theme guide's public-suite description |
| Task completion | N-02b (parallel reads + commit-last), I-02, I-06 (correction after FINAL) | Needs C9 |
| Multimodal | M-02 (partial transcript), M-04, M-05, M-09 | M-02 needs C1 |
| Timing/meta | T-03, T-04, T-06, **T-07** (watchdog) | Watchdog is wired in `entry.py` but **never exercised by any test** |

### 3.3 Module inventory gaps vs. the blueprint

`frames.py` (C2), `binder.py` (as a separate module — its logic lives inline in `kernel/executor.py`, which is fine), `workers/asr.py`, `timers.py` (exists as `TimerWheel` in `store/ledgers.py`, fine). `config/defaults.py` is an empty stub — the architecture's §20 "unknowns live in a PolicyRegistry" is realized as flat `Config` fields instead. That substitution is acceptable and not worth undoing.

---

## 4. Critical findings from this review

**F1 — Audio is unimplemented and un-ingestible.** `model/events.py`'s `PAYLOAD_TYPES` has no `audio_clip`. `adapters/codec.py` raises `CodecError` for any unknown `type`. `entry.py:96` calls `codec.decode(...)` with no `try/except`, so the first audio event ends the scenario. Verified by reading all three files.

**F2 — The codec is strict in a way that guarantees failure on any wire mismatch.** `HarnessCodec.decode` raises on: unknown event type, missing required field, **and any unknown field** (`unknown = [k for k in payload_dict if k not in field_names]`). The kit's wire format is an *unreleased guess*. If the real `text_chunk` carries so much as an extra `speaker` or `confidence` field, every scenario fails at event one. This single defect can take the entire prototype score to zero, and it is cheap to fix.

**F3 — Submission deliverables are missing and carry stated disqualification risk.** The rules require, in the tagged commit: working code + README + Docker (✓ have), **a presentation file named `CollegeName_TeamName`** (✗), and **everything referenced in the submission present in the tagged commit** — including the deck and the demo-video reference (✗). Plus a max-5-minute demo video (✗) and the release tag `PRISM_GENAI_HACKATHON_Y2026` (correctly not yet applied). "Teams NOT following the submission guideline would lead to direct disqualification" appears twice in the rules.

**F4 — The watchdog is never tested.** With a 120s wall-clock cap per scenario, the watchdog is what guarantees we emit *something* rather than time out silently. `ScenarioWatchdog` is referenced only in `entry.py`; `SimHarness` never wires it; no test covers T-07.

**F5 — Quality-multiplier exposure.** Up to ±20% rides on naturalness/truthfulness. Current user-facing text includes `"I need a bit more information — what should flight_id be?"` — which leaks a raw schema parameter name into speech — and a single invariant ACK string. Cheap to fix, disproportionate effect.

---

## 5. The plan

Each phase is independently freezable, flag-gated where it changes behavior, and ends with tests + a tag, exactly as V0–V4 did. **Keep that discipline** — it is what caught 8 real kernel bugs so far.

### Phase 0 — Submission safety net **(do first; small)**

Nothing here is code; it removes disqualification risk and can be done while other phases are in flight.

- `docs/submission/` — deck content as markdown, ready to export: theme ID, project title, team details, problem statement in own words, **architecture diagram**, tools/tech stack, innovation highlights, results (pull from `docs/measurements.md`), limitations (pull from `currentStatus.md` Known Risks). File named to the required `CollegeName_TeamName` convention once the team name is known.
- `docs/submission/demo_video_script.md` — a 5-minute shot list built on `demo/run_v4_demo.py` (already shows settle barrier, multimodal conflict, C10) plus the live Gemini demo.
- `README.md` — add theme ID, team details, deck link, demo-video link placeholder.
- `docs/submission/pre_tag_checklist.md` — the exact ordered steps for the final tag.

**Acceptance:** everything the Google Form asks for exists in-repo except the two external URLs (video, form).

### Phase A — Integration surface **(highest risk-reduction per hour; supersedes the original "Phase 1" scope below — see `/home/adi/.claude/plans/i-have-gemini-pro-declarative-phoenix.md` for the full writeup)**

Verifying the original Phase 1 scope surfaced a defect worse than "the codec is strict": **the only real entry point has never been executed and cannot work.** `entry.py`'s `run_scenario` is a plain synchronous function; the runner it wires in (`AsyncWorkerRunner`) dispatches jobs via `asyncio.ensure_future(...)`, which schedules a coroutine but never runs it unless something drives the event loop. Since `run_scenario` never `await`s anything, no worker job ever executes — the agent would ingest events and emit nothing, forever, until the watchdog times out with no salvage. Confirmed by grep: nothing in `src/`, `tests/`, or `demo/` has ever called `run_scenario` or `setup()`. Three defects, addressed together as Phase A:

1. **Async entry point is non-functional** — `entry.py` rewritten as `async def run_scenario(events: asyncio.Queue, actions: asyncio.Queue, ...)`, matching `guidelines/Theme_5_Guide.md` §3's stated contract ("two asynchronous queues") directly, with the worker runner actually scheduled on a running loop. Sync `HarnessIO` kept as a thin adapter so both invocation shapes work.
2. **Codec is strict** — make decode **tolerant**: unknown event type → return `[]` (recorded, not raised); unknown payload fields → ignored; missing optional fields → defaulted; alias map for plausible field-name variants (`call_id`/`id`, `ts_us`/`ts_ms`/`timestamp`/`ts`, `text`/`content`, `tools`/`manifest`); accept flat and `{"payload": {...}}`-nested shapes.
3. **Watchdog stops instead of salvaging** — implement §8.9: cancel in-flight reads, block new writes, emit a templated FINAL if the active goal has none yet. Wire `ScenarioWatchdog` into `sim/harness.py` too, so it's testable.

Plus: `audio_clip` added to `PAYLOAD_TYPES`/`EVENT_CLASS_BY_PAYLOAD_TYPE` (USER_CONTENT), store-only for now — degrades to "ignored input" instead of "crash". New tests: P-01, P-02, P-03, T-07, plus a format-variant suite (several plausible wire dialects through the same scenario) and a reference harness that drives the **real async entry point** end-to-end for the first time.

**Acceptance:** `demo/run_queue_harness.py` — the real async entry point, not `SimHarness` — actually emits ACK → TOOL_CALL → FINAL. A synthetic scenario containing unknown event types, unknown extra fields, malformed payloads and audio clips runs to completion and still emits correct actions for the parts it understands.

### Phase 2 — Audio path **(unlocks 30% of scenarios) — DONE, tag `v6-audio`**

Built per `docs/prompt 2.txt` §9.3 / §10.2, simplified in one place from what was originally scoped here — see `kernel/audio.py`'s module docstring for the exact trade-off:

- `Observation` (`model/types.py`) generalized with a `modality` field (`"frame"` | `"audio"`) and `asr_job_id`/`asr_done` tracking; `modality_seq` fixed to be per-modality (it was a global counter before — harmless while only frames existed, a real bug once audio coexists, caught while making this change). `kernel/perception.py._select_frame` updated to only ever consider `modality="frame"` observations — audio clips were otherwise eligible to be picked as a "frame" for vision analysis.
- `JobKind.ASR` + `workers/asr.py` (`ScriptedProvider`-mocked, same pattern as every other worker): returns `{segments: [{text, offset_us, end_us}], end_of_utterance}`. Wired into `workers/runner.py`.
- `Config.audio_mode` (`transcript_primary` | `audio_only` | `auto`, default `auto`) and `asr_closes_turn`; `asr_enabled` is the master gate (was a dead `False` field, now real).
- **New module `kernel/audio.py`** (`AsrScheduler`), same two-phase split as `PerceptionScheduler`: `decide()` dispatches an ASR job per unanalyzed audio observation (DECIDE phase, mirrors `PerceptionScheduler.decide`); `on_asr_result()` releases transcribed segments as ordinary text chunks via `TurnManager.on_chunk` (APPLY phase, mirrors `on_perception_result`).
- **Simplified `auto`-mode dedupe** (scope trim vs. the original plan here): instead of a timer-held dedupe window, the check happens *the moment the ASR result resolves* — if the open turn already has real chunks in it, the ASR output is discarded and an `asr.<obs_id>.disagreement` fact is recorded (M-01); otherwise it's released. Doesn't catch a text chunk arriving *after* the ASR result but still describing the same utterance; every `audio_only` scenario (no text ever arrives) is unaffected either way.
- **Real bug found and fixed while wiring `asr_closes_turn`**: closing a turn via `TurnManager.on_eot` alone does *not* trigger interpretation — the `session.pending_interpretation_turn` marker (and the settle-barrier timestamp, and the backchannel check) were bundled inside the `end_of_turn` *reducer*, not `TurnManager` itself. An audio-only scenario would close its turn and then dispatch nothing. Fixed by extracting that logic into a new `TurnManager.request_interpretation()` method both the ordinary `end_of_turn` reducer and the ASR-closes-turn path now call — one path for "what happens when a turn closes," not two that can drift.
- Self-repair text preserved verbatim (M-10) — nothing in this path strips or normalizes segment text.
- New tests (`tests/test_audio.py`, 5): N-06 (audio-only task completes end-to-end, no text chunks at all), a `transcript_primary`-never-dispatches-ASR sanity check, M-01 (disagreement discards ASR in favor of real text), M-10 (self-repair preserved verbatim), replay identity.

**Acceptance:** met — an audio-only scenario completes a task end-to-end (`test_n06_audio_only_request_completes_end_to_end`), verified via `assert_clean` (TraceChecker) and manually before the formal test existed.

### Phase 3 — Live multimodal provider **(prototype credibility + demo) — DONE, tag `v7-live-multimodal`**

Built. `Provider.complete_json` gained an optional `media: list[MediaPart] | None` argument (`workers/gateway.py`, `MediaPart(mime_type, data)`), additive so every existing text-only call site is unaffected. `GeminiProvider` sends real `inline_data` parts (already verified working for both images and audio earlier this project); `AnthropicProvider` accepts the parameter too and forwards image parts (Claude's Messages API has no audio-input modality at all — non-image media is deliberately skipped rather than sent malformed).

The kernel/store layer still never touches bytes, by design: `workers/runner.py` gained an optional `blob_resolver: Callable[[str], MediaPart | None]` (both `ScriptedRunner` and `AsyncWorkerRunner`), the *only* place in the codebase that resolves a harness-given reference into actual bytes — `kernel/perception.py`/`kernel/audio.py` now include `frame_id` (the harness's own reference, not this project's internal `obs_id`) in the dispatched view specifically so a live resolver has something to look up. `None` by default, so nothing about existing behavior changes unless a caller explicitly supplies one.

`demo/run_live_multimodal_demo.py`: a real synthesized PNG (pure Python, no dependency) and a real synthesized WAV tone (stdlib `wave`), both sent as actual bytes through the full pipeline. **Verified independently at the provider level** (a direct `GeminiProvider.complete_json(..., media=[...])` call with real image bytes, and separately with real audio bytes, both produced real, correct responses — confirmed earlier this session before this phase's code existed). The full end-to-end demo run itself hit the Gemini free tier's request quota (`HTTP 429 RESOURCE_EXHAUSTED`, "limit: 20") from the cumulative live calls made across this session's testing — a quota limit, not a defect; documented directly in the demo script's own docstring rather than hidden. The deterministic plumbing (blob resolution → `media` reaching `Provider.complete_json` correctly) is covered by 4 new tests in `tests/test_multimodal_media.py`, independent of any live model's availability.

**Acceptance:** partially met live (quota-limited, not code-limited — see above); fully met at the level this project can actually guarantee deterministically (`tests/test_multimodal_media.py`, all passing). Re-attempt the live end-to-end run once the quota window is clean, ideally without other live testing running concurrently.

### Phase 4 — Interruption-recovery depth **(35% category)** — DONE, tag `v8-chunk-anchor`

Cancel when the *correcting words are heard*, not at end-of-turn.

- `kernel/detector.py.QuickDetector.detect_values`: HIGH-confidence value extraction — schema enum match, and session-entity match (exact value already seen this session for exactly one slot name). Date/time and bare-number extraction (§9.2's other two extractors) are a **documented scope trim**, not built: this project's tool schemas never exercise unit/parameter disambiguation or a reference-date policy, and nothing depends on them.
- `hyp.<turn>.<name>` facts (`FactStatus.HYPOTHESIS`) are now written by `kernel/turns.py.TurnManager._detect_chunk_anchor` for every HIGH value found, regardless of context — bookkeeping/audit, visible in the decision log.
- Chunk-anchored cancellation implemented **without new cancellation machinery**: `store/session.py`'s `StoreTxn.mark_chunk_anchor(key)` feeds a slot's key into the same `changed_keys()` → `InvalidationEngine` pipeline a real fact change uses, without ever calling `facts.set()` on the slot itself — so the slot's committed value and digest are untouched, only its dependents (in-flight/proposed calls registered in `DependencyIndex`) get cancelled. Fires only when the HIGH value differs from the slot's current committed value (skipped if it already matches — a revert-safety check, same spirit as D4) and the chunk-anchor context holds (turn is an interruption, or the goal is active, or the chunk has a correction cue), matching `docs/prompt 2.txt` §8.2's precise rule.
- **Promotion (§11.4) is a documented no-op, not a scope trim to revisit later**: `PlanExecutor._bind`'s existing G4 check already refuses to bind a HYPOTHESIS-status fact as any call's input, so nothing in this implementation ever reads a `hyp.<turn>.<name>` fact as work to re-register — promotion only matters once Phase 6 (speculative interpretation) introduces work that reads hypotheses directly.
- New tests (`tests/test_phase4.py`, 5): chunk-level cancel (CANCEL emitted in the chunk's own step, before EOT — includes verifying the slot's committed value is untouched and a real, documented interaction where the immediately-retried call briefly re-binds to the still-uncommitted old value before the real EOT correction invalidates it too), a revert-safety no-op check, an inert-with-no-active-goal check, I-02 (interrupt during planning — proves the *existing* stale-read-set rejection already handles this, no new code needed), and replay identity.
- **I-06 (correction after FINAL) deliberately not built**: `emission.py._complete_goal` clears `goal.active` to `None` when FINAL is emitted, so a bare SLOT_UPDATE interpretation act after FINAL has no goal to attach to (`interpret_apply.py`'s `active_goal_id(store)` returns `None`) — verified by reading the code, not assumed. Real support needs either Interpreter-level disambiguation (does "actually Mumbai" refer to the just-completed goal, or is it unrelated?) or a "last completed goal, same intent" heuristic — both carry a real risk of silently reactivating the wrong goal, and neither is core to the chunk-anchor deliverable this phase is named for. Left for a future phase if scoring data ever shows I-06 matters.

**Acceptance:** met — for a mid-utterance correction, CANCEL is emitted in the step the chunk arrives, provably before end-of-turn (`test_chunk_anchor_cancels_in_flight_call_in_the_chunks_own_step` asserts the CANCEL and the still-unchanged committed slot value in the same step, with EOT sent only afterward).

### Phase 5 — Task-completion depth **(40% category)** — DONE, tag `v9-task-completion`

- **C2 response frames**: `JobKind.FRAME`, `workers/frame.py` (model writes a template with typed holes — success/empty/error branches, no result data ever reaches it), `kernel/frames.py.FrameScheduler` (dispatches the FRAME job the moment the plan's last call is *emitted*; `try_render`, called right after that call's result is consumed, selects a branch, evaluates each hole deterministically against the real result, type-checks by construction — any hole that fails to resolve aborts rendering — and writes `compose.<goal>.text` directly, the same fact Composer would write, so `kernel/responder.py`'s FINAL emission needed zero changes). Operator set per `docs/prompt 3.txt` C2: `field`, `count`, `min`/`max` by field, `first_k`, `exists`. Composer stays as the fallback whenever no valid frame exists (`kernel/task.py`'s EXECUTING branch skips the redundant COMPOSE dispatch only when `compose.<goal>.text` is already set). Gated behind `Config.frame_rendering_enabled` (default `False`). The frame's read set (goal facts/slots at dispatch time) is checked at render time via `Provenance.derivation_read_set`, so a correction drops it — verified directly (`tests/test_phase5.py`).
  - **Scope trim, documented in `kernel/frames.py`'s own module docstring**: only the `success`/`empty` branches are ever *selected* by this implementation. `error` is parsed/validated (the schema still requires it) but never chosen, because an error tool result routes a call to `CallStatus.FAILED`, not `CONSUMED` (`kernel/results.py`) — in this architecture a failed call is retried, not immediately terminal, and only becomes one via `PlanExecutor._fail_goal` once retries exhaust, a distinct goal-failure path this phase doesn't touch. A failed call simply never reaches `try_render`.
- **C9 commit-last ordering** in `kernel/executor.py.PlanExecutor._commit_last_blocked`: a WRITE step, even once its own `after` dependencies are satisfied, waits for every READ step in the plan that isn't downstream of it (a sibling/independent read) to resolve first — bounded by `Config.commit_last_wait_cap_ms` (default 3000) so a slow unrelated read can't block a write forever. Gated behind `Config.commit_last_ordering` (default `False`). Verified against N-02b directly: `book_flight` (after `search_flights` only) does not emit before an independent `get_weather` read resolves, with the flag on; emits promptly with it off; and the wait cap releases the write once it elapses even if `get_weather` still hasn't resolved.
- New tests (`tests/test_phase5.py`, 11): 4 for C9 (N-02b blocked/unblocked, wait-cap release, replay identity), 7 for C2 (grounded success-branch FINAL with no COMPOSE job ever dispatched, empty-branch selection, fallback to Compose when no frame rule exists, fallback when a hole fails to evaluate, flag-off behaves exactly as before, a frame invalidated by a correction is rejected at render time — a focused whitebox test constructing the stale-read-set race directly rather than relying on end-to-end timing, replay identity).

**Acceptance:** met — FINAL renders in the same step the last result arrives (no COMPOSE round-trip when a frame is valid), and every dynamic value in the rendered text is provably a hole evaluated on the consumed result (`test_frame_renders_final_grounded_in_real_result_without_compose` asserts the exact expected substitution).

### Phase 6 — Response latency **(15% category)** — DONE, tag `v10-response-latency`

Note: the plan file at `/home/adi/.claude/plans/i-have-gemini-pro-declarative-phoenix.md` only ever actually contained the *Phase A* plan (its own title says so) — the "speculative latency" design it was supposed to hold was never written down there. This phase's design was worked out fresh against `docs/prompt 2.txt` §11 (the speculative-execution class table and §11.3's coalescing algorithm) directly.

- **Speculative interpretation coalescing** (`kernel/task.py.TaskStateMachine._speculative_interpret`, `Config.speculative_interpretation_enabled`): while a turn is open, at most one INTERPRET job runs per turn, dispatched against the current prefix — coalesced exactly per §11.3 ("on a chunk: if no job is running, dispatch one; if one is running, record that the prefix has grown; when a job completes, if the prefix has grown and the turn is still open, dispatch one new job on the latest prefix"). The read-set subtlety the earlier plan draft flagged holds and was verified working: a speculative job's read set excludes `session.pending_interpretation_turn` (only a non-speculative, post-EOT dispatch includes it), or the turn later closing (which sets that fact) would self-invalidate the very job this mechanism exists to promote.
- **EOT promotion** (`kernel/turns.py.TurnManager._try_promote_speculative` / `_mark_eot_waiting_if_matching_job_in_flight`, `kernel/reducers.py._cache_speculative_interpretation`): a resolved speculative job is cached, not applied. At EOT, a cached result whose digest matches the final prefix is applied in the *same step* — zero additional model latency; a still-running matching job is waited for (via `txn.facts.is_valid(job.read_set)`, reusing the existing read-set machinery rather than a new comparison) instead of a redundant second dispatch. Anything else falls back to the unchanged non-speculative path.
- **Content-bearing ACK** (`kernel/responder.py.FastResponder._content_ack_text`, §9.3): names the HIGH-confidence value QuickDetector recorded for the triggering turn instead of the fixed generic string — covers a brand-new first utterance exactly as well as a correction, since Phase 4's `_detect_chunk_anchor` writes the hypothesis fact unconditionally regardless of goal existence (found via testing — an earlier docstring draft claimed a narrower scope before a test proved otherwise).
- **Real bug found and fixed during this build**: the speculative-result cache initially stored a digest computed from the job's `ReadSetEntry.digest` (itself already `compute_digest(normalize_value(fact.value))`) instead of the raw prefix-digest string the fact actually holds — a digest-of-a-digest that could never equal anything, so every promotion/coalescing comparison silently failed and every DECIDE-phase call re-dispatched a fresh speculative job (4 jobs where 1 was expected in a manual scratch run). Fixed by reading the fact's *current* value directly (already confirmed unchanged since dispatch by the read-set-validity check that runs earlier in the same code path) instead of digging into the read-set entry at all.
- **Not built (documented scope trim, not silently skipped)**: full inert-tail promotion (C1/M-02 — promoting across a trailing filler word like "please," which needs fuzzy prefix matching, not exact digest equality) and every other §11.1 speculative-execution class besides interpretation (speculative planning, speculative multimodal, the two OFF-by-default speculative-read classes). This phase's infrastructure (coalesced dispatch, caching, promotion) is the prerequisite inert-tail promotion would sit on top of.
- New tests (`tests/test_phase6.py`, 8): coalescing to one job per turn, zero-extra-job promotion at EOT, waiting for a matching in-flight job at EOT, fallback when the prefix grows past the cache, flag-off parity, content-ACK on a correction, content-ACK fallback to generic, replay identity.

**Acceptance:** met — real measured EOT→first-substantive-speech improvement in `docs/measurements.md`: on a T-06-shaped scenario (INTERPRET latency 300ms, chunk spacing 150ms, the blueprint's own numbers), TTFS(EOT) drops from 300,000µs to 0µs, a 100% reduction.

### Phase 7 — Safety, protocol and coverage breadth **(10% + technical-depth narrative)** — IN PROGRESS, not complete

- Expand `sim/checker.py` from ~5 checks to the blueprint's full **CS-01…CS-34** (`docs/theme05_implementation_blueprint.md` lines 2298–2331). Existing mapping: our P3≈CS-08, W1≈CS-13, S3≈CS-17 (partial), replay≈CS-22, static scan≈CS-21.
  - **Partial progress (a Gemini 3.1 Pro session, continuing a review thread after Sonnet's budget ran out, added 4 more checks on its own initiative rather than being asked to implement Phase 7 specifically — see currentStatus.md's Implemented section)**: CS-05 (CANCEL always precedes other emissions in a step), CS-09 (a cancelled call is never later CONSUMED), CS-14 (at most one write per lineage in flight at a time), CS-33 (CANCEL only ever targets an issued, unresolved call). The session's own claim of having "expanded to cover all traceable constraints from CS-01 to CS-34" was checked and found to overclaim significantly; corrected here.
  - **Phase 7's own first work session audited those 4 against the blueprint text** (see `sim/checker.py`'s module docstring for the full per-check finding): CS-09/CS-33 match closely; CS-05 checks a real invariant (fixed emission order) but isn't literally the blueprint's own CS-05 (step-level phase order, not stampable from current trace data — kept under the CS-05 slot as the closest real check available); CS-14 only covers the call-emission half of its invariant, not the effect-ledger-UNKNOWN half (no per-lineage effect history is retained to check that offline). Added **CS-27** (snapshot revisions non-decreasing) — new, sound, and unlike the round-3 checks shipped with a fault-injection test (`tests/test_checker_cs27.py`) proving it actually fires, not just "zero violations on the existing suite." **9 of 34 checks now implemented.** Investigated and explicitly deferred with reasons documented in `checker.py`'s docstring: CS-15 (gate rejections for blocked writes aren't recorded anywhere to check offline — needs new instrumentation), CS-16 (blueprint's "held"/"triage" terminology has no literal analog in this codebase), CS-26 (already genuinely enforced, just in `PlanExecutor._bind` rather than `CommitGate` as the blueprint states — an offline check is possible only against final evidence-ledger state, which has the same unsoundness CS-17 already documents), CS-30 (depends on inert-tail promotion, never built — explicitly deferred/CUT scope).
- Close the remaining untested scenarios: R-01, R-03, R-04, R-07, R-09, S-08, S-09, P-04, P-05, M-04, M-05, M-09, T-03, T-04 (S-07 is now covered, via the failure-honesty bugfix round).
  - **R-03 and S-08 closed** this session (`tests/test_phase7_scenario_coverage.py`). R-03 (out-of-order parallel-read results join correctly) passed on the first correct attempt — CS-11 (results matched only by call_id) already holds by construction, no positional matching anywhere in `ResultRouter`. S-08 (manifest update mid-plan) surfaced a real, verified, previously-undocumented behavior while building the test (not a bug — confirmed safe, but broader blast radius than the blueprint's one-line description implies): `kernel/executor.py`'s own comment states "every call's read set includes goal.active and catalog.version" *by design*, so **any** manifest update invalidates and immediately retries **every** in-flight READ call, not just ones referencing the specific tool that changed. This is safe for READs (idempotent, no double side effect) and is a deliberate conservative default, but means "in-flight result still routed" (the blueprint's stated outcome) actually plays out as "cancelled, retried, and the retry's result is what's routed" — worth being precise about in the submission's technical-depth narrative. R-01/R-09 (deferred, not attempted): both hinge on `CallStatus.STALE`/results-router branch 6 being reachable, which two independent review rounds already investigated (`reviews/gemini/`) and found effectively unreachable for a properly-registered in-flight call, since `InvalidationEngine` routes any invalidated in-flight call to CANCEL_REQUESTED (branch 4) first — building a test for R-01/R-09 as literally specified would need to first re-open that reachability question, not just write a scenario. S-09 needs a `modify_booking` compensation tool this project doesn't implement (see `docs/prompt 2.txt` §8.8's own note that this needs machinery not built). T-03 (PCT-randomized fuzzing across 500 schedules) is a separate testing-infrastructure investment, not a single scenario test. P-04's stated criteria ("snapshot keys ⊆ parameter names; types match schema; revisions monotonic") are partially covered incidentally: CS-17 and CS-27 already run against every test in the suite (covering the non-empty-digest and revision-monotonic halves), but the schema-membership/type-matching half isn't separately checked.

**Acceptance:** 34/34 checks run against every test (9/34 done); scenario coverage ≥ 55/64 (2 more closed this session).

### Phase 8 — Quality multiplier polish **(small, high leverage)**

- Rewrite user-facing templates so no raw schema parameter name ever reaches speech (F5), add the §9.3 digest-based deterministic phrasing variation, and hedge MEDIUM-confidence perception claims ("it looks like…").

**Acceptance:** no `config/templates.py` string can render a parameter identifier; repeated ACKs in one session differ deterministically.

---

## 6. Research and decisions still required

| # | Item | Owner | Why it matters |
|---|---|---|---|
| R1 | ~~Has the evaluation kit been released?~~ **RESOLVED 18 Sep: not released.** No portal link, no registration email, no drop found. Re-check periodically — nothing in the guide names a specific channel. | **User** | Highest-leverage unknown in the project, still unresolved in the sense that "not yet" isn't "never" — re-check before the 25 Sep freeze. Kit integration stays blocked until it lands; Phase A below hardens the codec/entry point to survive the guess being wrong either way. |
| R2 | Do audio scenarios ship a transcript alongside the WAV, or audio-only? | Resolved by R1 | Decides whether ASR is mandatory (audio-only) or a fallback (transcript-primary). Phase 2 is built to handle both, but the ordering of effort within it changes. |
| R3 | ~~Gemini free-tier audio input: shape and limits~~ **RESOLVED**: same `inline_data` mechanism as images, verified working directly (a synthesized WAV got a real, correct response). The free-tier request quota (`limit: 20`, exact window unclear from the error text) is tight enough to exhaust from this project's own cumulative live testing in one session — budget live-demo runs accordingly, don't chain them with other live calls. | — | Resolved during Phase 3. |
| R4 | Team name + college name | **User** | Required for deck nomenclature (`CollegeName_TeamName`) — a stated disqualification condition. |
| R5 | Demo video hosting (YouTube or Drive) | **User** | Link must be referenced from the tagged commit. |
| R6 | Which `Config` flags ship ON in the judged commit | Decide at final freeze | `vision_enabled` and `reference_bound_identifiers` currently default OFF. The judged run should almost certainly have them ON — but that decision needs one full green suite with the shipping configuration, not a flag flip at the last minute. |

---

## 7. Sequencing, and what to do if time runs short

Recommended order: **0 → A → 2 → 3 → 4 → 5 → 6 → 7 → 8**, inserting kit integration immediately after Phase 0 if R1 turns positive. **Phase A (`v5-integration`), Phase 2 (`v6-audio`), Phase 3 (`v7-live-multimodal`), Phase 4 (`v8-chunk-anchor`), Phase 5 (`v9-task-completion`), and Phase 6 (`v10-response-latency`) are done.** Next: Phase 7 (safety, protocol and coverage breadth, 10%-weighted category plus the submission rubric's technical-depth narrative).

Phases 0 and A were non-negotiable — one removes disqualification risk, the other removed a total-failure mode (the entry point that had never once been executed). Everything after that is additive scoring value, and each phase leaves the repo in a frozen, submittable state.

If time compresses, drop from the back: Phase 8 → 7. Phases 2-6 (audio, live multimodal, chunk-anchored cancellation, task-completion depth, response latency) are already done, so those trade-offs no longer apply — next up is Phase 7 (safety/protocol + coverage breadth), per the plan's own category-weight ordering.

---

## 8. Deliberately not planned

- **Kit wire-format integration** as speculative work — blocked until R1 resolves. Phase 1 makes the codec tolerant so the mismatch is survivable either way.
- **C3 event-anchored reaction scheduling** — an organizing principle over C1/C2/C13 whose value is mostly under clock Model A; this project committed to Model B. Revisit only if the released kit turns out to be Model A.
- **C11, C12, C14–C17** — rejected or deferred in the original innovation analysis itself; no reason to revisit.
- **A `PolicyRegistry`** replacing flat `Config` fields — pure refactor, zero scoring value.
