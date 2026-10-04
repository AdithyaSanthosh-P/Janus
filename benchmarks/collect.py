#!/usr/bin/env python3
"""Collects every FDB-v3 run on this machine into CSV files under benchmarks/.

  runs.csv             one row per scored run (headline numbers + what the run was)
  recordings.csv       one row per scenario in every text-replay run
  voice_recordings.csv one row per recording in every voice run (speaker, exact and
                       judged verdicts, latency, turn-ending evidence from the wire log)

Sources: run_output/** (text replays, local LiveKit runs; local only) and the cloud
results archives in docs/runs/ (committed, audio left out). Re-run after adding a run:

    python benchmarks/collect.py [--archives-dir DIR] [--fdb-root DIR]

`--fdb-root` is the Full-Duplex-Bench checkout's v3/ directory (default $FDB_V3_ROOT);
with it, every voice recording is re-scored with FDB's own exact-match evaluator and
the 'exact_pass' column is filled for runs whose own report was judged.

Scoring terms: "exact" = FDB exact-match scoring; "judged" = FDB `--use-llm` (gpt-4o).
`rescore_llm/` folders and `*.judged.json` reports are the same run re-scored with the
judge and appear as separate rows with the suffix `.judged`.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
import tarfile
import tempfile

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# (archive in docs/runs, root folder inside it, run id). Audio is left out of every archive.
ARCHIVES = [
    ("docs/runs/janus_fdbv3_voice_full100_2026-09-30.tgz", "janus_fdbv3_voice_full100_2026-09-30", "cloud_20260929_232528"),
    ("docs/runs/janus_fdbv3_voice_rerun32_2026-09-30.tgz", "janus_fdbv3_voice_rerun32_2026-09-30", "cloud_20260930_032600"),
    ("docs/runs/janus_fdbv3_voice_run64_2026-09-30.tgz", "janus_fdbv3_voice_run64_2026-09-30", "cloud_20260930_140536"),
    ("docs/runs/janus_fdbv3_voice_clean41_2026-09-30.tgz", "janus_fdbv3_voice_clean41_2026-09-30", "cloud_20260930_174502"),
    ("docs/runs/janus_fdbv3_voice_verify26_2026-10-02.tgz", "janus_fdbv3_voice_verify26_2026-10-02", "cloud_20261002_061207"),
    ("docs/runs/janus_fdbv3_voice_full100_2026-10-03.tgz", "janus_fdbv3_voice_full100_2026-10-03", "cloud_20261002_182600"),
    ("docs/runs/janus_fdbv3_voice_full100b_2026-10-03.tgz", "janus_fdbv3_voice_full100b_2026-10-03", "cloud_20261003_121822"),
    ("docs/runs/janus_fdbv3_voice_retest32_2026-10-03.tgz", "janus_fdbv3_voice_retest32_2026-10-03", "cloud_20261003_143517"),
    ("docs/runs/janus_fdbv3_voice_full100c_2026-10-03.tgz", "janus_fdbv3_voice_full100c_2026-10-03", "cloud_20261003_180457"),
    ("docs/runs/janus_fdbv3_voice_full100d_2026-10-04.tgz", "janus_fdbv3_voice_full100d_2026-10-04", "cloud_20261004_105851"),
    ("docs/runs/janus_fdbv3_voice_docker100_2026-10-04.tgz", "janus_fdbv3_voice_docker100_2026-10-04", "cloud_20261004_151517"),
]

# run id -> (class, phase, description). class: full100 | subset | debug
LABELS = {
    "text_fdb_v3_text_replay": ("subset", "Day 1", "First harness run, 20 scenarios (all e-commerce); bare fact-key bug stalled every scenario"),
    "text_fdb_v3_text_replay_full100": ("full100", "Day 1", "Full 100, gemini-3.5-flash-lite; G4 commit-intent gate blocked undeclared-mutability tools"),
    "text_fdb_v3_text_replay_full100_g4fix": ("full100", "Day 1", "G4 exemption for tools that declare no mutability"),
    "text_fdb_v3_text_replay_wp2": ("full100", "Day 2", "Multi-action interpretation + settle barrier + FDB profile"),
    "text_s2_full100_validation": ("full100", "S2", "Quick-win pack (11 items) before value rules"),
    "text_s2_full100_v2": ("full100", "S2", "Plus value rules (date ordinals, currency codes, account words, leading articles)"),
    "text_s2_full100_v3": ("full100", "S2", "Leading-article rule reverted (FDB labels inconsistent on articles)"),
    "text_s2_validation_20": ("subset", "S2", "20-scenario slice (alphabetical, all e-commerce) after the silent-stall fixes"),
    "text_s3_full100_A": ("full100", "S3", "Arm A: action plans and assume-and-say both off"),
    "text_s3_full100_A2": ("full100", "S3", "Arm A2: repeat of A (run-to-run noise)"),
    "text_s3_full100_B": ("full100", "S3", "Arm B: assume-and-say only"),
    "text_s3_full100_C": ("full100", "S3", "Arm C: action plans + assume-and-say, earlier prompt"),
    "text_s3_full100_C2": ("full100", "S3", "Arm C2: both on, current prompt (the arm kept)"),
    "text_s3_full100_D": ("full100", "S3", "Arm D: C2 re-run after late binding (judged), flash-lite"),
    "text_s3_full100_E_flash": ("full100", "S3", "Arm E: same as D with gemini-3.6-flash (became the default model)"),
    "text_openai_gpt41_subset": ("subset", "29 Sep", "gpt-4.1 as the reasoning model, 15 scenarios"),
    "text_openai_gpt41_subset_r3": ("subset", "29 Sep", "gpt-4.1, 15 scenarios, third repeat"),
    "text_text_replay_0930_setting": ("full100", "30 Sep", "Tried: 'update a setting' prompt rule (reverted: 75% vs 77%)"),
    "text_text_replay_0930_fixes": ("full100", "30 Sep", "Identifier-style value rule + spoken-ID joiner"),
    "text_text_replay_0930_final": ("full100", "30 Sep", "Configuration of the 30 Sep submission"),
    "text_text_replay_1002_valuerules": ("subset", "2 Oct", "Value rules for field names, search terms and amounts; 24 recordings with a search query or filter (exact-match scoring); vs 30 Sep: 20 identical calls, 4 improved, none worse"),
    "lk_20260927_173959": ("debug", "S1", "First LiveKit run, 1 recording (travel_01)"),
    "lk_20260927_174631": ("subset", "S1", "5 recordings (after the first reboot)"),
    "lk_20260927_175223": ("subset", "S1", "26-recording stratified sample, first voice baseline (laptop GPU)"),
    "lk_20260927_213947": ("subset", "S2", "Same 26 recordings after the quick-win pack and value rules"),
    "lk_20260927_222409": ("debug", "S2", "Targeted check of the speech-recognition silence fix"),
    "lk_20260927_223446": ("debug", "S2", "Targeted check of the speech-recognition silence fix"),
    "lk_20260927_225322": ("debug", "S2", "Targeted check, 4 recordings"),
    "lk_20260927_230003": ("debug", "S2", "Targeted check, 4 recordings"),
    "lk_20260927_230439": ("debug", "S2", "Targeted check, 2 recordings"),
    "lk_20260927_230718": ("subset", "S2", "26-recording sample after the speech-recognition CUDA fixes"),
    "lk_20260928_195832": ("debug", "S3", "One recording during the live-demo fixes"),
    "cloud_20260929_232528": ("full100", "Cloud", "All 100 recordings, native path, RTX 3090, local Whisper (fell back to CPU for the last quarter)"),
    "cloud_20260930_032600": ("subset", "Cloud", "32 hardest recordings of the full run, after the Whisper GPU fix"),
    "cloud_20260930_140536": ("subset", "Cloud", "64 recordings, hosted speech-to-text; 33 rooms were served by a stray demo worker and score 0 (31 valid)"),
    "cloud_20260930_174502": ("subset", "Cloud", "41 recordings (clean re-run of the 29 contaminated ids), hosted speech-to-text, judged"),
    "cloud_20261002_182600": ("full100", "Cloud", "All 100 recordings, judged, current code (commit 8020f79 plus the native setup fix), native path on a clean RTX 3090 box, hosted speech-to-text; first full run after the turn-ending fixes (run 3 Oct IST, box clock 2 Oct UTC)"),
    "cloud_20261003_121822": ("full100", "Cloud", "All 100 recordings, judged, commit 054c305 (fix pass of 3 Oct), same box and providers as cloud_20261002_182600: 65/100. Hosted speech-to-text was slower that day; 19 turns closed while their last segment was still being transcribed (3 in the 73% run), splitting requests. Fixed in a8d1fcd"),
    "cloud_20261003_143517": ("subset", "Cloud", "Re-test of the 23 ids (32 recordings) hit by the early turn close, commit a8d1fcd (the bridge waits for a running transcription): 24/32, against 15/32 in cloud_20261003_121822 and 23/32 in cloud_20261002_182600; no turn closed before its transcript"),
    "cloud_20261004_105851": ("full100", "Cloud", "All 100 recordings, judged, commit d1732c5 (conditional-branch, filter-update, identifier and value-form fixes), native path, RTX 3090: 73/100. The judge's OpenAI quota ran out for 53 of 100 response-quality scores (pass/fail and argument scoring unaffected). Against the 74% run: 4 better, 5 worse (two conditional recordings by design, two speech-to-text, one value form since fixed)"),
    "cloud_20261004_151517": ("full100", "Cloud", "All 100 recordings, judged, commit 1500c23, through ./reproduce.sh (Docker) on a clean Ubuntu 24.04 VM with an RTX 3090: 74/100, tool selection 0.964, argument accuracy 0.807, response quality 0.69, first word 7.1 s, no client aborts, no API errors"),
    "cloud_20261003_180457": ("full100", "Cloud", "All 100 recordings, judged, commit e296c85 (bridge waits for running transcriptions, review-pass fixes), same box and providers: 74/100, no turn closed before its transcript, response quality 0.72, turn-taking 100/100. Against the 73% run: 4 recordings better, 3 worse; against the 65% run: 10 better, 1 worse"),
    "text_textreplay_travel_typenoun": ("subset", "3 Oct", "Travel domain (20 recordings) after the type-value rules: 19/20 (travel_02 is the P9-9-9-90011 label); every doc_type canonical"),
    "text_textreplay_finance_typenoun": ("subset", "3 Oct", "Finance domain (25 recordings) after the <noun>_type rule (b922183): 25/25, every card and bill type in its bare form"),
    "text_textreplay_fix_1003": ("full100", "3 Oct", "Full 100 after the kernel fixes of the 3 Oct fix pass (clarify livelock, null slot delta), judged"),
    "text_textreplay_review_1003": ("full100", "3 Oct", "Full 100 on the code after the review pass (ae03f71), judged: gate before the second voice run. The model alternates \"travel\" and \"travel card\" for card_type between runs (finance_06/10 flip)"),
    "text_textreplay_cond_1004": ("subset", "4 Oct", "The four conditional scenarios after the conditional-branch fix (8a8122d), stall salvage 45 s: every branch matches the tool results; 0/4 against labels that expect both branches or a branch the result rules out"),
    "text_textreplay_cond_rule_1004": ("full100", "4 Oct", "Full 100 with the conditional-branch fix and the filter-update rule (4be3f70), stall salvage 45 s, judged: 76/100. Against review_1003: finance_06/10 pass, finance_20/travel_20 differ from their labels (conditional), housing_18 variance; housing_13/20/24/25 now call update_search_filter"),
    "text_textreplay_dash_1003": ("subset", "3 Oct", "The three recordings that dictate codes with \"dash\", after the identifier-joiner rule"),
    "text_textreplay_pets_1003": ("debug", "3 Oct", "Pet-wording prompt line on housing_05/14/15; Gemini was slow, several scenarios hit the 15 s stall salvage"),
    "text_textreplay_pets_base_1003": ("debug", "3 Oct", "Same three ids without the pet-wording line (the A/B baseline); same slowness"),
    "cloud_20261002_061207": ("subset", "Cloud", "26 recordings (10 split-turn failures, 5 fragile passes, 3 controls), local Whisper, after the turn-ending fix"),
}

RUN_FIELDS = ["run_id", "date", "kind", "class", "phase", "scored_by", "scenarios", "passed", "pass_rate",
              "tool_selection_acc", "argument_acc", "response_quality", "turn_take_rate", "avg_response_latency_s",
              "model", "stt", "commit", "overrides", "description", "source"]
REC_FIELDS = ["run_id", "scenario_id", "domain", "difficulty", "num_tools", "disfluency", "passed", "failure_reason",
              "expected_tools", "actual_tools", "missing", "unexpected", "eot_to_first_call_s", "eot_to_first_speech_s",
              "eot_to_final_s", "errors"]
VOICE_FIELDS = ["run_id", "scenario_id", "speaker", "domain", "difficulty", "expected_calls", "status", "exact_pass",
                "judged_pass", "failure_reason_exact", "expected_tools", "actual_tools", "n_calls", "turn_taken",
                "first_response_s", "tool_call_latency_s", "user_speech_end_s", "text_chunks", "end_of_turns",
                "premature_eots", "user_speech_events", "mid_speech_eots", "room"]


def _load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def _jsonl(path):
    try:
        with open(path) as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except Exception:  # noqa: BLE001
        return []


def _tool_metrics(tc):
    tc = tc or {}
    bm = tc.get("by_metric") or {}
    lat = tc.get("latency") or {}
    tt = tc.get("turn_taking") or {}
    return (bm.get("tool_selection_acc"), bm.get("argument_acc"), bm.get("response_qual"),
            tt.get("turn_take_rate"), lat.get("avg_response_latency_s"))


def _scored_by(meta, summary_text, judged_variant):
    if judged_variant:
        return "judged"
    judge = str(meta.get("judge", ""))
    if judge.startswith("gpt"):
        return "judged"
    if judge == "exact":
        return "exact"
    m = re.search(r"Judge:\s*(\w+)", summary_text or "")
    if m:
        return "judged" if m.group(1).lower() == "on" else "exact"
    return "exact"  # runs before the judge key existed (28 Sep) are exact-match


def _short_commit(c):
    """'7a22495-dirty' stays as is; a full hash is cut to 8 characters."""
    return c if "-" in c or len(c) <= 8 else c[:8]


def run_row(run_id, d, source, pr, tc, kind, judged_variant=False):
    meta = _load(os.path.join(d, "run_meta.json")) or {}
    manifest = _load(os.path.join(d, "manifest.json")) or {}
    agent = manifest.get("agent") or {}
    summary = ""
    if os.path.exists(os.path.join(d, "SUMMARY.md")):
        summary = open(os.path.join(d, "SUMMARY.md")).read()
    base_id = run_id[:-len(".judged")] if run_id.endswith(".judged") else run_id
    cls, phase, desc = LABELS.get(base_id, ("debug" if (pr.get("total_scenarios") or 0) <= 2 else "subset", "", ""))
    sel, arg, resp, take, lat = _tool_metrics(tc)
    return {
        "run_id": run_id,
        "date": (pr.get("evaluated_at") or meta.get("started_at") or "")[:10],
        "kind": kind, "class": cls, "phase": phase,
        "scored_by": _scored_by(meta, summary, judged_variant),
        "scenarios": pr.get("total_scenarios"), "passed": pr.get("passed"), "pass_rate": pr.get("overall_pass_rate"),
        "tool_selection_acc": sel, "argument_acc": arg, "response_quality": resp, "turn_take_rate": take,
        "avg_response_latency_s": lat,
        "model": meta.get("model") or agent.get("llm") or "", "stt": agent.get("stt") or "",
        "commit": _short_commit(meta.get("git_commit") or manifest.get("janus_commit") or ""),
        "overrides": " ".join(meta.get("overrides") or []), "description": desc, "source": source,
    }


def scenario_rows(run_id, pr, raw):
    by_id = {}
    for r in raw or []:
        by_id.setdefault(r.get("example_id"), []).append(r)
    rows = []
    for s in (pr or {}).get("scenario_results", []):
        ts = (s.get("checks") or {}).get("tool_selection") or {}
        sid = s.get("scenario_id", "")
        r = (by_id.get(sid) or [{}])[0]
        rows.append({
            "run_id": run_id, "scenario_id": sid, "domain": s.get("domain", ""), "difficulty": s.get("difficulty", ""),
            "num_tools": s.get("num_tools", ""), "disfluency": "|".join(s.get("disfluency") or []),
            "passed": int(bool(s.get("passed"))), "failure_reason": (s.get("failure_reason") or "")[:300],
            "expected_tools": "|".join(ts.get("expected") or []), "actual_tools": "|".join(ts.get("actual") or []),
            "missing": "|".join(ts.get("missing") or []), "unexpected": "|".join(ts.get("unexpected") or []),
            "eot_to_first_call_s": r.get("eot_to_first_call_s"), "eot_to_first_speech_s": r.get("eot_to_first_speech_s"),
            "eot_to_final_s": r.get("eot_to_final_s"), "errors": "|".join(r.get("errors") or []),
        })
    return rows


# --- voice recordings -------------------------------------------------------------

def _load_fdb(fdb_root):
    if not fdb_root or not os.path.isfile(os.path.join(fdb_root, "evaluate_pass_rate.py")):
        return None, {}
    sys.path.insert(0, fdb_root)
    try:
        import evaluate_pass_rate as epr  # type: ignore
    except Exception:  # noqa: BLE001
        return None, {}
    bench = {s["id"]: s for s in json.load(open(os.path.join(fdb_root, "benchmark_data_v2.json")))["scenarios"]}
    return epr, bench


def _key(sid, checks):
    ts = checks.get("tool_selection") or {}
    acts = tuple(json.dumps(d.get("actual_args"), sort_keys=True)
                 for d in ((checks.get("argument_accuracy") or {}).get("details") or []))
    return (sid, tuple(ts.get("actual") or ()), acts)


def _verdict_map(report):
    out = {}
    for s in (report or {}).get("scenario_results", []):
        out.setdefault(_key(s.get("scenario_id"), s.get("checks") or {}), []).append(bool(s.get("passed")))
    return out


def _wire_stats(d, room):
    path = os.path.join(d, "decisions", f"{room}.wire.jsonl")
    if not room or not os.path.exists(path):
        return {}
    events = [w for w in _jsonl(path) if w.get("dir") == "in"]
    chunks = [w for w in events if w.get("type") == "text_chunk" and (w.get("payload") or {}).get("text")]
    eots = [w for w in events if w.get("type") == "end_of_turn"]
    last_chunk = max([w["t_us"] for w in chunks] + [0])
    active, mid, n_speech = False, 0, 0
    for w in events:
        if w.get("type") == "user_speech":
            n_speech += 1
            active = bool((w.get("payload") or {}).get("active"))
        elif w.get("type") == "end_of_turn" and active:
            mid += 1
    return {"text_chunks": len(chunks), "end_of_turns": len(eots),
            "premature_eots": sum(1 for w in eots if w["t_us"] < last_chunk),
            "user_speech_events": n_speech, "mid_speech_eots": mid if n_speech else ""}


def voice_rows(run_id, d, reports, epr, bench):
    """reports: {'exact': report|None, 'judged': report|None} for this run."""
    judged_map = _verdict_map(reports.get("judged"))
    exact_map = _verdict_map(reports.get("exact"))
    rows = []
    for folder in sorted(glob.glob(os.path.join(d, "data", "*"))):
        res = _load(os.path.join(folder, "result_janus.json"))
        if not res:
            continue
        sid = res.get("example_id")
        spk = os.path.basename(folder).split("_")[-1][:6]
        calls = res.get("actual_tool_calls") or []
        lat = _load(os.path.join(folder, "latency_tool_analysis_janus.json")) or {}
        scen = bench.get(sid) or {}
        exact = judged = None
        failure = ""
        key = None
        if epr is not None and scen:
            ev = epr.evaluate_scenario_pass(scen, calls, use_llm=False)
            exact = int(bool(ev["passed"]))
            failure = (ev.get("failure_reason") or "")[:200]
            key = _key(sid, ev.get("checks") or {})
        if key is not None:
            if judged_map.get(key):
                judged = int(judged_map[key].pop(0))
            if exact is None and exact_map.get(key):
                exact = int(exact_map[key].pop(0))
        names = [c.get("function") for c in calls]
        rows.append({
            "run_id": run_id, "scenario_id": sid, "speaker": spk, "domain": scen.get("domain", ""),
            "difficulty": scen.get("difficulty", ""), "expected_calls": len(scen.get("expected_tool_calls") or []),
            "status": res.get("status", ""), "exact_pass": "" if exact is None else exact,
            "judged_pass": "" if judged is None else judged, "failure_reason_exact": failure,
            "expected_tools": "|".join(c.get("function", "") for c in scen.get("expected_tool_calls") or []),
            "actual_tools": "|".join(n for n in names if n), "n_calls": len(calls),
            "turn_taken": lat.get("turn_take_success", ""),
            "first_response_s": lat.get("first_response_latency_s", res.get("perceived_total_latency", "")),
            "tool_call_latency_s": lat.get("tool_call_latency_s", ""),
            "user_speech_end_s": res.get("user_speech_end_rel", ""), "room": res.get("room_name", ""),
            **_wire_stats(d, res.get("room_name")),
        })
    return rows


# --- main -------------------------------------------------------------------------

def _is_text_dir(d):
    return os.path.exists(os.path.join(d, "pass_rate_report.json")) and not os.path.isdir(os.path.join(d, "data"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archives-dir", default=REPO, help="repo root holding docs/runs/ (default: this repo)")
    ap.add_argument("--out-dir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--fdb-root", default=os.environ.get("FDB_V3_ROOT", ""))
    args = ap.parse_args()
    epr, bench = _load_fdb(args.fdb_root)
    if epr is None:
        print("note: no FDB checkout (--fdb-root); exact_pass is filled only where the run's own report was exact")
    runs, recs, voice = [], [], []
    ro = os.path.join(REPO, "run_output")

    # text replays and local LiveKit runs
    for pr_path in sorted(glob.glob(os.path.join(ro, "**", "pass_rate_report*.json"), recursive=True)):
        d = os.path.dirname(pr_path)
        rel = os.path.relpath(d, ro)
        if rel.startswith(("race_", "tmp")) or "/data/" in rel or "/tmp/" in rel:
            continue
        name = os.path.basename(pr_path)
        judged_variant = name.endswith(".judged.json") or rel.endswith("rescore_llm")
        pr = _load(pr_path)
        if not pr or (name != "pass_rate_report.json" and not judged_variant):
            continue
        base = rel.replace("/rescore_llm", "").replace("/", "_")
        is_voice = os.path.isdir(os.path.join(d, "data")) or os.path.isdir(os.path.join(os.path.dirname(d), "data"))
        run_id = ("lk_" + base[len("livekit_"):]) if base.startswith("livekit_") else "text_" + base
        kind = "voice_local" if base.startswith("livekit_") else "text_replay"
        suffix = ".judged" if judged_variant else ""
        tc = _load(os.path.join(d, name.replace("pass_rate", "tool_calls")))
        runs.append(run_row(run_id + suffix, d, f"run_output/{rel}", pr, tc, kind, judged_variant))
        if kind == "text_replay":
            recs.extend(scenario_rows(run_id + suffix, pr, _load(os.path.join(d, "raw_results.json"))))

    voice_dirs = {}
    for d in sorted(glob.glob(os.path.join(ro, "livekit_*"))):
        rid = "lk_" + os.path.basename(d)[len("livekit_"):]
        rep = {"exact": _load(os.path.join(d, "pass_rate_report.json")), "judged": _load(os.path.join(d, "pass_rate_report.judged.json"))}
        if rep["exact"] and os.path.isdir(os.path.join(d, "data")):
            voice.extend(voice_rows(rid, d, rep, epr, bench))

    # cloud archives
    with tempfile.TemporaryDirectory() as tmp:
        for archive, inner, rid in ARCHIVES:
            path = os.path.join(args.archives_dir, archive)
            if not os.path.exists(path):
                print(f"skip (not found): {archive}")
                continue
            with tarfile.open(path) as tar:
                def wanted(m):
                    if not (m.isfile() and m.name.startswith(inner + "/")) or m.name.endswith((".wav", ".mp3")):
                        return False
                    return "/decisions/" not in m.name or m.name.endswith(".wire.jsonl")
                tar.extractall(tmp, members=[m for m in tar.getmembers() if wanted(m)])
            d = os.path.join(tmp, inner)
            pr, tc = _load(os.path.join(d, "pass_rate_report.json")), _load(os.path.join(d, "tool_calls_report.json"))
            if not pr:
                continue
            rep = {"exact": pr, "judged": None}
            runs.append(run_row(rid, d, archive, pr, tc, "voice_cloud"))
            jpr = _load(os.path.join(d, "pass_rate_report.judged.json"))
            if jpr:
                rep["judged"] = jpr
                runs.append(run_row(rid + ".judged", d, archive, jpr, _load(os.path.join(d, "tool_calls_report.judged.json")),
                                    "voice_cloud", True))
            summary_path = os.path.join(d, "SUMMARY.md")
            if os.path.exists(summary_path) and "Judge: on" in open(summary_path).read():
                rep = {"exact": None, "judged": pr}
            voice.extend(voice_rows(rid, d, rep, epr, bench))

    runs.sort(key=lambda r: (r["date"], r["run_id"]))
    for name, fields, rows in (("runs.csv", RUN_FIELDS, runs), ("recordings.csv", REC_FIELDS, recs),
                               ("voice_recordings.csv", VOICE_FIELDS, voice)):
        with open(os.path.join(args.out_dir, name), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    print(f"{len(runs)} runs, {len(recs)} text-replay scenario rows, {len(voice)} voice recording rows")


if __name__ == "__main__":
    main()
