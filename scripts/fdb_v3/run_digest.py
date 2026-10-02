"""A compact digest of one FDB-v3 results directory (stdlib only, runs anywhere).

    python scripts/fdb_v3/run_digest.py results/<timestamp> [--out digest.md] [--passes]

Reads what the run already wrote (pass_rate_report.json, tool_calls_report.json,
data/*/result_janus.json, decisions/*.wire.jsonl, agent.log, retried.txt) and
prints a few thousand tokens: the headline, where the failures are, and one
short block per failing recording (expected vs actual calls, what the user said,
what the agent heard, what it answered, a guessed cause, the decision-log path).

Why it exists: a full run writes tens of thousands of log lines. A reviewer, human
or model, should read this digest and open one recording's decision log only when
a failure needs it -- never the raw logs.

The cause guess is a heuristic for triage, not a verdict: check it against the
decision log before changing code.
"""

from __future__ import annotations

import argparse
import ast
import collections
import json
import re
from pathlib import Path


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001 - a missing or partial file is a fact worth reporting, not a crash
        return None


def _lit(value):
    """result_janus.json stores some fields as Python-literal strings."""
    if isinstance(value, str):
        try:
            return ast.literal_eval(value)
        except Exception:  # noqa: BLE001
            return value
    return value


def _short(text, n=220) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _calls(calls) -> str:
    out = []
    for c in calls or []:
        args = c.get("args") or c.get("arguments") or {}
        out.append(f"{c.get('function') or c.get('tool_name')}({', '.join(f'{k}={v!r}' for k, v in args.items())})")
    return "; ".join(out) or "none"


def _wire(path: Path) -> dict:
    """What the agent heard, spoke and did in one room, from its wire log."""
    heard, spoke, calls, eots = [], [], [], 0
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("dir") == "in":
                if ev.get("type") == "text_chunk":
                    heard.append((ev.get("payload") or {}).get("text", ""))
                elif ev.get("type") == "end_of_turn":
                    eots += 1
            elif ev.get("dir") == "out":
                body = ev.get("body") or {}
                if ev.get("action_type") in ("speak", "clarify", "final"):
                    spoke.append((ev.get("action_type"), body.get("text", "")))
                elif ev.get("action_type") == "tool_call":
                    calls.append(f"{body.get('tool_name')}({', '.join(f'{k}={v!r}' for k, v in (body.get('arguments') or {}).items())})")
    return {"heard": " ".join(heard), "spoke": spoke, "calls": calls, "turns": eots}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(text).lower()))


def _guess_cause(res: dict, wire: dict, result_file: dict | None) -> str:
    """One short tag. Order matters: infrastructure first, then silence, then speech, then reasoning."""
    status = (result_file or {}).get("status")
    if status and status != "completed":
        return f"infra: runner status {status}"
    checks = res.get("checks", {})
    sel = checks.get("tool_selection", {})
    actual = sel.get("actual") or []
    if not actual:
        if any("couldn't finish" in t.lower() for _, t in wire["spoke"]):
            return "stall: no calls, the stall safety net fired"
        return "silent: the agent made no calls"
    if wire["turns"] > 1:
        return f"split_turn: the user's request closed {wire['turns']} turns"
    fdb_said = _words((result_file or {}).get("input_transcript", ""))
    heard = _words(wire["heard"])
    if fdb_said and heard:
        overlap = len(fdb_said & heard) / max(1, len(fdb_said))
        if overlap < 0.8:
            return f"stt: the agent heard a different sentence ({overlap:.0%} word overlap with the reference transcript)"
    if sel.get("missing") or sel.get("unexpected"):
        return f"wrong_tools: missing {sel.get('missing')} unexpected {sel.get('unexpected')}"
    for d in checks.get("argument_accuracy", {}).get("details", []):
        if not d.get("passed"):
            return f"wrong_args: {d.get('function')}"
    return "other"


def _agent_log_errors(path: Path, top: int = 8) -> list[str]:
    if not path.exists():
        return ["agent.log missing"]
    seen: collections.Counter = collections.Counter()
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("ERROR") or "kernel step failed" in line or re.match(r"^[A-Za-z_.]*(Error|Exception|Timeout)\b", line):
            seen[re.sub(r"[0-9a-f]{6,}|\d+", "#", line)[:140]] += 1
    return [f"{n}x {msg}" for msg, n in seen.most_common(top)] or ["none"]


def build(run: Path, include_passes: bool = False) -> str:
    report = _load(run / "pass_rate_report.json") or {}
    tools = _load(run / "tool_calls_report.json") or {}
    results = report.get("scenario_results", [])
    by_id = {r["scenario_id"]: r for r in results}
    files: dict[str, tuple[dict, Path]] = {}
    for d in sorted((run / "data").glob("*/")):
        rf = _load(d / "result_janus.json")
        if rf:
            files[rf.get("example_id", d.name)] = (rf, d)

    lines = [f"# Run digest: {run.name}", ""]
    lines.append(f"- Scenarios scored: {report.get('total_scenarios')}  passed: {report.get('passed')}  failed: {report.get('failed')}  strict pass rate: {report.get('overall_pass_rate')}")
    sm = run / "SUMMARY.md"
    if sm.exists():
        lines += [f"- {l[2:]}" for l in sm.read_text().splitlines() if l.startswith(("- Janus commit", "- Judge"))]
    tm = tools.get("turn_taking") or {}
    lines.append(f"- Tool metrics: {json.dumps(tools.get('by_metric'))[:300]}")
    lines.append(f"- Turn-taking: {json.dumps(tm)[:200]}  Latency: {json.dumps(tools.get('latency'))[:240]}")
    for key in ("by_domain", "by_difficulty", "by_disfluency_feature"):
        if report.get(key):
            lines.append(f"- {key}: {json.dumps(report[key])[:420]}")
    retried = run / "retried.txt"
    lines.append(f"- Recordings re-streamed after a client abort: {len(retried.read_text().split(chr(10))) - 1 if retried.exists() else 0}")
    missing = [i for i, (rf, _) in files.items() if i not in by_id]
    if missing:
        lines.append(f"- Recordings with a result file but no score (client abort or not evaluated): {', '.join(sorted(missing))}")
    lines += ["", "## Agent log: top errors (de-duplicated)"] + [f"- {e}" for e in _agent_log_errors(run / "agent.log")]

    failed = [r for r in results if not r.get("passed")]
    causes: collections.Counter = collections.Counter()
    blocks = []
    for res in sorted(failed, key=lambda r: r["scenario_id"]):
        rf, d = files.get(res["scenario_id"], ({}, None))
        room = (rf or {}).get("room_name")
        wire = _wire(run / "decisions" / f"{room}.wire.jsonl") if room else {"heard": "", "spoke": [], "calls": [], "turns": 0}
        cause = _guess_cause(res, wire, rf)
        causes[cause.split(":")[0]] += 1
        sel = res.get("checks", {}).get("tool_selection", {})
        arg_lines = []
        for det in res.get("checks", {}).get("argument_accuracy", {}).get("details", []):
            if not det.get("passed"):
                arg_lines.append(f"    {det.get('function')}: expected {det.get('expected_args')} got {det.get('actual_args')} ({_short(det.get('explanation'), 120)})")
        blocks.append("\n".join([
            f"### {res['scenario_id']}  [{res.get('domain')}, {res.get('difficulty')}, {','.join(res.get('disfluency') or []) or 'no disfluency'}]  cause guess: {cause}",
            f"- reason: {_short(res.get('failure_reason'), 200)}",
            f"- expected tools: {sel.get('expected')}  actual: {sel.get('actual')}",
            *([f"- wrong arguments:"] + arg_lines if arg_lines else []),
            f"- reference transcript: {_short((rf or {}).get('input_transcript'), 300)}",
            f"- agent heard ({wire['turns']} turn end(s)): {_short(wire['heard'], 300)}",
            f"- agent said: {_short(' | '.join(t for _, t in wire['spoke']), 260)}",
            f"- calls emitted: {_calls(_lit((rf or {}).get('actual_tool_calls')))}",
            f"- first speech: {_short(_lit((rf or {}).get('latency')), 90)}; decision log: {f'decisions/{room}.jsonl' if room else 'none (no room)'}",
        ]))

    lines += ["", f"## Failures by guessed cause ({len(failed)} total)"]
    lines += [f"- {c}: {n}" for c, n in causes.most_common()]
    lines += ["", "## Failing recordings", ""] + blocks
    if include_passes:
        lines += ["", "## Passing recordings", ", ".join(sorted(r["scenario_id"] for r in results if r.get("passed")))]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", help="a results/<timestamp> directory")
    ap.add_argument("--out", help="write here too (default: <run>/digest.md)")
    ap.add_argument("--passes", action="store_true", help="also list the passing recordings")
    args = ap.parse_args()
    run = Path(args.run)
    text = build(run, include_passes=args.passes)
    (Path(args.out) if args.out else run / "digest.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
