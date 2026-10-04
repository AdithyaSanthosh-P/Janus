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


# 30 Sep: Whisper's silence hallucinations ("you", "Hmm.") closed turns of
# their own after a single-request recording had finished.

@pytest.mark.parametrize("text", ["you", "You.", "Hmm.", "Um...", "Thank you.", "mmm", "you you", "Mhm.", "Mm-hmm.", "Uh-huh."])
def test_filler_only_segments_are_recognised(text):
    from prism_rt.adapters.voice_bridge import is_filler_segment

    assert is_filler_segment(text)


@pytest.mark.parametrize("text", ["", "   ", "you know", "Hmm, like I've been thinking", "Oh, and book it", "B O B", "Mhm, and book it", "Uh-huh, track order ABC"])
def test_segments_with_content_are_not_filler(text):
    from prism_rt.adapters.voice_bridge import is_filler_segment

    assert not is_filler_segment(text)


async def test_filler_segment_is_dropped_when_enabled():
    bridge, events, _actions, _said = _make_bridge()
    bridge.drop_filler_segments = True
    await bridge.on_segment_final("you")
    await bridge.on_segment_final("track order BOB")
    items = await _drain_nowait(events)
    assert [i["payload"]["text"] for i in items] == ["track order BOB"]


async def test_filler_segment_is_kept_by_default():
    bridge, events, _actions, _said = _make_bridge()
    await bridge.on_segment_final("Thank you.")
    items = await _drain_nowait(events)
    assert [i["payload"]["text"] for i in items] == ["Thank you."]


def test_bye_is_a_filler_segment():
    from prism_rt.adapters.voice_bridge import is_filler_segment

    assert is_filler_segment("Bye.")


@pytest.mark.parametrize("text", ["Nóimega déanaí.", "ہیلو", "بک می اے فلائٹ ٹو چنئی"])
def test_mostly_foreign_segments_are_noise(text):
    from prism_rt.adapters.voice_bridge import is_foreign_segment

    assert is_foreign_segment(text)


@pytest.mark.parametrize("text", ["Book it for José on Friday.", "find a café near me", "Dovekit", "", "123",
                                  "Zürich.", "São Paulo", "Müller", "jár"])
def test_english_segments_with_the_odd_accent_are_kept(text):
    from prism_rt.adapters.voice_bridge import is_foreign_segment

    assert not is_foreign_segment(text)


async def test_foreign_segment_is_dropped_when_enabled():
    bridge, events, _actions, _said = _make_bridge()
    bridge.drop_foreign_segments = True
    await bridge.on_segment_final("Nóimega déanaí.")
    await bridge.on_segment_final("Make it evening.")
    items = await _drain_nowait(events)
    assert [i["payload"]["text"] for i in items] == ["Make it evening."]


# 1 Oct audit: end-of-turn must follow the user's real silence, not the
# arrival time of a (late) transcript. Hosted speech-to-text delivers a
# segment ~1.1 s after it ends (p90 2.0 s); re-arming the end-of-turn timer
# on that arrival closed 86 turns mid-speech across 52 of 72 recordings.

async def test_late_transcript_while_user_speaks_never_arms_end_of_turn():
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=40)
    await bridge.on_speech_start()
    await bridge.on_speech_end()  # a short pause closes segment 1
    await asyncio.sleep(0.005)
    await bridge.on_speech_start()  # the user carries on
    await bridge.on_segment_final("the ID is A B")  # segment 1's transcript, late
    await asyncio.sleep(0.1)  # well past t_eot, user still speaking
    assert [e["type"] for e in await _drain_nowait(events)] == ["text_chunk"]

    await bridge.on_speech_end()
    await bridge.on_segment_final("C 1 2 3")
    await asyncio.sleep(0.1)
    items = await _drain_nowait(events)
    assert [e["type"] for e in items] == ["text_chunk", "end_of_turn"]


async def test_end_of_turn_waits_for_the_outstanding_transcript():
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    await bridge.on_speech_start()
    await bridge.on_speech_end()
    await asyncio.sleep(0.06)  # silence is long enough, but the words haven't arrived
    assert await _drain_nowait(events) == []
    await bridge.on_segment_final("track order BOB12")
    await asyncio.sleep(0.005)
    assert [e["type"] for e in await _drain_nowait(events)] == ["text_chunk", "end_of_turn"]


async def test_end_of_turn_counts_silence_from_speech_end_not_transcript_arrival():
    """Silence already past t_eot when the transcript lands: close at once,
    instead of waiting another full t_eot (the serial ~1.5 s the audit
    measured)."""
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=40)
    await bridge.on_speech_start()
    await bridge.on_speech_end()
    await asyncio.sleep(0.06)
    await bridge.on_segment_final("book it")
    await asyncio.sleep(0.01)  # far less than t_eot
    assert [e["type"] for e in await _drain_nowait(events)] == ["text_chunk", "end_of_turn"]


async def test_every_outstanding_segment_must_arrive_before_end_of_turn():
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    await bridge.on_speech_start()
    await bridge.on_speech_end()  # segment 1
    await bridge.on_speech_start()
    await bridge.on_speech_end()  # segment 2
    await bridge.on_segment_final("search flights to")
    await asyncio.sleep(0.06)
    assert [e["type"] for e in await _drain_nowait(events)] == ["text_chunk"]
    await bridge.on_segment_final("Chicago")
    await asyncio.sleep(0.005)
    assert [e["type"] for e in await _drain_nowait(events)] == ["text_chunk", "end_of_turn"]


async def test_a_transcript_that_never_arrives_is_waited_for_only_up_to_the_cap():
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    bridge.t_stt_wait_ms = 60
    await bridge.on_speech_start()
    await bridge.on_speech_end()
    await bridge.on_segment_final("cancel my order")
    await bridge.on_speech_start()
    await bridge.on_speech_end()  # this segment's transcript is lost
    await asyncio.sleep(0.03)
    assert [e["type"] for e in await _drain_nowait(events)] == ["text_chunk"]
    await asyncio.sleep(0.06)
    assert [e["type"] for e in await _drain_nowait(events)] == ["end_of_turn"]


async def test_no_end_of_turn_without_any_words_since_the_last_one():
    """A segment whose transcript is dropped (a filler or noise) leaves
    nothing to close."""
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    bridge.drop_filler_segments = True
    await bridge.on_speech_start()
    await bridge.on_speech_end()
    await bridge.on_segment_final("you")
    await asyncio.sleep(0.06)
    assert await _drain_nowait(events) == []


async def test_a_wordless_barge_in_still_ends_its_turn():
    """4 Oct voice run (housing_15): speech over the agent's ACK produced no
    transcript. The interruption had opened a turn in the kernel; without an
    end-of-turn it stayed open and held the pending search forever."""
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    bridge.drop_filler_segments = True
    bridge.on_agent_speaking_changed(True)
    await bridge.on_speech_start()
    bridge.on_agent_speaking_changed(False)
    await bridge.on_speech_end()
    await bridge.on_segment_final("Hmm.")  # dropped: no words
    await asyncio.sleep(0.06)
    assert [e["type"] for e in await _drain_nowait(events)] == ["interruption", "end_of_turn"]
    await bridge.on_speech_start()  # later noise, not over the agent: nothing to close
    await bridge.on_speech_end()
    await bridge.on_segment_final("you")
    await asyncio.sleep(0.06)
    assert await _drain_nowait(events) == []


async def test_one_end_of_turn_per_turn():
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    await bridge.on_speech_start()
    await bridge.on_speech_end()
    await bridge.on_segment_final("hello there")
    await asyncio.sleep(0.06)
    await bridge.on_speech_end()  # a stray VAD stop with nothing new said
    await asyncio.sleep(0.06)
    assert [e["type"] for e in await _drain_nowait(events)] == ["text_chunk", "end_of_turn"]


async def test_transcript_landing_just_before_its_own_speech_end_is_not_waited_for_again():
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    bridge.t_stt_wait_ms = 2000
    await bridge.on_speech_start()
    await bridge.on_segment_final("track order BOB12")  # a fast recognizer beat the VAD stop
    await bridge.on_speech_end()
    await asyncio.sleep(0.06)
    assert [e["type"] for e in await _drain_nowait(events)] == ["text_chunk", "end_of_turn"]


def _activity(items) -> list:
    return [(e["type"], e["payload"].get("active")) if e["type"] == "user_speech" else (e["type"],) for e in items]


async def test_user_activity_spans_speech_and_the_transcript_that_follows():
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    bridge.report_user_activity = True
    await bridge.on_speech_start()
    await bridge.on_speech_end()  # still active: the words are being transcribed
    await bridge.on_segment_final("track order BOB12")
    await asyncio.sleep(0.05)
    assert _activity(await _drain_nowait(events)) == [
        ("user_speech", True), ("text_chunk",), ("user_speech", False), ("end_of_turn",)]


async def test_user_activity_stays_on_across_a_pause_with_a_transcript_in_flight():
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    bridge.report_user_activity = True
    await bridge.on_speech_start()
    await bridge.on_speech_end()
    await bridge.on_speech_start()
    await bridge.on_segment_final("the ID is A B")
    await bridge.on_speech_end()
    await bridge.on_segment_final("C 1 2 3")
    await asyncio.sleep(0.05)
    assert _activity(await _drain_nowait(events)) == [
        ("user_speech", True), ("text_chunk",), ("text_chunk",), ("user_speech", False), ("end_of_turn",)]


async def test_user_activity_is_released_when_a_transcript_is_lost():
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    bridge.report_user_activity = True
    bridge.t_stt_wait_ms = 40
    await bridge.on_speech_start()
    await bridge.on_speech_end()  # its transcript never arrives, and no words came before
    await asyncio.sleep(0.08)
    assert _activity(await _drain_nowait(events)) == [("user_speech", True), ("user_speech", False)]


async def test_user_activity_is_not_reported_unless_enabled():
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    await bridge.on_speech_start()
    await bridge.on_speech_end()
    await bridge.on_segment_final("hello")
    await asyncio.sleep(0.05)
    assert [e["type"] for e in await _drain_nowait(events)] == ["text_chunk", "end_of_turn"]


async def test_events_reach_the_kernel_with_non_decreasing_timestamps():
    """2 Oct review (F1) worried an end-of-turn could carry an earlier stamp
    than a transcript queued before it and be reordered ahead of it. Every
    event is stamped from one monotonic clock when it is queued, and the
    driver steps once per event in arrival order; this pins the first half."""
    import time

    bridge, events, _actions, _said = _make_bridge(t_eot_ms=10, clock=time.monotonic)
    bridge.report_user_activity = True
    bridge.t_stt_wait_ms = 30
    for text in ("search flights to", "Chicago", "on Friday"):
        await bridge.on_speech_start()
        await asyncio.sleep(0.002)
        await bridge.on_speech_end()
        await asyncio.sleep(0.004)
        await bridge.on_segment_final(text)
    await bridge.on_speech_start()
    await bridge.on_speech_end()  # a transcript that never comes
    await asyncio.sleep(0.06)
    stamps = [e["ts_us"] for e in await _drain_nowait(events)]
    assert len(stamps) >= 5 and stamps == sorted(stamps) and len(set(stamps)) > 1


# 2 Oct review (F2, verified against livekit-agents 1.8.3): LiveKit's own
# barge-in stops only the utterance playing; speech already queued with
# session.say still plays afterwards. The bridge stops all of it once the
# user's words confirm the barge-in.

async def test_words_during_agent_speech_stop_all_queued_speech():
    stops = []
    bridge, events, _actions, _said = _make_bridge()
    bridge.stop_speaking = lambda: stops.append(True)
    bridge.on_agent_speaking_changed(True)
    await bridge.on_speech_start()
    await bridge.on_segment_final("no wait, make it Friday")
    assert stops == [True]


async def test_noise_or_filler_during_agent_speech_does_not_stop_it():
    stops = []
    bridge, _events, _actions, _said = _make_bridge()
    bridge.stop_speaking = lambda: stops.append(True)
    bridge.drop_filler_segments = True
    bridge.on_agent_speaking_changed(True)
    await bridge.on_speech_start()  # VAD alone is not enough
    await bridge.on_segment_final("Hmm.")
    await bridge.on_segment_final("")
    assert stops == []


async def test_words_while_the_agent_is_silent_stop_nothing():
    stops = []
    bridge, _events, _actions, _said = _make_bridge()
    bridge.stop_speaking = lambda: stops.append(True)
    await bridge.on_segment_final("book it for Friday")
    assert stops == []


async def test_a_running_transcription_is_waited_for_past_the_fixed_wait():
    """3 Oct voice run (19 of 100 recordings): hosted transcription of a
    long segment took more than t_stt_wait_ms, the turn closed without its
    last sentence, and the request was split in two. A transcription the
    recognizer reports as running holds the turn open (up to its cap)."""
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    bridge.t_stt_wait_ms = 60
    await bridge.on_speech_start()
    await bridge.on_speech_end()
    await bridge.on_segment_final("update my passport to P1122")
    await bridge.on_speech_start()
    await bridge.on_recognition(True)  # the long last segment is being transcribed
    await bridge.on_speech_end()
    await asyncio.sleep(0.12)  # well past t_stt_wait_ms
    assert [e["type"] for e in await _drain_nowait(events)] == ["text_chunk"]
    await bridge.on_recognition(False)
    await bridge.on_segment_final("and my licence to DL9090")
    await asyncio.sleep(0.005)
    assert [e["type"] for e in await _drain_nowait(events)] == ["text_chunk", "end_of_turn"]


async def test_a_running_transcription_is_waited_for_only_up_to_its_cap():
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    bridge.t_stt_running_cap_ms = 80
    await bridge.on_speech_start()
    await bridge.on_speech_end()
    await bridge.on_segment_final("cancel my order")
    await bridge.on_recognition(True)  # never finishes
    await asyncio.sleep(0.04)
    assert [e["type"] for e in await _drain_nowait(events)] == ["text_chunk"]
    await asyncio.sleep(0.07)
    assert [e["type"] for e in await _drain_nowait(events)] == ["end_of_turn"]


async def test_a_finished_transcription_s_text_gets_a_grace_before_the_turn_closes():
    """The transcript event follows the recognizer's finish; with the VAD
    count already at zero (the session and the transcriber split speech
    differently), the turn must not close in between."""
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    bridge.t_text_grace_ms = 60
    await bridge.on_speech_start()
    await bridge.on_segment_final("search flights to")  # counted before its own VAD stop
    await bridge.on_recognition(True)
    await bridge.on_speech_end()
    await asyncio.sleep(0.03)  # silence past t_eot, transcription still running
    assert [e["type"] for e in await _drain_nowait(events)] == ["text_chunk"]
    await bridge.on_recognition(False)
    await asyncio.sleep(0.02)  # inside the grace: the text is still on its way
    assert await _drain_nowait(events) == []
    await bridge.on_segment_final("Chicago")
    await asyncio.sleep(0.005)
    assert [e["type"] for e in await _drain_nowait(events)] == ["text_chunk", "end_of_turn"]


async def test_running_transcription_keeps_the_user_active_for_the_kernel():
    bridge, events, _actions, _said = _make_bridge(t_eot_ms=20)
    bridge.report_user_activity = True
    await bridge.on_speech_start()
    await bridge.on_segment_final("book a flight")
    await bridge.on_recognition(True)
    await bridge.on_speech_end()
    kinds = [(e["type"], e["payload"].get("active")) for e in await _drain_nowait(events)]
    assert ("user_speech", False) not in kinds
    await bridge.on_recognition(False)
    await bridge.on_segment_final("to Denver")
    await asyncio.sleep(0.03)
    kinds = [(e["type"], e["payload"].get("active")) for e in await _drain_nowait(events)]
    assert ("user_speech", False) in kinds and ("end_of_turn", None) in kinds
