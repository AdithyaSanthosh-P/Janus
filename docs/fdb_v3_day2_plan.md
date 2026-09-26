# FDB-v3 Day 2 — Implementation Plan (for the implementing session)

Written 2026-09-26 by the planning session. **Nothing in this document is implemented yet.**
It refines `docs/fdb_v3_implementation_plan.md` §5 and §9 (Day 2) using what Day 1 actually
measured. Where the two disagree, this document wins for Day 2 work, because it is based on
live runs rather than projections.

Deadline: **30 Sep 2026**. Branch: `fdb-v3-integration` (last tag `fdb-ckpt-6`, 349/349 tests).

---

## 0. Before writing any code

1. Read, in order: `currentStatus.md` (the "FDB-v3 Day 1" and "Day 2 start" sections),
   this file, then `docs/fdb_v3_implementation_plan.md` §3–§7.
2. Confirm the baseline:
   ```bash
   cd /home/adi/Desktop/Hackathons/Prism
   git status && git log --oneline -3          # clean, at or after edf2937/e015eb5
   source .venv/bin/activate && python -m pytest tests/ -q   # expect 349 passed
   docker images | grep -E "janus-fdb-v3|janus-invest"      # three images present
   ```
   The user may have migrated Ubuntu from the external HDD to an internal-SSD partition (or to
   WSL2) since this was written. If `docker images` is missing images, stop and tell the user —
   do not silently rebuild for 40 minutes.
3. Paths used by the Day-1 tooling (override with env vars if they moved):
   - `FDB_V3_ROOT` = `~/Desktop/Hackathons/fdb_work/Full-Duplex-Bench/v3` (FDB at commit `3e799c45`)
   - `FDB_V3_DATA_ROOT` = `~/Desktop/Hackathons/fdb_work/data_dl/extracted/fdb_v3_data_released`
   - `scripts/fdb_v3/fetch_fdb.sh` re-creates both from scratch (sha256-verified) if they're gone.
4. Secrets: `GEMINI_API_KEY` is in `.env`. Never print it. `OPENAI_API_KEY` (FDB's judge) and
   LiveKit Cloud credentials are **still not available** — plan around that (see WP6).

### Hard rules (from the user, unchanged)

- Preserve the architecture: single-writer kernel, read sets, invalidation, `ResultRouter`,
  `CommitGate`, commit-last ordering. Adapters only translate; nothing bypasses `Kernel.step()`.
- **All new behaviour behind `Config` flags that default off.** All existing tests stay green at
  every commit.
- Never modify Full-Duplex-Bench code or scoring. Never hardcode benchmark scenarios, utterances,
  or answers. Never tune a threshold or lexicon against individual test items.
- Do not use FDB's sample agent as a fallback.
- Commit after each green milestone and tag `fdb-ckpt-N` (next is `fdb-ckpt-7`). **Do not push**
  (this environment has no GitHub credentials anyway). Never create `PRISM_GENAI_HACKATHON_Y2026`.
- Say "NOT VERIFIED" until a test or a real run proves it. Update `currentStatus.md` after each
  work package.
- Python 3.10–3.12 compatible syntax only (no `ExceptionGroup`, `TaskGroup`, `Self`).
- Code that imports `livekit` must live **outside `src/prism_rt/`** so the core package and the
  test suite never need LiveKit installed.

---

## 1. What Day 1 measured (the evidence this plan is built on)

Full 100-recording text replay (`scripts/fdb_v3/run_text_replay.py`, `gemini-3.5-flash-lite`,
`--g4-exempt-undeclared-mutability`, exact-match scoring, no voice):

| Metric | Value |
|---|---|
| Strict pass rate | **33 / 100** |
| Tool-selection accuracy | 0.832 |
| Argument accuracy (exact match) | 0.411 |
| Pass rate by difficulty | easy 0.50 · medium 0.353 · hard 0.10 |
| Pass rate, self-correction recordings | 0.529 (17 recordings) |

Failure breakdown (computed from `run_output/fdb_v3_text_replay_full100_g4fix/raw_results.json`):

| Expected calls | Scenarios | Correct tool set |
|---|---|---|
| 1 | 66 | 61 |
| 2 | 18 | **0** |
| 3 | 16 | **0** |

- **Every one of the 39 failures is under-calling.** No scenario had an extra or wrong tool.
- **Janus never executes more than one tool per request.** 31 of the 34 multi-call scenarios end
  with a confident FINAL after the first call (e.g. "Your order … has been successfully confirmed"
  when `search_products` was also requested).
- 6 scenarios legitimately expect **the same function twice** with different arguments — so a
  "never call the same function twice" rule would be wrong. Only identical calls must be blocked
  (that is what G5 fingerprint / G6 lineage already do).
- Published baselines (`docs/measurements.md`): Pass@1 0.41–0.60, GPT-Realtime best at 0.600.

Root cause of the multi-call gap (read from code, **confirm with a decision log before fixing**):
the Planner's view (`kernel/task.py._build_plan_request`) contains only `intent` (one string),
`facts` (extracted slots), and the catalog — never the user's words. The Interpreter extracts
slots for the first action only. The Planner therefore plans one step, the goal completes, and the
Composer writes a success sentence from that one result.

Two things are already true and must **not** be rebuilt:
- Every FDB tool is undeclared-mutability → catalog default STATE_CHANGING →
  `_fix_step_kind` makes every FDB step a WRITE. So G3 (floor closed), G5, G6, G10/G11
  (settle / no newer turn) and the TRIAGE hold **already apply to every FDB call**. "Every tool
  call is irreversible" is mostly already in place.
- G4 (explicit commit intent) is exempted for undeclared tools by
  `Config.g4_exempt_undeclared_mutability` (done, `fdb-ckpt-5`).

---

## 2. Work packages and order

Recommended order: **WP1 → WP4 → WP5 → WP6** (the voice path is existential: without it the
benchmark score is 0 no matter how good the text path is), then **WP2 → WP3** (the biggest score
levers). WP2/WP3 touch different files (`workers/`, `kernel/task.py`, `kernel/proposals.py`)
from WP4–WP6 (`adapters/`, `scripts/`, `docker/`), so a second session can run them in parallel
if the user wants.

Rough effort: WP1 1–2 h · WP2 3–4 h · WP3 1 h · WP4 1–2 h · WP5 2 h · WP6 3–5 h (highest
uncertainty: LiveKit plugin APIs).

---

### WP1 — The FDB commit profile (plan §5), small

Goal: one place that turns on every FDB-specific policy, plus the "incomplete turn" settle
extension.

1. **Profile constructor**, e.g. `fdb_v3_config() -> Config` in a new `src/prism_rt/profiles.py`
   (a function that returns an ordinary `Config`; no global default changes). Sets:
   - `g4_exempt_undeclared_mutability=True`
   - `settle_barrier_enabled=True`, `settle_ms=1000` (with the voice bridge's 1.0 s end-of-turn
     silence this means no tool executes before ~2.0 s of user silence)
   - new `settle_ms_incomplete=2500` (→ ~3.5 s total, see 2.)
   - no automatic retries: check the exact semantics of `max_read_retries` / `max_write_retries`
     in `kernel/executor.py` first (is it "retries" or "attempts"?) and set whatever means
     *zero re-executions*. A retry is an extra logged call, which fails FDB's strict pass.
   - leave `triage_hold_enabled=True` (default) — it is the main real protection (below).
   - add new WP2/WP3 flags here as they land.
   - Use it from `scripts/fdb_v3/run_text_replay.py` via a `--profile fdb` option (keep the old
     flags working).
2. **Incomplete-turn settle extension** (new flag, default off):
   - At G10 (`kernel/commit.py`), use `settle_ms_incomplete` instead of `settle_ms` when the
     latest closed user turn looks unfinished: its text (the `turn.<tid>.prefix` fact) ends —
     after stripping punctuation — with a trailing filler or connective, or with an existing
     `CORRECTION_CUES`/`HOLD_CUES` phrase (`config/lexicons.py`), or the latest interpretation
     act was UNCLEAR.
   - Add the needed generic English lexicon to `config/lexicons.py` (e.g. fillers `um, uh, er,
     hmm, like`; connectives `and, or, but, so, then, to, for, with, the, a, an, of, my`).
     **Fixed general-English lists, written before any scoring run, never adjusted to make a
     particular recording pass.**
   - G10 must also schedule its liveness wake (TimerWheel / `next_wake_us`) at the *extended*
     time, or the blocked call will only be re-checked when some other event arrives
     (the D1 bug class — see `currentStatus.md` P0.1).
3. Honest expectation to record: with live-model latency, a call is only *proposed* ~T_eot +
   INTERPRET + PLAN ≈ 1.0 + 1.5 + 1.9 ≈ 4.4 s after speech ends — longer than the longest
   mid-request pause in the data (3.46 s). So G10 will rarely be the thing that saves us; G11 (a
   newer turn is open) and the TRIAGE hold (a closed turn not yet interpreted) are. Implement G10's
   extension anyway (speculative interpretation later shortens that 4.4 s), but test G11/TRIAGE
   paths explicitly.

Tests (T2, `SimHarness` + `ScriptedProvider`, new file e.g. `tests/test_fdb_profile.py`):
- pause-split correction: "book a flight to Paris" + EOT, then 1.5 s later "no, Berlin" + EOT →
  no call with Paris is ever emitted; exactly one call with Berlin.
- a turn ending in "and" is not committed before the extended settle, and is committed after it
  (with only liveness wakes driving the clock — use `fire_liveness_if_due`, not fixed-step
  `drain`, so a missing wake is caught).
- tool error → no second attempt; honest FINAL with `task_completed=False`.
- same function twice with different args (two `track_order` calls) is still allowed; the
  identical call twice is blocked.
- flag-off parity: nothing changes without the profile (the existing 349 tests prove this).

Exit: tests green; T3 with `--profile fdb` on the first 20 scenarios not worse than 45 % strict
(the Day-1 number with the G4 flag). Tag `fdb-ckpt-7`.

---

### WP2 — Multi-action requests (the biggest score lever: 34 % of the benchmark is at 0 %)

1. **Confirm the root cause first.** Run one 2-call and one 3-call scenario with `--debug`
   (e.g. `--only ecommerce_10`, `--only ecommerce_18`) and read the JSONL decision log
   (`/tmp/janus_fdb_decision_log_<folder>.jsonl`): check which slots INTERPRET committed and how
   many steps the PLAN produced. Also call the live Interpreter/Planner in isolation with the
   same view, the way Day 1 found the bare-fact-key bug (see `currentStatus.md`).
2. **Design (recommended; adjust if step 1 shows otherwise):**
   - Interpreter: add an optional, additive `actions` field to `INTERPRET_SCHEMA`
     (`workers/interpreter.py`) — the ordered list of tool names the user asked for — and prompt
     it to extract slot_deltas for *every* requested action, not just the first. Store it as a
     goal fact (e.g. `goal.<gid>.actions`) in `kernel/interpret_apply.py`.
   - Same parameter name needed with two different values (the 6 same-function scenarios, and
     shared names like `max_price`): allow an indexed slot name (e.g. `order_id#2`). The Planner
     binds a parameter to any fact key, so slot names need not equal parameter names — but
     **first check** whether `interpret_apply` or `executor._invalid_slot_input` rejects slot names
     that aren't catalog parameters.
   - Planner view (`kernel/task.py._build_plan_request`): add `actions` and the goal's user
     utterance text as context; put the text's fact key(s) in the PLAN job's read set so a new or
     corrected turn makes an in-flight PLAN stale. Prompt: one step per requested action, in order;
     bind user-stated values to `slot.$G.<name>` facts, chained values to `step_output` — do not
     copy user-stated values in as literals (literals bypass correction invalidation).
   - **Completion guard** (flagged): a goal must not complete while any requested action has no
     step in the plan. Replan once; if still missing, give an honest partial FINAL
     (`task_completed=False`) naming what was not done. This makes "confident FINAL after one of
     three" structurally impossible.
3. Pitfalls to test for explicitly:
   - **Replanning after a step already executed.** G6's lineage is `goal_id:step_key`, and
     `step_key` is the model's `local_id`. If a replan reuses a local_id for a *different* tool,
     G6 wrongly blocks it (the `v10-3` livelock class). If it gives the *same* executed action a
     new local_id with identical args, G5 (fingerprint) must still block the duplicate. Both need
     tests.
   - Chained arguments: FDB expects e.g. `add_to_cart(product_id="$RESULT_0.…")`. The Day-1
     generic field search (`kernel/executor.py._bfs_find_field`) resolves the real nested field;
     keep a test with the real `search_products` result shape
     (`{"products": [{"product_id": …}]}`).
   - A correction ("…add two — no, three of them") on a multi-action goal must still invalidate
     only what it touches.

Tests: scripted multi-action goals (2-step chained, 3-step, same-function-twice, correction
mid-goal, completion guard, replan-after-execute). Then live T3, full 100, with the profile.

Exit: multi-call scenarios with the correct tool set > 0 (target ≥ 50 % of 34); single-call not
worse than 61/66; overall strict ≥ 33. Tag the next checkpoint.

---

### WP3 — Honest FINAL wording, small

Evidence: travel_21's FINAL said a flight booking was "successfully confirmed" when only
`search_flights` ran; ecommerce_10 said an order was "successfully confirmed" for a tracking
lookup. The Composer's prompt (`workers/composer.py`) sees only raw result facts — not which tools
ran, nor what the user asked for.

- Add to the COMPOSE view (`kernel/task.py._build_compose_request`): the executed calls
  (tool, args, ok/error) and, once WP2 lands, the requested actions not done.
- Prompt: report only what the executed list shows; state the key values from the results (IDs,
  prices, dates); plainly say what was not done; never say booked/confirmed/added unless that tool
  actually ran; one or two spoken sentences. (FDB's response-quality judge compares against lines
  like "I'll look up flights to Chicago for December 12th right away.")
- Tests: unit-test the view contents (scripted providers can't test honesty). Then a live spot
  check on travel_21, ecommerce_10 and two others — NOT VERIFIED until run.

---

### WP4 — The voice bridge (pure translation, no LiveKit import)

New `src/prism_rt/adapters/voice_bridge.py`. One class used by **both** the offline audio replay
(WP5) and the LiveKit agent (WP6), so the exact logic that runs live is tested offline.

Responsibilities (translation only — it never drops, delays, or rewrites a Janus action):
- Inputs: `on_speech_start(t)`, `on_segment_final(text, t)`, `on_speech_end(t)`, a periodic
  `tick(now)`, plus the manifest (from `adapters/fdb_manifest.py`).
- Emits Janus raw events in `HarnessCodec`'s dialect **always with `ts_us`** (Day-1 lesson: an
  event without `ts_us` is silently dropped by `HarnessCodec.decode`). Use **monotonic**
  microseconds since the bridge started, not `time.time()`: `CoupledClock` (`adapters/clock.py`)
  is "last event timestamp + monotonic elapsed", so a monotonic source keeps both in one time base.
- End of turn: emit `end_of_turn` after `T_eot` (default 1000 ms, constructor argument) of silence
  after the last final segment; a new speech start cancels the pending timer. This is the one piece
  of timing the bridge owns — it is the transport's "user stopped" signal, like the old kit's
  `end_of_turn` flag, not a semantic decision.
- User speech starting while the agent is speaking → `interruption` event (FDB never does this;
  real users do).
- Actions: SPEAK/CLARIFY/FINAL text → `say(text)` callback; TOOL_CALL →
  `FdbToolAdapter.execute(...)` → `tool_result` event (with `ts_us`); CANCEL → debug log only
  (FDB mock calls can't be cancelled; under WP1 a call is never executed before it's final).
- Interim transcripts: ignored in this version (Janus chunks are append-only).
- Janus itself runs as `Runtime.run_scenario(events, actions)` (`entry.py`) in the same event loop.

Unit tests with a fake clock and fake `say`/tool sinks: segment → chunk events; EOT only after
`T_eot` of silence; speech start cancels a pending EOT; tool call → tool_result with `ts_us`;
barge-in → interruption. Tag the next checkpoint.

---

### WP5 — T4 offline audio replay

New `scripts/fdb_v3/run_audio_replay.py`. Replaces T3's "whole transcript as one chunk" with the
recordings' real speech timing.

1. `ffmpeg` → 16 kHz mono. faster-whisper `large-v3-turbo`, **`vad_filter=True` is mandatory**
   (without it Whisper invents sentences in the 30 s silent tail — measured last session),
   `word_timestamps=True`. Split into segments at gaps ≥ 0.55 s (Silero's default, which is what
   LiveKit will use).
2. Replay in real time through the WP4 bridge into Janus (live `gemini-3.5-flash-lite`): emit each
   segment's final at `segment_end + ~0.15 s` (measured STT latency); let the bridge's own
   `T_eot` timer produce end of turn.
3. Collect spoken text (the FDB "transcript") and tool calls (FdbToolAdapter log). Optional
   `--tts-roundtrip`: Kokoro → Parakeet (already verified to preserve IDs/prices, so off by
   default). Score with FDB's own evaluators — factor the scoring code out of
   `run_text_replay.py` into a small shared module rather than copying it.
4. Also record first-speech, first-tool-call and FINAL latency relative to speech end.

Environment: faster-whisper is in the `janus-invest-stt` image, not in `janus-fdb-v3`; the model
weights are already cached in the Docker volume `janus_invest_hf`
(`models--mobiuslabsgmbh--faster-whisper-large-v3-turbo`, `models--hexgrad--Kokoro-82M`). Either
run T4 in a container with that volume mounted at `/root/.cache/huggingface`, or add
faster-whisper to `janus-fdb-v3` (WP6 needs that anyway).

Exit: 5 recordings end to end — at least 2 self-correction recordings and 1 with a ≥ 1 s
mid-request pause — no crash, calls logged, and no phantom segments from the silent tail.
T3 not worse than Day 1.

---

### WP6 — LiveKit host

New `scripts/fdb_v3/janus_livekit_agent.py` (+ `scripts/fdb_v3/lk_plugins/` for the STT/TTS
classes). Mirror FDB's own `v3/lk_agent_tool.py` structure:

```python
server = AgentServer()

@server.rtc_session()
async def entrypoint(ctx):
    session = AgentSession(vad=silero_vad, stt=StreamAdapter(stt=FasterWhisperSTT(...), vad=silero_vad),
                           tts=KokoroTTS(...), llm=None)
    # user_input_transcribed (final only) + user-state events -> VoiceBridge
    # bridge.say -> session.say(text, allow_interruptions=True)
    # tools: FdbToolAdapter(MockAPIRegistry(latency_profile=...), room_name=ctx.room.name,
    #                        log_path="/tmp/agent_tool_calls.log")
    # Janus: setup(config=fdb_v3_config(), provider=GeminiProvider("gemini-3.5-flash-lite"))
    await session.start(room=ctx.room, agent=Agent(instructions=""))

if __name__ == "__main__":
    agents.cli.run_app(server)
```

**Verify in the container's installed `livekit-agents==1.8.3` source before writing code**
(don't trust memory or this sketch): the custom STT abstract method (`_recognize_impl`?) and
`stt.StreamAdapter` signature; the custom TTS API (`synthesize` → `ChunkedStream`, emitter
style in 1.x); the `AgentSession` event names for final transcripts and user speaking state; that
`llm=None` skips auto-reply (the plan cites `voice/agent_activity.py` ~2836); the option limiting
idle worker processes (each process loads its own models — set to 1 on the 8 GB GPU).

Parity with FDB's own agent: `MockAPIRegistry(latency_profile="instant")` by default with the same
`--latency <profile>` CLI override FDB's agents accept; log line format and room keying are already
byte-compatible (`adapters/fdb_tool_adapter.py`). The agent must share `/tmp` with FDB's runner
(same container, or bind-mount `/tmp`), because FDB reads `/tmp/agent_tool_calls.log`.

Container (`docker/fdb_v3/Dockerfile`): add `faster-whisper`, `kokoro`, `soundfile`, and bake in
spaCy `en_core_web_sm` (Kokoro downloads it at runtime otherwise — seen on Day 1). Pin versions
(`janus-invest-*` used kokoro 0.9.4, faster-whisper 1.2.1). Watch for torch/nemo conflicts.
**Verify by running the image and importing things — Day 1's build "succeeded" three times while
broken.** Mount `janus_invest_hf` at `/root/.cache/huggingface` so weights (and Parakeet) are
cached across runs. Adding a layer after the big pip step keeps its cache; editing the big pip
line re-downloads ~5 GB at ~2.3 MB/s.

**No LiveKit Cloud account yet — use a local dev server instead:**
```bash
docker run --rm --network host livekit/livekit-server --dev
# LIVEKIT_URL=ws://127.0.0.1:7880  LIVEKIT_API_KEY=devkey  LIVEKIT_API_SECRET=secret
```
FDB's `livekit_inference.py` only reads those three env vars. FDB agents use automatic dispatch
(no agent name), so **only one agent worker may be registered** at a time or FDB's reference agent
and Janus will both join. FDB's `--provider` is only a label for output file names, so
`--provider janus` works (no allowlist). Check `run_tool_benchmark_all_released.py` for how to limit
a run to one or a few recordings.

Exit: one FDB recording streamed by FDB's own `livekit_inference.py` into a local room → Janus
hears it, logs ≥ 1 tool call to `/tmp/agent_tool_calls.log`, speaks, and FDB writes
`result_janus.json`. LiveKit Cloud stays NOT VERIFIED until the user provides credentials (Day 3).

---

## 3. Day 2 exit checklist

- [ ] WP1: profile + incomplete-turn settle, T2 tests green, `fdb-ckpt-7`
- [ ] WP4: voice bridge + unit tests
- [ ] WP5: T4 on 5 recordings end to end
- [ ] WP6: one recording through FDB's own LiveKit client against a local dev server
- [ ] WP2: multi-call scenarios > 0 %; T3 full 100 re-measured with the profile
- [ ] WP3: honest FINAL, live spot-checked
- [ ] All tests green at every commit; `currentStatus.md` updated per WP; nothing pushed

Report at the end of Day 2: T3 full-100 numbers with the profile; T4 per-recording results and
latencies; what was not finished and why.

## 4. Deliberately not in Day 2

- Tuning `T_eot` / settle values against scores (they're fixed distribution-level constants).
- Acknowledgement timing. An ACK at 1.0 s can land inside a mid-request pause (25 recordings pause
  ≥ 1 s), which FDB's paper counts as an interruption; that metric doesn't affect pass rate. Measure
  it in T4/T5 and decide on Day 4.
- Speculative interpretation on segment finals (latency work, Day 4).
- Jev / LiveKit turn-detector comparison (Day 4, needs a key).
- The extension use case, README, video, one-command script (Days 5–6).

## 5. Needs the user

- LiveKit Cloud URL/key/secret (for Day 3's T5 run and Samsung's re-run instructions).
- `OPENAI_API_KEY` (FDB's gpt-4o judge — every number so far is stricter exact-match).
- Pushing the branch (no GitHub credentials in this environment).
- Whether to run WP2/WP3 in a parallel session.
