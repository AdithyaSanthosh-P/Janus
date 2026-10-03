"""3 Oct voice runs: the model wrote a document type the way it is spoken
("driver's license") although the tool's own description gives its values as
snake_case words ('passport', 'id_card'). The value is written in that form;
nothing else is touched."""

import pytest

from prism_rt.kernel.action_plans import coerce_to_schema

DOC_TYPE = {"type": "string", "description": "Type of document, e.g. 'passport' or 'id_card'"}


@pytest.mark.parametrize("spoken, written", [
    ("driver's license", "driver_license"),
    ("Driver License", "driver_license"),
    ("ID card", "id_card"),
    ("passport", "passport"),
    ("credit-card", "credit_card"),
])
def test_value_takes_the_form_of_the_described_examples(spoken, written):
    assert coerce_to_schema(spoken, DOC_TYPE) == written


@pytest.mark.parametrize("value", ["P1122", "the one I renewed last week in March", "DL-9090", "Riley Kim 2"])
def test_ids_numbers_and_sentences_are_untouched(value):
    assert coerce_to_schema(value, DOC_TYPE) == value


def test_parameters_without_snake_case_examples_are_untouched():
    for prop in ({"type": "string", "description": "Passenger full name"},
                 {"type": "string", "description": "Bank account identifier, e.g. 'checking'"},
                 {"type": "string"}):
        assert coerce_to_schema("Riley Kim", prop) == "Riley Kim"
        assert coerce_to_schema("savings account", prop) == "savings account"


def test_enum_matching_still_applies():
    prop = {"type": "string", "enum": ["Driver_License", "passport"], "description": "e.g. 'id_card'"}
    assert coerce_to_schema("driver's license", prop) == "Driver_License"
