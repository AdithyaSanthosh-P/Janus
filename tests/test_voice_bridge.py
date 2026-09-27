"""Day 2 WP4 (docs/fdb_v3_day2_plan.md): VoiceBridge unit tests, driven
with a fake clock and fake say/tool sinks -- no real asyncio.sleep wall
time and no LiveKit/audio dependency, matching the plan's own stated
test list."""

from __future__ import annotations

import asyncio

import pytest

from prism_rt.adapters.voice_bridge import VoiceBridge


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _make_bridge(*, t_eot_ms: int = 20, clock: FakeClock | None = None, execute_tool=None):
    events: "asyncio.Queue[dict | None]" = asyncio.Queue()
    actions: "asyncio.Queue[dict]" = asyncio.Queue()
    said: list[str] = []

    async def say(text: str) -> None:
        said.append(text)

    async def default_execute_tool(tool_name: str, args: dict) -> dict:
        return {"status": "success", "tool": tool_name, "args": args}

    bridge = VoiceBridge(
        events=events,
        actions=actions,
        say=say,
        execute_tool=execute_tool or default_execute_tool,
        t_eot_ms=t_eot_ms,
        clock=clock or FakeClock(),
    )
    return bridge, events, actions, said


async def _drain_nowait(q: "asyncio.Queue") -> list:
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


async def test_segment_final_emits_a_text_chunk_with_ts_us():
    bridge, events, _actions, _said = _make_bridge()
    await bridge.on_segment_final("book a flight to Chicago")
    items = await _drain_nowait(events)
    assert len(items) == 1
    assert items[0]["type"] == "text_chunk"
    assert items[0]["payload"]["text"] == "book a flight to Chicago"
    assert isinstance(items[0]["ts_us"], int)


async def test_blank_final_segment_is_still_emitted_as_an_empty_chunk():
    """Found live (2026-09-27): a real, VAD-detected speech segment that
    STT genuinely transcribes as nothing (heavy filler/hesitation, quiet
    audio) must still open/extend a turn -- otherwise the kernel never
    gets a chance to close it, interpret it, and speak an honest "didn't
    catch that" instead of staying silent forever. Dropping it here used
    to make that impossible regardless of anything on the kernel side."""
    bridge, events, _actions, _said = _make_bridge()
    await bridge.on_segment_final("   ")
    items = await _drain_nowait(events)
    assert len(items) == 1
    assert items[0]["type"] == "text_chunk"
    assert items[0]["payload"]["text"] == ""


async def test_end_of_turn_fires_only_after_t_eot_of_silence():
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    await bridge.on_segment_final("hello")
    await _drain_nowait(events)  # the chunk itself
    await asyncio.sleep(0.005)  # well under t_eot
    assert await _drain_nowait(events) == []
    await asyncio.sleep(0.03)  # now past t_eot
    items = await _drain_nowait(events)
    assert len(items) == 1
    assert items[0]["type"] == "end_of_turn"


async def test_new_speech_cancels_a_pending_eot_timer():
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    await bridge.on_segment_final("hello")
    await _drain_nowait(events)
    await asyncio.sleep(0.01)  # under t_eot -- timer still pending
    await bridge.on_speech_start()  # cancels it
    await asyncio.sleep(0.03)  # would have fired by now if not cancelled
    assert await _drain_nowait(events) == []


async def test_speech_start_while_agent_speaking_is_an_interruption():
    bridge, events, _actions, _said = _make_bridge()
    bridge.on_agent_speaking_changed(True)
    await bridge.on_speech_start()
    items = await _drain_nowait(events)
    assert len(items) == 1
    assert items[0]["type"] == "interruption"


async def test_speech_start_while_agent_silent_is_not_an_interruption():
    bridge, events, _actions, _said = _make_bridge()
    bridge.on_agent_speaking_changed(False)
    await bridge.on_speech_start()
    assert await _drain_nowait(events) == []


async def test_speak_action_calls_say():
    bridge, _events, actions, said = _make_bridge()
    bridge.start()
    await actions.put({"action_type": "final", "body": {"text": "Your flight is booked."}})
    await asyncio.sleep(0.01)
    assert said == ["Your flight is booked."]
    await bridge.stop()


async def test_tool_call_action_executes_and_emits_tool_result_with_ts_us():
    async def execute_tool(tool_name, args):
        assert tool_name == "search_flights"
        assert args == {"destination": "Chicago"}
        return {"flight_id": "FL123"}

    bridge, events, actions, _said = _make_bridge(execute_tool=execute_tool)
    bridge.start()
    await actions.put({"action_type": "tool_call", "body": {"call_id": "c-1", "tool_name": "search_flights", "arguments": {"destination": "Chicago"}}})
    await asyncio.sleep(0.01)
    items = await _drain_nowait(events)
    assert len(items) == 1
    assert items[0]["type"] == "tool_result"
    assert items[0]["payload"]["call_id"] == "c-1"
    assert items[0]["payload"]["status"] == "ok"
    assert items[0]["payload"]["result"] == {"flight_id": "FL123"}
    assert isinstance(items[0]["ts_us"], int)
    await bridge.stop()


async def test_tool_call_failure_emits_error_tool_result_not_a_crash():
    async def execute_tool(tool_name, args):
        raise RuntimeError("boom")

    bridge, events, actions, _said = _make_bridge(execute_tool=execute_tool)
    bridge.start()
    await actions.put({"action_type": "tool_call", "body": {"call_id": "c-1", "tool_name": "x", "arguments": {}}})
    await asyncio.sleep(0.01)
    items = await _drain_nowait(events)
    assert len(items) == 1
    assert items[0]["payload"]["status"] == "error"
    await bridge.stop()


async def test_manifest_is_emitted_with_ts_us():
    bridge, events, _actions, _said = _make_bridge()
    await bridge.on_manifest([{"name": "search_flights"}])
    items = await _drain_nowait(events)
    assert len(items) == 1
    assert items[0]["type"] == "manifest"
    assert items[0]["payload"]["tools"] == [{"name": "search_flights"}]
    assert isinstance(items[0]["ts_us"], int)
