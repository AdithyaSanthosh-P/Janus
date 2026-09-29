"""Kernel X-ray: one HTML timeline per conversation, rendered from the logs.

Inputs are the two JSONL streams a session already writes:
  - the decision log (`DecisionLogger`): one record per kernel step -- fact
    changes and the rule behind each, invalidations, emissions, rejections;
  - a wire trace (optional): what crossed the boundary -- user text chunks,
    end-of-turn, tool results going in; speech, tool calls, cancels coming out.

Output is a single self-contained HTML file (no scripts, no network): four
swimlanes -- USER, KERNEL, AGENT, TOOLS -- on one time axis. Work the kernel
invalidated is drawn struck through; a hold (CommitGate settle, floor open)
is drawn once with its duration, not once per step. Purely a reader of logs:
it never touches a decision (`observability` may only import `model`).
"""

from __future__ import annotations

import asyncio
import html
import json
import time
from pathlib import Path
from typing import Any, Iterable

LANES = ("USER", "KERNEL", "AGENT", "TOOLS")

# Fact-change rules that say what the kernel *understood* -- shown as one row.
_UNDERSTOOD = {
    "interpret_apply.commit_intent": "user committed to the action",
    "interpret_apply.deny": "user declined",
    "interpret_apply.requested_actions": "actions understood",
    "speculation.applied": "speculative interpretation applied (no extra wait)",
    "speculation.inert_tail_promoted": "speculative interpretation kept across a trailing filler word",
    "interpret_apply.merge": "split request merged into one interpretation",
}

_REASONS = {
    "G3": "floor still open: the user may still be speaking",
    "G4": "no explicit go-ahead from the user",
    "G5": "duplicate of an action already done",
    "G6": "this step already took effect once",
    "G10": "settling: waiting out the user's possible correction",
    "G11": "settling: the turn sounded unfinished",
}


class TracedQueue(asyncio.Queue):
    """An `asyncio.Queue` that appends every item put on it to a wire-trace
    JSONL, stamped with microseconds since the trace began. Wrap the two queues
    between the host and the runtime and the X-ray gets the user text, tool
    results, speech and tool calls that the decision log does not carry."""

    def __init__(self, path: str | Path, direction: str, start: float | None = None) -> None:
        super().__init__()
        self._fh = open(path, "a", encoding="utf-8")
        self._dir = direction
        self._start = start if start is not None else time.monotonic()

    def _write(self, item: Any) -> None:
        if not isinstance(item, dict):
            return
        t = int((time.monotonic() - self._start) * 1e6)
        if self._dir == "in":
            rec = {"dir": "in", "t_us": t, "type": item.get("type"), "payload": item.get("payload")}
        else:
            rec = {"dir": "out", "t_us": t, "action_type": item.get("action_type"), "body": item.get("body")}
        self._fh.write(json.dumps(rec, default=str) + "\n")
        self._fh.flush()

    def put_nowait(self, item: Any) -> None:  # type: ignore[override]
        # asyncio.Queue.put() ends in put_nowait(), and the kernel's output
        # writer (entry.py _QueueWriter) calls put_nowait() directly --
        # overriding only put() missed every outgoing action (found by review).
        self._write(item)
        super().put_nowait(item)


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _short(value: Any, limit: int = 90) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _args(args: dict) -> str:
    return ", ".join(f"{k}={_short(v, 40)}" for k, v in (args or {}).items())


def build_rows(decisions: Iterable[dict], wire: Iterable[dict] = ()) -> list[dict]:
    rows: list[dict] = []
    calls: dict[str, dict] = {}  # call_id -> its TOOLS row, to strike it through later

    for ev in wire:
        t = int(ev.get("t_us", 0))
        if ev.get("dir") == "in":
            kind, body = ev.get("type"), ev.get("payload") or {}
            if kind == "text_chunk":
                rows.append({"t": t, "lane": "USER", "cls": "user", "text": f"“{body.get('text', '')}”"})
            elif kind == "end_of_turn":
                rows.append({"t": t, "lane": "USER", "cls": "quiet", "text": "end of turn"})
            elif kind == "interruption":
                rows.append({"t": t, "lane": "USER", "cls": "user", "text": "interrupts the agent"})
            elif kind == "video_frame":
                rows.append({"t": t, "lane": "USER", "cls": "quiet", "text": f"camera frame {body.get('frame_id', '')}"})
            elif kind == "tool_result":
                status = body.get("status", "ok")
                text = f"result {status}: {_short(body.get('result'))}"
                rows.append({"t": t, "lane": "TOOLS", "cls": "result" if status == "ok" else "bad", "text": text})
        elif ev.get("dir") == "out":
            action, body = ev.get("action_type"), ev.get("body") or {}
            if action in ("speak", "clarify", "final"):
                rows.append({"t": t, "lane": "AGENT", "cls": "final" if action == "final" else "agent", "text": body.get("text", ""), "tag": action})
            elif action == "tool_call":
                row = {"t": t, "lane": "TOOLS", "cls": "call", "text": f"{body.get('tool_name')}({_args(body.get('arguments') or {})})", "tag": "call"}
                rows.append(row)
                if body.get("call_id"):
                    calls[body["call_id"]] = row
            elif action == "cancel":
                rows.append({"t": t, "lane": "AGENT", "cls": "cancel", "text": f"cancels {body.get('call_id') or 'in-flight work'}", "tag": "cancel"})

    hold: dict | None = None
    for rec in decisions:
        t = int(rec.get("now_us", 0))
        for change in rec.get("fact_changes", []):
            note = _UNDERSTOOD.get(change.get("rule"))
            if note and change.get("new_status") != "retracted":
                rows.append({"t": t, "lane": "KERNEL", "cls": "kernel", "text": note})
        inv = rec.get("invalidations") or {}
        dead = list(inv.get("invalidated_call_ids", [])) + list(inv.get("discarded_call_ids", []))
        if dead:
            for cid in dead:
                if cid in calls:
                    calls[cid]["struck"] = True
                    calls[cid]["tag"] = "invalidated"
            what = ", ".join(k for k in inv.get("changed_keys", []) if k.startswith(("slot.", "goal.")))[:120]
            rows.append({"t": t, "lane": "KERNEL", "cls": "invalidate",
                         "text": f"a fact changed{(' (' + what + ')') if what else ''}: {len(dead)} piece(s) of work invalidated, same step"})
        rejected = rec.get("rejected") or []
        if rejected:
            reason = rejected[0].get("reason", "")
            code = reason.split(":")[0].split("_")[0].upper()
            label = next((v for k, v in _REASONS.items() if k in reason.upper()), reason)
            if hold and hold["reason"] == reason:
                hold["until"] = t
                hold["count"] += 1
            else:
                hold = {"t": t, "until": t, "reason": reason, "count": 1, "label": label}
                rows.append({"t": t, "lane": "KERNEL", "cls": "hold", "hold": hold})
        else:
            hold = None

    for row in rows:
        h = row.get("hold")
        if h:
            span = (h["until"] - h["t"]) / 1e6
            row["text"] = f"held: {h['label']}" + (f" ({span:.1f}s)" if span >= 0.05 else "")
    rows.sort(key=lambda r: (r["t"], LANES.index(r["lane"])))
    return rows


_CSS = """
:root{--bg:#fff;--fg:#1b1f24;--muted:#6b7280;--line:#e5e7eb;--user:#dbeafe;--kernel:#fef3c7;--agent:#dcfce7;--tool:#ede9fe;--bad:#fee2e2;--badfg:#991b1b}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--fg:#e6e8eb;--muted:#9aa3ad;--line:#2a3038;--user:#1e3a5f;--kernel:#4a3b0f;--agent:#14401f;--tool:#33285a;--bad:#4c1d1d;--badfg:#fca5a5}}
*{box-sizing:border-box}body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto}h1{font-size:20px;margin:0 0 4px}p.sub{color:var(--muted);margin:0 0 16px}
.grid{display:grid;grid-template-columns:64px repeat(4,1fr);gap:6px 8px;align-items:start}
.head{font-weight:600;font-size:12px;letter-spacing:.06em;color:var(--muted);padding-bottom:6px;border-bottom:2px solid var(--line)}
.t{font-variant-numeric:tabular-nums;color:var(--muted);font-size:12px;padding-top:6px;text-align:right}
.card{grid-row:auto;padding:6px 9px;border-radius:8px;font-size:13.5px;overflow-wrap:anywhere}
.user{background:var(--user)}.quiet{background:none;border:1px dashed var(--line);color:var(--muted)}
.kernel,.hold{background:var(--kernel)}.invalidate{background:var(--kernel);border-left:4px solid #d97706}
.agent,.final{background:var(--agent)}.final{font-weight:600}.cancel{background:var(--agent);border-left:4px solid #d97706}
.call,.result{background:var(--tool)}.bad{background:var(--bad);color:var(--badfg)}
.struck{text-decoration:line-through;opacity:.75}.tag{display:inline-block;margin-left:6px;font-size:10.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);text-decoration:none}
.struck .tag{color:var(--badfg)}
.legend{font-size:12.5px;color:var(--muted);margin-top:16px}
@media (max-width:760px){.grid{grid-template-columns:48px repeat(4,minmax(120px,1fr));overflow-x:auto}}
"""


def render(rows: list[dict], *, title: str = "Kernel X-ray", subtitle: str = "") -> str:
    t0 = rows[0]["t"] if rows else 0
    body = ['<div class="grid"><div class="head"></div>' + "".join(f'<div class="head">{l}</div>' for l in LANES)]
    for row in rows:
        col = LANES.index(row["lane"]) + 2
        cls = row["cls"] + (" struck" if row.get("struck") else "")
        tag = f'<span class="tag">{html.escape(row["tag"])}</span>' if row.get("tag") else ""
        body.append(f'<div class="t" style="grid-column:1">{(row["t"] - t0) / 1e6:.2f}s</div>')
        body.append(f'<div class="card {cls}" style="grid-column:{col}">{html.escape(row["text"])}{tag}</div>')
    body.append("</div>")
    dur = ((rows[-1]["t"] - t0) / 1e6) if rows else 0
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body><main>"
        f"<h1>{html.escape(title)}</h1><p class=\"sub\">{html.escape(subtitle)} {dur:.1f}s, {len(rows)} events.</p>"
        + "".join(body)
        + '<p class="legend">Struck-through work was invalidated by a later correction in the same kernel step: it was cancelled and its result discarded. '
        "A KERNEL &ldquo;held&rdquo; row is the CommitGate refusing to act yet.</p></main></body></html>"
    )


def render_files(decision_log: str | Path, wire_log: str | Path | None, out: str | Path, *, title: str = "Kernel X-ray") -> None:
    decisions = load_jsonl(decision_log)
    wire = load_jsonl(wire_log) if wire_log and Path(wire_log).exists() else []
    Path(out).write_text(render(build_rows(decisions, wire), title=title, subtitle=Path(decision_log).stem), encoding="utf-8")


if __name__ == "__main__":  # python -m prism_rt.observability.xray decisions.jsonl [wire.jsonl] out.html
    import sys

    args = sys.argv[1:]
    if len(args) == 2:
        render_files(args[0], None, args[1])
    elif len(args) == 3:
        render_files(args[0], args[1], args[2])
    else:
        raise SystemExit("usage: python -m prism_rt.observability.xray DECISIONS.jsonl [WIRE.jsonl] OUT.html")
