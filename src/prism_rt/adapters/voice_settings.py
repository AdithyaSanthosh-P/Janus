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
    (re.compile(r"\b(?:thread|threat|friend)\s+door\b", re.IGNORECASE), "front door"),
    # "Hey Janus" heard as "A. Janus" / "Aeganus" (Parakeet, 24 Oct).
    (re.compile(r"^\s*(?:a|eh|ay)\.?,?\s+janus\b|^\s*aeganus\b", re.IGNORECASE), "Hey Janus"),
    # A whole utterance of just "Now" is an answer: "No" (Parakeet, 24 Oct).
    (re.compile(r"^\s*now\s*([.!]?)\s*$", re.IGNORECASE), r"No\1"),
)


def fix_misheard_words(text: str) -> str:
    for pattern, word in _MISHEARD:
        text = pattern.sub(word, text)
    return text


# Demo mode: the end-of-turn filler varies with the turn instead of always
# "Okay, one moment." (team feedback, 24 Oct: the same phrase every turn
# sounded mechanical). A question gets a "checking" phrase, a request an
# acknowledgement; the same phrase is never used twice in a row.
FILLERS_FOR_QUESTION = ("Let me check.", "One sec.", "Let me see.")
FILLERS_FOR_REQUEST = ("Okay.", "Sure.", "Mm-hmm.", "Alright.", "Okay, one moment.")
_QUESTION_START = re.compile(
    r"^\W*(?:what|what's|whats|is|are|was|were|did|does|do|how|why|when|where|who|which|has|have)\b", re.IGNORECASE)
_REQUEST_START = re.compile(r"^\W*(?:can|could|would|will)\s+you\b|^\W*please\b", re.IGNORECASE)


def choose_filler(said: str, last: str | None = None, rng=None) -> str:
    """The filler for a turn: a "checking" phrase for a question ("Is the door
    locked?"), an acknowledgement for a request ("Turn off the switch.", "Can you
    lock it?"); never `last` again."""
    import random

    text = str(said or "").strip()
    question = not _REQUEST_START.match(text) and (bool(_QUESTION_START.match(text)) or text.endswith("?"))
    pool = [f for f in (FILLERS_FOR_QUESTION if question else FILLERS_FOR_REQUEST) if f != last]
    return (rng or random).choice(pool)


def keep_speech_at_turn_end(activity_cls) -> None:
    """Patch LiveKit's `AgentActivity.on_end_of_turn` so a closed user turn no
    longer cuts the speech already playing.

    With no LLM, LiveKit still runs its reply path when a user turn closes, and
    that path first interrupts the current speech (agent_activity.
    _user_turn_completed_impl: `current_speech.interrupt(source="user_turn")`)
    and only then finds nothing to reply with. Its turn closes about 1.9 s after
    the user stops, so a Janus answer that started sooner was cut a moment into
    it: "Hi, what can I do for you?" stopped after "Hi," (phone, 24 Oct; four
    cuts in one session, each 1.93 s after the user's last word, with no user
    speech). The turn is now committed with `skip_reply`, which skips that
    interrupt. Speech LiveKit has paused because the user talked over it keeps
    the normal path, so a real barge-in still stops it. Idempotent."""
    original = activity_cls.on_end_of_turn
    if getattr(original, "_janus_keeps_speech", False):
        return

    def on_end_of_turn(self, info):
        if getattr(self, "_paused_speech", None) is None:
            info.skip_reply = True
        return original(self, info)

    on_end_of_turn._janus_keeps_speech = True
    activity_cls.on_end_of_turn = on_end_of_turn
