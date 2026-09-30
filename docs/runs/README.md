# Benchmark run logs

FDB-v3 voice runs of Janus over LiveKit Cloud, on a cloud RTX 3090 via the native path (`scripts/fdb_v3/native_run.sh`, the same `repro_inner.sh` the Docker reproduction runs). Audio is left out; everything else each run wrote is here.

| Archive | What | Result |
|---|---|---|
| `janus_fdbv3_voice_full100_2026-09-30.tgz` | All 100 recordings, 29-30 Sep, before the fixes listed in the README | 41 % exact match, **42 % judged** (`*.judged.json`: FDB's `--use-llm`, gpt-4o, scored afterwards on a separate machine) |
| `janus_fdbv3_voice_rerun32_2026-09-30.tgz` | The 32 hardest recordings of that run, after the speech-to-text GPU fix | 11 / 32 exact match (was 8 / 32); turn-taking 32 / 32 |

Each archive holds:
- **Configuration and seeds:** `manifest.json`, with the commit, package versions, the full kernel profile, seeds and whether the judge was on.
- **Providers:** `PROVIDER.txt`, the provider declaration for that run.
- **FDB's reports:** `pass_rate_report.json` and `tool_calls_report.json`.
- **Logs:** the runner, agent and evaluator logs.
- **Per-recording results:** `data/*/result_janus.json`, with FDB's transcripts, the tool calls and latency.
- **Per-room kernel decision logs:** `decisions/*.jsonl` and `*.wire.jsonl`. Render one with `python -m prism_rt.observability.xray D.jsonl W.jsonl out.html`.
