"""LiveKit AEC warm-up setting for the voice worker (adapters/voice_settings.py).

Found in the 19 Oct full run: LiveKit replaces the audio sent to speech-to-text
with silence while the agent speaks during the first 3 s of agent speech (its
AEC warm-up). With the end-of-turn filler, that first speech now overlaps a
user who paused mid-sentence, and their words were lost (finance_04, finance_16).
"""

import pytest

from prism_rt.adapters.voice_settings import aec_warmup_duration


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
