# benchmarks/

The data behind [`BENCHMARKS.md`](../BENCHMARKS.md). All of it can be regenerated:

```bash
python benchmarks/collect.py --fdb-root <Full-Duplex-Bench>/v3   # runs.csv, recordings.csv, voice_recordings.csv
python benchmarks/curated.py                                     # other_measurements.csv, failure_classes.csv
python benchmarks/build_report.py                                # BENCHMARKS.md
```

`collect.py` reads the cloud voice runs from `docs/runs/*.tgz` (committed, audio left out) and the text-replay and local LiveKit runs from `run_output/` (local, not committed; the CSVs keep their numbers). `--fdb-root` lets it re-score every voice recording with FDB's own exact-match evaluator; without it, `exact_pass` is filled only where the run's own report was exact. `failures.csv` is not regenerated: it was extracted once, on 2 Oct, from the per-recording failure analyses, and is maintained by hand.

## Columns

**runs.csv** — `run_id` (`text_*` text replay, `lk_*` local LiveKit, `cloud_*` cloud voice; a `.judged` suffix is the same run re-scored with FDB's judge) · `date` · `kind` (`text_replay`, `voice_local`, `voice_cloud`) · `class` (`full100`, `subset`, `debug`) · `phase` · `scored_by` (`exact` or `judged`) · `scenarios`, `passed`, `pass_rate` · `tool_selection_acc`, `argument_acc`, `response_quality` (FDB's, turn-taken samples only) · `turn_take_rate` · `avg_response_latency_s` (speech end to first agent word, turn-taken only) · `model`, `stt`, `commit` (`unknown` = copied tree), `overrides` (config overrides of the run) · `description` · `source`.

**recordings.csv** (text replay, one row per scenario) — `scenario_id`, `domain`, `difficulty`, `num_tools`, `disfluency` (`|`-separated) · `passed` · `failure_reason` · `expected_tools`, `actual_tools`, `missing`, `unexpected` · `eot_to_first_call_s`, `eot_to_first_speech_s`, `eot_to_final_s` (seconds from end of turn) · `errors`.

**voice_recordings.csv** (one row per recording) — `scenario_id`, `speaker` (first six characters of FDB's recording id; two scenarios have two speakers) · `status` (`completed`, or `inference_failed` when FDB's client aborted) · `exact_pass`, `judged_pass` (blank when that scoring was not run) · `failure_reason_exact` · `expected_tools`, `actual_tools`, `n_calls` · `first_response_s`, `tool_call_latency_s`, `user_speech_end_s` · wire-log evidence: `text_chunks`, `end_of_turns`, `premature_eots` (end-of-turn sent before the last transcript chunk arrived), `user_speech_events` and `mid_speech_eots` (from 2 Oct, when the agent received a speech signal) · `room`.

**failures.csv** — `failure_set` (`ledger_2026-09-30`: 56 failing recordings after the 30 Sep fixes · `audit_2026-10-01`: the 36 distinct judged failures on the 30 Sep code · `verify_2026-10-02`: the 10 failures of the 2 Oct run · `full100_2026-10-03`: the 27 failures of the 3 Oct morning run, heuristic · `final_2026-10-03`: the 26 failures of the final run, each confirmed in its decision log) · `scenario_id`, `speaker`, `domain` · `cause_class` (`split_turn`, `reasoning`, `label_mismatch`, `schema_gap`, `stt_error`, `value_form`, `infra`, `client_abort`, `wrong_arg`, `missing_call`, `extra_call`; for `final_2026-10-03` also `undeclared_arg`, `value_type`, `required_omitted`, `value_not_said`, `label_vs_audio`, `label_wording`, `conditional`, `filter_fold`) · `cause_detail`, `contributing` · `text_replay` (did the scenario pass in text replay) · `turn_closes` · `expected`, `actual` · `run_verdict` · `verify_2oct` (the recording's result in the 2 Oct run, if it was re-run). Join to `voice_recordings.csv` on `scenario_id` + `speaker`.

**failure_classes.csv** — `taxonomy`, `class`, `definition`, `recordings`, `status`.

**other_measurements.csv** — `category` (`tests`, `kernel_latency`, `latency_sim`, `exploration`, `speech`, `simulator_kit`, `turn_ending_replay`, `turn_ending_live`, `published_reference`), `metric`, `value`, `unit`, `date`, `condition`, `source`.

## Reading it correctly

- Compare runs only on the same recordings: the laptop's 26-recording sample, the cloud runs' subsets and the full 100 are different sets.
- Scoring differs between runs (exact vs judged) and so does speech-to-text (local vs hosted); the `description` and `stt` columns say which.
- Single-scenario debug runs (`class = debug`) are kept for completeness; filter them out for trend plots.
- Run-to-run noise on 100 text scenarios is about 1-2 points; on 26 recordings two or three can flip with no code change.
