"""LiveKit AEC warm-up setting for the voice worker (adapters/voice_settings.py).

Found in the 19 Oct full run: LiveKit replaces the audio sent to speech-to-text
with silence while the agent speaks during the first 3 s of agent speech (its
AEC warm-up). With the end-of-turn filler, that first speech now overlaps a
user who paused mid-sentence, and their words were lost (finance_04, finance_16).
"""

import pytest

from prism_rt.adapters.voice_settings import aec_warmup_duration, keep_speech_at_turn_end


def test_fdb_mode_defaults_to_off():
    # The benchmark streams a recording on its own track: there is no echo.
    assert aec_warmup_duration("fdb", None) is None
    assert aec_warmup_duration("fdb", "") is None


def test_demo_mode_keeps_livekit_default():
    # A live room can carry the agent's own voice back through a loudspeaker.
    assert aec_warmup_duration("demo", None) == 3.0


def test_zero_turns_it_off():
    assert aec_warmup_duration("demo", "0") is None
    assert aec_warmup_duration("fdb", "0.0") is None


def test_explicit_seconds():
    assert aec_warmup_duration("fdb", "1.5") == 1.5
    assert aec_warmup_duration("demo", " 2 ") == 2.0


@pytest.mark.parametrize("raw", ["abc", "-1"])
def test_bad_value_is_rejected(raw):
    with pytest.raises(ValueError):
        aec_warmup_duration("fdb", raw)


def test_the_filler_fits_the_turn_and_never_repeats():
    """Team feedback, 24 Oct: "Okay, one moment." every turn sounded mechanical."""
    import random

    from prism_rt.adapters.voice_settings import FILLERS_FOR_QUESTION, FILLERS_FOR_REQUEST, choose_filler

    rng = random.Random(1)
    for said in ("Is the front door locked?", "What does this error mean?", "did you lock it"):
        assert choose_filler(said, rng=rng) in FILLERS_FOR_QUESTION
    for said in ("Turn off the switch.", "Can you lock the front door?", "Set an alarm for six thirty."):
        assert choose_filler(said, rng=rng) in FILLERS_FOR_REQUEST
    last = None
    for _ in range(30):
        now = choose_filler("Turn off the switch.", last, rng=rng)
        assert now != last
        last = now


class _Info:
    skip_reply = False


class _Activity:
    """Stand-in for LiveKit's AgentActivity: its reply path cuts the current
    speech unless the turn is committed with skip_reply."""

    def __init__(self, paused=None):
        self._paused_speech = paused
        self.cut = False

    def on_end_of_turn(self, info):
        if not info.skip_reply:
            self.cut = True
        return True


def test_turn_end_no_longer_cuts_playing_speech():
    # Phone, 24 Oct: "Hi, what can I do for you?" stopped 1.93 s after the user's
    # last word, when LiveKit closed the turn, with no user speech in between.
    cls = type("A", (_Activity,), {})
    keep_speech_at_turn_end(cls)
    activity, info = cls(), _Info()
    assert activity.on_end_of_turn(info) is True
    assert info.skip_reply and not activity.cut


def test_turn_end_still_cuts_speech_the_user_talked_over():
    # LiveKit pauses speech the user talks over; its normal path then stops it.
    cls = type("A", (_Activity,), {})
    keep_speech_at_turn_end(cls)
    activity, info = cls(paused=object()), _Info()
    activity.on_end_of_turn(info)
    assert not info.skip_reply and activity.cut


def test_patch_is_idempotent():
    cls = type("A", (_Activity,), {})
    keep_speech_at_turn_end(cls)
    patched = cls.on_end_of_turn
    keep_speech_at_turn_end(cls)
    assert cls.on_end_of_turn is patched
