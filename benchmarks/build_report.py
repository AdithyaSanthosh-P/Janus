#!/usr/bin/env python3
"""Fills the tables in benchmarks/report_template.md from the CSVs and writes BENCHMARKS.md.

    python benchmarks/collect.py [...]      # refresh the CSVs from run_output/ and the archives
    python benchmarks/build_report.py       # rebuild BENCHMARKS.md

A line `<!--table:NAME-->` in the template is replaced by the generated table NAME.
"""
from __future__ import annotations

import collections
import csv
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))


def rows(name):
    with open(os.path.join(HERE, name)) as fh:
        return list(csv.DictReader(fh))


RUNS, REC, VOICE, FAIL, CLASSES, OTHER = (rows(n) for n in (
    "runs.csv", "recordings.csv", "voice_recordings.csv", "failures.csv", "failure_classes.csv", "other_measurements.csv"))
RUN = {r["run_id"]: r for r in RUNS}


def pct(x, digits=1):
    try:
        return f"{float(x) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return ""


def num(x, digits=3):
    try:
        return f"{float(x):.{digits}f}"
    except (TypeError, ValueError):
        return ""


def md(header, body):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in body]
    return "\n".join(out)


def run_cells(r, with_desc=True):
    n = int(float(r["scenarios"] or 0))
    return [r["date"], f"`{r['run_id']}`", r["scored_by"], f"{r['passed']}/{n}", pct(r["pass_rate"]),
            num(r["tool_selection_acc"]), num(r["argument_acc"])] + ([r["description"]] if with_desc else [])


# --- tables ----------------------------------------------------------------------

def t_text_timeline():
    keep = [r for r in RUNS if r["kind"] == "text_replay" and r["class"] == "full100" and not r["run_id"].endswith(".judged")]
    body = [run_cells(r) for r in sorted(keep, key=lambda r: (r["date"], r["pass_rate"] and float(r["pass_rate"])))]
    return md(["Date", "Run", "Scored", "Passed", "Strict pass", "Tool sel.", "Arg. acc.", "What changed"], body)


def t_s3_arms():
    order = ["A", "A2", "B", "C", "C2"]
    body = []
    for a in order:
        ex, ju = RUN.get(f"text_s3_full100_{a}"), RUN.get(f"text_s3_full100_{a}.judged")
        body.append([a, ex["description"].split(": ", 1)[1], ex["passed"], num(ex["argument_acc"]), ju["passed"], num(ju["argument_acc"])])
    for a, label in (("D", "D"), ("E_flash", "E")):
        r = RUN[f"text_s3_full100_{a}"]
        body.append([label, r["description"].split(": ", 1)[1], "-", "-", r["passed"], num(r["argument_acc"])])
    return md(["Arm", "Configuration", "Exact passed /100", "Exact arg. acc.", "Judged passed /100", "Judged arg. acc."], body)


def t_voice_runs():
    keep = [r for r in RUNS if r["kind"] in ("voice_local", "voice_cloud") and int(float(r["scenarios"] or 0)) >= 4]
    body = []
    for r in sorted(keep, key=lambda r: (r["date"], r["run_id"])):
        lat = num(r["avg_response_latency_s"], 1)
        body.append(run_cells(r, with_desc=False) + [lat + (" s" if lat else ""), r["description"]])
    return md(["Date", "Run", "Scored", "Passed", "Strict pass", "Tool sel.", "Arg. acc.", "Avg response", "What it was"], body)


def t_domain():
    def by(rows_, key, passkey, run):
        d = collections.defaultdict(lambda: [0, 0])
        for r in rows_:
            if r["run_id"] == run and r[passkey] != "":
                d[r[key]][1] += 1
                d[r[key]][0] += r[passkey] == "1"
        return d
    text = by(REC, "domain", "passed", "text_text_replay_0930_final")
    vx = by(VOICE, "domain", "exact_pass", "cloud_20260929_232528")
    vj = by(VOICE, "domain", "judged_pass", "cloud_20260929_232528")
    nj = by(VOICE, "domain", "judged_pass", "cloud_20261002_182600")
    body = []
    for dom in ("ecommerce_support", "finance_billing", "housing_location", "travel_identity"):
        f = lambda d: f"{d[dom][0]}/{d[dom][1]}"  # noqa: E731
        body.append([dom, f(text), f(vj), f(vx), f(nj)])
    tot = lambda d: f"{sum(v[0] for v in d.values())}/{sum(v[1] for v in d.values())}"  # noqa: E731
    body.append(["**all**", tot(text), tot(vj), tot(vx), tot(nj)])
    return md(["Domain", "Text replay, judged (30 Sep final)", "Voice, judged (29-30 Sep)", "Voice, exact (29-30 Sep)", "**Voice, judged (3 Oct)**"], body)


def t_calls():
    def by(rows_, key, passkey, run):
        d = collections.defaultdict(lambda: [0, 0])
        for r in rows_:
            if r["run_id"] == run and r[passkey] != "":
                d[r[key]][1] += 1
                d[r[key]][0] += r[passkey] == "1"
        return d
    text = by(REC, "num_tools", "passed", "text_text_replay_0930_final")
    vx = by(VOICE, "expected_calls", "exact_pass", "cloud_20260929_232528")
    nj = by(VOICE, "expected_calls", "judged_pass", "cloud_20261002_182600")
    body = [[f"{k} call" + ("s" if k != "1" else ""), f"{text[k][0]}/{text[k][1]}", f"{vx[k][0]}/{vx[k][1]}", f"{nj[k][0]}/{nj[k][1]}"] for k in ("1", "2", "3")]
    return md(["Expected tool calls", "Text replay, judged", "Voice, exact (29-30 Sep)", "**Voice, judged (3 Oct)**"], body)


def t_full100_flips():
    """Per recording, the 30 Sep full run (judged) against the 3 Oct full run (judged)."""
    old = {(r["scenario_id"], r["speaker"]): r for r in VOICE if r["run_id"] == "cloud_20260929_232528"}
    new = [r for r in VOICE if r["run_id"] == "cloud_20261002_182600"]
    c = collections.Counter()
    for r in new:
        o = old.get((r["scenario_id"], r["speaker"]))
        was = "?" if o is None or o["judged_pass"] == "" else o["judged_pass"]
        c[(was, r["judged_pass"])] += 1
    rows = [["fail", "pass", "fixed", c[("0", "1")]], ["pass", "pass", "unchanged pass", c[("1", "1")]],
            ["fail", "fail", "unchanged fail", c[("0", "0")]], ["pass", "fail", "**regressed**", c[("1", "0")]]]
    return md(["30 Sep (judged)", "3 Oct (judged)", "Change", "Recordings"], rows)


def _previous_verdict(sid, spk):
    """The 30 Sep verdict for a recording: the clean re-run if it has one, else the 64-recording run
    when its room was served by the cloud worker, else the full run."""
    for run in ("cloud_20260930_174502", "cloud_20260930_140536", "cloud_20260929_232528"):
        for r in VOICE:
            if r["run_id"] == run and r["scenario_id"] == sid and r["speaker"] == spk and (run != "cloud_20260930_140536" or r["end_of_turns"] != ""):
                return run, r
    return None, None


def t_flips():
    new = [r for r in VOICE if r["run_id"] == "cloud_20261002_061207"]
    body = []
    for r in sorted(new, key=lambda r: (r["scenario_id"], r["speaker"])):
        run, old = _previous_verdict(r["scenario_id"], r["speaker"])
        was = "-" if old is None else ("pass" if old["exact_pass"] == "1" else "fail")
        now = "pass" if r["exact_pass"] == "1" else "fail"
        change = {"failpass": "fixed", "passfail": "**regressed**"}.get(was + now, "same")
        if r["status"] == "inference_failed":
            change += " (FDB client aborted)"
        body.append([f"{r['scenario_id']} / {r['speaker']}", was, (old["premature_eots"] if old and old["end_of_turns"] != "" else "-"), now,
                     (r["premature_eots"] if r["end_of_turns"] != "" else "- (client aborted)"), change])
    return md(["Recording", "30 Sep (exact)", "30 Sep premature end-of-turns", "2 Oct (exact)", "2 Oct premature", "Change"], body)


def t_turn_summary():
    new = [r for r in VOICE if r["run_id"] == "cloud_20261002_061207" and r["end_of_turns"] != ""]
    old = []
    for r in new:
        _, o = _previous_verdict(r["scenario_id"], r["speaker"])
        if o is not None and o["end_of_turns"] != "":
            old.append(o)
    pairs = [(r, o) for r in new for o in [_previous_verdict(r["scenario_id"], r["speaker"])[1]] if o is not None and o["end_of_turns"] != ""]
    n = len(pairs)
    body = [["Recordings compared (both runs have a wire log)", n, n],
            ["Premature end-of-turns (end-of-turn sent before the user's last transcript chunk arrived)", sum(int(o["premature_eots"] or 0) for _, o in pairs), sum(int(r["premature_eots"] or 0) for r, _ in pairs)],
            ["Recordings kept as one turn", sum(1 for _, o in pairs if int(o["premature_eots"] or 0) == 0), sum(1 for r, _ in pairs if int(r["premature_eots"] or 0) == 0)],
            ["End-of-turns while the user was speaking", "not measurable (no speech signal)", sum(int(r["mid_speech_eots"] or 0) for r, _ in pairs)]]
    return md(["Measure", "30 Sep (before the fix)", "2 Oct (after the fix)"], body)


def t_turn_replay():
    cells = collections.defaultdict(dict)
    for r in OTHER:
        if r["category"] == "turn_ending_replay":
            metric, _, side = r["metric"].rpartition(" (")
            cells[(metric, r["unit"])][side.rstrip(")")] = r["value"]
    body = [[m, u, v["old bridge"], v["new bridge"]] for (m, u), v in cells.items()]
    return md(["Measure", "Unit", "Old bridge", "New bridge"], body)


def t_premature_by_run():
    body = []
    for rid in ("cloud_20260929_232528", "cloud_20260930_032600", "cloud_20260930_174502", "cloud_20261002_061207", "cloud_20261002_182600"):
        rs = [r for r in VOICE if r["run_id"] == rid and r["end_of_turns"] != ""]
        prem = sum(int(r["premature_eots"] or 0) for r in rs)
        split = sum(1 for r in rs if int(r["premature_eots"] or 0) > 0)
        by_split = collections.defaultdict(lambda: [0, 0])
        for r in rs:
            k = "one turn" if int(r["premature_eots"] or 0) == 0 else "split"
            by_split[k][1] += 1
            by_split[k][0] += r["exact_pass"] == "1"
        body.append([f"`{rid}`", len(rs), prem, split,
                     f"{by_split['one turn'][0]}/{by_split['one turn'][1]}", f"{by_split['split'][0]}/{by_split['split'][1]}"])
    return md(["Run", "Recordings with wire log", "Premature end-of-turns", "Recordings split", "Exact pass, one turn", "Exact pass, split"], body)


def t_failure_sets():
    body = []
    for fs in ("ledger_2026-09-30", "audit_2026-10-01", "verify_2026-10-02", "full100_2026-10-03", "final_2026-10-03"):
        c = collections.Counter(r["cause_class"] for r in FAIL if r["failure_set"] == fs)
        body.append([f"`{fs}`", sum(c.values()), ", ".join(f"{k} {v}" for k, v in c.most_common())])
    return md(["Failure set", "Recordings", "By cause"], body)


def t_failure_classes():
    body = [[f"`{r['taxonomy']}`", r["class"], r["definition"], r["recordings"], r["status"]] for r in CLASSES]
    return md(["Taxonomy", "Class", "Definition", "Recordings", "Status"], body)


def t_failure_by_domain():
    body = []
    for fs in ("audit_2026-10-01",):
        c = collections.Counter(r["domain"] for r in FAIL if r["failure_set"] == fs)
        for dom, n in c.most_common():
            causes = collections.Counter(r["cause_class"] for r in FAIL if r["failure_set"] == fs and r["domain"] == dom)
            body.append([dom, n, ", ".join(f"{k} {v}" for k, v in causes.most_common())])
    return md(["Domain (36 judged failures, current code)", "Failures", "Causes"], body)


def t_tests():
    body = [[r["date"], r["value"], r["condition"]] for r in OTHER if r["category"] == "tests"]
    return md(["Date", "Tests passing", "At that point"], body)


def t_other(category, cols=("metric", "value", "unit", "condition")):
    body = [[r[c] for c in cols] for r in OTHER if r["category"] == category]
    return md([c.capitalize() for c in cols], body)


def t_all_runs():
    body = []
    for r in RUNS:
        n = int(float(r["scenarios"] or 0))
        body.append([r["date"], f"`{r['run_id']}`", r["kind"], r["class"], r["scored_by"], f"{r['passed']}/{n}", pct(r["pass_rate"]),
                     num(r["tool_selection_acc"]), num(r["argument_acc"]), r["model"], r["commit"]])
    return md(["Date", "Run", "Kind", "Class", "Scored", "Passed", "Rate", "Tool sel.", "Arg. acc.", "Model", "Commit"], body)


TABLES = {
    "text_timeline": t_text_timeline, "s3_arms": t_s3_arms, "voice_runs": t_voice_runs, "domain": t_domain, "calls": t_calls,
    "flips": t_flips, "full100_flips": t_full100_flips, "turn_replay": t_turn_replay, "turn_summary": t_turn_summary, "premature_by_run": t_premature_by_run, "failure_sets": t_failure_sets,
    "failure_classes": t_failure_classes, "failure_by_domain": t_failure_by_domain, "tests": t_tests, "all_runs": t_all_runs,
    "kernel_latency": lambda: t_other("kernel_latency", ("metric", "value", "unit")),
    "sim_latency": lambda: t_other("latency_sim", ("metric", "value", "unit")),
    "exploration": lambda: t_other("exploration", ("metric", "value", "condition")),
    "speech": lambda: t_other("speech", ("metric", "value", "unit")),
    "simulator_kit": lambda: t_other("simulator_kit", ("metric", "value", "unit", "condition")),
    "published": lambda: md(["System", "Strict pass@1 (%)", "First response (s)"], [
        [r["metric"].split(", ", 1)[1], r["value"], next(x["value"] for x in OTHER if x["category"] == "published_reference"
                                                         and x["metric"] == "first response latency, " + r["metric"].split(", ", 1)[1])]
        for r in OTHER if r["category"] == "published_reference" and r["metric"].startswith("strict")]),
}


def main():
    template = open(os.path.join(HERE, "report_template.md")).read()
    counts = {"runs": len(RUNS), "recordings": f"{len(REC):,}", "voice": len(VOICE), "failures": len(FAIL)}
    template = re.sub(r"\{\{(\w+)\}\}", lambda m: str(counts[m.group(1)]), template)
    out = re.sub(r"<!--table:(\w+)-->", lambda m: TABLES[m.group(1)](), template)
    with open(os.path.join(REPO, "BENCHMARKS.md"), "w") as fh:
        fh.write(out)
    print(f"BENCHMARKS.md written ({len(out.splitlines())} lines)")


if __name__ == "__main__":
    main()
