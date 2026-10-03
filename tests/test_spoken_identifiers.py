"""30 Sep voice runs: Whisper writes a spoken code as letters, digit words and
separators. For an identifier parameter (`*_id`, `*_number`, `*_code`) the
value is joined back into the code; anything phrase-like is left alone."""

import pytest

from prism_rt.canonical import canonicalize_spoken_id


@pytest.mark.parametrize("spoken, code", [
    ("F A S T nine nine", "FAST99"),
    ("P.O. 999", "PO999"),
    ("one, two, three, ABC", "123ABC"),
    ("DL. five five five", "DL555"),
    ("E77-2211", "E772211"),
    ("ABC one two three", "ABC123"),
    ("A-B-C-1-2-3", "ABC123"),
    ("D... Dash L-5-5-5", "DL555"),  # 3 Oct voice run, travel_07
    ("P dash five dash two", "P52"),
])
def test_spoken_code_is_joined_for_an_identifier_parameter(spoken, code):
    assert canonicalize_spoken_id(spoken, "order_id") == code
    assert canonicalize_spoken_id(spoken, "slot.g-0001.a0.doc_number") == code


@pytest.mark.parametrize("value", ["the one from last week", "my order", "XYZ88", "dash", "the dash cam"])
def test_phrases_and_compact_codes_are_untouched(value):
    assert canonicalize_spoken_id(value, "order_id") == value


def test_non_identifier_parameters_keep_the_old_rule():
    assert canonicalize_spoken_id("F A S T nine nine", "city") == "F A S T nine nine"
    assert canonicalize_spoken_id("X-Y-Z-8-8", "city") == "XYZ88"
    assert canonicalize_spoken_id("New York") == "New York"
