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


# Demo mode: words the recognizer is known to mishear in this home, fixed in
# the transcript (22 Oct rehearsal: "Tomos Tractor", "thermal start",
# "Homostat"; "Janice"). This replaced naming them in the recognizer's prompt,
# which made it invent whole commands from background noise ("Janus, turn on
# the AC and set the thermostat to 72 degrees", 23 Oct, sent as the user's).
_MISHEARD = (
    (re.compile(r"\b(?:tomos\s+tractor|thermal\s+start|homo\s*stat|thermo\s+stat|thermos\s+stat)\b", re.IGNORECASE), "thermostat"),
    (re.compile(r"\bjanice\b", re.IGNORECASE), "Janus"),
    # A whole utterance of just "Now" is an answer: "No" (Parakeet, 24 Oct).
    (re.compile(r"^\s*now\s*([.!]?)\s*$", re.IGNORECASE), r"No\1"),
)


def fix_misheard_words(text: str) -> str:
    for pattern, word in _MISHEARD:
        text = pattern.sub(word, text)
    return text
