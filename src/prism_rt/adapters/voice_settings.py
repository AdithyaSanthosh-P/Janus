"""Voice-worker settings that need no LiveKit import, so the test suite can
check them (everything under `prism_rt/voice/` imports livekit-agents)."""

from __future__ import annotations

import re

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


def echoes_prompt(text: str, prompt: str, *, run: int = 5) -> bool:
    """True when a transcript repeats `run` or more consecutive words of the
    speech-to-text prompt: the recognizer, given noise or a near-silent
    segment, returned its own instructions (found on a phone, 23 Oct: "The
    speaker is talking in English … set alarms and timers on the phone." came
    through as the user's words and was acted on)."""
    words = lambda s: re.findall(r"[a-z0-9']+", s.lower())  # noqa: E731
    said, source = words(text), " " + " ".join(words(prompt)) + " "
    return any(" " + " ".join(said[i:i + run]) + " " in source for i in range(len(said) - run + 1))
