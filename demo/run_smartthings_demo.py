"""SmartThings extension demo: smart-home control with commit-safety, text only.

Runs the real Janus kernel with the live Gemini API over the SmartThings tool
pack (`prism_rt.smartthings`), against the mock backend or the real SmartThings
cloud with virtual devices. The voice path is the LiveKit worker
(`JANUS_MODE=demo`, same toolset).

Story (four user turns):
  1. "Lock the front door and set the AC to 24 -- no wait, make it 26."
        -> the lock command and ONE setpoint command, 26; 24 is never sent.
  2. "Lock the front door again."   -> already locked: nothing is sent.
  3. "Unlock the front door."       -> a security action: the agent asks first.
  4. "Yes, go ahead."               -> one unlock command.

Backend: JANUS_ST_MODE=mock (default) or real (needs ST_PAT in the environment
or .env). `--stuck lock` makes the mock accept lock/unlock commands without
the state ever changing, to show the "sent, not confirmed" outcome.

Run:  PYTHONPATH=src:. python demo/run_smartthings_demo.py [--stuck lock] [--trace] [--turn N]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(REPO_ROOT / ".env")

from prism_rt.model.types import ActionType, JobKind  # noqa: E402
from prism_rt.observability.effects import effect_summary, format_effect_summary  # noqa: E402
from prism_rt.profiles import demo_config  # noqa: E402
from prism_rt.sim.harness import SimHarness  # noqa: E402
from prism_rt.smartthings.factory import backend_mode, make_backend  # noqa: E402
from prism_rt.smartthings.mock import MockSmartThings  # noqa: E402
from prism_rt.smartthings.tools import MANIFEST, SmartThingsToolset  # noqa: E402
from prism_rt.workers.gateway import GeminiProvider  # noqa: E402

RULE = "-" * 78
TURNS = [
    [("lock the front door and set the AC to twenty four", 0, False), ("no wait, make it twenty six", 1_500_000, True)],
    [("lock the front door again", 0, True)],
    [("unlock the front door", 0, True)],
    [("yes, go ahead", 0, True)],
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stuck", help="mock only: capability whose commands are accepted but never change state, e.g. lock")
    ap.add_argument("--trace", action="store_true", help="print every model call and its JSON answer")
    ap.add_argument("--turn", type=int, default=0, help="run only this turn; 0 = all")
    args = ap.parse_args()

    backend = make_backend()
    if args.stuck:
        if not isinstance(backend, MockSmartThings):
            raise SystemExit("--stuck needs JANUS_ST_MODE=mock")
        backend.stuck.add(args.stuck)
    toolset = SmartThingsToolset(backend)
    live_exec = toolset.make_executor()

    def run_tool(name: str, arguments: dict) -> dict:
        return asyncio.run(live_exec(name, arguments))

    provider = GeminiProvider()
    if args.trace:
        import json

        inner = provider.complete_json

        def traced(kind, prompt, schema, *, media=None):
            out = inner(kind, prompt, schema, media=media)
            print(f"            <{kind}> -> {json.dumps(out)[:700]}")
            return out

        provider.complete_json = traced  # type: ignore[method-assign]

    h = SimHarness(
        demo_config(),
        seed=7,
        provider=provider,
        worker_latency_us={k: 15_000 for k in JobKind},
        tools={t["name"]: {"latency_ms": 150, "handler": (lambda a, n=t["name"]: run_tool(n, a))} for t in MANIFEST},
    )
    print(RULE)
    print(f"SmartThings demo, backend: {backend_mode()}")
    print(RULE)
    h.send(0, [{"type": "manifest", "payload": {"tools": toolset.manifest}}])

    def show(report) -> bool:
        spoke = False
        for er in report.emit_report.emitted:
            a = er.action
            if a.action_type in (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL):
                print(f"  AGENT     {a.body.text}")
                spoke = True
            elif a.action_type == ActionType.TOOL_CALL:
                print(f"            [tool {a.body.tool_name}({dict(a.body.arguments)})]")
                spoke = True
            elif a.action_type == ActionType.CANCEL:
                print("            [cancelled superseded work]")
        return spoke

    def send(ts: int, events: list):
        return h.send(ts, events) if events else h.advance(ts)

    t = h.clock.now_us() + 200_000
    for n, chunks in enumerate(TURNS, start=1):
        if args.turn and n != args.turn:
            continue
        print(f"\nTurn {n}")
        for text, gap, end in chunks:
            t += gap
            print(f"  USER      {text}")
            show(send(t, [{"type": "text_chunk", "payload": {"text": text}}]))
            if end:
                t += 1_100_000
                show(send(t, [{"type": "end_of_turn", "payload": {}}]))
        quiet_since, limit = t, t + 60_000_000
        while t < limit:
            t += 20_000
            if show(send(t, [])):
                quiet_since = t
            if t - quiet_since > 12_000_000 and not h.store.call_ledger.non_terminal():
                break
        t += 500_000

    print(RULE)
    if isinstance(backend, MockSmartThings):
        print(f"commands sent to the (mock) devices: {backend.commands}")
    print(format_effect_summary(effect_summary(h.store)))
    print(RULE)


if __name__ == "__main__":
    main()
