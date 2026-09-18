"""Reference harness for `entry.py`'s real async entry point.

Every other demo in this directory (`run_v0_demo.py`, `run_v1_demo.py`,
`run_v4_demo.py`) drives the kernel through `sim/harness.py`'s
`SimHarness` — a deterministic test double. This one is different on
purpose: it drives `prism_rt.entry.Runtime.run_scenario` directly, the
literal `guidelines/Theme_5_Guide.md` §3 contract ("two asynchronous
queues"), with `AsyncWorkerRunner` actually dispatching jobs on a real
running event loop. Before Phase A this had never been executed once —
`run_scenario`/`setup()` had zero callers anywhere in this repository.

Run:
    cd /home/adi/Desktop/Hackathons/Prism
    source .venv/bin/activate
    PYTHONPATH=src:. python demo/run_queue_harness.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_rt import entry
from prism_rt.config import Config
from prism_rt.workers.gateway import ScriptedProvider

SEARCH_FLIGHTS_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"destination": {"type": "string"}}, "required": ["destination"]},
}

RULE = "-" * 78


async def main() -> None:
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register(
        "plan",
        "search_flights",
        {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]},
    )
    provider.register("compose", "s1", {"text": "Found a flight to Pune on AI-505.", "claims": ["result:s1"]})

    runtime = entry.setup(Config(), provider=provider)

    events: "asyncio.Queue[dict | None]" = asyncio.Queue()
    actions: "asyncio.Queue[dict]" = asyncio.Queue()

    for raw in [
        {"ts_us": 0, "type": "manifest", "payload": {"tools": [SEARCH_FLIGHTS_TOOL]}},
        {"ts_us": 100_000, "type": "text_chunk", "payload": {"text": "Find flights to Pune"}},
        {"ts_us": 150_000, "type": "end_of_turn", "payload": {}},
        # A mock tool_result: a real harness would deliver this asynchronously
        # once its own search_flights call resolves; here we just hand it
        # straight to the queue a little later, in wall-clock time, so the
        # kernel actually has to wait for it (proving the loop's polling
        # doesn't require a new external event to make progress).
    ]:
        await events.put(raw)

    async def deliver_tool_result_once_call_is_seen() -> None:
        # Poll the action queue side-channel for the TOOL_CALL so we know
        # the real call_id to answer — a live harness would already have
        # this from having actually run the tool.
        while True:
            await asyncio.sleep(0.05)
            for a in collected_actions:
                if a["action_type"] == "tool_call":
                    await events.put(
                        {"ts_us": 300_000, "type": "tool_result", "payload": {"call_id": a["body"]["call_id"], "status": "ok", "result": {"flight_id": "AI-505"}}}
                    )
                    return

    collected_actions: list[dict] = []

    async def collect_actions() -> None:
        while True:
            encoded = await actions.get()
            collected_actions.append(encoded)
            body = encoded.get("body", {})
            text = body.get("text")
            if encoded["action_type"] == "tool_call":
                print(f"            [internal: calling {body['tool_name']}({body['arguments']})]")
            elif text:
                print(f"  AGENT     {text}")
            if encoded["action_type"] == "final":
                await events.put(None)  # end the scenario
                return

    print(RULE)
    print("Queue-driven demo — the real async entry point, not SimHarness")
    print(RULE)
    print('  USER      "Find flights to Pune"')

    await asyncio.gather(
        runtime.run_scenario(events, actions, meta={"seed": 7}),
        collect_actions(),
        deliver_tool_result_once_call_is_seen(),
    )

    print(RULE)
    print(f"Actions emitted: {len(collected_actions)} — {[a['action_type'] for a in collected_actions]}")


if __name__ == "__main__":
    asyncio.run(main())
