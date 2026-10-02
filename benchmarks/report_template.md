# Janus benchmark record

Every benchmark run of Janus on this project, in one place: FDB-v3 text-replay and voice runs, the failures behind the misses, and the other measurements (tests, kernel latency, adversarial exploration). The numbers come from the runs' own reports; nothing here is estimated unless it says so.

This file is generated: `python benchmarks/collect.py` refreshes the CSVs from the run folders and archives, and `python benchmarks/build_report.py` rebuilds this page from `benchmarks/report_template.md`. The data is in `benchmarks/`:

| File | One row per | Use it for |
|---|---|---|
| `runs.csv` | scored run ({{runs}}) | score vs time, configuration comparisons |
| `recordings.csv` | scenario in each text-replay run ({{recordings}}) | per-scenario and per-domain pass rates, text-replay latency |
| `voice_recordings.csv` | recording in each voice run ({{voice}}) | per-speaker before/after, turn-ending evidence, latency |
| `failures.csv` | failing recording in an analysed failure set ({{failures}}) | cause analysis; joins to `voice_recordings.csv` on scenario + speaker |
| `failure_classes.csv` | failure class in a taxonomy | class definitions, counts, status |
| `other_measurements.csv` | measurement | tests, kernel latency, exploration, speech round trip, reference numbers |

Dates are 2026. Reports, logs and per-room decision logs of the five cloud voice runs are in `docs/runs/` (audio left out).

## 1. Where things stand (2 Oct)

| Measure | Value | Run | Note |
|---|---|---|---|
| Text replay, all 100, judged | **77%** (77/100) | `text_text_replay_0930_final` | Tool selection 0.949, argument accuracy 0.803; configuration of the 30 Sep submission |
| Voice over LiveKit, all 100, exact | **41%** (41/100) | `cloud_20260929_232528` | 29-30 Sep, before the voice fixes below; local Whisper |
| Voice over LiveKit, all 100, judged | **42%** (42/100) | `cloud_20260929_232528.judged` | Same run, scored with FDB's judge |
| Voice, 41 recordings the full run mostly failed, judged | **17/41** (41.5%) | `cloud_20260930_174502` | Re-run of the same recordings with fixes and hosted speech-to-text; the full run had 6/41 on them |
| Voice, 26 recordings, exact | **16/26** (61.5%) | `cloud_20261002_061207` | After the turn-ending fix; the same recordings scored 12/26 on 30 Sep |

There is **no full-100 voice run of the current code**. The sections below say which comparisons are like for like and which are not; the voice-versus-text gap (77% against 41-42%) is the main open question, and section 4 shows most of it is the voice front end rather than the reasoning.

For context, the benchmark's public paper reports (judged strict pass@1):

<!--table:published-->

These are reference numbers only: different hardware, models and run dates, and no per-recording detail.

## 2. Text replay: the reasoning path in isolation

`scripts/fdb_v3/run_text_replay.py` feeds each scenario's transcript through Janus's real async entry point (live model, no audio) and scores with FDB's own evaluator, so it measures decisions without speech recognition or turn-taking. All 100 recordings, one run each, so differences of a point or two are noise (see arms A and A2 below).

<!--table:text_timeline-->

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

<!--table:s3_arms-->

A and A2 are the same configuration: the run-to-run spread on 100 scenarios is about 1-2 points. C2 against the average of A and A2 is +4.5 points exact and +6 judged.

### By domain and difficulty (30 Sep final, judged)

Finance 25/25, travel 19/20, e-commerce 23/29, housing 10/26. By expected calls: 1 call 54/66, 2 calls 14/18, 3 calls 9/16. By difficulty: easy 30/36, medium 26/34, hard 21/30. By disfluency: self-correction 82.4%, false start 75%, filler 76%, hesitation 70%, pause 61%. Housing is the weakest domain for a structural reason: the `search_apartments` schema declares no `pets_allowed` parameter and `update_search_filter.filter_name` has no declared values, while the labels use both.

## 3. Voice runs: the scored path

Voice runs stream FDB's recordings through LiveKit to the Janus worker (speech recognition, kernel, speech synthesis) and score the tool calls it makes. "Exact" is FDB's exact-match scoring; "judged" is its gpt-4o argument judge.

<!--table:voice_runs-->

Read these with care:

- **The laptop runs (27-28 Sep) use a 26-recording stratified sample** of the first scenarios of each domain, not the full 100, so they are not comparable with the full runs. Within the sample, 42.3% to 57.7% shows the effect of the S2 fixes on the real voice path.
- **`cloud_20260930_140536` is contaminated.** A demo worker, registered without a name on the same LiveKit project, was handed 33 of the 64 rooms and scored 0 on them. Only the other 31 recordings (12 exact, 13 judged) are valid.
- **`cloud_20260930_174502` re-ran the 29 scenario ids that run lost**, with the stray worker gone: 17/41 judged, against 6/41 on the same recordings in the full run.
- **Speech-to-text differs between runs.** The full run used local Whisper, which fell back from the GPU to the CPU for its last quarter (decode 0.18 s to about 12 s per segment; pass rate 47% before the switch and 24% after). The two later 30 Sep runs (`cloud_20260930_140536`, `cloud_20260930_174502`) used hosted transcription (`gpt-4o-mini-transcribe`); the 32-recording re-run used local Whisper on the GPU. The 2 Oct run used local Whisper (on the GPU, enforced) because the hosted key had no credit.
- **FDB's own client crashed (SIGABRT) on 8 of 100 recordings in the full run and 2 of 26 on 2 Oct**; these score 0 whatever the agent did. In 6 of the 8 the agent's own logs show correct calls.
- **Judged and exact differ by at most one or two recordings per run** (FDB's judge accepts near-misses such as "Vegas" for "Las Vegas").

### Voice against text, same 100 recordings

<!--table:domain-->

<!--table:calls-->

The gap is wide at every size: about 32 points for one-call scenarios (82% to 50%) and about 44 for two- and three-call ones (78% to 33%, 56% to 12.5%).

## 4. What changed on the voice path, and what it did

The turn-ending result below is the strongest evidence in the record, so the chain of events is given in order.

1. **29-30 Sep, full voice run: 41%.** Reading the decision logs, the dominant cause was not reasoning (text replay: 77%).
2. **1 Oct audit** of 72 current-code recordings: the voice bridge restarted its end-of-turn timer every time a transcript arrived. Hosted transcription returns a segment about 1.1 s after it ends (p90 2.0 s), usually after the user has resumed, so turns were closed while the user was still speaking: 86 early closes in 52 of 72 recordings, none after 1.5 s or more of real silence. Single-turn recordings passed 13/17; two turns 8/24; three or more 6/31.
3. **Fix (1 Oct):** end-of-turn follows voice-activity silence, waits for every ended segment's transcript, and the kernel receives a "user is speaking" signal that holds tool calls and speech.
4. **Replay of real timing** (FDB word timestamps and each room's measured transcription times, 72 recordings, through the old and the new bridge code). The replay models voice-activity segments from word gaps, so its old-bridge numbers are harsher than the live run; the direction is the point.

<!--table:turn_replay-->

5. **Live check (2 Oct)** on 26 recordings that had run on 30 Sep (10 split-turn failures, 5 fragile passes, 3 controls), the same code otherwise:

<!--table:turn_summary-->

Per recording (premature end-of-turns are turns that closed with more speech still to come):

<!--table:flips-->

Pass rate against how often a recording was split, per run:

<!--table:premature_by_run-->

Two of the 26 recordings were client aborts with no wire log, so the turn comparison covers 24. Counted by FDB word timestamps (the 1 Oct audit's definition: a turn closed while the speaker's words continued) the same 26 recordings had 31 premature closes on 30 Sep and none on 2 Oct, and 5 against 26 recordings kept as one turn; the wire-order count above is larger because it also counts a turn closed just before a late transcript arrived. Both are zero after the fix.

The 2 Oct run changed two things at once (the fix, and local instead of hosted speech-to-text), so part of the gain may come from faster transcription rather than the fix. The absence of early closes is the fix's effect; the 12 to 16 pass count is a small sample (26 recordings, where two or three can flip without any code change). A like-for-like run needs hosted transcription.

## 5. Failures

The failures behind the misses were classified by reading each recording's decision log, wire log and FDB's report. Three sets, in time order; `benchmarks/failures.csv` has one row per recording.

<!--table:failure_sets-->

### Classes

<!--table:failure_classes-->

### Where the failures were (audit set, 30 Sep code)

<!--table:failure_by_domain-->

Reading across the sets:

- **Split turns were the single largest recoverable cause** (10 of the 36 distinct judged failures on the 30 Sep code, and a contributing cause in 30 of the 36). On 2 Oct 6 of those 10 recordings pass; the other 4 no longer split but fail on label wording (3) or a client abort (1).
- **Label and schema questions are a floor, not a bug list.** Seven failures have labels that disagree with the audio or are stricter than the judge, and six need `pets_allowed`, which the declared schema lacks. Passing them would mean fitting labels rather than following the tool definition, so they are left alone on purpose.
- **Housing carries most of the remaining reasoning failures** (a filter update folded into a search, values typed as strings where the label has numbers).
- **Infrastructure noise is real:** client aborts (7 recordings on 30 Sep, 2 on 2 Oct), a stray worker taking rooms, and a Whisper CPU fallback each cost points that were not about the agent.

## 6. Other measurements

### Test suite

<!--table:tests-->

### Kernel step latency

`Kernel.step()` over 44,200 steps in an I-03-style scenario (against a 5 ms budget):

<!--table:kernel_latency-->

### Time to first speech in the deterministic simulator

<!--table:sim_latency-->

### Adversarial schedule exploration

Seeded and targeted perturbations of tool, model and user-event timing, each checked by nine oracles. A clean run means no violation in the explored space, not a proof.

<!--table:exploration-->

### Speech round trip

<!--table:speech-->

### The superseded simulator kit (24 Sep)

The organizers' first evaluation kit (replaced by FDB-v3 on 24 Sep) was integrated first; the runs below used its nine public scenarios. The score was low mostly because of integration gaps found and fixed that day, and the kit no longer counts.

<!--table:simulator_kit-->

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

<!--table:all_runs-->
