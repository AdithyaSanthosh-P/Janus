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
_FILLER_ONLY = re.compile(r"^(?:you|thank you|thanks|bye|hmm+|mm+|um+|uh+|ah+|oh|okay|so)$")


def is_filler_segment(text: str) -> bool:
    """True when a finalized segment carries no content at all -- only a
    filler or a silence hallucination -- once case, punctuation and
    ellipses are ignored. An empty segment is not filler (see
    `on_segment_final`: it still opens a turn on purpose)."""
    words = re.sub(r"[^\w\s']", " ", text.lower()).split()
    if not words:
        return False
    return all(_FILLER_ONLY.match(w) for w in words) or _FILLER_ONLY.match(" ".join(words)) is not None


def is_foreign_segment(text: str) -> bool:
    """True when a segment is mostly not English: half its letters in another
    script (Urdu), or several accented letters making up 15 % or more of it
    ("Nóimega déanaí.").
    Found live, 30 Sep: with no headphones the agent's own voice came back
    through the microphone and was transcribed as foreign words. Speech-to-
    text is told to write English, so such text is noise; a name with one
    accent ("book it for José") stays well under the threshold."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    other_script = sum(1 for c in letters if ord(c) > 0x24F)  # beyond Latin Extended-B
    accented = sum(1 for c in letters if 127 < ord(c) <= 0x24F)
    if other_script / len(letters) >= 0.5:
        return True
    # Accented Latin letters: several of them, not one ("Zürich", "José" stay).
    return accented >= 2 and accented / len(letters) >= 0.15


@dataclass
class VoiceBridge:
    events: "asyncio.Queue[dict | None]"
    actions: "asyncio.Queue[dict]"
    say: Callable[[str], Awaitable[None]]
    execute_tool: Callable[[str, dict], Awaitable[dict]]  # (tool_name, args) -> result dict
    t_eot_ms: int = 1000
    drop_filler_segments: bool = False  # see is_filler_segment
    drop_foreign_segments: bool = False  # see is_foreign_segment
    clock: Callable[[], float] = field(default=time.monotonic)
    on_interruption_logged: Callable[[str], None] = field(default=lambda msg: None)
    # Day 2 WP5 (docs/fdb_v3_day2_plan.md): a caller driving a replay
    # (T4's offline audio replay, or a live host) needs to know when a
    # FINAL specifically (not just any SPEAK/CLARIFY) occurred, to stop
    # waiting -- `say()` alone loses that distinction (it's called for
    # all three). Optional so every existing caller/test is unaffected.
    on_final: Callable[[], None] = field(default=lambda: None)
    # How long after a speech segment ends its transcript is waited for
    # before the turn closes without it (a transcription that failed or
    # timed out must not hold the turn open forever).
    t_stt_wait_ms: int = 4000
    # Send `user_speech` events (active while the user speaks or their words
    # are still being transcribed) so the kernel can hold calls and speech
    # before any text exists (Config.vad_floor_enabled decides whether it
    # does). Off by default: hosts without VAD have nothing to report.
    report_user_activity: bool = False
    # Stops the agent's current and queued speech (the host's
    # `session.interrupt()`). Called when the user's words arrive while the
    # agent is speaking -- found by the 2 Oct review: LiveKit's own barge-in
    # stops only the utterance playing, so queued speech (an ACK, then the
    # answer to the request just corrected) still played over the user.
    stop_speaking: Callable[[], None] = field(default=lambda: None)

    _start_wall: float = field(init=False, repr=False)
    _eot_task: "asyncio.Task | None" = field(default=None, init=False, repr=False)
    _consumer_task: "asyncio.Task | None" = field(default=None, init=False, repr=False)
    _agent_speaking: bool = field(default=False, init=False, repr=False)
    _stopped: bool = field(default=False, init=False, repr=False)
    # End-of-turn state (see `_schedule_eot`). Times are event-loop seconds.
    _user_speaking: bool = field(default=False, init=False, repr=False)
    _vad_seen: bool = field(default=False, init=False, repr=False)
    _pending_segments: int = field(default=0, init=False, repr=False)
    _last_speech_end: "float | None" = field(default=None, init=False, repr=False)
    _last_final_at: "float | None" = field(default=None, init=False, repr=False)
    _words_since_eot: bool = field(default=False, init=False, repr=False)
    _activity_reported: bool = field(default=False, init=False, repr=False)

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
        """User speech started (VAD). Cancels any pending end-of-turn
        countdown (the user is still talking); if the agent was speaking,
        this is a barge-in -- FDB's own recordings never do this, but a
        real voice call will."""
        self._user_speaking = True
        self._vad_seen = True
        self._cancel_eot_timer()
        await self._report_activity()
        if self._agent_speaking:
            await self.events.put({"type": "interruption", "ts_us": self.now_us(), "payload": {}})
            self.on_interruption_logged("user started speaking while the agent was speaking")

    async def on_segment_final(self, text: str) -> None:
        """A finalized STT segment. Janus chunks are append-only -- an
        interim/partial transcript is never sent here, only a segment
        the STT layer itself considers final.

        Arriving words never restart the end-of-turn countdown by
        themselves: the countdown follows the user's silence (see
        `_schedule_eot`). A transcript can land well after its segment
        ended -- often while the user is already saying the next one.

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
        # Can go below zero: with a fast recognizer the transcript may land
        # a moment before the VAD stop of its own segment (the session and
        # the transcriber each run their own VAD stream), and that stop
        # must not then wait for a transcript that already came.
        self._pending_segments -= 1
        self._last_final_at = self._loop_now()
        if not (
            (self.drop_filler_segments and is_filler_segment(text))
            or (self.drop_foreign_segments and is_foreign_segment(text))
        ):
            await self.events.put({"type": "text_chunk", "ts_us": self.now_us(), "payload": {"text": text.strip()}})
            self._words_since_eot = True
            if self._agent_speaking and text.strip():
                self.stop_speaking()  # real words over the agent: a barge-in
        # A dropped segment (filler, noise) sends no chunk, but it still
        # was the transcript an earlier speech end was waiting for.
        await self._report_activity()
        self._schedule_eot()

    async def on_speech_end(self) -> None:
        """User speech stopped (VAD). Each such stop ends a segment whose
        transcript is still to come; the silence countdown starts here."""
        if self._user_speaking:
            self._user_speaking = False
            self._pending_segments += 1
            self._last_speech_end = self._loop_now()
        await self._report_activity()
        self._schedule_eot()

    def on_agent_speaking_changed(self, speaking: bool) -> None:
        """Wired to the TTS/session's own speaking-state signal so
        `on_speech_start` can tell a barge-in from ordinary silence."""
        self._agent_speaking = speaking

    @staticmethod
    def _loop_now() -> float:
        # Event-loop time, the clock `asyncio.sleep` itself runs on, so the
        # countdown arithmetic below and the sleep that carries it out agree.
        # (`self.clock` only stamps events and may be a test's fake.)
        return asyncio.get_running_loop().time()

    def _schedule_eot(self) -> None:
        """(Re)plans the single end-of-turn for the current turn.

        The turn closes once all of these hold:
        - the user is not speaking (VAD);
        - `t_eot_ms` of silence has passed since speech last stopped --
          counted from the VAD stop, not from when a transcript arrived;
        - every segment that has ended has had its transcript delivered,
          or `t_stt_wait_ms` has passed since the last stop (a lost
          transcription must not hold the turn open);
        - some words were sent since the previous end-of-turn.

        Found by the 1 Oct audit: re-arming a plain timer on every arriving
        transcript closed 86 turns while the user was still speaking (52 of
        72 recordings), because hosted transcription lands ~1.1 s after its
        segment, by which time the user has usually resumed.

        A host that never reports VAD (the offline replay, unit tests)
        counts silence from the last transcript, as before."""
        self._cancel_eot_timer()
        if self._stopped or self._user_speaking:
            return
        if not self._words_since_eot:
            if self._pending_segments > 0 and self._last_speech_end is not None:
                # Nothing to close yet, but a transcript is awaited: stop
                # waiting for it after t_stt_wait_ms all the same.
                self._eot_task = asyncio.create_task(
                    self._give_up_at(self._last_speech_end + self.t_stt_wait_ms / 1000)
                )
            return
        silence_from = self._last_speech_end if self._vad_seen else self._last_final_at
        if silence_from is None:
            return
        deadline = silence_from + self.t_eot_ms / 1000
        if self._pending_segments > 0 and self._last_speech_end is not None:
            deadline = max(deadline, self._last_speech_end + self.t_stt_wait_ms / 1000)
        self._eot_task = asyncio.create_task(self._eot_at(deadline))

    def _cancel_eot_timer(self) -> None:
        if self._eot_task is not None and not self._eot_task.done():
            self._eot_task.cancel()
        self._eot_task = None

    async def _eot_at(self, deadline: float) -> None:
        try:
            await asyncio.sleep(max(0.0, deadline - self._loop_now()))
        except asyncio.CancelledError:
            return
        self._words_since_eot = False
        self._pending_segments = 0  # anything still outstanding was given up on
        await self._report_activity()
        await self.events.put({"type": "end_of_turn", "ts_us": self.now_us(), "payload": {}})

    async def _give_up_at(self, deadline: float) -> None:
        try:
            await asyncio.sleep(max(0.0, deadline - self._loop_now()))
        except asyncio.CancelledError:
            return
        self._pending_segments = 0
        await self._report_activity()

    async def _report_activity(self) -> None:
        """Sends `user_speech` when the user's audible activity changes:
        active while they speak or any segment's transcript is outstanding.
        Sent after the segment's own text_chunk, so the kernel never sees
        the floor released before the words that took it."""
        if not self.report_user_activity or self._stopped:
            return
        active = self._user_speaking or self._pending_segments > 0
        if active == self._activity_reported:
            return
        self._activity_reported = active
        await self.events.put({"type": "user_speech", "ts_us": self.now_us(), "payload": {"active": active}})

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
