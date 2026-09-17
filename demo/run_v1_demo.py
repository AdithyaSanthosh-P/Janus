"""Terminal demo of V1: a real (if scripted) spoken conversation.

Unlike demo/run_v0_demo.py, this has actual ACK/CLARIFY/FINAL text — V1
adds turn management, a task state machine, LLM workers, and FastResponder
on top of V0's kernel. The Interpreter/Planner/Composer responses below are
canned (ScriptedProvider) rather than live-model, exactly as every V1 test
uses them, but everything else — the kernel, the cancellation, the
CommitGate admission, the goal state machine — is the real thing.

Run:
    cd /home/adi/Desktop/Hackathons/Prism
    source .venv/bin/activate
    PYTHONPATH=src:. python demo/run_v1_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

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
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find me flights to Pune",
        {
            "act": "new_goal",
            "intent": "search_flights",
            "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
        },
    )
    provider.register(
        "interpret",
        "actually Mumbai",
        {
            "act": "slot_update",
            "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}],
        },
    )
    provider.register(
        "plan",
        "goal_intent: search_flights",
        {
            "steps": [
                {
                    "local_id": "s1",
                    "tool": "search_flights",
                    "kind": "read",
                    "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}},
                    "after": [],
                }
            ]
        },
    )
    provider.register(
        "compose",
        "flights",
        {"text": "I found a 6:10am flight to Mumbai on AI-505 for 4,200 rupees.", "claims": ["result:s1"]},
    )

    h = SimHarness(
        Config(),
        seed=7,
        provider=provider,
        worker_latency_us={JobKind.INTERPRET: 15_000, JobKind.PLAN: 15_000, JobKind.COMPOSE: 15_000},
        tools={"search_flights": {"latency_ms": 600, "response": {"flights": [{"flight_id": "AI-505", "time": "06:10", "price": 4200}]}}},
    )

    print(RULE)
    print("V1 demo — a real interruption, with real speech")
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
