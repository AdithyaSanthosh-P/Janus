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
