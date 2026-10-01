"""1 Oct audit, end to end through the production wiring: VoiceBridge ->
the real async driver (`entry.Runtime.run_scenario`, CoupledClock, real
asyncio time) -> the kernel, with the FDB profile.

The pattern from the archived voice runs: the user pauses briefly, carries
on, and the first segment's transcript lands while they are still speaking.
Before the fix, that late transcript armed end-of-turn mid-speech; the first
half was interpreted on its own and its search went out with the assumed
budget, then again with the real one. Timings are scaled down ~10x."""

from __future__ import annotations

import asyncio

from prism_rt.adapters.voice_bridge import VoiceBridge
from prism_rt.entry import setup
from prism_rt.profiles import fdb_v3_config
from prism_rt.workers.gateway import ScriptedProvider

APT = {"name": "search_apartments", "parameters": {"type": "object", "properties": {
    "city": {"type": "string"}, "bedrooms": {"type": "integer"}, "max_price": {"type": "number"}},
    "required": ["city", "bedrooms", "max_price"]}}


async def _run(report_user_activity: bool) -> list[dict]:
    provider = ScriptedProvider()
    # First match wins: the whole sentence carries the real budget.
    provider.register("interpret", "two thousand", {
        "act": "new_goal", "intent": "search_apartments", "slot_deltas": [],
        "actions": [{"tool": "search_apartments", "args": {"city": "Chicago", "bedrooms": 2, "max_price": 2000}}]})
    provider.register("interpret", "Chicago", {
        "act": "new_goal", "intent": "search_apartments", "slot_deltas": [],
        "actions": [{"tool": "search_apartments", "args": {"city": "Chicago", "bedrooms": 2, "max_price": 10000},
                     "assumed": ["max_price"]}]})
    provider.register("compose", "", {"text": "Found one.", "claims": []})

    events, actions = asyncio.Queue(), asyncio.Queue()
    calls: list[dict] = []

    async def say(_text: str) -> None:
        return None

    async def tool(_name: str, args: dict) -> dict:
        calls.append(dict(args))
        return {"results": [{"id": "APT1"}]}

    bridge = VoiceBridge(events=events, actions=actions, say=say, execute_tool=tool,
                         t_eot_ms=150, report_user_activity=report_user_activity)
    runtime = setup(config=fdb_v3_config(settle_ms=150, settle_ms_incomplete=300, watchdog_timeout_ms=0),
                    provider=provider)
    task = asyncio.create_task(runtime.run_scenario(events, actions))
    bridge.start()
    await bridge.on_manifest([APT])

    await bridge.on_speech_start()
    await asyncio.sleep(0.2)
    await bridge.on_speech_end()  # a short pause closes segment 1
    await asyncio.sleep(0.15)
    await bridge.on_speech_start()  # the user carries on
    await asyncio.sleep(0.15)
    await bridge.on_segment_final("I'm interested in a two-bedroom in Chicago")  # segment 1, late
    await asyncio.sleep(0.7)
    await bridge.on_speech_end()
    await asyncio.sleep(0.3)
    await bridge.on_segment_final("and keep the max price around two thousand a month")
    await asyncio.sleep(1.5)

    await bridge.stop()
    await events.put(None)
    await asyncio.wait_for(task, 5)
    return calls


async def test_a_late_transcript_mid_speech_yields_one_call_with_the_whole_sentence():
    assert await _run(report_user_activity=True) == [{"city": "Chicago", "bedrooms": 2, "max_price": 2000}]


async def test_the_turn_is_whole_even_without_the_activity_signal():
    """The bridge's end-of-turn fix alone keeps this a single turn."""
    assert await _run(report_user_activity=False) == [{"city": "Chicago", "bedrooms": 2, "max_price": 2000}]
