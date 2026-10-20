"""Voice-worker settings that need no LiveKit import, so the test suite can
check them (everything under `prism_rt/voice/` imports livekit-agents)."""

from __future__ import annotations

# livekit-agents' own default (agent_session._DEFAULT_AEC_WARMUP_DURATION).
LIVEKIT_AEC_WARMUP_S = 3.0


def aec_warmup_duration(mode: str, raw: str | None) -> float | None:
    """`AgentSession(aec_warmup_duration=...)` from JANUS_AEC_WARMUP_S.

    During the warm-up (from the agent's first speech) LiveKit swaps the audio
    sent to speech-to-text for silence while the agent speaks, and ignores
    barge-in. With the end-of-turn filler, that first speech overlaps a user
    who paused mid-sentence, and their words were lost (19 Oct full run:
    finance_04, finance_16). The benchmark streams a recording on its own
    track, with no echo to guard against, so fdb mode turns it off; demo mode
    keeps LiveKit's default for a live room with a loudspeaker. None = off.
    """
    if raw is None or raw.strip() == "":
        return None if mode == "fdb" else LIVEKIT_AEC_WARMUP_S
    seconds = float(raw)
    if seconds < 0:
        raise ValueError(f"JANUS_AEC_WARMUP_S must be >= 0, not {raw!r}")
    return seconds or None
