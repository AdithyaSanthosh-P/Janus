"""VoiceBridge: pure translation between a speech transport (segment
finals with real-time timing) and Janus's own event/action queues (Day
2 WP4, docs/fdb_v3_day2_plan.md).

Never imports livekit. Both the offline audio-replay harness (WP5,
scripts/fdb_v3/run_audio_replay.py) and the live LiveKit host (WP6) feed
this exact same class -- so the timing logic that runs live (the T_eot
end-of-turn timer, barge-in detection) is proven by WP5's offline runs
too, not first exercised live.

Translation only -- this never drops, delays, or rewrites a Janus
action, and it never decides anything the kernel is supposed to decide
(no commit/settle logic lives here; that's CommitGate's G10/G11). Its
only piece of owned timing is T_eot: the transport's own "user stopped
talking" signal, translated into an ordinary `end_of_turn` event -- not
a semantic decision about task completion.

Every event pushed to `events` always carries `ts_us` (Day 1 lesson,
docs/fdb_v3_implementation_plan.md's T3 harness: `HarnessCodec.decode`
silently drops any event with no resolvable timestamp -- see
scripts/fdb_v3/run_text_replay.py's own `_now_us` for the same fix
applied there). Timestamps are monotonic microseconds since the bridge
was constructed, matching `CoupledClock`'s own model (`adapters/clock.py`:
"last event timestamp + real monotonic elapsed") so events pushed here
and Janus's own internal clock stay on one time base.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable


# Whole-segment transcripts that are Whisper's well-known output on silence
# or breath rather than speech. Found in the 29-30 Sep full voice run: "you"
# (16 times) and "Hmm." (10) each closed a turn of their own after the user
# had finished, and the agent answered them.
_FILLER_ONLY = re.compile(r"^(?:you|thank you|thanks|hmm+|mm+|um+|uh+|ah+|oh|okay|so)$")


def is_filler_segment(text: str) -> bool:
    """True when a finalized segment carries no content at all -- only a
    filler or a silence hallucination -- once case, punctuation and
    ellipses are ignored. An empty segment is not filler (see
    `on_segment_final`: it still opens a turn on purpose)."""
    words = re.sub(r"[^\w\s']", " ", text.lower()).split()
    if not words:
        return False
    return all(_FILLER_ONLY.match(w) for w in words) or _FILLER_ONLY.match(" ".join(words)) is not None


@dataclass
class VoiceBridge:
    events: "asyncio.Queue[dict | None]"
    actions: "asyncio.Queue[dict]"
    say: Callable[[str], Awaitable[None]]
    execute_tool: Callable[[str, dict], Awaitable[dict]]  # (tool_name, args) -> result dict
    t_eot_ms: int = 1000
    drop_filler_segments: bool = False  # see is_filler_segment
    clock: Callable[[], float] = field(default=time.monotonic)
    on_interruption_logged: Callable[[str], None] = field(default=lambda msg: None)
    # Day 2 WP5 (docs/fdb_v3_day2_plan.md): a caller driving a replay
    # (T4's offline audio replay, or a live host) needs to know when a
    # FINAL specifically (not just any SPEAK/CLARIFY) occurred, to stop
    # waiting -- `say()` alone loses that distinction (it's called for
    # all three). Optional so every existing caller/test is unaffected.
    on_final: Callable[[], None] = field(default=lambda: None)

    _start_wall: float = field(init=False, repr=False)
    _eot_task: "asyncio.Task | None" = field(default=None, init=False, repr=False)
    _consumer_task: "asyncio.Task | None" = field(default=None, init=False, repr=False)
    _agent_speaking: bool = field(default=False, init=False, repr=False)
    _stopped: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self._start_wall = self.clock()

    def now_us(self) -> int:
        return int(round((self.clock() - self._start_wall) * 1_000_000))

    def start(self) -> None:
        """Begins consuming Janus's own emitted actions (SPEAK/CLARIFY/
        FINAL -> say(); TOOL_CALL -> execute_tool() -> tool_result).
        Call once, after `events`/`actions` are wired to a running
        `Runtime.run_scenario` task."""
        self._consumer_task = asyncio.create_task(self._consume_actions())

    async def stop(self) -> None:
        self._stopped = True
        self._cancel_eot_timer()
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None

    async def on_manifest(self, tools: list[dict]) -> None:
        await self.events.put({"type": "manifest", "ts_us": self.now_us(), "payload": {"tools": tools}})

    async def on_video_frame(self, frame_id: str) -> None:
        """A camera frame the host has stored under `frame_id` (the same id
        its blob resolver returns bytes for). Store-only in the kernel: a
        frame is analysed only when a question about it opens."""
        await self.events.put({"type": "video_frame", "ts_us": self.now_us(), "payload": {"frame_id": frame_id}})

    async def on_speech_start(self) -> None:
        """User speech started. Cancels any pending end-of-turn timer
        (the user is still talking); if the agent was speaking, this is
        a barge-in -- FDB's own recordings never do this, but a real
        voice call will."""
        self._cancel_eot_timer()
        if self._agent_speaking:
            await self.events.put({"type": "interruption", "ts_us": self.now_us(), "payload": {}})
            self.on_interruption_logged("user started speaking while the agent was speaking")

    async def on_segment_final(self, text: str) -> None:
        """A finalized STT segment. Janus chunks are append-only -- an
        interim/partial transcript is never sent here, only a segment
        the STT layer itself considers final. Restarts the T_eot timer:
        speech has clearly continued past any earlier silence.

        Sends the text_chunk even when `text` strips to empty -- found
        live (2026-09-27): a real, VAD-detected speech segment that STT
        genuinely transcribes as nothing (heavy filler/hesitation, quiet
        audio) used to be silently dropped here, so no turn ever opened
        for it and the kernel never got a chance to close it, interpret
        it, and speak an honest "didn't catch that"
        (never_silent_unclear_enabled). `kernel/turns.py.TurnManager.
        on_chunk` accepts an empty chunk fine (it only ever contributes
        nothing to the joined prefix text); `request_interpretation`'s own
        `not turn.chunks` guard is satisfied by a turn holding one empty
        chunk, so interpretation still dispatches -- on an empty
        transcript, which a live model reads as UNCLEAR, which
        `never_silent_unclear_enabled` already turns into a spoken
        re-ask instead of silence."""
        if self.drop_filler_segments and is_filler_segment(text):
            # Not speech: no chunk, and the running end-of-turn countdown
            # (if any) is left alone rather than restarted.
            return
        await self.events.put({"type": "text_chunk", "ts_us": self.now_us(), "payload": {"text": text.strip()}})
        self._restart_eot_timer()

    async def on_speech_end(self) -> None:
        """The transport's own end-of-utterance/silence-start signal
        (e.g. VAD dropping to not-speaking). Starts (or restarts) the
        T_eot countdown if it isn't already running."""
        if self._eot_task is None or self._eot_task.done():
            self._restart_eot_timer()

    def on_agent_speaking_changed(self, speaking: bool) -> None:
        """Wired to the TTS/session's own speaking-state signal so
        `on_speech_start` can tell a barge-in from ordinary silence."""
        self._agent_speaking = speaking

    def _restart_eot_timer(self) -> None:
        self._cancel_eot_timer()
        if not self._stopped:
            self._eot_task = asyncio.create_task(self._eot_after_silence())

    def _cancel_eot_timer(self) -> None:
        if self._eot_task is not None and not self._eot_task.done():
            self._eot_task.cancel()
        self._eot_task = None

    async def _eot_after_silence(self) -> None:
        try:
            await asyncio.sleep(self.t_eot_ms / 1000)
        except asyncio.CancelledError:
            return
        await self.events.put({"type": "end_of_turn", "ts_us": self.now_us(), "payload": {}})

    async def _consume_actions(self) -> None:
        while True:
            action = await self.actions.get()
            action_type = action.get("action_type")
            body = action.get("body") or {}
            if action_type in ("speak", "clarify", "final"):
                text = body.get("text")
                if text:
                    await self.say(text)
                if action_type == "final":
                    self.on_final()
            elif action_type == "tool_call":
                await self._run_tool_call(body)
            # "cancel": FDB's mock tool calls can't be cancelled once
            # executed, and under the settle-barrier profile a call is
            # never executed before it's final (§5.1) -- a no-op here is
            # correct, not a gap.

    async def _run_tool_call(self, body: dict) -> None:
        call_id = body.get("call_id")
        tool_name = body.get("tool_name")
        args = body.get("arguments") or {}
        try:
            result = await self.execute_tool(tool_name, args)
            payload = {"call_id": call_id, "status": "ok", "result": result}
        except Exception as exc:  # noqa: BLE001 - one bad call must not kill the bridge
            payload = {"call_id": call_id, "status": "error", "result": {"error": str(exc)}}
        await self.events.put({"type": "tool_result", "ts_us": self.now_us(), "payload": payload})
