#!/usr/bin/env python3
"""Writes the hand-curated tables: other_measurements.csv and failure_classes.csv.

The values come from docs/measurements.md, currentStatus.md and the 1-2 Oct analyses
(the failure-class counts are from the per-recording failure sets in failures.csv and
the 29-30 Sep diagnosis of the full voice run). Edit the literals below when a
measurement is added; the one derived value (the split-turn status) is computed from
failures.csv.

    python benchmarks/curated.py
"""
import collections
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
M = []


def m(cat, metric, value, unit, date, cond, src):
    M.append(dict(category=cat, metric=metric, value=value, unit=unit, date=date, condition=cond, source=src))


# --- tests ----------------------------------------------------------------------
for d, n, note in [("2026-09-27", 328, "before S2"), ("2026-09-27", 352, "S2 quick-win pack"),
                   ("2026-09-28", 377, "S3 action plans + late binding"), ("2026-09-29", 418, "extension + reproduction + docs"),
                   ("2026-09-30", 447, "voice-path fixes"), ("2026-09-30", 486, "polish pass"), ("2026-09-30", 499, "submission"),
                   ("2026-10-01", 519, "turn-ending fix"), ("2026-10-02", 522, "speculative-promotion fix"),
                   ("2026-10-02", 524, "observability"), ("2026-10-02", 533, "trailing-off settle"),
                   ("2026-10-02", 554, "equivalent restatement"), ("2026-10-02", 556, "duplicate detection"),
                   ("2026-10-02", 557, "no repeated acknowledgement"), ("2026-10-02", 562, "independent actions"),
                   ("2026-10-02", 563, "follow-ups see results"), ("2026-10-02", 571, "partial failure, barge-in, blank arguments"),
                   ("2026-10-02", 572, "prompt contradiction"), ("2026-10-02", 575, "class K follow-ups")]:
    m("tests", "tests passing", n, "count", d, note, "currentStatus.md / pytest")

# --- kernel and simulator ----------------------------------------------------------
for k, v in (("mean", 9.2), ("p50", 5.8), ("p95", 17.9), ("p99", 82.3), ("max", 2760.8)):
    m("kernel_latency", "per-step wall time " + k, v, "microseconds", "2026-09-23",
      "44,200 steps over 200 runs of an I-03-style scenario; budget 5,000 us", "docs/measurements.md")
for metric, v, cond in (("TTFS after end-of-turn, speculative interpretation off", 300000, "INTERPRET latency 300 ms, chunk spacing 150 ms"),
                        ("TTFS after end-of-turn, speculative interpretation on", 0, "same scenario"),
                        ('TTFS after end-of-turn, trailing "please", C1 off', 100000, "INTERPRET 300 ms"),
                        ('TTFS after end-of-turn, trailing "please", C1 on', 0, "INTERPRET 300 ms")):
    m("latency_sim", metric, v, "microseconds", "2026-09", cond, "docs/measurements.md")
for metric, sched, fail, cond in (("first run, before fixes (text)", 471, 126, "9 scenarios"),
                                  ("after fixes (text)", 9142, 0, "10 scenarios, 3 seeds"),
                                  ("after fixes (multimodal)", 8194, 0, "9 scenarios"),
                                  ("TRIAGE hold disabled alone (text)", 3122, 704, "ablation"),
                                  ("ASR capture order + dedupe window disabled", 912, 629, "ablation"),
                                  ("TRIAGE hold disabled alone (multimodal)", 927, 161, "ablation")):
    m("exploration", "schedules explored: " + metric, sched, "count", "2026-09", cond, "docs/measurements.md")
    m("exploration", "failing schedules: " + metric, fail, "count", "2026-09", cond, "docs/measurements.md")

# --- speech round trip -------------------------------------------------------------
rt_path = os.path.join(REPO, "run_output", "kokoro_parakeet_roundtrip", "roundtrip_results.json")
if os.path.exists(rt_path):
    rt = json.load(open(rt_path))
    m("speech", "TTS->ASR round trip: sentences with all facts intact", sum(1 for r in rt if r.get("ok")), f"of {len(rt)}", "2026-09-25",
      "Kokoro-82M -> Parakeet-tdt-0.6b-v2; order ids, amounts, codes", "run_output/kokoro_parakeet_roundtrip")
    m("speech", "TTS->ASR round trip: individual facts intact", sum(sum(r["facts_survived"].values()) for r in rt),
      f"of {sum(len(r['facts_survived']) for r in rt)}", "2026-09-25", "same", "run_output/kokoro_parakeet_roundtrip")

# --- superseded organizer kit --------------------------------------------------------
m("simulator_kit", "weighted score, Janus after the first integration pass", 10.4, "points (0-100)", "2026-09-24",
  "9 public scenarios, 3 repetitions, gemini-3.6-flash; superseded by FDB-v3 on 24 Sep", "run_output/after_57bc9a8+wt_eval_*.txt")
m("simulator_kit", "weighted score, Janus after the timer-liveness fix", 8.9, "points (0-100)", "2026-09-24", "same",
  "run_output/after2_timerfix_eval_*.txt")

# --- turn ending ---------------------------------------------------------------------
for metric, old, new, unit in (("recordings kept as one turn", 12, 66, "of 72"), ("end-of-turns inside speech", 98, 0, "count"),
                               ("final turn close after last word, median", 2.85, 2.08, "seconds")):
    m("turn_ending_replay", metric + " (old bridge)", old, unit, "2026-10-01",
      "real speech timing of 72 voice recordings replayed through each bridge", "1 Oct audit")
    m("turn_ending_replay", metric + " (new bridge)", new, unit, "2026-10-01", "same", "1 Oct audit")
# live: wire-order definition (end-of-turn sent before the last transcript chunk arrived), the 24 of 26 recordings with a wire log
for metric, a, b, unit in (("premature end-of-turns, wire order", 42, 0, "count"), ("recordings kept as one turn, wire order", 2, 24, "of 24")):
    m("turn_ending_live", metric + " (30 Sep)", a, unit, "2026-09-30", "same recordings; hosted STT on 30 Sep", "voice_recordings.csv")
    m("turn_ending_live", metric + " (2 Oct)", b, unit, "2026-10-02", "same recordings; local Whisper", "voice_recordings.csv")
# live: audit definition (turn closed while the speaker's words continued, FDB word timestamps), all 26 recordings
for metric, a, b, unit in (("premature end-of-turns, word-timestamp definition", 31, 0, "count"),
                           ("recordings kept as one turn, word-timestamp definition", 5, 26, "of 26"),
                           ("exact-match passes", 12, 16, "of 26")):
    m("turn_ending_live", metric + " (30 Sep)", a, unit, "2026-09-30", "same 26 recordings", "1-2 Oct analyses")
    m("turn_ending_live", metric + " (2 Oct)", b, unit, "2026-10-02", "same 26 recordings", "1-2 Oct analyses")

# --- the benchmark paper's reference numbers (context only) ---------------------------------
for name, p, lat in (("GPT-Realtime", 60.0, 6.89), ("Gemini Live 3.1", 54.0, 4.25), ("Gemini Live 2.5", 49.0, 7.26),
                     ("Cascaded (Whisper -> GPT-4o -> TTS)", 45.0, 10.12), ("Grok", 43.0, 6.65), ("Ultravox", 41.0, 8.40)):
    m("published_reference", "strict pass@1, " + name, p, "percent", "2026", "FDB-v3 paper, judged", "FDB-v3 paper")
    m("published_reference", "first response latency, " + name, lat, "seconds", "2026", "FDB-v3 paper", "FDB-v3 paper")

with open(os.path.join(HERE, "other_measurements.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["category", "metric", "value", "unit", "date", "condition", "source"])
    w.writeheader()
    w.writerows(M)

# --- failure classes ----------------------------------------------------------------
split_outcome = collections.Counter()
fail_path = os.path.join(HERE, "failures.csv")
if os.path.exists(fail_path):
    for r in csv.DictReader(open(fail_path)):
        if r["failure_set"] == "audit_2026-10-01" and r["cause_class"] == "split_turn":
            split_outcome[r["verify_2oct"] or "not re-run"] += 1
split_status = (f"Turn splitting fixed 1 Oct; on 2 Oct {split_outcome['pass']} of {sum(split_outcome.values())} of these recordings pass, "
                f"{split_outcome['fail']} fail for other reasons (label wording, a client abort)")
C = [
    ("final_2026-10-03", "undeclared_arg", "Label expects an argument the published tool does not declare (pets_allowed, category)", 7, "Not fitted: Janus sends declared parameters only (README, Benchmark labels)"),
    ("final_2026-10-03", "value_type", "Label types update_search_filter.value as bool/int; the tool declares str", 2, "Not fitted: the declared type is sent"),
    ("final_2026-10-03", "required_omitted", "Label leaves out an argument the tool requires (bedrooms, max_price, city)", 2, "Not fitted: unstated counts/budgets assumed aloud, an unstated city asked for"),
    ("final_2026-10-03", "value_not_said", "Label holds a value the user never says (a city)", 1, "Not fitted: the agent asks"),
    ("final_2026-10-03", "label_vs_audio", "Label disagrees with the recording (1800 vs eight hundred; a passport number)", 2, "Not fitted: what was said is used"),
    ("final_2026-10-03", "label_wording", 'Label wording stricter than what was said ("keyboards")', 2, "Not fitted"),
    ("final_2026-10-03", "conditional", "Conditional request: label expects a branch the tool result rules out", 1, "4 Oct: only the branch the result supports runs; travel_20 and finance_20, which passed by running every branch, now differ from their labels too"),
    ("final_2026-10-03", "filter_fold", "A request to change a saved filter answered with a search instead of the filter tool", 4, "4 Oct: interpreter rule (see BENCHMARKS.md section 5)"),
    ("final_2026-10-03", "stt_error", "Speech-to-text misheard an identifier or a city", 3, "Open"),
    ("final_2026-10-03", "value_form", "Model wording of a type value (travel card, drivers_license)", 2, "travel card fixed by the type-noun rule (b922183); drivers_license open"),
    ("audit_2026-10-01", "split_turn", "Turn ended mid-sentence by the voice bridge; the first half was acted on alone", 10, split_status),
    ("audit_2026-10-01", "reasoning", "Interpretation error that also fails in text replay (filter update folded into a search, conditionals, typed values)", 9, "Open; several are label-schema questions"),
    ("audit_2026-10-01", "label_mismatch", 'Label disagrees with the audio or is stricter than the judge ("keyboards" vs "keyboard", city never spoken)', 7, "Mostly not recoverable honestly"),
    ("audit_2026-10-01", "schema_gap", "Label needs pets_allowed, which the declared tool schema does not have", 6, "Open; deliberate (integrity question)"),
    ("audit_2026-10-01", "stt_error", "Speech-to-text dropped or misheard a word", 3, "Hosted STT helps; whole-turn re-transcription not built"),
    ("audit_2026-10-01", "infra", "Agent joined late (a stray demo worker shared the LiveKit project)", 1, "Operational; not a code fault"),
    ("full100_2026-09-29", "A", "FDB client aborted after the stream (SIGABRT); no result recorded", 8, "Late speech at teardown removed by the filler filter; 0 aborts in a later 32-recording run, 2 in the 2 Oct run"),
    ("full100_2026-09-29", "B", "Late or stalled execution: agent silent, calls 15-40 s after speech end or never", 13, "Whisper CPU fallback (fixed 30 Sep) and two kernel liveness bugs (fixed)"),
    ("full100_2026-09-29", "C", "Premature end-of-turn at a mid-utterance pause gave a partial interpretation", 6, "Turn-ending fix 1 Oct"),
    ("full100_2026-09-29", "C2", "Same root cause, extra calls: a read ran on the first half, then again after the rest", 4, "Turn-ending fix 1 Oct"),
    ("full100_2026-09-29", "D", "Enum-like values not in the canonical form the tool implies (driver license, filter keys)", 7, "Interpreter value rule 30 Sep"),
    ("full100_2026-09-29", "E", 'Spoken-ID shapes from Whisper not canonicalised ("P.O. 999", "F A S T nine nine")', 3, "Spoken-ID joiner 30 Sep"),
    ("full100_2026-09-29", "F", 'Exact-match brittleness the judge usually accepts ("Vegas" vs "Las Vegas")', 5, "Judge scoring"),
    ("full100_2026-09-29", "G", "Speech-to-text or label disagrees with the audio", 4, "Not recoverable"),
    ("full100_2026-09-29", "H", "Tool-schema gap (search_apartments has no pets_allowed)", 3, "Open; deliberate"),
    ("full100_2026-09-29", "I", "Interpretation or reasoning error", 3, "Partly open"),
    ("full100_2026-09-29", "J", "Conditionals: both branches executed", 2, "Open"),
    ("full100_2026-09-29", "K", "Sequential-chain blocking: an independent action waited on a blocked one", 1, "Independent-action fix 2 Oct (not yet re-measured)"),
]
with open(os.path.join(HERE, "failure_classes.csv"), "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["taxonomy", "class", "definition", "recordings", "status"])
    w.writerows(C)
print(f"{len(M)} measurements, {len(C)} failure classes")
