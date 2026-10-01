"""Inbound event envelope and payload types.

All payload dataclasses are frozen and immutable, matching the "immutable
records" contract in `docs/prompt 2.txt` §2.2 — nothing outside a reducer
running inside a kernel step may construct or hold a mutable view of an
event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prism_rt.model.types import EventClass


@dataclass(frozen=True)
class Envelope:
    ts_us: int
    event_class: EventClass
    seq: int
    event_id: str
    payload_type: str
    payload: Any

    @property
    def ordering_key(self) -> tuple[int, int, int]:
        return (self.ts_us, int(self.event_class), self.seq)


# --- Payload dataclasses (one per Envelope.payload_type) -------------------


@dataclass(frozen=True)
class ManifestPayload:
    tools: list  # list[dict]


@dataclass(frozen=True)
class TextChunkPayload:
    text: str
    turn_id: str | None = None
    is_final: bool = False


@dataclass(frozen=True)
class EndOfTurnPayload:
    turn_id: str | None = None


@dataclass(frozen=True)
class UserSpeechPayload:
    """The user's audible activity, from the voice host: `active` while they
    are speaking or their last words are still being transcribed. Lets the
    kernel know the floor is taken before any text exists (1 Oct audit)."""

    active: bool


@dataclass(frozen=True)
class InterruptionPayload:
    reason: str | None = None


@dataclass(frozen=True)
class ToolResultPayload:
    call_id: str
    status: str  # "ok" or "error"
    result: Any = None
    error: dict | None = None


@dataclass(frozen=True)
class WorkerResultPayload:
    job_id: str
    kind: str
    status: str  # "ok" or "error"
    proposal: dict | None = None


@dataclass(frozen=True)
class VideoFramePayload:
    frame_id: str


@dataclass(frozen=True)
class AudioClipPayload:
    """V(post-4) Phase A: accepted and stored (as an Observation), not yet
    analyzed — ASR (Phase 2 of `docs/post_v4_implementation_plan.md`)
    consumes this later. `clip_id` is a harness-given reference, matching
    `VideoFramePayload.frame_id`'s pattern; no audio bytes are held here."""

    clip_id: str


@dataclass(frozen=True)
class TimerFiredPayload:
    timer_id: str
    timer_kind: str
    ref: str | None = None
    liveness_fire: bool = False


@dataclass(frozen=True)
class WatchdogPayload:
    wall_elapsed_s: float


# Maps Envelope.payload_type -> the dataclass it decodes to. Used by the
# codec and by tests constructing envelopes without importing every class.
PAYLOAD_TYPES: dict[str, type] = {
    "manifest": ManifestPayload,
    "text_chunk": TextChunkPayload,
    "end_of_turn": EndOfTurnPayload,
    "interruption": InterruptionPayload,
    "user_speech": UserSpeechPayload,
    "tool_result": ToolResultPayload,
    "worker_result": WorkerResultPayload,
    "video_frame": VideoFramePayload,
    "audio_clip": AudioClipPayload,
    "timer_fired": TimerFiredPayload,
    "watchdog": WatchdogPayload,
}

# Maps payload_type -> EventClass, per docs/prompt 2.txt §5.1.
EVENT_CLASS_BY_PAYLOAD_TYPE: dict[str, EventClass] = {
    "manifest": EventClass.SETUP,
    "interruption": EventClass.INTERRUPTION,
    "text_chunk": EventClass.USER_CONTENT,
    "user_speech": EventClass.USER_CONTENT,
    "video_frame": EventClass.USER_CONTENT,
    "audio_clip": EventClass.USER_CONTENT,
    "end_of_turn": EventClass.END_OF_TURN,
    "tool_result": EventClass.TOOL_RESULT,
    "worker_result": EventClass.WORKER_RESULT,
    "timer_fired": EventClass.TIMER,
    "watchdog": EventClass.TIMER,
}
