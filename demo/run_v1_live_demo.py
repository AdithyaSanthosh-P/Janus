"""Terminal demo of V1 against a *live* model: same flight-booking-with-
correction scenario as demo/run_v1_demo.py, but answered by the real Gemini
API instead of ScriptedProvider. Wording will vary run to run — this is a
sanity check that the kernel's turn management, cancellation, and
CommitGate admission all still work end-to-end against a live provider,
not a scripted transcript to match exactly.

Requires a GEMINI_API_KEY, either already exported or in a `.env` file at
the repo root (KEY=VALUE, loaded below without any extra dependency).

Run:
    cd /home/adi/Desktop/Hackathons/Prism
    source .venv/bin/activate
    PYTHONPATH=src:. python demo/run_v1_live_demo.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))


def _load_dotenv(path: Path) -> None:
    """Minimal `.env` loader: KEY=VALUE lines, skips blanks/comments, never
    overrides a variable already set in the real environment."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()


_load_dotenv(REPO_ROOT / ".env")

from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import GeminiProvider

SEARCH_FLIGHTS_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"destination": {"type": "string"}},
        "required": ["destination"],
    },
}

RULE = "-" * 78


def say(who: str, text: str) -> None:
    print(f"  {who:9} {text}")


def flush_speech(harness: SimHarness, until_us: int, *, step_us: int = 10_000) -> None:
    t = harness.clock.now_us()
    while t < until_us:
        t += step_us
        report = harness.advance(t)
        for er in report.emit_report.emitted:
            action = er.action
            if action.action_type in (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL):
                say("AGENT", action.body.text)
            elif action.action_type == ActionType.CANCEL:
                print(f"            [internal: cancelling {action.body.target_call_id} — {action.body.reason}]")
            elif action.action_type == ActionType.TOOL_CALL:
                print(f"            [internal: calling {action.body.tool_name}({action.body.arguments})]")


def main() -> None:
    provider = GeminiProvider()

    h = SimHarness(
        Config(),
        seed=7,
        provider=provider,
        worker_latency_us={JobKind.INTERPRET: 15_000, JobKind.PLAN: 15_000, JobKind.COMPOSE: 15_000},
        tools={"search_flights": {"latency_ms": 600, "response": {"flights": [{"flight_id": "AI-505", "time": "06:10", "price": 4200}]}}},
    )

    print(RULE)
    print("V1 live demo — real interruption, real model (Gemini)")
    print(RULE)

    h.send(0, [{"type": "manifest", "payload": {"tools": [SEARCH_FLIGHTS_TOOL]}}])

    say("USER", '"Find me flights to Pune"')
    h.send(100_000, [{"type": "text_chunk", "payload": {"text": "Find me flights to Pune"}}])
    h.send(150_000, [{"type": "end_of_turn", "payload": {}}])
    flush_speech(h, 250_000)

    say("USER", '"actually Mumbai" (interrupting)')
    h.send(h.clock.now_us() + 20_000, [{"type": "interruption", "payload": {}}])
    h.send(h.clock.now_us() + 5_000, [{"type": "text_chunk", "payload": {"text": "actually Mumbai"}}])
    h.send(h.clock.now_us() + 5_000, [{"type": "end_of_turn", "payload": {}}])
    flush_speech(h, h.clock.now_us() + 2_000_000)

    print(RULE)
    goal = h.store.goals.all()[0]
    print(f"Goal finished: status={goal.status.value}")
    print("Final committed slots:", {k: v for k, v in h.store.facts.snapshot_committed().items() if k.startswith("slot.")})


if __name__ == "__main__":
    main()
