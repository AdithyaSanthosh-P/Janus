# Benchmark run logs

FDB-v3 voice runs of Janus over LiveKit Cloud, on a cloud RTX 3090 via the native path (`scripts/fdb_v3/native_run.sh`, the same `repro_inner.sh` the Docker reproduction runs). Audio is left out; everything else each run wrote is here.

| Archive | What | Result |
|---|---|---|
| `janus_fdbv3_voice_full100_2026-09-30.tgz` | All 100 recordings, 29-30 Sep, before the fixes listed in the README | 41 % exact match, **42 % judged** (`*.judged.json`: FDB's `--use-llm`, gpt-4o, scored afterwards on a separate machine) |
| `janus_fdbv3_voice_rerun32_2026-09-30.tgz` | The 32 hardest recordings of that run, after the speech-to-text GPU fix | 11 / 32 exact match (was 8 / 32); turn-taking 32 / 32 |
| `janus_fdbv3_voice_run64_2026-09-30.tgz` | 64 recordings with hosted speech-to-text. **Contaminated:** a stray demo worker on the same LiveKit project took 33 of the 64 rooms (scored 0); 31 recordings are valid | 12 / 64 exact (12 / 31 valid) |
| `janus_fdbv3_voice_clean41_2026-09-30.tgz` | Clean re-run of the 29 scenario ids that run lost (41 recordings), hosted speech-to-text, judge on | 17 / 41 judged |
| `janus_fdbv3_voice_verify26_2026-10-02.tgz` | 26 recordings re-run after the turn-ending fix (10 split-turn failures, 5 fragile passes, 3 controls); local Whisper because the hosted key had no credit; judge not run | 16 / 26 exact (the same recordings: 12 / 26 on 30 Sep) |

[`BENCHMARKS.md`](../../BENCHMARKS.md) reads all of these (`python benchmarks/collect.py`) together with the local run folders.

Each archive holds:
- **Configuration and seeds:** `manifest.json`, with the commit, package versions, the full kernel profile, seeds and whether the judge was on.
- **Providers:** `PROVIDER.txt`, the provider declaration for that run.
- **FDB's reports:** `pass_rate_report.json` and `tool_calls_report.json`.
- **Logs:** the runner, agent and evaluator logs.
- **Per-recording results:** `data/*/result_janus.json`, with FDB's transcripts, the tool calls and latency.
- **Per-room kernel decision logs:** `decisions/*.jsonl` and `*.wire.jsonl`. Render one with `python -m prism_rt.observability.xray D.jsonl W.jsonl out.html`.
