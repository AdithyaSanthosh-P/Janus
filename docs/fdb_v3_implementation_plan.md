# Janus × Full-Duplex-Bench v3 — Implementation Plan

Status: **plan only, nothing below is implemented.** Written 2026-09-24 after three
read-only investigations (benchmark recordings + source, LiveKit Agents source,
STT/TTS/LLM measurements). Deadline: **2026-09-30**.

Evidence tags used throughout:

- **[FDB]** verified in Full-Duplex-Bench source, commit `3e799c45` (2026-05-20), `v3/`
- **[DATA]** measured on the released benchmark recordings (`fdb_v3_data_released.zip`,
  sha256 `37545bd896f81718136598cf5be25d42ea9aa22efcd91f58370938d05d7d672f`, 100 recordings)
- **[LK]** verified in `livekit-agents` 1.8.3 / `livekit-plugins-{silero,google,openai}` 1.8.3 source
- **[JANUS]** verified in this repository (branch `fdb-v3-integration`, commit `ba85d59`)
- **[MEASURED]** timed on this machine (RTX 4060 Laptop 8 GB, residential network); samples are small and stated
- **[GUIDE]** `docs/Theme05_Participant_Guide.md` (Samsung, 2026-09-24)
- **[ASSUMPTION]** / **[UNKNOWN]** — not verified; each has a day-1 check

---

## 1. What is being scored (the contract)

| Fact | Evidence |
|---|---|
| Round 1 = 60 % FDB-v3 re-run by Samsung with our one-command script, 20 % extension, 20 % docs/video; tie-break on strict pass rate | [GUIDE] §4–5 |
| Each recording is streamed into a fresh LiveKit room (`eval-<uuid8>`) by a headless "user" client in real time (48 kHz mono PCM16, 20 ms frames, then 1.5 s silence) | [FDB] `run_tool_benchmark.py:222`, `livekit_inference.py:99-102, 252-282` |
| The agent's audio is recorded at 24 kHz for **exactly the input's duration**, then transcribed by NVIDIA Parakeet (`nvidia/parakeet-tdt-0.6b-v2`); that transcript is the agent's "response" | [FDB] `livekit_inference.py:139-180`, `run_tool_benchmark.py:61, 407-412` |
| Tool calls are read **only** from lines the agent appends to `/tmp/agent_tool_calls.log`: `{"room", "call": {"function", "args", "timestamp_start", "timestamp_end"}}`; the agent executes the mock tools itself, in-process | [FDB] `lk_agent_tool.py:206-373`, `run_tool_benchmark.py:430-455` |
| **Strict pass** = the multiset of called function names equals the expected multiset (any extra call, including a repeat of the same function, fails) **and** every argument is judged correct; args are judged on the *first* call of each function | [FDB] `evaluate_pass_rate.py:165-236` |
| Judge = `gpt-4o`, temperature 0 (alias, not a dated snapshot); tolerant of formatting, aliases, ±5 % numbers, `$RESULT_n` references | [FDB] `evaluate_pass_rate.py:87-123` |
| Response quality: gpt-4o compares the **whole** agent transcript with the expected AI line; partial completion of a multi-step task scores 0 | [FDB] `evaluate_tool_calls.py:116-158, 284-300` |
| Latency metrics: first response, first tool call, task completion — all relative to the end of the user's first turn (first word gap > 2 s) | [FDB] `run_tool_benchmark.py:351-363`, `analyze_tool_latency.py:1-25` |
| **100 recordings of 79 scenarios**, each **one** user request (no small-talk opener in the released data); 17 self-correction ("rollback") recordings; domains: ecommerce 29, finance 25, housing 26, travel 20 | [DATA] per-folder `metadata.json` (overrides `benchmark_data_v2.json`, [FDB] `run_tool_benchmark_all_released.py:126-128`) |
| Audio formats: 83× 48 kHz mono 32-bit, 10× 48 kHz stereo 32-bit, 7× 16 kHz 16-bit — all need ffmpeg conversion | [DATA], [FDB] `livekit_inference.py:49-83` |
| **User speech lasts 6.0–25.8 s (median 14.7 s), then 30.2–33.1 s of silence** (median 31 s) — the agent's response window is ≈ 30 s | [DATA] faster-whisper + Silero VAD word timestamps; two models agree |
| Mid-request pauses: longest pause per recording ≥ 0.55 s in **72/100**, ≥ 1.0 s in 25, ≥ 1.5 s in 13, ≥ 2.0 s in 5, ≥ 3.0 s in 2 (max 3.46 s) | [DATA] |
| No seeds anywhere in FDB; mock latency jitter is unseeded; `search_flights`/`search_apartments`/`calculate_commute` get 200–800 ms random delay even under `--latency instant`; `time.sleep` inside async tool methods | [FDB] `latency_injector.py:117, 170-183, 229-263` |
| FDB is licensed CC BY-NC 4.0 | [FDB] `LICENSE` |

**Consequences that drive the design**

1. An executed tool call can never be taken back. Cancellation, which Janus is built around, earns nothing here. What earns points is **not executing a call until the request is final**, and never executing the same function twice.
2. Endpointing is the dominant risk. With the stock template's 0.55 s silence rule, 72 % of recordings would be cut mid-request.
3. There is plenty of time (≈ 30 s) but latency is still measured. First speech should be fast; tool execution should be deliberate.
4. The judge hears only what is **spoken** and sees only what is **logged**.

---

## 2. Final architecture and component boundaries

```
 FDB headless client (user)                      FDB scorer (offline, no LiveKit)
        │ audio (WebRTC)                                  ▲ /tmp/agent_tool_calls.log
        ▼                                                 │ output WAV → Parakeet ASR
┌──────────────── LiveKit Agents worker (one process per room) [LK] ─────────────────┐
│ AgentSession(vad=Silero, stt=<STT>, tts=<TTS>, llm=None)                            │
│   ├─ user_input_transcribed (interim/final) ──┐                                     │
│   ├─ VAD / end-of-speech timing ───────────────┤                                     │
│   └─ session.say(text) ◄──────────────┐        │                                     │
│                                       │        ▼                                     │
│   ┌──────────── Voice adapter (NEW, thin, stateless translation) ───────────────┐  │
│   │ transcript/VAD events → Janus raw events (Janus's own HarnessCodec dialect)   │  │
│   │ Janus actions: SPEAK/CLARIFY/FINAL → session.say; TOOL_CALL → Tool adapter    │  │
│   └───────────────▲──────────────────────────────────────────────┬───────────────┘  │
│                   │ actions (asyncio.Queue)          events      │                   │
│   ┌───────────────┴──────── Janus Runtime.run_scenario (UNCHANGED core) ──────────┐ │
│   │ TurnManager → INTERPRET/PLAN/COMPOSE workers → PlanExecutor → CommitGate →     │ │
│   │ EmissionGate; FactStore, read sets, invalidation, ResultRouter, TimerWheel     │ │
│   │ + NEW policy profile "irreversible tool calls" (Config flags, §5)             │ │
│   └───────────────────────────────────────────────────────────────▲──────────────┘ │
│   ┌──────────── Tool adapter (NEW) ───────────────────────────────┴──────────────┐  │
│   │ TOOL_CALL → fill schema defaults → FDB MockAPIRegistry.call (in a thread) →    │  │
│   │ append FDB log line → push tool_result event back into Janus                  │  │
│   └──────────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

Boundaries (what may touch what):

- **LiveKit layer**: owns audio, VAD, STT, TTS, and the room lifecycle. It never decides anything.
- **Voice adapter**: translates only. It never mutates Janus state; everything enters through `Kernel.step()` as ordinary events ([JANUS] `entry.py`, `kernel/step.py`).
- **Janus core**: every decision. The single writer, read sets, invalidation, CommitGate, ResultRouter and commit-last ordering are **unchanged**. New behaviour arrives only as Config-flagged policy.
- **Tool adapter**: executes a call only because Janus emitted a `TOOL_CALL`, which means CommitGate already admitted it. Results come back as ordinary `tool_result` events through ResultRouter.

Why option B (host Janus inside LiveKit) is valid [LK]:

- `AgentSession` with `llm=None` still runs VAD/STT and emits `user_input_transcribed` (interim and final) (`voice/agent_activity.py:2497-2545`).
- It calls `Agent.on_user_turn_completed`, then deliberately skips replying: `elif self.llm is None: return  # skip response if no llm is set` (`:2836-2837`).
- `session.say(text)` speaks arbitrary text at any time (`voice/agent_session.py:1530`).
- On Linux each job runs in its own process (`worker.py:144-150`), giving natural per-scenario isolation.

---

## 3. Interfaces

### 3.1 LiveKit → voice adapter → Janus (inbound)

| LiveKit signal | Janus raw event (HarnessCodec dialect) | Notes |
|---|---|---|
| job start (room joined) | `manifest` with the tool list (§3.3) | one per room; fresh `Runtime`, `SessionStore` |
| STT **final** transcript for a VAD segment | `text_chunk {text}` | appended to the open turn ([JANUS] `kernel/turns.py:53`). Interim transcripts are *not* fed to the kernel in v1 (they are revisions, and Janus chunks are append-only). With 0.55 s VAD segmentation one request arrives as several finals: 72 % of recordings pause that long |
| sustained silence ≥ `T_eot` since last speech (§5) | `end_of_turn` | adapter timer, stamped with the adapter's monotonic offset |
| user speech starts while agent is speaking | `interruption` | barge-in; FDB never does this, but real use does |
| tool adapter result | `tool_result {call_id, status: ok, result}` | |

Timestamps: `ts_us` = monotonic microseconds since job start. The `CoupledClock` (Model A, [JANUS] `adapters/clock.py`) is the production clock.

### 3.2 Janus → voice adapter / tool adapter (outbound)

| Janus action | Effect |
|---|---|
| `speak` / `clarify` / `final` (body text) | `session.say(text, allow_interruptions=True)` |
| `tool_call {call_id, tool_name, arguments}` | tool adapter (§3.3) |
| `cancel {target_call_id}` | no-op plus a debug log. FDB tool calls are synchronous mock calls that cannot be cancelled. Under the §5 policy a cancel of an *executed* call cannot happen before execution anyway |

### 3.3 Tool adapter

- **Manifest**: generated at start-up by introspecting FDB's own tool definitions: `inspect.signature` plus docstrings of `AssistantFnc` methods in the pinned FDB checkout. It yields JSON-Schema `parameters` (types from annotations, `required` = parameters without defaults, `default` recorded) and descriptions. No tool is hand-typed and none is special-cased. **Mutability** is not declared by FDB, so it is left undeclared (Janus's safe default, state-changing: [JANUS] `store/catalog.py:139-148`). This is intentional under §5.
- **Execution**: fill parameter defaults exactly as the Python signature would, e.g. `add_to_cart(quantity=1)` or `calculate_commute(mode="driving")`, because FDB's templates log the defaulted value ([FDB] `lk_agent_tool.py:363-373`). Then call `MockAPIRegistry.call(...)` via `asyncio.to_thread`, since it `time.sleep`s. Then append the log line in FDB's exact format with `time.time()` start/end, and return the result.
- **Isolation**: the log is keyed by room name. Nothing is cached across rooms.

---

## 4. Component decisions with measured evidence

### 4.1 LLM (INTERPRET / PLAN / COMPOSE)

| Model | INTERPRET median | PLAN median | Notes |
|---|---|---|---|
| gemini-3.6-flash (current default) | 5.49 s (n=3) | — | "thinking" on by default — the main cause of today's slowness |
| gemini-3.6-flash, `thinkingBudget: 0` | 2.19 s (n=16, p90 2.64 s) | 2.82 s (n=4) | |
| **gemini-3.5-flash-lite** | **1.54 s** (n=17, p90 1.61 s) | **1.91 s** (n=4) | rejects `thinkingBudget` (HTTP 400) |
| gemini-3.1-flash-lite | 2.72 s (n=3) | — | |
| gemini-2.5-flash / -lite | HTTP 404 | | listed but unavailable on this key |

[MEASURED] with Janus's real Interpreter `build_prompt` on the 17 self-correction recordings.

**Correctness finding:** in no inspected case did either model pick the **pre-correction** value; given the whole utterance, self-corrections are understood. The residual misses were:
- omitted optional arguments that have defaults (fixed by §3.3 default-filling);
- formatting (`B-7` vs `B7`, `British pounds` vs `GBP`), which the judge tolerates but which is avoidable: the Interpreter prompt only lists *required parameter names*, never descriptions such as "3-letter currency code" ([JANUS] `workers/interpreter.py:60-63`, `kernel/task.py:244`);
- one genuine miss (`housing_11` city).

**Decision:** `gemini-3.5-flash-lite` for all three roles, **gated** by a day-1 accuracy run on all 100 transcripts. If it trails `gemini-3.6-flash` with thinking off by more than 5 points of strict pass, switch to the latter. Add a `thinkingConfig` option to `GeminiProvider` ([JANUS] `workers/gateway.py:186-195` sets none today). Temperature stays 0.

### 4.2 STT

| Candidate | Speed | Accuracy (expected-value survival) | Reliability | Other |
|---|---|---|---|---|
| **faster-whisper large-v3-turbo, fp16, GPU, Silero VAD** | RTF **0.0088** (4,718 s audio in 41 s) → a 15 s request ≈ 0.13 s after its VAD segment closes | on the 87 recordings both candidates transcribed: 2 misses unique to Whisper, 2 unique to Gemini, 31 shared (the shared ones are normalisation cases such as "pounds" → `GBP`, which the LLM must do anyway) | 0 failures /100 | **must** run with VAD: without it Whisper hallucinated whole sentences into the 30 s silent tail (e.g. "If you are not going to make the order, you are going to make the order. Thank you.") |
| gemini-3.5-transcribe-live (streaming, LiveKit plugin default) | interim containing the last word ≈ +1.1 s, final **+1.7 to +2.0 s** after speech ends (n=3) | verbatim, keeps disfluencies | **13/100 batch requests failed** after 3 retries at 5-way concurrency | one key for STT+TTS+LLM; network/quota dependency at evaluation time |

[MEASURED], [DATA]. LiveKit wraps non-streaming STT with VAD via `stt.StreamAdapter(stt=…, vad=…)` [LK] `stt/stream_adapter.py:22-167`, which requires a small custom `stt.STT` subclass for faster-whisper.

**Decision:** local **faster-whisper large-v3-turbo + Silero VAD** through `StreamAdapter`. The reasons: lowest latency, no quota or network failure mode during Samsung's re-run, equal accuracy, and it fits the 48 GB evaluation GPU easily. Alternate, kept behind a Config switch: Gemini live STT (`google.beta.GeminiSTT`). **Day-1 checks:** measure VRAM per worker process, and set `num_idle_processes` accordingly ([LK] `worker.py:316-318` defaults to one per CPU core in production, each loading its own models).

### 4.3 TTS

| Candidate | Time to first audio | Complete (≈ 4–8 s of speech) | Speech→ASR round trip (IDs, dates, prices) |
|---|---|---|---|
| **Kokoro-82M, local GPU** | **0.089 s** (n=9) | 0.089 s | "Your order **ABC 123**…", "flight **FL-123**, for **$450**", "cart total is **$199.98**" — all facts kept |
| gemini-3.8-flash-lite-tts (streaming) | 1.06 s (n=3) | 2.09 s | "Your order, **ABC123**…", "flight **FL123** for **$450**", "**$199.98**" — all facts kept |
| gemini-3.8-flash-tts | 2.00 s | 3.38 s | — |
| gemini-3.1-flash-tts-preview / 2.5-flash-preview-tts | 4.45 s / 5.01 s (non-streaming) | | — |

[MEASURED]. Round trip transcribed with faster-whisper as a stand-in for FDB's Parakeet.

**Decision:** **Kokoro-82M local**, wrapped as a custom LiveKit `tts.TTS`. Alternate behind a Config switch: `gemini-3.8-flash-lite-tts` via the existing plugin `google.beta.GeminiTTS` [LK]. **Day-1 check:** repeat the round trip with FDB's own Parakeet model, because the judge sees only Parakeet's transcript.

### 4.4 VAD

Silero (LiveKit plugin; defaults `min_silence_duration=0.55`, [LK] `plugins/silero/vad.py:63-64`) is used **only for segmentation**. Turn completion and tool commitment are Janus policy (§5), not VAD.

---

## 5. Tool-commit and correction policy (exact)

The policy is a new Janus Config profile, enabled only by the LiveKit entrypoint, so the kit and legacy paths and all 334 existing tests are unaffected. It adds no new gate mechanism; it reuses CommitGate's existing conditions G3/G5/G6/G10/G11 ([JANUS] `kernel/commit.py:83-136`).

1. **Every tool call is treated as irreversible for admission.** Today reads bypass everything after G8 ([JANUS] `kernel/commit.py:86-87`). Under the profile, *all* calls must pass:
   - the floor-closed check (G3);
   - no duplicate fingerprint (G5) and no duplicate plan-step lineage (G6);
   - **settle**: at least `T_commit` of user silence since the last speech, measured from the last STT segment end (G10);
   - no newer turn open (G11);
   - the triage hold (latest turn interpreted, existing G3 clause).
2. **Commit intent (G4).** It stays required for tools declared state-changing. FDB tools are undeclared, and requiring an Interpreter-set `commit_intent` for "search for flights" would block reads the user plainly asked for. The recommended treatment: under the profile, an accepted interpretation of a *completed, settled* turn counts as commit intent for undeclared tools. **Decision pending (day 2)**, with a SimHarness test either way.
3. **Timing values** (fixed in advance, not tuned per scenario):
   - `T_eot = 1.0 s` of silence closes a Janus turn, which triggers interpretation and allows the acknowledgement;
   - `T_commit = 2.0 s` of silence allows execution;
   - extended to **3.5 s** when the turn so far ends *incomplete*: a trailing filler, conjunction or correction cue (existing lexicons, [JANUS] `config/lexicons.py`), an UNCLEAR interpretation, or a missing required argument.

   Rationale [DATA]:

   | Threshold | Recordings with a longer mid-request pause |
   |---|---|
   | 0.55 s | 72 |
   | 1.0 s | 25 |
   | 2.0 s | 5 |
   | 3.5 s | 0 (max observed 3.46 s) |

   The ≈ 30 s response window absorbs the extra 1–3.5 s. These are distribution-level constants from general disfluent-speech statistics, chosen once and **not grid-searched against scores** (the guide forbids tuning on test items, [GUIDE] §6). They will be cross-checked on the optional FDB v1/v1.5 practice data.
4. **Before commit**, a correction ("Paris — no, Berlin") simply invalidates the proposed call through read-set validation ([JANUS] `kernel/invalidation.py`). Nothing is ever executed or logged. This is the case the policy exists for.
5. **After commit**, the executed call stands; it is logged and cannot be undone. If a later correction changes the request, Janus issues the corrected call (new fingerprint) and speaks an honest reconciliation. Strict pass fails either way, but argument accuracy and response quality recover, and the user gets what they asked for.
6. **No automatic retries** under the profile: FDB mocks never fail transiently, and a retry would be an extra logged call.
7. Unknown tool or invalid arguments: never executed (G1/G8); Janus clarifies or fails honestly (existing `_fail_goal` path).
8. **Chained arguments** (`$RESULT_n`, e.g. `add_to_cart(product_id)` from `search_products`): the planner cannot know result shapes; FDB publishes none, and the measured plan bound `path: "product_id"` while the real result is `{"products": [{"product_id": …}]}` ([FDB] `mock_apis.py:48-50`). A step-output binding whose path does not resolve is resolved at bind time by a generic, schema-free search for the named field in the upstream result (first match, breadth-first). If that is ambiguous, the step blocks and Janus clarifies. It must never guess silently. This is a PlanExecutor binding change, with tests.

---

## 6. Latency budget (from the end of user speech, t = 0)

| Stage | Budget | Basis |
|---|---|---|
| Last STT segment final | 0.55 s VAD + ~0.15 s | [LK] Silero default; [MEASURED] RTF |
| Janus turn close (`T_eot`) | 1.0 s | §5 |
| **First speech** (acknowledgement): TTS first audio | **≈ 1.1 s** (Kokoro +0.09 s) | [MEASURED] |
| INTERPRET | 1.5 s, starting at the first segment final; speculative interpretation ([JANUS] `kernel/task.py._speculative_interpret`) can overlap it with the settle wait | [MEASURED] |
| PLAN | 1.9 s | [MEASURED] |
| **Tool execution** (≥ `T_commit`) | **≈ 3.5–4.5 s** (settle 2.0–3.5 s runs in parallel with INTERPRET/PLAN) | §5 |
| Mock tool | 0–0.8 s per call (×1–3 chained) | [FDB] |
| COMPOSE | 1.5 s, or ≈ 0 with response frames (C2, [JANUS] `kernel/frames.py`) | [MEASURED] |
| **Task completion** (final answer audible) | **≈ 5–8 s** | sum |
| Hard limit | ≈ 30 s | [DATA] |

The published FDB baselines' latencies have **not** been read yet (the arXiv paper, [UNKNOWN]). That's a day-1 item, so our targets can be compared against them.

---

## 7. Test strategy

| Level | What | Oracle | When |
|---|---|---|---|
| T0 | all 334 existing tests | unchanged | every commit |
| T1 unit | manifest introspection (12 tools, types, required, defaults); tool adapter log line byte-compatible with FDB's parser; voice-adapter mapping; default filling; step-output field search | direct assertions; FDB's own `json.loads` path | day 1–2 |
| T2 kernel policy (deterministic `SimHarness`) | irreversible profile: pause-split request with a correction → zero calls before settle; repeat function never executed twice; late correction → one corrected call + reconciliation; G4 decision; no retries | `TraceChecker` + new assertions | day 2 |
| T3 **text replay** (offline, no LiveKit, no audio) | the 100 recordings' transcripts, segmented with real word timings, fed through the voice adapter's event mapping → Janus with the FDB tool adapter → `result_janus.json` → **FDB's own, unmodified** `evaluate_pass_rate` / `evaluate_tool_calls` | FDB scorer (exact match without a key; gpt-4o judge with key) | daily from day 1; the main regression metric |
| T4 audio replay (offline) | stream each WAV through our VAD+STT in real time → Janus → Kokoro to a WAV → Parakeet → FDB result format | FDB scorer + latency analyser | day 2–3 |
| T5 LiveKit | FDB's own `run_tool_benchmark_all_released.py --provider janus` on a 20-recording stratified subset, then all 100 | FDB scorer | day 3–5 |

Existing Janus test rules carry over: deterministic `ScriptedProvider` for T0–T2; live models only in T3–T5; "NOT VERIFIED" until a run passes.

---

## 8. FDB-v3 validation strategy

- A dedicated FDB runtime environment (container, GPU via CDI):
  - Python 3.11;
  - **pinned** `livekit-agents==1.8.3` and plugins, `livekit==1.1.20`. FDB's README asks for `~=1.3`, which floats to the newest 1.x, so we pin what we test;
  - `nemo_toolkit[asr]` pinned, `openai`, `ffmpeg`;
  - FDB cloned at `3e799c45`, data verified by sha256.
- Never modify FDB files. Custom-provider runs call the three evaluators directly with `--provider janus`, because the shell wrapper hard-codes its provider list ([FDB] `run_all_evaluations_released.sh`).
- Report per run:
  - strict pass rate, tool F1, argument accuracy, response accuracy, turn-take rate;
  - first-response, tool-call and task-completion latency;
  - broken down by domain, difficulty and rollback.
- Store with commit hash, configuration and versions. **Three full runs** for variance before quoting a number.
- Compare with the published baselines (paper) once read.
- The judge needs `OPENAI_API_KEY` ([FDB] `evaluate_pass_rate.py:75-80`). Without it, pass rate falls back to exact match and response accuracy is 0. Development may use exact match; quoted results use the judge.

---

## 9. Day-by-day sequence

| Day | Deliverables | Exit test |
|---|---|---|
| **1 — Thu 25** | FDB runtime container; T3 text-replay harness; tool adapter + manifest introspection; Interpreter prompt shows parameter descriptions, types and optional parameters with defaults; step-output field search; LLM accuracy gate (§4.1); Parakeet round trip for Kokoro; read the FDB paper's baselines; copy the investigation scripts into the repo | T1 green; first T3 score on all 100 transcripts recorded |
| **2 — Fri 26** | irreversible tool-commit profile (§5) + end-of-turn/settle policy; voice adapter + LiveKit host (`AgentSession`, faster-whisper STT and Kokoro TTS classes); T4 audio-replay harness | T2 green; T3 not worse than day 1; T4 runs end to end on 5 recordings |
| **3 — Sat 27** | first LiveKit run (T5) on a 20-recording stratified subset | **Safety checkpoint (§10)** |
| **4 — Sun 28** | full 100-recording run; failure analysis; fixes; latency work (speculative interpretation on segments, response frames C2); rerun | full-run pass rate ≥ day-3 subset rate |
| **5 — Mon 29** | extension use case end to end (§13); one-command reproduction script verified in a clean container; 3 full runs for variance | reproduction script passes on a clean machine |
| **6 — Tue 30** | README + architecture diagram + results/logs; deck (≤ 8 slides); video script; **no new features**; final tag only with your confirmation | submission checklist complete |

## 10. Day-3 safety checkpoint

On the 20-recording subset, stratified across domains, difficulties and ≥ 4 self-corrections:

1. 0 crashes, hangs or watchdog salvages; the agent speaks and logs in 20/20 (turn-take 100 %).
2. Strict pass rate on the subset ≥ (T3 text-replay rate on the same 20) − 10 points. In other words, the voice path does not lose much.
3. Median first response ≤ 3 s; every task completes inside the recording window.

If a criterion fails, **no sample-agent fallback**. The corrective options, in order:
- switch STT and/or TTS to the measured alternate (a Config switch, no code change);
- disable speculative features and run the plain end-of-turn path;
- move extension work from day 5 to a smaller scope and spend days 4–5 on the voice path.

The decision and its evidence get recorded in `currentStatus.md`.

## 11. Rollback and checkpoint strategy

- Work on branch `fdb-v3-integration`. Tag each green milestone `fdb-ckpt-N`: day-1 harness, day-2 policy, day-3 LiveKit, full run, repro script.
- Every new behaviour sits behind Config flags that default off. The kit/legacy profile and all 334 tests stay green at every commit, so `main` can always fall back.
- FDB is never vendored or modified; it is cloned at the pinned commit by the script. This avoids licence and drift issues.
- Each evaluation artefact is stored with commit hash, config and versions under a gitignored results folder. Any number we quote is reproducible from its commit.
- No secrets in git; `.env` is gitignored ([JANUS] `.gitignore:11`).

## 12. Risks and mitigations

| Risk | Likelihood / impact | Mitigation |
|---|---|---|
| Premature tool execution on paused or corrected speech | high / fatal per scenario | §5 settle + triage + read-set invalidation; T2 and T3 rollback cases |
| Whisper hallucination in silence → phantom turns and calls | high without VAD / fatal | VAD mandatory; T4 checks the 30 s tails produce no segments |
| Parakeet mis-transcribes our TTS (IDs, numbers) → response judged wrong | medium | day-1 round trip through Parakeet; adjust TTS text normalisation if needed |
| Gemini rate limits / 5xx (13 % batch STT failures seen) | medium | STT and TTS local, so only the LLM is hosted; bounded retry already in `GeminiProvider` ([JANUS] `workers/gateway.py:55-75`); paid tier |
| LiveKit Cloud free-tier limits, or a network outage during Samsung's re-run | [UNKNOWN] | document the account requirement clearly; sequential runs (FDB is sequential anyway) |
| GPU memory: one Whisper + Kokoro copy per LiveKit process | medium on 8 GB, low on 48 GB | cap idle processes; measure on day 1 |
| Heavy runtime image (the investigation TTS image is 14 GB with PyTorch) | medium: slow reproduction | slim final image (CUDA runtime only, no dev tools), pre-download model weights in the setup step |
| `livekit-agents` version drift (`~=1.3` floats) | medium | pin exact versions in our requirements |
| Event-loop starvation from blocking code (the class of bug behind D5) | low now | D5 fix + yield in the driver; mock tools, STT and TTS in threads |
| Cross-scenario state leakage | low | process per job [LK]; fresh `Runtime` per room; no module-level mutable state ([JANUS] CS-24 audit) |
| Judge nondeterminism / unpinned `gpt-4o` alias | low–medium | three runs; report spread |
| Over-fitting to public items | reputational / disqualifying | no scenario strings or thresholds tuned per item; generic manifest introspection; constants fixed before scoring runs |

## 13. Extension use case (20 %)

**Primary: camera-grounded device troubleshooting.** It's the guide's own example. The user points a phone camera at a device and asks "what is this port for?", then corrects mid-sentence ("no wait, the other one"). The agent grounds the answer in the frame, looks up the manual, and opens exactly one support ticket.

Reused: Janus V3 perception ([JANUS] `kernel/perception.py`, `workers/vision.py`, `MediaPart`/`blob_resolver`), the correction/invalidation machinery, the duplicate-write protection (CommitGate G5/G6), and the old kit's `lookup_manual`/`create_support_ticket` tool semantics as the extension's mock tools.

New: a LiveKit video track → `video_frame` events plus a blob resolver.

Time-box: day 5. If video-track integration isn't working by midday, fall back to an **in-car destination change** (audio only, reroute tool, correction mid-route). It still runs end to end.

## 14. One-command reproduction requirements

`scripts/run_fdb_v3.sh` (name TBD) must, on a clean machine with Docker and an NVIDIA GPU:

1. check prerequisites and the required environment variables: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `GEMINI_API_KEY`, `OPENAI_API_KEY` (judge). Never print their values;
2. build the pinned runtime image;
3. clone FDB at `3e799c45`, then download and sha256-verify the data;
4. write `v3/.env.local` from the environment;
5. start the Janus LiveKit worker in the background and wait for registration;
6. run `run_tool_benchmark_all_released.py --provider janus`;
7. run `evaluate_tool_calls.py`, `evaluate_pass_rate.py` (both `--use-llm`) and `analyze_tool_latency.py`;
8. collect the reports, logs, configuration, versions and commit hash into one results folder;
9. stop the worker.

It must also declare the provider: "custom LiveKit agent (Janus) using Gemini 3.5 Flash-Lite + local faster-whisper large-v3-turbo + local Kokoro-82M". Everything that can be seeded is seeded (temperature 0, model versions pinned). FDB's own unseeded mock jitter is documented.

## 15. Unresolved decisions (need a human)

1. **LiveKit Cloud account** (URL, key, secret) — required for T5 and for Samsung's re-run instructions.
2. **OpenAI API key** — required by FDB's judge for any quotable number.
3. G4 commit-intent handling for undeclared tools (§5.2) — recommendation above; confirm on day 2 with tests.
4. `T_commit` trade-off (pass rate vs tool-call latency): recommendation is 2.0 s, adaptive to 3.5 s.
5. Extension choice (device troubleshooting vs in-car).
6. Team name, demo video recording, final submission tag.

## Appendix — where the measurements came from

The investigation scripts live in the session scratchpad and are *not* in the repo:
- `measure_audio.py`, `stt_bench/{bench_stt.py, pauses.py}`;
- `live_stt/bench_live_stt.py`, `llm_bench.py`, `tts_bench/bench_tts.py`;
- raw JSON results.

They are scheduled to be copied into the repository on day 1 so every number above can be regenerated.
