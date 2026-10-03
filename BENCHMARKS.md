# Janus benchmark record

Every benchmark run of Janus on this project, in one place: FDB-v3 text-replay and voice runs, the failures behind the misses, and the other measurements (tests, kernel latency, adversarial exploration). The numbers come from the runs' own reports; nothing here is estimated unless it says so.

This file is generated: `python benchmarks/collect.py` refreshes the CSVs from the run folders and archives, and `python benchmarks/build_report.py` rebuilds this page from `benchmarks/report_template.md`. The data is in `benchmarks/`:

| File | One row per | Use it for |
|---|---|---|
| `runs.csv` | scored run (80) | score vs time, configuration comparisons |
| `recordings.csv` | scenario in each text-replay run (2,344) | per-scenario and per-domain pass rates, text-replay latency |
| `voice_recordings.csv` | recording in each voice run (602) | per-speaker before/after, turn-ending evidence, latency |
| `failures.csv` | failing recording in an analysed failure set (129) | cause analysis; joins to `voice_recordings.csv` on scenario + speaker |
| `failure_classes.csv` | failure class in a taxonomy | class definitions, counts, status |
| `other_measurements.csv` | measurement | tests, kernel latency, exploration, speech round trip, reference numbers |

Dates are 2026. Reports, logs and per-room decision logs of the six cloud voice runs are in `docs/runs/` (audio left out).

## 1. Where things stand (3 Oct)

| Measure | Value | Run | Note |
|---|---|---|---|
| **Voice over LiveKit, all 100, judged, current code** | **73%** (73/100) | `cloud_20261002_182600` | 3 Oct, first full run after the turn-ending fixes; hosted speech-to-text; tool selection 0.947, argument accuracy 0.768, response quality 0.626; turn-taking 99/100; average latency 8.1 s; no FDB client aborts; exact-match scoring gives 68/100 |
| Text replay, all 100, judged | **77%** (77/100) | `text_text_replay_0930_final` | Tool selection 0.949, argument accuracy 0.803; configuration of the 30 Sep submission |
| Voice over LiveKit, all 100, exact | **41%** (41/100) | `cloud_20260929_232528` | 29-30 Sep, before the voice fixes below; local Whisper |
| Voice over LiveKit, all 100, judged | **42%** (42/100) | `cloud_20260929_232528.judged` | Same run, scored with FDB's judge |
| Voice, 41 recordings the full run mostly failed, judged | **17/41** (41.5%) | `cloud_20260930_174502` | Re-run of the same recordings with fixes and hosted speech-to-text; the full run had 6/41 on them |
| Voice, 26 recordings, exact | **16/26** (61.5%) | `cloud_20261002_061207` | After the turn-ending fix; the same recordings scored 12/26 on 30 Sep |

The 3 Oct run is the first full-100 voice run since the turn-ending fixes. It is one run (hosted models and FDB's mock-tool timing add run-to-run noise of a few recordings), on the code at commit `8020f79` plus a fix to the native setup script that does not touch the agent. The voice-versus-text gap, which was 77% against 41-42% on 30 Sep, is now 77% against 73%. The sections below say which comparisons are like for like and which are not.

For context, the benchmark's public paper reports (judged strict pass@1):

| System | Strict pass@1 (%) | First response (s) |
|---|---|---|
| GPT-Realtime | 60.0 | 6.89 |
| Gemini Live 3.1 | 54.0 | 4.25 |
| Gemini Live 2.5 | 49.0 | 7.26 |
| Cascaded (Whisper -> GPT-4o -> TTS) | 45.0 | 10.12 |
| Grok | 43.0 | 6.65 |
| Ultravox | 41.0 | 8.4 |

These are reference numbers only: different hardware, models and run dates, and no per-recording detail.

## 2. Text replay: the reasoning path in isolation

`scripts/fdb_v3/run_text_replay.py` feeds each scenario's transcript through Janus's real async entry point (live model, no audio) and scores with FDB's own evaluator, so it measures decisions without speech recognition or turn-taking. All 100 recordings, one run each, so differences of a point or two are noise (see arms A and A2 below).

| Date | Run | Scored | Passed | Strict pass | Tool sel. | Arg. acc. | What changed |
|---|---|---|---|---|---|---|---|
| 2026-09-25 | `text_fdb_v3_text_replay_full100` | exact | 21/100 | 21.0% | 0.493 | 0.250 | Full 100, gemini-3.5-flash-lite; G4 commit-intent gate blocked undeclared-mutability tools |
| 2026-09-25 | `text_fdb_v3_text_replay_full100_g4fix` | exact | 33/100 | 33.0% | 0.832 | 0.411 | G4 exemption for tools that declare no mutability |
| 2026-09-26 | `text_fdb_v3_text_replay_wp2` | exact | 42/100 | 42.0% | 0.931 | 0.505 | Multi-action interpretation + settle barrier + FDB profile |
| 2026-09-27 | `text_s2_full100_validation` | exact | 52/100 | 52.0% | 0.942 | 0.610 | Quick-win pack (11 items) before value rules |
| 2026-09-27 | `text_s2_full100_v2` | exact | 64/100 | 64.0% | 0.920 | 0.703 | Plus value rules (date ordinals, currency codes, account words, leading articles) |
| 2026-09-27 | `text_s2_full100_v3` | exact | 68/100 | 68.0% | 0.941 | 0.735 | Leading-article rule reverted (FDB labels inconsistent on articles) |
| 2026-09-28 | `text_s3_full100_B` | exact | 62/100 | 62.0% | 0.908 | 0.678 | Arm B: assume-and-say only |
| 2026-09-28 | `text_s3_full100_A` | exact | 65/100 | 65.0% | 0.927 | 0.723 | Arm A: action plans and assume-and-say both off |
| 2026-09-28 | `text_s3_full100_A2` | exact | 66/100 | 66.0% | 0.923 | 0.713 | Arm A2: repeat of A (run-to-run noise) |
| 2026-09-28 | `text_s3_full100_C` | exact | 66/100 | 66.0% | 0.964 | 0.740 | Arm C: action plans + assume-and-say, earlier prompt |
| 2026-09-28 | `text_s3_full100_C2` | exact | 70/100 | 70.0% | 0.974 | 0.768 | Arm C2: both on, current prompt (the arm kept) |
| 2026-09-28 | `text_s3_full100_D` | judged | 73/100 | 73.0% | 0.933 | 0.767 | Arm D: C2 re-run after late binding (judged), flash-lite |
| 2026-09-28 | `text_s3_full100_E_flash` | judged | 73/100 | 73.0% | 0.953 | 0.760 | Arm E: same as D with gemini-3.6-flash (became the default model) |
| 2026-09-30 | `text_text_replay_0930_setting` | judged | 75/100 | 75.0% | 0.963 | 0.795 | Tried: 'update a setting' prompt rule (reverted: 75% vs 77%) |
| 2026-09-30 | `text_text_replay_0930_final` | judged | 77/100 | 77.0% | 0.949 | 0.803 | Configuration of the 30 Sep submission |
| 2026-09-30 | `text_text_replay_0930_fixes` | judged | 77/100 | 77.0% | 0.959 | 0.807 | Identifier-style value rule + spoken-ID joiner |
| 2026-10-03 | `text_textreplay_fix_1003` | judged | 79/100 | 79.0% | 0.924 | 0.802 | Full 100 after the kernel fixes of the 3 Oct fix pass (clarify livelock, null slot delta), judged |

What moved the score, in order:

| Step | Change | Effect |
|---|---|---|
| Day 1 | Harness plus a fix for bare fact-binding keys in live plans | 0% to 21% |
| Day 1-2 | The commit-intent gate (G4) no longer blocks tools that declare no mutability (FDB declares none) | 21% to 33% |
| Day 2 | Multi-action interpretation, settle barrier, FDB profile | 33% to 42% |
| S2 | 11 quick wins: never-silent safety nets, speech lint, targeted re-extraction | 42% to 52% |
| S2 | Value rules read from the mismatch data: date ordinals (12 of 38 argument mismatches), currency codes, account words | 52% to 68% |
| S3 | Per-action interpretation compiled straight into the plan; assume-and-say for unstated numbers; late binding of chained arguments | 68% to 73% judged |
| 28 Sep | Default model gemini-3.5-flash-lite to gemini-3.6-flash | Same 73%; 3-call scenarios 50% to 69% |
| 30 Sep | Identifier-style value rule, spoken-ID joiner | 73% to 77% judged |

### S3 A/B (28 Sep, same day, same machine)

The kill rule for action plans was a judged gain of at least 5 points. Exact-match and judged scores are shown because late binding (arguments taken from an earlier result) only registers under the judge.

| Arm | Configuration | Exact passed /100 | Exact arg. acc. | Judged passed /100 | Judged arg. acc. |
|---|---|---|---|---|---|
| A | action plans and assume-and-say both off | 65 | 0.723 | 66 | 0.723 |
| A2 | repeat of A (run-to-run noise) | 66 | 0.713 | 68 | 0.718 |
| B | assume-and-say only | 62 | 0.678 | 63 | 0.675 |
| C | action plans + assume-and-say, earlier prompt | 66 | 0.740 | 72 | 0.765 |
| C2 | both on, current prompt (the arm kept) | 70 | 0.768 | 73 | 0.767 |
| D | C2 re-run after late binding (judged), flash-lite | - | - | 73 | 0.767 |
| E | same as D with gemini-3.6-flash (became the default model) | - | - | 73 | 0.760 |

A and A2 are the same configuration: the run-to-run spread on 100 scenarios is about 1-2 points. C2 against the average of A and A2 is +4.5 points exact and +6 judged.

### By domain and difficulty (30 Sep final, judged)

Finance 25/25, travel 19/20, e-commerce 23/29, housing 10/26. By expected calls: 1 call 54/66, 2 calls 14/18, 3 calls 9/16. By difficulty: easy 30/36, medium 26/34, hard 21/30. By disfluency: self-correction 82.4%, false start 75%, filler 76%, hesitation 70%, pause 61%. Housing is the weakest domain for a structural reason: the `search_apartments` schema declares no `pets_allowed` parameter and `update_search_filter.filter_name` has no declared values, while the labels use both.

## 3. Voice runs: the scored path

Voice runs stream FDB's recordings through LiveKit to the Janus worker (speech recognition, kernel, speech synthesis) and score the tool calls it makes. "Exact" is FDB's exact-match scoring; "judged" is its gpt-4o argument judge.

| Date | Run | Scored | Passed | Strict pass | Tool sel. | Arg. acc. | Avg response | What it was |
|---|---|---|---|---|---|---|---|---|
| 2026-09-27 | `lk_20260927_174631` | exact | 1/5 | 20.0% | 0.750 | 0.250 | 8.2 s | 5 recordings (after the first reboot) |
| 2026-09-27 | `lk_20260927_175223` | exact | 11/26 | 42.3% | 0.984 | 0.524 | 7.5 s | 26-recording stratified sample, first voice baseline (laptop GPU) |
| 2026-09-27 | `lk_20260927_213947` | exact | 15/26 | 57.7% | 0.970 | 0.682 | 6.2 s | Same 26 recordings after the quick-win pack and value rules |
| 2026-09-27 | `lk_20260927_222409` | exact | 3/6 | 50.0% | 1.000 | 0.750 | 10.4 s | Targeted check of the speech-recognition silence fix |
| 2026-09-27 | `lk_20260927_223446` | exact | 3/6 | 50.0% | 1.000 | 0.750 | 6.2 s | Targeted check of the speech-recognition silence fix |
| 2026-09-27 | `lk_20260927_225322` | exact | 2/4 | 50.0% | 1.000 | 1.000 | 12.2 s | Targeted check, 4 recordings |
| 2026-09-27 | `lk_20260927_230003` | exact | 3/4 | 75.0% | 1.000 | 1.000 | 5.3 s | Targeted check, 4 recordings |
| 2026-09-27 | `lk_20260927_230718` | exact | 15/26 | 57.7% | 0.841 | 0.619 | 6.1 s | 26-recording sample after the speech-recognition CUDA fixes |
| 2026-09-29 | `lk_20260927_230718.judged` | judged | 16/26 | 61.5% | 0.841 | 0.667 | 6.1 s | 26-recording sample after the speech-recognition CUDA fixes |
| 2026-09-30 | `cloud_20260929_232528` | exact | 41/100 | 41.0% | 0.844 | 0.611 | 8.0 s | All 100 recordings, native path, RTX 3090, local Whisper (fell back to CPU for the last quarter) |
| 2026-09-30 | `cloud_20260929_232528.judged` | judged | 42/100 | 42.0% | 0.844 | 0.611 | 8.0 s | All 100 recordings, native path, RTX 3090, local Whisper (fell back to CPU for the last quarter) |
| 2026-09-30 | `cloud_20260930_032600` | exact | 11/32 | 34.4% | 0.877 | 0.500 | 7.8 s | 32 hardest recordings of the full run, after the Whisper GPU fix |
| 2026-09-30 | `cloud_20260930_140536` | exact | 12/64 | 18.8% | 0.363 | 0.240 | 7.1 s | 64 recordings, hosted speech-to-text; 33 rooms were served by a stray demo worker and score 0 (31 valid) |
| 2026-09-30 | `cloud_20260930_174502` | judged | 17/41 | 41.5% | 0.835 | 0.524 | 6.4 s | 41 recordings (clean re-run of the 29 contaminated ids), hosted speech-to-text, judged |
| 2026-10-02 | `cloud_20261002_061207` | exact | 16/26 | 61.5% | 0.958 | 0.736 | 6.8 s | 26 recordings (10 split-turn failures, 5 fragile passes, 3 controls), local Whisper, after the turn-ending fix |
| 2026-10-02 | `cloud_20261002_182600` | judged | 73/100 | 73.0% | 0.947 | 0.768 | 8.1 s | All 100 recordings, judged, current code (commit 8020f79 plus the native setup fix), native path on a clean RTX 3090 box, hosted speech-to-text; first full run after the turn-ending fixes (run 3 Oct IST, box clock 2 Oct UTC) |
| 2026-10-03 | `cloud_20261003_121822` | judged | 65/100 | 65.0% | 0.917 | 0.704 | 9.0 s | All 100 recordings, judged, commit 054c305 (fix pass of 3 Oct), same box and providers as cloud_20261002_182600: 65/100. Hosted speech-to-text was slower that day; 19 turns closed while their last segment was still being transcribed (3 in the 73% run), splitting requests. Fixed in a8d1fcd |
| 2026-10-03 | `cloud_20261003_143517` | judged | 24/32 | 75.0% | 0.963 | 0.807 | 7.1 s | Re-test of the 23 ids (32 recordings) hit by the early turn close, commit a8d1fcd (the bridge waits for a running transcription): 24/32, against 15/32 in cloud_20261003_121822 and 23/32 in cloud_20261002_182600; no turn closed before its transcript |

Read these with care:

- **The laptop runs (27-28 Sep) use a 26-recording stratified sample** of the first scenarios of each domain, not the full 100, so they are not comparable with the full runs. Within the sample, 42.3% to 57.7% shows the effect of the S2 fixes on the real voice path.
- **`cloud_20260930_140536` is contaminated.** A demo worker, registered without a name on the same LiveKit project, was handed 33 of the 64 rooms and scored 0 on them. Only the other 31 recordings (12 exact, 13 judged) are valid.
- **`cloud_20260930_174502` re-ran the 29 scenario ids that run lost**, with the stray worker gone: 17/41 judged, against 6/41 on the same recordings in the full run.
- **Speech-to-text differs between runs.** The full run used local Whisper, which fell back from the GPU to the CPU for its last quarter (decode 0.18 s to about 12 s per segment; pass rate 47% before the switch and 24% after). The two later 30 Sep runs (`cloud_20260930_140536`, `cloud_20260930_174502`) used hosted transcription (`gpt-4o-mini-transcribe`); the 32-recording re-run used local Whisper on the GPU. The 2 Oct run used local Whisper (on the GPU, enforced) because the hosted key had no credit.
- **FDB's own client crashed (SIGABRT) on 8 of 100 recordings in the full run and 2 of 26 on 2 Oct**; these score 0 whatever the agent did. In 6 of the 8 the agent's own logs show correct calls.
- **Judged and exact differ by at most one or two recordings per run** (FDB's judge accepts near-misses such as "Vegas" for "Las Vegas").

### Voice against text, same 100 recordings

| Domain | Text replay, judged (30 Sep final) | Voice, judged (29-30 Sep) | Voice, exact (29-30 Sep) | **Voice, judged (3 Oct)** |
|---|---|---|---|---|
| ecommerce_support | 23/29 | 14/29 | 15/29 | 23/29 |
| finance_billing | 25/25 | 16/25 | 16/25 | 24/25 |
| housing_location | 10/26 | 4/26 | 4/26 | 10/26 |
| travel_identity | 19/20 | 8/20 | 6/20 | 16/20 |
| **all** | 77/100 | 42/100 | 41/100 | 73/100 |

| Expected tool calls | Text replay, judged | Voice, exact (29-30 Sep) | **Voice, judged (3 Oct)** |
|---|---|---|---|
| 1 call | 54/66 | 33/66 | 52/66 |
| 2 calls | 14/18 | 6/18 | 14/18 |
| 3 calls | 9/16 | 2/16 | 7/16 |

On 30 Sep the gap was wide at every size: about 32 points for one-call scenarios (82% to 50%) and about 44 for two- and three-call ones. On 3 Oct the one- and two-call scenarios are close to text replay; three-call scenarios are the weakest, and housing (38.5%) is the one domain still far below the others.

### Full run after the fixes (3 Oct), per recording against 30 Sep (both judged, same 100 recordings)

| 30 Sep (judged) | 3 Oct (judged) | Change | Recordings |
|---|---|---|---|
| fail | pass | fixed | 32 |
| pass | pass | unchanged pass | 41 |
| fail | fail | unchanged fail | 26 |
| pass | fail | **regressed** | 1 |

The 3 Oct run used hosted speech-to-text and the 30 Sep run local Whisper (which fell back to the CPU for its last quarter), so the comparison includes the speech-to-text change as well as the code fixes, as with the 2 Oct run in section 4. The full per-run report, with all 27 failures, is in [`BENCHMARK_FULL100_2026-10-03.md`](BENCHMARK_FULL100_2026-10-03.md).

## 4. What changed on the voice path, and what it did

The turn-ending result below is the strongest evidence in the record, so the chain of events is given in order.

1. **29-30 Sep, full voice run: 41%.** Reading the decision logs, the dominant cause was not reasoning (text replay: 77%).
2. **1 Oct audit** of 72 current-code recordings: the voice bridge restarted its end-of-turn timer every time a transcript arrived. Hosted transcription returns a segment about 1.1 s after it ends (p90 2.0 s), usually after the user has resumed, so turns were closed while the user was still speaking: 86 early closes in 52 of 72 recordings, none after 1.5 s or more of real silence. Single-turn recordings passed 13/17; two turns 8/24; three or more 6/31.
3. **Fix (1 Oct):** end-of-turn follows voice-activity silence, waits for every ended segment's transcript, and the kernel receives a "user is speaking" signal that holds tool calls and speech.
4. **Replay of real timing** (FDB word timestamps and each room's measured transcription times, 72 recordings, through the old and the new bridge code). The replay models voice-activity segments from word gaps, so its old-bridge numbers are harsher than the live run; the direction is the point.

| Measure | Unit | Old bridge | New bridge |
|---|---|---|---|
| recordings kept as one turn | of 72 | 12 | 66 |
| end-of-turns inside speech | count | 98 | 0 |
| final turn close after last word, median | seconds | 2.85 | 2.08 |

5. **Live check (2 Oct)** on 26 recordings that had run on 30 Sep (10 split-turn failures, 5 fragile passes, 3 controls), the same code otherwise:

| Measure | 30 Sep (before the fix) | 2 Oct (after the fix) |
|---|---|---|
| Recordings compared (both runs have a wire log) | 24 | 24 |
| Premature end-of-turns (end-of-turn sent before the user's last transcript chunk arrived) | 42 | 0 |
| Recordings kept as one turn | 2 | 24 |
| End-of-turns while the user was speaking | not measurable (no speech signal) | 0 |

Per recording (premature end-of-turns are turns that closed with more speech still to come):

| Recording | 30 Sep (exact) | 30 Sep premature end-of-turns | 2 Oct (exact) | 2 Oct premature | Change |
|---|---|---|---|---|---|
| ecommerce_04 / 6998ab | pass | 1 | pass | 0 | same |
| ecommerce_10 / 6998ab | pass | 1 | fail | - (client aborted) | **regressed** (FDB client aborted) |
| ecommerce_13 / 5ff07b | pass | 2 | pass | 0 | same |
| ecommerce_13 / 61517d | fail | 3 | pass | 0 | fixed |
| ecommerce_15 / 56780a | pass | 2 | fail | 0 | **regressed** |
| ecommerce_15 / 66c4f3 | fail | 2 | pass | 0 | fixed |
| ecommerce_19 / 66f59c | fail | 1 | pass | 0 | fixed |
| ecommerce_21 / 65e8cf | fail | 1 | pass | 0 | fixed |
| ecommerce_21 / 69a9cf | fail | 3 | fail | 0 | same |
| finance_12 / 65e8cf | pass | 1 | pass | 0 | same |
| finance_12 / 69a9cf | fail | 2 | pass | 0 | fixed |
| finance_18 / 6998ab | pass | 1 | fail | 0 | **regressed** |
| housing_02 / 5f4a4d | pass | 2 | pass | 0 | same |
| housing_03 / 5f4a4d | fail | 3 | fail | 0 | same |
| housing_06 / 5f4a4d | fail | 2 | fail | 0 | same |
| housing_09 / 695bd1 | fail | 3 | fail | - (client aborted) | same (FDB client aborted) |
| housing_10 / 66f59c | fail | 1 | fail | 0 | same |
| housing_17 / 65e8cf | pass | 2 | pass | 0 | same |
| housing_17 / 69a9cf | fail | 3 | pass | 0 | fixed |
| travel_07 / 5ff07b | pass | 0 | pass | 0 | same |
| travel_07 / 61517d | fail | 2 | pass | 0 | fixed |
| travel_11 / 66c4f3 | pass | 0 | pass | 0 | same |
| travel_21 / 65e8cf | fail | 3 | fail | 0 | same |
| travel_21 / 69a9cf | fail | 3 | fail | 0 | same |
| travel_23 / 65e8cf | pass | 1 | pass | 0 | same |
| travel_23 / 69a9cf | pass | 1 | pass | 0 | same |

Pass rate against how often a recording was split, per run:

| Run | Recordings with wire log | Premature end-of-turns | Recordings split | Exact pass, one turn | Exact pass, split |
|---|---|---|---|---|---|
| `cloud_20260929_232528` | 92 | 137 | 68 | 13/24 | 28/68 |
| `cloud_20260930_032600` | 32 | 25 | 16 | 7/16 | 4/16 |
| `cloud_20260930_174502` | 41 | 65 | 34 | 6/7 | 9/34 |
| `cloud_20261002_061207` | 24 | 0 | 0 | 16/24 | 0/0 |
| `cloud_20261002_182600` | 100 | 18 | 17 | 57/83 | 11/17 |

Two of the 26 recordings were client aborts with no wire log, so the turn comparison covers 24. Counted by FDB word timestamps (the 1 Oct audit's definition: a turn closed while the speaker's words continued) the same 26 recordings had 31 premature closes on 30 Sep and none on 2 Oct, and 5 against 26 recordings kept as one turn; the wire-order count above is larger because it also counts a turn closed just before a late transcript arrived. Both are zero after the fix.

The 2 Oct run changed two things at once (the fix, and local instead of hosted speech-to-text), so part of the gain may come from faster transcription rather than the fix. The absence of early closes is the fix's effect; the 12 to 16 pass count is a small sample (26 recordings, where two or three can flip without any code change). A like-for-like run needs hosted transcription.

## 5. Failures

The failures behind the misses were classified by reading each recording's decision log, wire log and FDB's report. Four sets, in time order; `benchmarks/failures.csv` has one row per recording. The causes of the 3 Oct set (`full100_2026-10-03`) are heuristic (derived from the recording's calls, wire log and reference transcript, marked `HEURISTIC` in the file) and have not yet been confirmed recording by recording in the decision logs.

| Failure set | Recordings | By cause |
|---|---|---|
| `ledger_2026-09-30` | 56 | wrong_arg 22, missing_call 15, client_abort 7, extra_call 7, stt_error 5 |
| `audit_2026-10-01` | 36 | split_turn 10, reasoning 8, label_mismatch 7, schema_gap 6, stt_error 3, value_form 1, infra 1 |
| `verify_2026-10-02` | 10 | label_mismatch 4, stt_error 3, client_abort 2, value_form 1 |
| `full100_2026-10-03` | 27 | wrong_arg 12, missing_call 7, split_turn 5, stt_error 3 |

### Classes

| Taxonomy | Class | Definition | Recordings | Status |
|---|---|---|---|---|
| `audit_2026-10-01` | split_turn | Turn ended mid-sentence by the voice bridge; the first half was acted on alone | 10 | Turn splitting fixed 1 Oct; on 2 Oct 6 of 10 of these recordings pass, 4 fail for other reasons (label wording, a client abort) |
| `audit_2026-10-01` | reasoning | Interpretation error that also fails in text replay (filter update folded into a search, conditionals, typed values) | 9 | Open; several are label-schema questions |
| `audit_2026-10-01` | label_mismatch | Label disagrees with the audio or is stricter than the judge ("keyboards" vs "keyboard", city never spoken) | 7 | Mostly not recoverable honestly |
| `audit_2026-10-01` | schema_gap | Label needs pets_allowed, which the declared tool schema does not have | 6 | Open; deliberate (integrity question) |
| `audit_2026-10-01` | stt_error | Speech-to-text dropped or misheard a word | 3 | Hosted STT helps; whole-turn re-transcription not built |
| `audit_2026-10-01` | infra | Agent joined late (a stray demo worker shared the LiveKit project) | 1 | Operational; not a code fault |
| `full100_2026-09-29` | A | FDB client aborted after the stream (SIGABRT); no result recorded | 8 | Late speech at teardown removed by the filler filter; 0 aborts in a later 32-recording run, 2 in the 2 Oct run |
| `full100_2026-09-29` | B | Late or stalled execution: agent silent, calls 15-40 s after speech end or never | 13 | Whisper CPU fallback (fixed 30 Sep) and two kernel liveness bugs (fixed) |
| `full100_2026-09-29` | C | Premature end-of-turn at a mid-utterance pause gave a partial interpretation | 6 | Turn-ending fix 1 Oct |
| `full100_2026-09-29` | C2 | Same root cause, extra calls: a read ran on the first half, then again after the rest | 4 | Turn-ending fix 1 Oct |
| `full100_2026-09-29` | D | Enum-like values not in the canonical form the tool implies (driver license, filter keys) | 7 | Interpreter value rule 30 Sep |
| `full100_2026-09-29` | E | Spoken-ID shapes from Whisper not canonicalised ("P.O. 999", "F A S T nine nine") | 3 | Spoken-ID joiner 30 Sep |
| `full100_2026-09-29` | F | Exact-match brittleness the judge usually accepts ("Vegas" vs "Las Vegas") | 5 | Judge scoring |
| `full100_2026-09-29` | G | Speech-to-text or label disagrees with the audio | 4 | Not recoverable |
| `full100_2026-09-29` | H | Tool-schema gap (search_apartments has no pets_allowed) | 3 | Open; deliberate |
| `full100_2026-09-29` | I | Interpretation or reasoning error | 3 | Partly open |
| `full100_2026-09-29` | J | Conditionals: both branches executed | 2 | Open |
| `full100_2026-09-29` | K | Sequential-chain blocking: an independent action waited on a blocked one | 1 | Independent-action fix 2 Oct (not yet re-measured) |

### Where the failures were (audit set, 30 Sep code)

| Domain (36 judged failures, current code) | Failures | Causes |
|---|---|---|
| housing | 20 | reasoning 7, schema_gap 6, split_turn 4, label_mismatch 3 |
| ecommerce | 9 | split_turn 4, label_mismatch 3, reasoning 1, value_form 1 |
| travel | 6 | stt_error 3, split_turn 1, label_mismatch 1, infra 1 |
| finance | 1 | split_turn 1 |

Reading across the sets:

- **Split turns were the single largest recoverable cause** (10 of the 36 distinct judged failures on the 30 Sep code, and a contributing cause in 30 of the 36). On 2 Oct 6 of those 10 recordings pass; the other 4 no longer split but fail on label wording (3) or a client abort (1).
- **Label and schema questions are a floor, not a bug list.** Seven failures have labels that disagree with the audio or are stricter than the judge, and six need `pets_allowed`, which the declared schema lacks. Passing them would mean fitting labels rather than following the tool definition, so they are left alone on purpose.
- **Housing carries most of the remaining reasoning failures** (a filter update folded into a search, values typed as strings where the label has numbers).
- **Infrastructure noise is real:** client aborts (7 recordings on 30 Sep, 2 on 2 Oct), a stray worker taking rooms, and a Whisper CPU fallback each cost points that were not about the agent.

## 6. Other measurements

### Test suite

| Date | Tests passing | At that point |
|---|---|---|
| 2026-09-27 | 328 | before S2 |
| 2026-09-27 | 352 | S2 quick-win pack |
| 2026-09-28 | 377 | S3 action plans + late binding |
| 2026-09-29 | 418 | extension + reproduction + docs |
| 2026-09-30 | 447 | voice-path fixes |
| 2026-09-30 | 486 | polish pass |
| 2026-09-30 | 499 | submission |
| 2026-10-01 | 519 | turn-ending fix |
| 2026-10-02 | 522 | speculative-promotion fix |
| 2026-10-02 | 524 | observability |
| 2026-10-02 | 533 | trailing-off settle |
| 2026-10-02 | 554 | equivalent restatement |
| 2026-10-02 | 556 | duplicate detection |
| 2026-10-02 | 557 | no repeated acknowledgement |
| 2026-10-02 | 562 | independent actions |
| 2026-10-02 | 563 | follow-ups see results |
| 2026-10-02 | 571 | partial failure, barge-in, blank arguments |
| 2026-10-02 | 572 | prompt contradiction |
| 2026-10-02 | 575 | class K follow-ups |

### Kernel step latency

`Kernel.step()` over 44,200 steps in an I-03-style scenario (against a 5 ms budget):

| Metric | Value | Unit |
|---|---|---|
| per-step wall time mean | 9.2 | microseconds |
| per-step wall time p50 | 5.8 | microseconds |
| per-step wall time p95 | 17.9 | microseconds |
| per-step wall time p99 | 82.3 | microseconds |
| per-step wall time max | 2760.8 | microseconds |

### Time to first speech in the deterministic simulator

| Metric | Value | Unit |
|---|---|---|
| TTFS after end-of-turn, speculative interpretation off | 300000 | microseconds |
| TTFS after end-of-turn, speculative interpretation on | 0 | microseconds |
| TTFS after end-of-turn, trailing "please", C1 off | 100000 | microseconds |
| TTFS after end-of-turn, trailing "please", C1 on | 0 | microseconds |

### Adversarial schedule exploration

Seeded and targeted perturbations of tool, model and user-event timing, each checked by nine oracles. A clean run means no violation in the explored space, not a proof.

| Metric | Value | Condition |
|---|---|---|
| schedules explored: first run, before fixes (text) | 471 | 9 scenarios |
| failing schedules: first run, before fixes (text) | 126 | 9 scenarios |
| schedules explored: after fixes (text) | 9142 | 10 scenarios, 3 seeds |
| failing schedules: after fixes (text) | 0 | 10 scenarios, 3 seeds |
| schedules explored: after fixes (multimodal) | 8194 | 9 scenarios |
| failing schedules: after fixes (multimodal) | 0 | 9 scenarios |
| schedules explored: TRIAGE hold disabled alone (text) | 3122 | ablation |
| failing schedules: TRIAGE hold disabled alone (text) | 704 | ablation |
| schedules explored: ASR capture order + dedupe window disabled | 912 | ablation |
| failing schedules: ASR capture order + dedupe window disabled | 629 | ablation |
| schedules explored: TRIAGE hold disabled alone (multimodal) | 927 | ablation |
| failing schedules: TRIAGE hold disabled alone (multimodal) | 161 | ablation |

### Speech round trip

| Metric | Value | Unit |
|---|---|---|
| TTS->ASR round trip: sentences with all facts intact | 4 | of 4 |
| TTS->ASR round trip: individual facts intact | 8 | of 8 |

### The superseded simulator kit (24 Sep)

The organizers' first evaluation kit (replaced by FDB-v3 on 24 Sep) was integrated first; the runs below used its nine public scenarios. The score was low mostly because of integration gaps found and fixed that day, and the kit no longer counts.

| Metric | Value | Unit | Condition |
|---|---|---|---|
| weighted score, Janus after the first integration pass | 10.4 | points (0-100) | 9 public scenarios, 3 repetitions, gemini-3.6-flash; superseded by FDB-v3 on 24 Sep |
| weighted score, Janus after the timer-liveness fix | 8.9 | points (0-100) | same |

## 7. Method and caveats

- **Scoring.** FDB's `evaluate_pass_rate.py` passes a recording only if the multiset of tool names matches (nothing missing, nothing extra) and every expected argument matches; extra arguments are ignored. Only the tool-call log counts toward pass rate; speech does not. The judged variant uses gpt-4o for argument equivalence and response quality.
- **Exact against judged.** Both are reported. Scoring changed over time: runs before 28 Sep had no judge key and are exact only.
- **Samples differ.** "26 recordings" on the laptop and on 2 Oct are different sets. Compare runs only on the same recordings (`voice_recordings.csv` joins on scenario + speaker).
- **Run-to-run noise.** On 100 text scenarios one repeat differs by 1-2 points (arms A and A2); on 26 voice recordings, 2-3 recordings can flip without any code change. The hosted model is not bit-reproducible even at temperature 0.
- **Speaker identity.** Two scenarios have two speakers' recordings each; `speaker` is the first six characters of FDB's per-recording id.
- **Premature end-of-turn** (in `voice_recordings.csv`) counts end-of-turn events that precede the last non-empty transcript chunk of the same recording, from the agent's wire log. It is available for runs from 29 Sep onward and is not the same as the audit's word-timestamp count (see section 4). `mid_speech_eots` is available only from 2 Oct, when the agent began receiving a speech-activity signal.
- **Failure causes** (`failures.csv`, `failure_classes.csv`) were assigned by hand from the logs; the 36-recording audit set had its histogram checked against the per-recording table, but a cause is a judgement, and `contributing` records the secondary one.
- **Not measured:** a full-100 voice run of the current code, and any run with hosted transcription after the turn-ending fix. An estimate from the recordings re-tested so far puts the current code between 55% and 65% judged; that is an estimate, not a result.

Commands:

```bash
python -m pytest tests/ -q                                         # the suite
PYTHONPATH=src:. python scripts/fdb_v3/run_text_replay.py --profile fdb --model gemini-3.6-flash --use-llm   # text replay, judged
./reproduce.sh [--ids "travel_01 ..."]                             # voice, one command (Docker)
scripts/fdb_v3/native_run.sh [--judge] [--ids "..."]               # voice, native
python benchmarks/collect.py && python benchmarks/build_report.py  # refresh this record
```

## Appendix: every run

The commit column is the code the run used (`-dirty` means uncommitted changes at the time). `unknown` marks the 30 Sep cloud runs, made from a copied source tree with no git history; from 2 Oct a run's manifest also records a digest of the source (`janus_source_sha256`). Single-scenario debug runs are included for completeness.

| Date | Run | Kind | Class | Scored | Passed | Rate | Tool sel. | Arg. acc. | Model | Commit |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-25 | `text_fdb_v3_text_replay` | text_replay | subset | exact | 0/20 | 0.0% | 0.200 | 0.000 |  |  |
| 2026-09-25 | `text_fdb_v3_text_replay_full100` | text_replay | full100 | exact | 21/100 | 21.0% | 0.493 | 0.250 |  |  |
| 2026-09-25 | `text_fdb_v3_text_replay_full100_g4fix` | text_replay | full100 | exact | 33/100 | 33.0% | 0.832 | 0.411 |  |  |
| 2026-09-26 | `text_fdb_v3_text_replay_wp2` | text_replay | full100 | exact | 42/100 | 42.0% | 0.931 | 0.505 |  |  |
| 2026-09-27 | `lk_20260927_173552` | voice_local | debug | exact | 0/0 | 0.0% |  |  |  |  |
| 2026-09-27 | `lk_20260927_173959` | voice_local | debug | exact | 0/1 | 0.0% | 1.000 | 0.000 |  |  |
| 2026-09-27 | `lk_20260927_174631` | voice_local | subset | exact | 1/5 | 20.0% | 0.750 | 0.250 |  |  |
| 2026-09-27 | `lk_20260927_175223` | voice_local | subset | exact | 11/26 | 42.3% | 0.984 | 0.524 |  |  |
| 2026-09-27 | `lk_20260927_213947` | voice_local | subset | exact | 15/26 | 57.7% | 0.970 | 0.682 |  |  |
| 2026-09-27 | `lk_20260927_222409` | voice_local | debug | exact | 3/6 | 50.0% | 1.000 | 0.750 |  |  |
| 2026-09-27 | `lk_20260927_223446` | voice_local | debug | exact | 3/6 | 50.0% | 1.000 | 0.750 |  |  |
| 2026-09-27 | `lk_20260927_225322` | voice_local | debug | exact | 2/4 | 50.0% | 1.000 | 1.000 |  |  |
| 2026-09-27 | `lk_20260927_230003` | voice_local | debug | exact | 3/4 | 75.0% | 1.000 | 1.000 |  |  |
| 2026-09-27 | `lk_20260927_230439` | voice_local | debug | exact | 2/2 | 100.0% | 1.000 | 1.000 |  |  |
| 2026-09-27 | `lk_20260927_230718` | voice_local | subset | exact | 15/26 | 57.7% | 0.841 | 0.619 |  |  |
| 2026-09-27 | `text_s2_full100_v2` | text_replay | full100 | exact | 64/100 | 64.0% | 0.920 | 0.703 |  |  |
| 2026-09-27 | `text_s2_full100_v3` | text_replay | full100 | exact | 68/100 | 68.0% | 0.941 | 0.735 |  |  |
| 2026-09-27 | `text_s2_full100_validation` | text_replay | full100 | exact | 52/100 | 52.0% | 0.942 | 0.610 |  |  |
| 2026-09-27 | `text_s2_housing13_after_fix` | text_replay | debug | exact | 0/1 | 0.0% |  |  |  |  |
| 2026-09-27 | `text_s2_housing13_after_fix2` | text_replay | debug | exact | 0/1 | 0.0% | 0.000 | 0.000 |  |  |
| 2026-09-27 | `text_s2_validation_20` | text_replay | subset | exact | 15/20 | 75.0% | 0.950 | 0.750 |  |  |
| 2026-09-27 | `text_s2_validation_housing11` | text_replay | debug | exact | 0/1 | 0.0% | 0.000 | 0.000 |  |  |
| 2026-09-27 | `text_s2_validation_housing13` | text_replay | debug | exact | 0/1 | 0.0% |  |  |  |  |
| 2026-09-27 | `text_s2_validation_housing13_retry` | text_replay | debug | exact | 0/1 | 0.0% |  |  |  |  |
| 2026-09-27 | `text_stall_debug_finance_15` | text_replay | debug | exact | 1/1 | 100.0% | 1.000 | 1.000 |  |  |
| 2026-09-27 | `text_stall_debug_housing_11` | text_replay | debug | exact | 0/1 | 0.0% |  |  |  |  |
| 2026-09-27 | `text_stall_debug_housing_13` | text_replay | debug | exact | 0/1 | 0.0% |  |  |  |  |
| 2026-09-28 | `lk_20260928_195832` | voice_local | debug | exact | 0/1 | 0.0% | 1.000 | 0.500 |  |  |
| 2026-09-28 | `text_s3_full100_A` | text_replay | full100 | exact | 65/100 | 65.0% | 0.927 | 0.723 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_full100_A.judged` | text_replay | full100 | judged | 66/100 | 66.0% | 0.927 | 0.723 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_full100_A2` | text_replay | full100 | exact | 66/100 | 66.0% | 0.923 | 0.713 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_full100_A2.judged` | text_replay | full100 | judged | 68/100 | 68.0% | 0.923 | 0.718 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_full100_B` | text_replay | full100 | exact | 62/100 | 62.0% | 0.908 | 0.678 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_full100_B.judged` | text_replay | full100 | judged | 63/100 | 63.0% | 0.908 | 0.675 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_full100_C` | text_replay | full100 | exact | 66/100 | 66.0% | 0.964 | 0.740 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_full100_C.judged` | text_replay | full100 | judged | 72/100 | 72.0% | 0.964 | 0.765 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_full100_C2` | text_replay | full100 | exact | 70/100 | 70.0% | 0.974 | 0.768 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_full100_C2.judged` | text_replay | full100 | judged | 73/100 | 73.0% | 0.974 | 0.767 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_full100_D` | text_replay | full100 | judged | 73/100 | 73.0% | 0.933 | 0.767 | gemini-3.5-flash-lite | f70857c-dirty |
| 2026-09-28 | `text_s3_full100_E_flash` | text_replay | full100 | judged | 73/100 | 73.0% | 0.953 | 0.760 | gemini-3.6-flash | 8fa5e67 |
| 2026-09-28 | `text_s3_targeted_1000_ecommerce_20_` | text_replay | debug | exact | 0/1 | 0.0% | 0.800 | 1.000 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_1000_housing_10_` | text_replay | debug | exact | 0/1 | 0.0% | 1.000 | 0.000 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_1000_housing_13_` | text_replay | debug | exact | 0/1 | 0.0% | 0.000 | 0.000 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_1000_housing_18_` | text_replay | debug | exact | 0/1 | 0.0% | 0.667 | 0.500 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_1000_housing_20_` | text_replay | debug | exact | 0/1 | 0.0% | 1.000 | 0.667 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_1000_housing_21_` | text_replay | debug | exact | 0/1 | 0.0% | 0.000 | 0.000 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_1000_housing_22_` | text_replay | debug | exact | 0/1 | 0.0% | 1.000 | 0.333 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_1000_housing_24_` | text_replay | debug | exact | 0/2 | 0.0% | 0.250 | 0.167 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_1000_housing_25_` | text_replay | debug | exact | 0/1 | 0.0% | 0.500 | 0.000 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_1000_travel_07_` | text_replay | debug | exact | 1/2 | 50.0% | 0.500 | 0.500 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_inc2_1012_ecommerce_20_` | text_replay | debug | exact | 0/1 | 0.0% | 0.800 | 1.000 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_inc2_1012_housing_10_` | text_replay | debug | exact | 0/1 | 0.0% | 1.000 | 0.000 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_inc2_1012_housing_13_` | text_replay | debug | exact | 0/1 | 0.0% | 0.000 | 0.000 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_inc2_1012_housing_18_` | text_replay | debug | exact | 0/1 | 0.0% | 1.000 | 0.500 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_inc2_1012_housing_20_` | text_replay | debug | exact | 0/1 | 0.0% | 1.000 | 0.667 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_inc2_1012_housing_21_` | text_replay | debug | exact | 0/1 | 0.0% | 0.000 | 0.000 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_inc2_1012_housing_22_` | text_replay | debug | exact | 0/1 | 0.0% | 1.000 | 0.333 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_inc2_1012_housing_24_` | text_replay | debug | exact | 0/2 | 0.0% | 0.900 | 0.500 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_inc2_1012_housing_25_` | text_replay | debug | exact | 0/1 | 0.0% | 0.800 | 0.333 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-28 | `text_s3_targeted_inc2_1012_travel_07_` | text_replay | debug | exact | 0/2 | 0.0% | 0.000 | 0.000 | gemini-3.5-flash-lite | 7a22495-dirty |
| 2026-09-29 | `lk_20260927_230718.judged` | voice_local | subset | judged | 16/26 | 61.5% | 0.841 | 0.667 |  |  |
| 2026-09-29 | `text_openai_gpt41_subset` | text_replay | subset | judged | 7/15 | 46.7% | 0.960 | 0.633 | gpt-4.1 | dd1e648-dirty |
| 2026-09-29 | `text_openai_gpt41_subset_r3` | text_replay | subset | judged | 8/15 | 53.3% | 0.960 | 0.622 | gpt-4.1 | 871e088-dirty |
| 2026-09-30 | `cloud_20260929_232528` | voice_cloud | full100 | exact | 41/100 | 41.0% | 0.844 | 0.611 | gemini-3.6-flash | unknown |
| 2026-09-30 | `cloud_20260929_232528.judged` | voice_cloud | full100 | judged | 42/100 | 42.0% | 0.844 | 0.611 | gemini-3.6-flash | unknown |
| 2026-09-30 | `cloud_20260930_032600` | voice_cloud | subset | exact | 11/32 | 34.4% | 0.877 | 0.500 | gemini-3.6-flash | unknown |
| 2026-09-30 | `cloud_20260930_140536` | voice_cloud | subset | exact | 12/64 | 18.8% | 0.363 | 0.240 | gemini-3.6-flash | unknown |
| 2026-09-30 | `cloud_20260930_174502` | voice_cloud | subset | judged | 17/41 | 41.5% | 0.835 | 0.524 | gemini-3.6-flash | unknown |
| 2026-09-30 | `text_text_replay_0930_final` | text_replay | full100 | judged | 77/100 | 77.0% | 0.949 | 0.803 | gemini-3.6-flash | a928087-dirty |
| 2026-09-30 | `text_text_replay_0930_fixes` | text_replay | full100 | judged | 77/100 | 77.0% | 0.959 | 0.807 | gemini-3.6-flash | 31ae4cb-dirty |
| 2026-09-30 | `text_text_replay_0930_setting` | text_replay | full100 | judged | 75/100 | 75.0% | 0.963 | 0.795 | gemini-3.6-flash | 838cea7-dirty |
| 2026-10-02 | `cloud_20261002_061207` | voice_cloud | subset | exact | 16/26 | 61.5% | 0.958 | 0.736 | gemini-3.6-flash | 86e5fc95 |
| 2026-10-02 | `cloud_20261002_182600` | voice_cloud | full100 | judged | 73/100 | 73.0% | 0.947 | 0.768 | gemini-3.6-flash | 8020f79b |
| 2026-10-02 | `text_text_replay_1002_valuerules` | text_replay | subset | exact | 11/24 | 45.8% | 0.911 | 0.618 | gemini-3.6-flash | 1405be2-dirty |
| 2026-10-03 | `cloud_20261003_121822` | voice_cloud | full100 | judged | 65/100 | 65.0% | 0.917 | 0.704 | gemini-3.6-flash | 054c305a |
| 2026-10-03 | `cloud_20261003_143517` | voice_cloud | subset | judged | 24/32 | 75.0% | 0.963 | 0.807 | gemini-3.6-flash | a8d1fcdb |
| 2026-10-03 | `text_textreplay_dash_1003` | text_replay | subset | judged | 6/6 | 100.0% | 1.000 | 1.000 | gemini-3.6-flash | 2752bc8-dirty |
| 2026-10-03 | `text_textreplay_fix_1003` | text_replay | full100 | judged | 79/100 | 79.0% | 0.924 | 0.802 | gemini-3.6-flash | 02ce797-dirty |
| 2026-10-03 | `text_textreplay_pets_1003` | text_replay | debug | judged | 0/6 | 0.0% | 0.667 | 0.000 | gemini-3.6-flash | 6ddb2c9-dirty |
| 2026-10-03 | `text_textreplay_pets_base_1003` | text_replay | debug | judged | 0/6 | 0.0% | 0.667 | 0.000 | gemini-3.6-flash | 6ddb2c9-dirty |
