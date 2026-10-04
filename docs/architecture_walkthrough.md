# Janus — architecture and working walkthrough

A guided tour of how the system works, for a reader who wants to understand the code before opening it. Written from the module docstrings in `src/prism_rt/` and kept current to 4 Oct 2026; where a mechanism is described, the file that implements it is named so it can be checked. The design rationale is in `docs/prompt 2.txt`; the numbers are in `BENCHMARKS.md`.

---

## 1. The one-paragraph version

Janus is a full-duplex voice agent in which **no language model ever acts on its own**. LLM calls (interpret, plan, compose, vision, bind, …) run as async *workers* that return **proposals**. A single synchronous **kernel** is the only thing that mutates session state and the only thing that emits anything to the outside world (speech, tool calls, cancels). Every proposal, call and pending utterance records the facts it depended on (`key, version, digest`); when a fact changes (the user corrects "Pune" to "Mumbai"), everything that read the old value is cancelled **in the same kernel step**. Writes (state-changing tools) pass one `CommitGate` and are recorded in an effect ledger *before* they are emitted, which is what prevents double bookings and false "done" claims.

Scored by: 60 % FDB-v3 re-run via `reproduce.sh` over LiveKit, 20 % use-case extension (device care with camera), 20 % docs/architecture/video.

---

## 2. The big picture (layers)

```
User (voice + camera)
   │  WebRTC
   ▼
LiveKit worker  (src/prism_rt/voice/)         ← only layer that imports livekit
   Silero VAD · STT (hosted OpenAI gpt-4o-mini-transcribe by default | local faster-whisper) · Kokoro TTS
   │  segment finals, barge-in, camera frames
   ▼
VoiceBridge  (adapters/voice_bridge.py)       ← pure translation, owns only the T_eot timer
   │  events in  (dict/Envelope)            ▲ actions out (SPEAK/TOOL_CALL/CANCEL/CLARIFY/FINAL)
   ▼                                          │
Runtime / entry.py  (composition root, async loop, watchdog)
   │
   ▼
KERNEL  (kernel/step.py)  ◄──── single writer, synchronous, ≤5 ms/step
   │  reads/writes
   ▼
STORE (store/)  facts, dependency index, call/effect ledgers, goals, plans, turns, jobs, timers
   │  dispatch jobs (JobRequest)          ▲ WORKER_RESULT events (proposals + read sets)
   ▼                                      │
WorkerRunner (workers/runner.py) ──► ModelGateway (workers/gateway.py) ──► Gemini 3.6 Flash / OpenAI
   │
   └─ Tool executors (adapters/fdb_tool_adapter.py → FDB mock APIs;  devicecare/tools.py for the extension)
         results come back as TOOL_RESULT events
```

Important property: **everything in the middle (kernel, store, model) is transport-agnostic.** The same kernel runs under `sim/harness.py` (tests, stepped clock, scripted LLM), under `entry.py` (generic async queues), under text replay (`scripts/fdb_v3/run_text_replay.py`), and under LiveKit (`voice/agent.py`). That is why the test suite (635 tests) says something real about the voice path.

### Package boundaries (enforced)
`model` imports nothing → `store` → `kernel`. `kernel` must **not** import `workers` (it dispatches by `JobKind` through the scheduler). `workers` must **not** import `store` or `kernel`. `observability` may import only `model`. Nothing but `voice/` may import `livekit`/`faster_whisper`/`kokoro`. Time only comes from `ClockPort`; `time.time`/`sleep`/`datetime.now`/`asyncio.sleep`/`loop.time` are banned in `kernel/`, `store/` and worker/tool code by a static check (TraceChecker T1). The single wall-clock reader is the watchdog (105 s).

---

## 3. The kernel step (the heart of the system)

`kernel/step.py` — `Kernel.step(batch)`. Never awaits. Eight phases:

| # | Phase | What happens | Where |
|---|---|---|---|
| 1 | ORDER | Sort batch by `(ts, event class, seq)` | `kernel/ordering.py` |
| 2 | APPLY | Run one reducer per envelope; also clock-driven work: perception lease expiry, release of held ASR transcripts | `kernel/reducers.py`, `perception.py`, `audio.py` |
| 3 | INVALIDATE | Take the changed fact keys, walk the dependency index, mark dependent calls/jobs/proposals stale | `kernel/invalidation.py`, `store/facts.py` |
| 4 | DECIDE | Fixed component order (below) | many |
| 5 | EMIT | `EmissionGate` validates (G9: read set still valid *at emission*), orders and stamps actions | `kernel/emission.py` |
| 6 | COMMIT | Transaction commit → `ChangeSet` | `store/session.py` |
| 7 | DISPATCH | Actually call `runner.submit()` for the jobs DECIDE requested | `step.py` |
| 8 | LOG | Decision-log line per step | `observability/decision_log.py` |

**DECIDE order (fixed):** cancellation actions → call deadline expiry (P0.4) → `BindScheduler` (S3 late binding) → `TaskStateMachine` (INTERPRET/PLAN/COMPOSE/EXTRACT dispatch) → `PerceptionScheduler` → `AsrScheduler` → `FrameScheduler` → `PlanExecutor.propose_ready_calls` → `CommitGate.scan_and_admit` → `FastResponder`. Each sees post-invalidation state and nothing decided later in the same step.

**Emission order (fixed):** `CANCEL → SPEAK → TOOL_CALL → CLARIFY → FINAL`. So a correction's cancel always goes out before the new call or speech.

### Inbound events (`model/events.py`)
`manifest`, `text_chunk`, `end_of_turn`, `interruption`, `tool_result`, `worker_result`, `video_frame`, `audio_clip`, `timer_fired`, `watchdog`.

### Outbound actions (`model/actions.py`)
`SPEAK` (ACK/INFORM/PROGRESS text), `TOOL_CALL`, `CANCEL`, `CLARIFY`, `FINAL` (carries `task_completed` and a claim grade: RESULT for reads, EFFECT_DONE for confirmed writes).

---

## 4. The state: `store/`

| Module | Role |
|---|---|
| `store/facts.py` | `FactStore` — versioned, digest-validated facts (`slot.<goal>.<param>`, `turn.<id>.prefix`, `asr.<obs>.held`, step outputs, perception claims…). `DependencyIndex` maps a fact key → everything that read it. Mutation only allowed inside the step's transaction (`MutationGuard`). |
| `store/ledgers.py` | `CallLedger` (every tool call and its status: PROPOSED/IN_FLIGHT/…), `EffectLedger` (write effects: PENDING/CONFIRMED/UNKNOWN/FAILED, fingerprint + lineage), `GoalRegistry`, `PlanStore`, `TurnLog`, `JobTable`, `EvidenceStore` (perception). |
| `store/catalog.py` | `ToolCatalog` — parses the tool manifest, detects READ vs WRITE mutability, quarantines bad tools individually, **defaults unknown mutability to STATE_CHANGING** (safe default). |
| `store/session.py` | `SessionStore` aggregate and `StoreTxn`. One per session, no module-level mutable state, so sessions are isolated. |
| `canonical.py` | Canonical JSON + digests, so semantically equal values hash equal. Every digest and fingerprint comes from here. |

**Read sets (the core mechanism).** A read set is a list of `(key, version, digest)`. It is valid iff every fact still exists, isn't retracted, and the digest is unchanged. Consequence: if the user says Pune → Mumbai → Pune, the digest matches again and finished work is reused; if a value really changed, work that read it is dead. There is no global generation counter; `goal.active` is the coarse generation, fact versions the fine one.

---

## 5. Kernel components, one by one

**Input side**
- `kernel/turns.py` `TurnManager` — assembles turns from `text_chunk`s, keeps a prefix digest as a *real fact* (`turn.<id>.prefix`), so an INTERPRET job in flight is invalidated automatically if more speech arrives.
- `kernel/detector.py` `QuickDetector` — deterministic cue and HIGH-confidence value extraction per chunk (enum matches, session entities). Powers **chunk-anchored cancellation** (Phase 4): a high-confidence correcting value heard mid-utterance cancels the stale in-flight call immediately, not at end-of-turn.
- `kernel/audio.py` `AsrScheduler` — audio clip → transcript → ordinary text chunks. Holds transcripts and releases them in capture order, dedupes against the harness's own text.
- `kernel/perception.py` `PerceptionScheduler` — camera/vision question lifecycle: frame alignment, VISION job dispatch, claim acceptance, conflicts, lease expiry. No-op unless `vision_enabled`.

**Understanding / planning**
- `kernel/task.py` `TaskStateMachine` — the *sole* dispatcher of INTERPRET / PLAN / COMPOSE / EXTRACT jobs; pure function of state, idempotent every step. No explicit TRIAGE state: whatever the interpretation says (SLOT_UPDATE, NEW_GOAL, ABORT, RETURN_TO_GOAL …) is applied uniformly.
- `kernel/interpret_apply.py` — turns a `TurnInterpretation` into goal/fact mutations. A correction is just an ordinary fact change through `FactStore.set`, so the unmodified invalidation engine does the cancelling. With the **rebinder** on, a purely local slot change stays in EXECUTING with no Planner round-trip.
- `kernel/proposals.py` — parses and validates untrusted worker output (kernel-side so acceptance lives next to read-set validation).
- `kernel/action_plans.py` (S3, `action_plans_enabled`) — the Interpreter returns `actions[]` (one entry per tool call, with literal args) and the kernel **compiles the plan directly in the same step** — no PLAN round-trip. Args live at `slot.<gid>.a<i>.<param>`.
- `kernel/binder.py` + `workers/binder.py` (S3 late binding) — chained args ("the cheapest one", "from there") are chosen from the upstream step's **real result** after it exists, never guessed before.
- **Conditional requests** (`Config.conditional_actions_enabled`) — "if one is under $50 add two, otherwise track my order": the Interpreter marks each conditional action with the earlier action that decides it (`only_if`), and `BindScheduler` has a BIND job check that test against the real result before the step may run (verdict at `cond.<goal>.<step>`, grounded like a bound value). A step whose condition does not hold is skipped, not failed; the answer says what was skipped and why. An action whose condition cannot be checked never runs.

**Acting**
- `kernel/executor.py` `PlanExecutor` — turns ready plan steps into PROPOSED calls (dependencies via `after`, bindings resolved from facts), handles retries and call-deadline expiry. Does *not* emit; it only creates call records.
- `kernel/commit.py` `CommitGate` — admission control for every call. Rules: G1 tool usable, G2 read set valid, G8 args match schema, TRIAGE hold (no new call while a fresh user turn awaits interpretation), watchdog guard, G3 floor closed, G4 explicit commit intent (when required), G5 no duplicate fingerprint, G6 no existing PENDING/CONFIRMED effect for the same plan-step lineage, G7 no UNKNOWN effect, G10/G11 settle barrier (wait out the settle window; no new turn open). G9 is enforced by `EmissionGate`. Writes are recorded in the effect ledger before emission.
- `kernel/results.py` `ResultRouter` — seven-branch decision for an incoming tool result (still relevant? stale? duplicate? post-cancel reconcile?).
- `kernel/emission.py` `EmissionGate` — the only exit. Validates, orders, stamps, writes; nothing is buffered.

**Speaking**
- `kernel/responder.py` `FastResponder` — templated ACK (once per plan dispatch), CLARIFY (once per distinct target), INFORM (duplicate-write / unknown-outcome notices, reconcile notices), FINAL, claim-graded (an INTENDED-grade "booking now" while a write waits on the settle barrier). Honest failure FINALs (`task_completed=False`).
- `kernel/frames.py` (C2 response frames) — as soon as the plan's last call is emitted, a FRAME worker writes a template with typed holes (`field`, `count`, `min/max`, `first_k`, `exists`) bound to result paths; when the real result lands, the kernel fills the holes itself and emits a FINAL grounded in real data with **no COMPOSE round-trip**. The model can invent wording, never values.
- `kernel/replies.py` — honest replies when there is no active goal ("thank you", "has it been booked?", a request no tool covers) built from the call ledger, never from model text.
- `observability/speechlint.py` — post-lint on SPEAK/CLARIFY/FINAL: humanises raw identifiers, rejects ACKs that claim completion.

**Recovery details worth knowing**: transitive invalidation, settle barrier, absence read sets, claim grades, rebinder (V2); post-cancel "reconcile INFORM" if a cancelled write went through anyway; `v10-3` honest-failure when a corrected write's lineage is blocked by a confirmed original.

---

## 6. Workers: `workers/`

All workers are `run_*`/`build_prompt` pairs that talk **only** to `ModelGateway.complete_json(kind, prompt, schema, media=None)`; parsing is kernel-side.

| Job kind | File | Does |
|---|---|---|
| INTERPRET | `interpreter.py` | transcript + context → `TurnInterpretation` (act, slots, actions[], visual reference). Can run **speculatively** on the transcript prefix while the user is still speaking; applied at EOT with zero extra latency when the digest matches (Phase 6, 300 ms → 0 ms measured). |
| PLAN | `planner.py` | goal + facts + catalog → `Plan` (skipped when `action_plans_enabled`) |
| COMPOSE | `composer.py` | response plan → text (with template fallback if the truth-check fails) |
| VISION | `vision.py` | frame bytes + question targets → perception claims |
| ASR | `asr.py` | audio clip → segments |
| FRAME | `frame.py` | response template with typed holes |
| EXTRACT | `extractor.py` | one-parameter re-extraction before a generic clarify (“re-read before asking”, Q5) |
| BIND | `binder.py` | pick chained arg from real results |

- `workers/gateway.py` — `ModelGateway` + providers: `ScriptedProvider` (all tests), `GeminiProvider` (default live), `OpenAIProvider`, `AnthropicProvider`. Retry/backoff on transient errors. `MediaPart` carries real image/audio bytes.
- `workers/runner.py` — `ScriptedRunner` (deterministic, latency-gated so results never land in the dispatch step) and `AsyncWorkerRunner` (real asyncio). A `blob_resolver` maps the harness `frame_id` to bytes **outside** the kernel.

---

## 7. Adapters and the voice path

- `adapters/clock.py` — `ClockPort`: `SteppedClock` (tests, Model B) and `CoupledClock` (production, Model A, so a timer such as the settle wake still fires when the harness is silent).
- `adapters/codec.py` — wire JSON ↔ Envelope/Action; tolerant decode (drops what it can't parse rather than raising).
- `adapters/output_writer.py`, `transcript_segmenter.py` — action sink / segmenting helpers.
- `adapters/fdb_manifest.py` — builds the Janus tool manifest by **AST-parsing FDB's own `lk_agent_tool.py`** (never hand-typed, no per-tool special cases).
- `adapters/fdb_tool_adapter.py` — executes admitted TOOL_CALLs against FDB's unmodified `MockAPIRegistry`, including the default-filling FDB's own pipeline does.
- `adapters/voice_bridge.py` — translates speech-transport events ↔ kernel events/actions. Owns the end-of-turn timer and barge-in detection. The turn closes after 1.5 s of VAD silence, and only once every transcription still running has delivered its text (the transcriber reports each one's start and finish, `on_recognition`); the kernel is told when the user is speaking (`user_speech`) so it holds calls and speech meanwhile. Used identically by offline audio replay and the live worker.
- `voice/agent.py` — the LiveKit worker (`python -m prism_rt.voice.agent start`): Silero VAD + STT + Kokoro TTS, **no LLM in the session** — every utterance/tool call comes from the kernel. One job = one room = one fresh `Runtime`. Mode `fdb` (benchmark toolset/profile) or `demo` (device care + camera). Env knobs documented in its docstring.
- `voice/speech.py` — the speech plugins: hosted `gpt-4o-mini-transcribe` (default, `JANUS_STT=openai`) or pinned faster-whisper large-v3-turbo on the GPU (`JANUS_STT=local`), plus Kokoro-82M; `voice/camera.py` — `FrameStore` (blob resolver) and `FrameSampler` (~1 fps JPEGs).
- `entry.py` — composition root: `setup()`, async `run_scenario` over two `asyncio.Queue`s (the official contract), 50 ms poll for worker results, watchdog salvage.
- `profiles.py` — `fdb_v3_config()` and `demo_config()`: named combinations of `Config` flags. `config.py` — the frozen `Config`, every post-V1 behaviour behind a flag (e.g. `action_plans_enabled`, `speculative_interpretation_enabled`, `vision_enabled`, `commit_last_ordering`, `reference_bound_identifiers`, `conversational_replies_enabled`).

---

## 8. The tools

**FDB-v3 (the benchmark):** 12 deterministic mock tools over four domains (travel, finance, e-commerce, housing). They live in FDB's repo (`v3/mock_apis.py`), are **not** copied into Janus; the manifest is introspected from FDB source and calls go through `FdbToolAdapter`. In the FDB profile their mutability is undeclared, so the safe default (STATE_CHANGING) is applied, with G4 exemptions in the profile.

**Extension — device care (`devicecare/tools.py`, `DeviceCareToolset`):**
- Reads: `identify_indicator`, `lookup_error_code`, `get_fix_steps`, `check_warranty`
- Writes: `book_technician`, `open_support_ticket`
- Two mock devices over a local knowledge base: Wi-Fi router (LED patterns) and washing machine (error codes, audio-only works). Mutability is declared in the manifest. State lives on the toolset instance (one per session).
- Flow: camera frame → VISION worker → perception claim (`led_color=orange`) → planner/compiled action → `identify_indicator`; a booking waits for explicit go-ahead + settle window; a correction after the booking went through is never re-run as a second booking.

---

## 9. How the pieces connect — worked example (correction)

User: "Look up the orange light… sorry, actually red."

1. VAD/STT produce segment finals → `VoiceBridge` → `text_chunk` events → mailbox.
2. Step N: `TurnManager` updates the prefix fact; `QuickDetector` may see a HIGH enum value ("red") that contradicts `slot.<g>.color=orange`.
3. APPLY writes the new slot value. INVALIDATE finds the in-flight `identify_indicator(orange)` call (its read set contains that slot) and marks it invalid. DECIDE emits **CANCEL** first.
4. At end-of-turn, INTERPRET (maybe already run speculatively) returns SLOT_UPDATE color=red; `interpret_apply` writes facts; `PlanExecutor` proposes a new call for the same step key; `CommitGate` admits it (read: no settle needed unless `settle_reads_enabled`).
5. `FrameScheduler` had pre-written the answer template; when the red result arrives, the kernel renders a **FINAL** grounded in the red result. No orange text can survive: G9 re-checks the read set at emission.
6. For a **write** ("book Thursday… no, Friday"): the gate holds the write for the settle window and until the floor is closed; if Thursday was already cancelled before emission nothing was booked; if it had gone through, the effect ledger blocks a second booking and the responder says so honestly.

---

## 10. Verification and observability

- `sim/harness.py` (`SimHarness`, stepped clock, mock tools), `ScriptedProvider` — fully deterministic. Replay identity (same input → byte-identical decision log) is a required check.
- `sim/checker.py` `TraceChecker` — runs after every test: static no-wall-clock scan, P1 structure, CS-03/05/08/09/13/14/17/27/33, replay identity. It has had false positives; verify the checker's assumption before blaming the kernel.
- `sim/explorer.py` + `minimize.py` — bounded adversarial schedule exploration over perturbed timings; oracles checked after each run; minimiser shrinks a failing schedule to the race.
- `observability/` — `decision_log` (JSONL per step), `metrics` (TTFS, tool latency, cancel latency), `xray` (HTML swimlane timeline per session, `scripts/make_xray.py`), `watchdog`.
- Tests: 635 (`tests/`), per version/phase/review round; `reviews/index.md` logs every independent review round and which findings were disproven.

---

## 11. Running and reproduction

| Goal | Command |
|---|---|
| Unit/scenario suite | `source .venv/bin/activate && python -m pytest tests/ -v` |
| Text-only accuracy loop (safe on laptop) | `PYTHONPATH=src:. python scripts/fdb_v3/run_text_replay.py --profile fdb --model gemini-3.5-flash-lite` |
| Extension, text + image | `PYTHONPATH=src:. python demo/run_device_care_demo.py` |
| Extension, voice + camera | `JANUS_MODE=demo python -m prism_rt.voice.agent start` + Playground client (`scripts/make_demo_token.py`) |
| One-command FDB-v3 reproduction (scored) | `./reproduce.sh [--ids "travel_01 …"] [--no-judge]` — builds pinned Docker image, clones FDB at commit `3e799c4…`, sha256-checks data, starts worker, runs FDB runner + 3 evaluators, writes `results/<ts>/` |
| No-Docker fallback | `scripts/fdb_v3/native_setup.sh` then `native_run.sh` |

**Do not run LiveKit voice runs on the dev laptop** (OOM). Use a cloud GPU VM (≥ 32 GB RAM, driver ≥ 525). Keys needed: LiveKit URL/key/secret, `GEMINI_API_KEY`, `OPENAI_API_KEY` (STT + judge).

---

## 12. Where the project stands

- **Submission tag** `PRISM_GENAI_HACKATHON_Y2026` marks the 30 Sep submission; development continued on `fdb-v3-integration` until the 4 Oct deadline.
- **Measured (3 Oct, all 100 recordings, judged):** voice over LiveKit 74 % strict pass (run-to-run range 65–74 % that day), text replay 77 %. Self-correction 0.765 on voice. `BENCHMARKS.md` has every run, the failures behind the misses and which comparisons are like for like.
- **The 26 remaining voice misses:** 17 are labels that disagree with the published tool definitions, the recording, or the condition the user set (README, "Benchmark labels and the published tools"); the rest are speech-to-text errors and value wording.
- **Not yet run end to end:** `./reproduce.sh` with a GPU (every measured run used the native path, `scripts/fdb_v3/native_run.sh`).
- **Known limits:** speech-to-text is not streaming (each segment is transcribed after a pause), so the first word arrives about 8.5 s after the user stops (part of that is deliberate: a 1.5 s turn-end wait and a settle window before writes); the model is a hosted API and not bit-reproducible; the extension's device backend is a simulation.

---

## 13. Reading map (what to open for what)

| Question | Open |
|---|---|
| Why is it shaped like this? | `docs/prompt1.txt` → `docs/prompt 2.txt` (§3 read sets, §5 step, §7 tools, §8 recovery, §13 clock, §21.1 package boundaries) |
| What's the exact contract? | `guidelines/Theme_5_Guide.md`, `docs/Theme05_Participant_Guide.md` |
| How FDB-v3 is wired | `docs/fdb_v3_implementation_plan.md` |
| What was built when | `docs/post_v4_implementation_plan.md`, `docs/prototype_version_plan.md`, `currentStatus.md` Implemented section |
| Gate rules / data models | `docs/sonnet_implementation_plan.md` Appendices A–C, `docs/theme05_implementation_blueprint.md` (look up, don't read whole) |
| Innovations C1–C17 | `docs/prompt 3.txt` |
| Numbers | `docs/measurements.md`, `docs/runs/`, README Results |
| Bugs found by reviewers | `reviews/index.md` |
