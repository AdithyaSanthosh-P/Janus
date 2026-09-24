#!/usr/bin/env python3
"""Render a scenario run as a self-contained HTML timeline.

    python tools/timeline.py scenarios/pub_02_text_interrupt.json
    python tools/timeline.py tests/conformance/conf_23_correction_inside_commit_hold.json -o out.html
    python tools/timeline.py --trace results.json          # a run_local.py --json dump

Three lanes on one virtual clock - what the user did, what DUET said, and
what the tools were doing - with every tool call drawn as a bar from issue to
completion. A cancelled call is drawn CUT at the moment of the cancel, in the
colour of the interruption that invalidated it: epoch invalidation (M1) made
visible. Under the lanes, the state snapshot after each epoch, with the slots
that changed highlighted, and the score breakdown.

No dependencies beyond the kit; the page has no scripts and loads nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from harness.runner import EvaluationHarness  # noqa: E402
from harness.scorer import score_scenario  # noqa: E402

SPOKEN = {"filler_speech": "filler", "clarification_request": "ask",
          "final_response": "final"}


def run(scenario: Dict[str, Any]) -> List[Dict[str, Any]]:
    from agent.agent import ParticipantAgent
    try:
        from duet import config
        config.STRICT = False
    except ImportError:
        pass

    async def go():
        h = EvaluationHarness(scenario, lambda a, b: ParticipantAgent(a, b),
                              time_scale=1.0, verbose=False)
        await h.prepare()
        return await h.run()
    return asyncio.run(go())


def _esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def build_page(scenario: Dict[str, Any], trace: List[Dict[str, Any]]) -> str:
    score = score_scenario(scenario, trace)
    end = max([e.get("t_ms", 0) for e in trace] + [1000.0]) + 300.0
    width = 1100.0

    def x(t: float) -> float:
        return 150.0 + (width - 170.0) * (t / end)

    user, speech, calls, epochs = [], [], {}, []
    for e in trace:
        t = float(e.get("t_ms", 0))
        kind = e.get("kind")
        if kind == "event":
            et = e.get("event_type")
            p = e.get("payload") or {}
            if et in ("user_speech_chunk", "interruption", "user_audio_chunk", "video_frame"):
                label = p.get("text") or p.get("audio_ref") or p.get("image_ref") or et
                user.append((t, et, str(label)))
                if et == "interruption":
                    epochs.append(t)
        elif kind == "action":
            a = e.get("action")
            if a in SPOKEN:
                speech.append((t, SPOKEN[a], (e.get("payload") or {}).get("text", ""),
                               e.get("state_snapshot")))
            elif a == "tool_call":
                cid = e.get("call_id") or (e.get("payload") or {}).get("call_id")
                calls[cid] = {"t0": t, "api": e.get("api_name"), "args": e.get("args") or {},
                              "t1": None, "how": "running"}
        elif kind in ("tool_completed", "tool_cancelled", "tool_abandoned"):
            cid = e.get("call_id")
            if cid in calls and calls[cid]["t1"] is None:
                calls[cid]["t1"] = t
                calls[cid]["how"] = {"tool_completed": e.get("status", "success"),
                                     "tool_cancelled": "cancelled",
                                     "tool_abandoned": "abandoned"}[kind]

    rows: List[str] = []
    lane_y = {"user": 40, "speech": 120, "tools": 200}
    tool_rows = max(1, len(calls))
    height = lane_y["tools"] + 34 * tool_rows + 60

    # time grid
    step = 500.0
    t = 0.0
    while t <= end:
        rows.append('<line x1="%.1f" y1="20" x2="%.1f" y2="%d" class="grid"/>' % (x(t), x(t), height - 30))
        rows.append('<text x="%.1f" y="%d" class="tick">%dms</text>' % (x(t), height - 12, t))
        t += step
    for name, y in (("User", lane_y["user"]), ("DUET says", lane_y["speech"]), ("Tools", lane_y["tools"])):
        rows.append('<text x="12" y="%d" class="lane">%s</text>' % (y + 5, name))

    # interruptions as epoch boundaries
    for i, t in enumerate(epochs, 1):
        rows.append('<line x1="%.1f" y1="20" x2="%.1f" y2="%d" class="epoch"/>' % (x(t), x(t), height - 30))
        rows.append('<text x="%.1f" y="16" class="epochlabel">epoch %d</text>' % (x(t) + 3, i))

    for t, et, label in user:
        cls = "interrupt" if et == "interruption" else "user"
        rows.append('<circle cx="%.1f" cy="%d" r="6" class="%s"><title>%s</title></circle>'
                    % (x(t), lane_y["user"], cls, _esc("%dms %s: %s" % (t, et, label))))
    for i, (t, kind, text, _snap) in enumerate(speech):
        y = lane_y["speech"] + (i % 3) * 16 - 16
        rows.append('<rect x="%.1f" y="%d" width="10" height="10" class="%s"><title>%s</title></rect>'
                    % (x(t) - 5, y, kind, _esc("%dms %s: %s" % (t, kind, text))))
    for i, (cid, c) in enumerate(calls.items()):
        y = lane_y["tools"] + i * 34
        t1 = c["t1"] if c["t1"] is not None else end
        cls = {"cancelled": "cut", "error": "err", "abandoned": "err"}.get(c["how"], "done")
        args = ", ".join("%s=%s" % (k, v) for k, v in c["args"].items() if not isinstance(v, list))
        rows.append('<rect x="%.1f" y="%d" width="%.1f" height="18" rx="3" class="bar %s">'
                    '<title>%s</title></rect>' % (x(c["t0"]), y, max(x(t1) - x(c["t0"]), 3), cls,
                                                  _esc("%s %s(%s) %s at %dms" % (cid, c["api"], args, c["how"], t1))))
        rows.append('<text x="%.1f" y="%d" class="barlabel">%s</text>'
                    % (x(c["t0"]) + 4, y + 13, _esc("%s(%s)" % (c["api"], args))[:70]))
        if c["how"] == "cancelled":
            rows.append('<text x="%.1f" y="%d" class="cutlabel">cancelled</text>' % (x(t1) + 4, y + 13))

    svg = ('<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="timeline">%s</svg>'
           % (width, height, "".join(rows)))

    # transcript with snapshots and changed slots
    lines, previous = [], {}
    for e in trace:
        kind, t = e.get("kind"), int(e.get("t_ms", 0))
        if kind == "event" and e.get("event_type") in ("user_speech_chunk", "interruption",
                                                      "user_audio_chunk", "video_frame"):
            p = e.get("payload") or {}
            who = "INTERRUPT" if e["event_type"] == "interruption" else "USER"
            lines.append('<tr class="%s"><td>%d</td><td>%s</td><td>%s</td><td></td></tr>'
                         % (who.lower(), t, who, _esc(p.get("text") or p.get("audio_ref") or p.get("image_ref"))))
        elif kind == "action" and e.get("action") in SPOKEN:
            snap = (e.get("state_snapshot") or {}).get("slots") or {}
            cells = []
            for k, v in snap.items():
                changed = previous.get(k) != v
                cells.append('<span class="%s">%s=%s</span>' % ("chg" if changed else "slot", _esc(k), _esc(v)))
            previous = dict(snap)
            lines.append('<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td></tr>'
                         % (t, SPOKEN[e["action"]].upper(), _esc((e.get("payload") or {}).get("text")),
                            " ".join(cells)))
        elif kind == "action" and e.get("action") == "tool_call":
            lines.append('<tr class="call"><td>%d</td><td>CALL</td><td>%s %s</td><td></td></tr>'
                         % (t, _esc(e.get("api_name")),
                            _esc({k: v for k, v in (e.get("args") or {}).items() if not isinstance(v, list)})))
        elif kind == "tool_cancelled":
            lines.append('<tr class="cutrow"><td>%d</td><td>CANCEL</td><td>%s</td><td></td></tr>'
                         % (t, _esc(e.get("call_id"))))

    parts = ["%s %.1f/%.1f" % (k, b["points"], b["weight"]) for k, b in score.get("breakdown", {}).items()]
    title = _esc(scenario.get("scenario_id", "scenario"))
    return PAGE % {"title": title, "total": score.get("total", 0.0), "parts": _esc("  |  ".join(parts)),
                   "svg": svg, "rows": "".join(lines),
                   "desc": _esc((scenario.get("metadata") or {}).get("description", ""))}


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DUET timeline</title>
<style>
:root { --bg:#fbfaf7; --fg:#1c1b19; --muted:#6f6b63; --line:#e3dfd6; --user:#3b6fb6;
        --int:#c2410c; --filler:#8a8578; --ask:#7c3aed; --final:#15803d; --done:#9cc3a3;
        --cut:#c2410c; --err:#b91c1c; --chg:#fde68a; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
        --bg:#171614; --fg:#ece9e2; --muted:#a39e93; --line:#34312c; --done:#3f6b48; --chg:#6b5a1c; } }
:root[data-theme="dark"] { --bg:#171614; --fg:#ece9e2; --muted:#a39e93; --line:#34312c; --done:#3f6b48; --chg:#6b5a1c; }
body { background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, sans-serif; margin:0; padding:24px 16px; }
main { max-width:1150px; margin:0 auto; }
h1 { font-size:20px; margin:0 0 4px; overflow-wrap:anywhere; } .sub { color:var(--muted); margin:0 0 16px; }
.wrap svg { min-width:900px; display:block; }
td:nth-child(3) { overflow-wrap:anywhere; }
.score { font-size:28px; font-weight:650; } .parts { color:var(--muted); }
.grid { stroke:var(--line); } .tick { fill:var(--muted); font-size:10px; }
.lane { fill:var(--fg); font-size:12px; font-weight:600; }
.epoch { stroke:var(--int); stroke-dasharray:4 3; } .epochlabel { fill:var(--int); font-size:10px; }
.user { fill:var(--user); } .interrupt { fill:var(--int); }
.filler { fill:var(--filler); } .ask { fill:var(--ask); } .final { fill:var(--final); }
.bar.done { fill:var(--done); } .bar.cut { fill:none; stroke:var(--cut); stroke-width:2; stroke-dasharray:5 3; }
.bar.err { fill:var(--err); } .barlabel { fill:var(--fg); font-size:11px; }
.cutlabel { fill:var(--cut); font-size:11px; font-weight:600; }
.wrap { overflow-x:auto; border:1px solid var(--line); border-radius:8px; padding:8px; }
table { border-collapse:collapse; width:100%%; margin-top:18px; font-size:14px; }
td { border-top:1px solid var(--line); padding:6px 8px; vertical-align:top; }
td:first-child { color:var(--muted); width:70px; text-align:right; } td:nth-child(2) { width:90px; font-weight:600; }
tr.interrupt td { color:var(--int); } tr.cutrow td { color:var(--cut); } tr.call td { color:var(--muted); }
.slot, .chg { display:inline-block; padding:1px 6px; margin:1px; border-radius:4px; font-size:12px; border:1px solid var(--line); }
.chg { background:var(--chg); }
.legend { color:var(--muted); font-size:13px; margin-top:8px; }
</style></head><body><main>
<h1>%(title)s</h1><p class="sub">%(desc)s</p>
<p><span class="score">%(total).1f</span> / 100 &nbsp; <span class="parts">%(parts)s</span></p>
<div class="wrap">%(svg)s</div>
<p class="legend">Dashed vertical lines are interruptions (a new epoch). Solid bars are tool calls that ran to
completion; dashed outlines are calls DUET cancelled, cut at the moment of the cancel.
Highlighted slots changed at that utterance. Hover any mark for detail.</p>
<table>%(rows)s</table>
</main></body></html>
"""


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", nargs="?", help="scenario JSON to run")
    ap.add_argument("--trace", help="a run_local.py --json dump instead of a live run")
    ap.add_argument("-o", "--out", default="")
    args = ap.parse_args(argv)

    if args.trace:
        with open(args.trace, "r", encoding="utf-8") as fh:
            dump = json.load(fh)
        entry = dump[0] if isinstance(dump, list) else dump
        trace = entry["trace"]
        scenario = {"scenario_id": entry.get("scenario_id", "trace")}
        if args.scenario:
            with open(args.scenario, "r", encoding="utf-8") as fh:
                scenario = json.load(fh)
    elif args.scenario:
        with open(args.scenario, "r", encoding="utf-8") as fh:
            scenario = json.load(fh)
        trace = run(scenario)
    else:
        ap.error("give a scenario file or --trace")
        return 2

    out = args.out or os.path.join(ROOT, "generated",
                                   "timeline_" + scenario.get("scenario_id", "run") + ".html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(build_page(scenario, trace))
    print("wrote " + out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
