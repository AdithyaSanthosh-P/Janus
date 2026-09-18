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
| C9 | Commit-last ordering + settled commit barrier | Select (M3) | ◑ settle barrier built (V2); commit-last ordering not |
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

### Phase 3 — Live multimodal provider **(prototype credibility + demo)**

Currently `Provider.complete_json` is text-only, so `workers/vision.py` passes frames *by reference* and has never seen a pixel. This is the single biggest "is it really working?" hole for the jury.

- Extend the `Provider` protocol with an optional `media` argument (`list[{mime_type, data}]`, default `None`) — additive, so `ScriptedProvider`/`AnthropicProvider` are unaffected.
- `GeminiProvider`: send `inline_data` parts (already verified working for images earlier in this project; audio uses the same shape).
- `workers/vision.py` and `workers/asr.py` pass real blobs when available.
- `demo/run_live_multimodal_demo.py`: a real PNG + a real interruption, answered by the live model.

**Acceptance:** the live demo answers a question about a real image and transcribes a real WAV clip. Tests stay 100% scripted.

### Phase 4 — Interruption-recovery depth **(35% category)**

Cancel when the *correcting words are heard*, not at end-of-turn.

- `kernel/detector.py`: add HIGH-confidence **value** extraction (schema enum match, unambiguous date/number, value already seen for that slot this session) — the module's own docstring already describes this as deliberately deferred.
- Write `hyp.<turn>.<name>` facts with `FactStatus.HYPOTHESIS` (the enum member exists and is currently **never written by anything** — verified by grep).
- Chunk-anchored cancellation: a HIGH hypothesis that differs from a slot an in-flight call depends on invalidates that call **in the same step as the chunk** (`docs/prompt 2.txt` §8.1 CHUNK row). Hypotheses never touch committed facts and never gate writes.
- Promotion (§11.4): when the turn's interpretation commits, re-register hypothesis-dependent work against the committed fact iff digests match.
- New tests: chunk-level cancel (CANCEL emitted in the chunk's step, before EOT), I-02, I-06.

**Acceptance:** for a mid-utterance correction, CANCEL is emitted in the step the chunk arrives — measurably earlier than today's EOT-anchored cancel.

### Phase 5 — Task-completion depth **(40% category)**

- **C2 response frames**: `JobKind.FRAME`, `workers/frame.py` (model writes a template with typed holes), `kernel/frames.py` (validate holes against the tool's output schema, select branch — non-empty success / empty success / error — evaluate holes, type-check, render, emit FINAL in the same step the result lands). Operator set per `docs/prompt 3.txt` C2: `field`, `count`, `min`/`max` by field, `first-k`, `exists`. Composer stays as the fallback. The frame's read set includes goal facts, so a correction drops it.
- **C9 commit-last ordering** in `kernel/executor.py`: a step reachable from a write is never ahead of the write (CS-31) → scenario N-02b.
- New tests: N-02b, frame-rendered FINAL, CS-32 (a frame-rendered response contains data only from holes), frame invalidated by correction.

**Acceptance:** FINAL renders in the same step the last result arrives, and every data value in it is provably a hole evaluated on a consumed result.

### Phase 6 — Response latency **(15% category)**

This is the previously-approved V5 plan, now correctly sequenced after the larger categories. The plan file at `/home/adi/.claude/plans/i-have-gemini-pro-declarative-phoenix.md` still holds and should be followed as written — including the read-set subtlety it identified (`session.pending_interpretation_turn` must be dropped from a speculative job's read set, or EOT self-invalidates the very job it should promote).

Add on top of it: **content-bearing ACK** (§9.3 — emit a content ACK only when a HIGH hypothesis exists, which Phase 4 provides), since the scored metric is *"time to first **substantive** spoken action"* and today's ACK is a fixed generic string.

New tests: M-02 (partial transcript / inert tail, i.e. C1), T-06.

**Acceptance:** a measured EOT→first-substantive-speech improvement, reported in `docs/measurements.md` flag-off vs flag-on.

### Phase 7 — Safety, protocol and coverage breadth **(10% + technical-depth narrative)**

- Expand `sim/checker.py` from ~5 checks to the blueprint's full **CS-01…CS-34** (`docs/theme05_implementation_blueprint.md` lines 2298–2331). Existing mapping: our P3≈CS-08, W1≈CS-13, S3≈CS-17 (partial), replay≈CS-22, static scan≈CS-21.
- Close the remaining untested scenarios: R-01, R-03, R-04, R-07, R-09, S-07 (**named in the public suite**), S-08, S-09, P-04, P-05, M-04, M-05, M-09, T-03, T-04.

**Acceptance:** 34/34 checks run against every test; scenario coverage ≥ 55/64.

### Phase 8 — Quality multiplier polish **(small, high leverage)**

- Rewrite user-facing templates so no raw schema parameter name ever reaches speech (F5), add the §9.3 digest-based deterministic phrasing variation, and hedge MEDIUM-confidence perception claims ("it looks like…").

**Acceptance:** no `config/templates.py` string can render a parameter identifier; repeated ACKs in one session differ deterministically.

---

## 6. Research and decisions still required

| # | Item | Owner | Why it matters |
|---|---|---|---|
| R1 | ~~Has the evaluation kit been released?~~ **RESOLVED 18 Sep: not released.** No portal link, no registration email, no drop found. Re-check periodically — nothing in the guide names a specific channel. | **User** | Highest-leverage unknown in the project, still unresolved in the sense that "not yet" isn't "never" — re-check before the 25 Sep freeze. Kit integration stays blocked until it lands; Phase A below hardens the codec/entry point to survive the guess being wrong either way. |
| R2 | Do audio scenarios ship a transcript alongside the WAV, or audio-only? | Resolved by R1 | Decides whether ASR is mandatory (audio-only) or a fallback (transcript-primary). Phase 2 is built to handle both, but the ordering of effort within it changes. |
| R3 | Gemini free-tier audio input: shape and limits | Implementer | Needed for Phase 3's live ASR. Image `inline_data` is already verified working in this project; audio is the same mechanism but unverified. |
| R4 | Team name + college name | **User** | Required for deck nomenclature (`CollegeName_TeamName`) — a stated disqualification condition. |
| R5 | Demo video hosting (YouTube or Drive) | **User** | Link must be referenced from the tagged commit. |
| R6 | Which `Config` flags ship ON in the judged commit | Decide at final freeze | `vision_enabled` and `reference_bound_identifiers` currently default OFF. The judged run should almost certainly have them ON — but that decision needs one full green suite with the shipping configuration, not a flag flip at the last minute. |

---

## 7. Sequencing, and what to do if time runs short

Recommended order: **0 → A → 2 → 3 → 4 → 5 → 6 → 7 → 8**, inserting kit integration immediately after Phase 0 if R1 turns positive. **Phase A (tag `v5-integration`) and Phase 2 (tag `v6-audio`) are done.**

Phases 0 and A were non-negotiable — one removes disqualification risk, the other removed a total-failure mode (the entry point that had never once been executed). Everything after that is additive scoring value, and each phase leaves the repo in a frozen, submittable state.

If time compresses, drop from the back: Phase 8 → 7 → 6 → 5. Phase 2 (audio) is already done, so that trade-off no longer applies — next up is Phase 3 (live multimodal provider) or Phase 4 (interruption-recovery depth), per the plan's own category-weight ordering.

---

## 8. Deliberately not planned

- **Kit wire-format integration** as speculative work — blocked until R1 resolves. Phase 1 makes the codec tolerant so the mismatch is survivable either way.
- **C3 event-anchored reaction scheduling** — an organizing principle over C1/C2/C13 whose value is mostly under clock Model A; this project committed to Model B. Revisit only if the released kit turns out to be Model A.
- **C11, C12, C14–C17** — rejected or deferred in the original innovation analysis itself; no reason to revisit.
- **A `PolicyRegistry`** replacing flat `Config` fields — pure refactor, zero scoring value.
