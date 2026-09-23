#!/usr/bin/env python3
"""Generate tests/conformance/*.json - the cases the kit does NOT cover.

    python tools/make_conformance.py

Why a generator rather than hand-written JSON: every one of these scenarios
turns on timing arithmetic. A conformance test is only a real test if an
uncancelled stale call PROVABLY completes more than CANCEL_GRACE_MS (800ms)
after the interruption that invalidated it. Getting that wrong produces a
scenario that passes whether or not the agent cancels anything, which is
worse than having no test at all.

So delays are pinned with `tool_overrides` (the same mechanism pub_08 uses)
instead of relying on the seeded defaults, and each scenario records its
margin in `_design_notes`. Keys beginning with underscore are organizer
annotations: the harness strips them and the agent never sees them.

The nine public scenarios are worth zero points. The exam is ~60 hidden
scenarios that re-skin them and combine them harder. These eight are our
attempt to fail early on the combinations the kit leaves untested:

  conf_01  two interruptions in one scenario, 800ms apart
  conf_02  retraction - the user cancels the request outright
  conf_03  full intent change - abandon the domain, not just a slot
  conf_04  interruption DURING a booking (the double-commit trap)
  conf_05  visual grounding with no device_hint
  conf_06  paraphrase + reasoning over results (cheapest, not first)
  conf_07  empty-but-successful tool result
  conf_08  state-modifying tool times out - must NOT blind-retry
"""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "tests", "conformance")

# Mirror of harness.scorer.CANCEL_GRACE_MS, used in the margin arithmetic.
GRACE_MS = 800


def chunk(t, text, end=False):
    return {"timestamp_ms": t, "event_type": "user_speech_chunk",
            "payload": {"text": text, "end_of_turn": end}}


def interrupt(t, text):
    return {"timestamp_ms": t, "event_type": "interruption", "payload": {"text": text}}


def frame(t, image_ref, frame_id, device_hint=None):
    payload = {"frame_id": frame_id, "image_ref": image_ref}
    if device_hint is not None:
        payload["device_hint"] = device_hint
    return {"timestamp_ms": t, "event_type": "video_frame", "payload": payload}


def latency(*specs):
    return {"respond_to": [
        {"event_index": i, "full_credit_ms": 800, "zero_credit_ms": 2500}
        for i in specs]}


# ---------------------------------------------------------------------------
# conf_01 - two interruptions, 800ms apart
# ---------------------------------------------------------------------------
# search(A) issued ~810, pinned delay 1800 -> completes ~2610
#   interrupt_1 at 1500 -> deadline 2300. 2610 > 2300, margin 310ms.
# search(B) issued ~1510, pinned delay 1800 -> completes ~3310
#   interrupt_2 at 2300 -> deadline 3100. 3310 > 3100, margin 210ms.
# search(C) issued ~2310, pinned delay 1500 -> completes ~3810, well inside
#   the 6000ms tail after the last event at 2300.
CONF_01 = {
    "scenario_id": "conf_01_double_interrupt",
    "metadata": {"modality": "text", "difficulty": "L3",
                 "description": "Two interruptions 800ms apart. Both superseded "
                                "searches must be cancelled and neither re-issued."},
    "_design_notes": {
        "stale_A_completes_ms": 2610, "interrupt_1_deadline_ms": 1500 + GRACE_MS,
        "stale_B_completes_ms": 3310, "interrupt_2_deadline_ms": 2300 + GRACE_MS,
        "why": "both margins positive, so an uncancelled call is always a violation",
    },
    "tool_overrides": {
        "flight_search": [
            {"call_index": 0, "delay_ms": 1800},
            {"call_index": 1, "delay_ms": 1800},
            {"call_index": 2, "delay_ms": 1500},
        ]
    },
    "events": [
        chunk(100, "Please book a flight to "),
        chunk(800, "Boston for tomorrow.", end=True),
        interrupt(1500, "Wait, actually make it New York."),
        interrupt(2300, "No sorry, Chicago. Chicago is right."),
    ],
    "ground_truth": {
        "checkpoints": [
            {"id": "search_final_city", "type": "tool_called", "tool": "flight_search",
             "args_subset": {"destination": ["chicago", "chi"]},
             "after_ms": 2300, "must_complete": True, "weight": 0.35},
            {"id": "no_first_city_after_i1", "type": "tool_not_called",
             "tool": "flight_search", "args_subset": {"destination": ["boston", "bos"]},
             "after_ms": 1500, "weight": 0.2},
            {"id": "no_second_city_after_i2", "type": "tool_not_called",
             "tool": "flight_search",
             "args_subset": {"destination": ["new york", "nyc"]},
             "after_ms": 2300, "weight": 0.2},
            {"id": "final_names_final_city", "type": "final_response_contains",
             "any_of": ["chicago", "fl-chi"], "weight": 0.15},
            {"id": "ack_names_final_city", "type": "spoken_contains",
             "any_of": ["chicago"], "after_ms": 2300, "before_ms": 3500,
             "weight": 0.1},
        ],
        "recovery": {
            "interrupt_at_ms": 2300,
            "invalidated_calls": [
                {"tool": "flight_search",
                 "args_subset": {"destination": ["boston", "bos"]},
                 "invalid_after_ms": 1500},
                {"tool": "flight_search",
                 "args_subset": {"destination": ["new york", "nyc"]},
                 "invalid_after_ms": 2300},
            ],
            "required_state_after_interrupt": {
                "slots.destination": ["chicago", "chi"]},
        },
        "latency": latency(1, 2, 3),
    },
}

# ---------------------------------------------------------------------------
# conf_02 - retraction
# ---------------------------------------------------------------------------
# search issued ~710, pinned delay 2400 -> completes ~3110.
# retraction at 1600 -> deadline 2400. Margin 710ms.
# The agent must cancel, acknowledge, and NOT report flight results afterwards.
CONF_02 = {
    "scenario_id": "conf_02_retraction",
    "metadata": {"modality": "text", "difficulty": "L2",
                 "description": "User retracts the request outright. Work must be "
                                "abandoned and no results reported."},
    "_design_notes": {"stale_completes_ms": 3110,
                      "deadline_ms": 1600 + GRACE_MS, "margin_ms": 710},
    "tool_overrides": {"flight_search": [{"call_index": 0, "delay_ms": 2400}]},
    "events": [
        chunk(100, "Can you find flights to "),
        chunk(700, "Miami next Friday?", end=True),
        interrupt(1600, "Actually, never mind. Forget it."),
    ],
    "ground_truth": {
        "checkpoints": [
            {"id": "no_search_after_retraction", "type": "tool_not_called",
             "tool": "flight_search", "after_ms": 1600, "weight": 0.3},
            {"id": "acknowledged_retraction", "type": "spoken_contains",
             "any_of": ["no problem", "okay", "sure", "understood", "fine",
                        "dropping", "leave it", "stopping", "nothing sent",
                        "never mind", "forget"],
             "after_ms": 1600, "before_ms": 2800, "weight": 0.3},
            {"id": "no_false_results_reported", "type": "spoken_not_contains",
             "any_of": ["i found", "here are the flights", "fl-mia",
                        "departing", "price"],
             "after_ms": 1600, "weight": 0.4},
        ],
        "recovery": {
            "interrupt_at_ms": 1600,
            "invalidated_calls": [
                {"tool": "flight_search",
                 "args_subset": {"destination": ["miami", "mia"]},
                 "invalid_after_ms": 1600}],
        },
        "latency": latency(1, 2),
    },
}

# ---------------------------------------------------------------------------
# conf_03 - full intent change
# ---------------------------------------------------------------------------
# search issued ~710, pinned delay 2200 -> completes ~2910.
# pivot at 1300 -> deadline 2100. Margin 810ms.
# Slots from the abandoned domain must not leak into the new one.
CONF_03 = {
    "scenario_id": "conf_03_intent_change",
    "metadata": {"modality": "text", "difficulty": "L3",
                 "description": "User abandons the whole domain mid-execution. "
                                "Stale work cancelled, slots cleared, new tool chosen."},
    "_design_notes": {"stale_completes_ms": 2910,
                      "deadline_ms": 1300 + GRACE_MS, "margin_ms": 810},
    "tool_overrides": {"flight_search": [{"call_index": 0, "delay_ms": 2200}]},
    "events": [
        chunk(100, "I need a flight to "),
        chunk(700, "Denver on Friday.", end=True),
        interrupt(1300, "Forget the flight. My TV is showing a blinking red light."),
    ],
    "ground_truth": {
        "checkpoints": [
            {"id": "pivoted_to_manual", "type": "tool_called", "tool": "lookup_manual",
             "args_present": ["query"], "after_ms": 1300, "must_complete": True,
             "weight": 0.35},
            {"id": "no_flight_work_after_pivot", "type": "tool_not_called",
             "tool": "flight_search", "after_ms": 1300, "weight": 0.25},
            {"id": "final_addresses_new_problem", "type": "final_response_contains",
             "any_of": ["led", "blinking", "light", "status", "page", "manual"],
             "weight": 0.2},
            {"id": "abandoned_slot_not_leaked", "type": "spoken_not_contains",
             "any_of": ["denver"], "after_ms": 1300, "weight": 0.2},
        ],
        "recovery": {
            "interrupt_at_ms": 1300,
            "invalidated_calls": [
                {"tool": "flight_search",
                 "args_subset": {"destination": ["denver", "den"]},
                 "invalid_after_ms": 1300}],
            "required_state_after_interrupt": {
                "intent": ["lookup_manual", "create_support_ticket", "device_help"]},
        },
        "latency": latency(1, 2),
    },
}

# ---------------------------------------------------------------------------
# conf_04 - interruption DURING a booking (the double-commit trap)
# ---------------------------------------------------------------------------
# search issued ~910, pinned delay 1200 -> completes ~2110.
# book(8AM) issued ~2120, pinned delay 2500 -> would complete ~4620.
# interrupt at 2600 -> deadline 3400. Margin 1220ms: the in-flight booking is
# unambiguously still running and must be cancelled.
# book(2PM) issued ~2610, pinned delay 900 -> completes ~3510, inside the tail.
#
# This is the scenario that separates a correct idempotency ledger from a
# hopeful one: the agent must cancel an irreversible call it already issued,
# then issue a DIFFERENT one, and end with exactly one successful booking.
CONF_04 = {
    "scenario_id": "conf_04_interrupt_during_booking",
    "metadata": {"modality": "text", "difficulty": "L4",
                 "description": "Interruption lands while a state-modifying call is "
                                "in flight. Exactly one booking may succeed."},
    "_design_notes": {"stale_booking_completes_ms": 4620,
                      "deadline_ms": 2600 + GRACE_MS, "margin_ms": 1220},
    "tool_overrides": {
        "flight_search": [{"call_index": 0, "delay_ms": 1200}],
        "book_flight": [
            {"call_index": 0, "delay_ms": 2500},
            {"call_index": 1, "delay_ms": 900},
        ],
    },
    "events": [
        chunk(100, "Find a flight to Denver and "),
        chunk(900, "book the 8 AM one for Alice.", end=True),
        interrupt(2600, "Wait, make it the 2 PM flight instead."),
    ],
    "ground_truth": {
        "checkpoints": [
            {"id": "booked_corrected_flight", "type": "tool_called",
             "tool": "book_flight",
             "args_subset": {"flight_id": ["fl-den-2pm"],
                             "passenger_name": ["alice"]},
             "after_ms": 2600, "must_complete": True, "weight": 0.4},
            {"id": "no_superseded_booking_after", "type": "tool_not_called",
             "tool": "book_flight", "args_subset": {"flight_id": ["fl-den-8am"]},
             "after_ms": 2600, "weight": 0.25},
            {"id": "final_confirms_correct_flight", "type": "final_response_contains",
             "any_of": ["bk-", "2 pm", "2pm", "14:00"], "weight": 0.2},
            {"id": "ack_names_correction", "type": "spoken_contains",
             "any_of": ["2 pm", "2pm", "14:00", "afternoon"],
             "after_ms": 2600, "before_ms": 3800, "weight": 0.15},
        ],
        "recovery": {
            "interrupt_at_ms": 2600,
            "invalidated_calls": [
                {"tool": "book_flight",
                 "args_subset": {"flight_id": ["fl-den-8am"]},
                 "invalid_after_ms": 2600}],
            "required_state_after_interrupt": {"slots.flight_id": ["fl-den-2pm"]},
        },
        "latency": latency(1, 2),
        "safety": {"max_fillers": 4},
    },
}

# ---------------------------------------------------------------------------
# conf_05 - visual grounding with no device_hint
# ---------------------------------------------------------------------------
# PROTOCOL.md says device_hint "may be absent in harder scenarios". The agent
# must not send an invalid enum value and must not hedge across port types.
CONF_05 = {
    "scenario_id": "conf_05_visual_no_hint",
    "metadata": {"modality": "visual", "difficulty": "L3",
                 "description": "Frame-grounded question with no device_hint. "
                                "Must commit to one answer without hedging."},
    "events": [
        frame(100, "frames/pub_07_f017.png", "f_101"),
        chunk(700, "What is this port used for?", end=True),
    ],
    "ground_truth": {
        "checkpoints": [
            {"id": "manual_lookup", "type": "tool_called", "tool": "lookup_manual",
             "args_present": ["query"], "must_complete": True, "weight": 0.3},
            {"id": "hybrid_embedding_passed", "type": "tool_called",
             "tool": "lookup_manual", "args_present": ["query", "image_embedding"],
             "weight": 0.15},
            {"id": "final_grounded", "type": "final_response_contains",
             "any_of": ["hdmi"], "weight": 0.3},
            {"id": "no_hedging", "type": "spoken_not_contains",
             "any_of": ["headphone", "usb", "ethernet"], "weight": 0.15},
            {"id": "cites_source", "type": "final_response_contains",
             "any_of": ["manual", "page"], "weight": 0.1},
        ],
        "latency": latency(1),
    },
}

# ---------------------------------------------------------------------------
# conf_06 - paraphrase plus reasoning over results
# ---------------------------------------------------------------------------
# No "find flights to X" phrasing anywhere, and "the cheapest" requires
# comparing the returned prices (2PM is $99, 8AM is $129) rather than taking
# flights[0]. Keyword rules and first-result heuristics both fail here.
CONF_06 = {
    "scenario_id": "conf_06_paraphrase_cheapest",
    "metadata": {"modality": "text", "difficulty": "L3",
                 "description": "Unusual phrasing plus a superlative that requires "
                                "reading the result set, not taking the first row."},
    "tool_overrides": {
        "flight_search": [{"call_index": 0, "delay_ms": 1400}],
        "book_flight": [{"call_index": 0, "delay_ms": 900}],
    },
    "events": [
        chunk(100, "Denver, Friday - "),
        chunk(800, "put me on the cheapest one, name's Alice.", end=True),
    ],
    "ground_truth": {
        "checkpoints": [
            {"id": "searched", "type": "tool_called", "tool": "flight_search",
             "args_subset": {"destination": ["denver", "den"]},
             "must_complete": True, "weight": 0.3},
            {"id": "booked_cheapest", "type": "tool_called", "tool": "book_flight",
             "args_subset": {"flight_id": ["fl-den-2pm"],
                             "passenger_name": ["alice"]},
             "must_complete": True, "weight": 0.45},
            {"id": "final_confirms", "type": "final_response_contains",
             "any_of": ["bk-", "99", "2 pm", "2pm"], "weight": 0.25},
        ],
        "latency": latency(1),
        "safety": {"max_fillers": 3},
    },
}

# ---------------------------------------------------------------------------
# conf_07 - empty but successful result
# ---------------------------------------------------------------------------
# docs/TOOLS.md: "An empty pages list is a *successful* call with no hits -
# handle it gracefully." The failure mode is inventing a citation.
CONF_07 = {
    "scenario_id": "conf_07_empty_result",
    "metadata": {"modality": "text", "difficulty": "L2",
                 "description": "Successful tool call with zero results. Must say so "
                                "rather than fabricate a citation."},
    "tool_overrides": {"lookup_manual": [{"call_index": 0, "delay_ms": 1300}]},
    "events": [
        chunk(100, "My toaster is making a strange rattling noise - "),
        chunk(900, "does the manual say anything about that?", end=True),
    ],
    "ground_truth": {
        "checkpoints": [
            {"id": "attempted_lookup", "type": "tool_called", "tool": "lookup_manual",
             "args_present": ["query"], "must_complete": True, "weight": 0.3},
            {"id": "admits_no_results", "type": "final_response_contains",
             "any_of": ["sorry", "could not", "couldn't", "not find", "nothing",
                        "no results", "do not have", "don't have", "not covered"],
             "weight": 0.4},
            {"id": "no_fabricated_citation", "type": "spoken_not_contains",
             "any_of": ["page 8", "page 12", "page 21", "page 23", "page 25",
                        "page 27", "page 42", "page 57"],
             "weight": 0.3},
        ],
        "latency": latency(1),
    },
}

# ---------------------------------------------------------------------------
# conf_08 - state-modifying tool times out
# ---------------------------------------------------------------------------
# search issued ~110, delay 1200 -> completes ~1310.
# book issued ~1320, delay 1000, injected timeout -> error at ~2320.
#
# docs/TOOLS.md: retry a state-modifying tool only with evidence the first
# attempt did not commit. A timeout is NOT such evidence - the booking may
# have gone through upstream. The correct behaviours are to verify or to ask,
# never to fire the same booking again.
#
# The last checkpoint encodes a hazard we found reading the scorer: because
# spoken_not_contains is plain substring matching with no future-guard logic,
# a checkpoint like this penalises even an honest promise ("I'll get that
# booked"). Robust phrasing avoids the verb root entirely before completion.
CONF_08 = {
    "scenario_id": "conf_08_state_mod_timeout",
    "metadata": {"modality": "text", "difficulty": "L4",
                 "description": "State-modifying call times out. Must not blind-retry, "
                                "must not claim success."},
    "_design_notes": {"book_error_at_ms": 2320,
                      "why": "timeout leaves commit state UNKNOWN; retrying risks "
                             "a double booking the scorer cannot see but the user pays for"},
    "tool_overrides": {
        "flight_search": [{"call_index": 0, "delay_ms": 1200}],
        "book_flight": [{"call_index": 0, "delay_ms": 1000, "error": "timeout",
                         "detail": "upstream booking API timed out"}],
    },
    "events": [
        chunk(100, "Book me the 8 AM flight to Denver for Alice please.", end=True),
    ],
    "ground_truth": {
        "checkpoints": [
            {"id": "attempted_once", "type": "tool_called", "tool": "book_flight",
             "args_subset": {"flight_id": ["fl-den-8am"]},
             "min_count": 1, "weight": 0.25},
            {"id": "no_blind_retry", "type": "tool_not_called", "tool": "book_flight",
             "args_subset": {"flight_id": ["fl-den-8am"]},
             "after_ms": 2320, "weight": 0.35},
            {"id": "tells_the_user", "type": "final_response_contains",
             "any_of": ["sorry", "could not", "couldn't", "not able", "unable",
                        "try again", "check", "did not go through",
                        "didn't go through", "timed out"],
             "weight": 0.25},
            {"id": "never_claims_success", "type": "spoken_not_contains",
             "any_of": ["booked", "reserved", "confirmed", "all set"],
             "weight": 0.15},
        ],
        "latency": latency(0),
        "safety": {"max_fillers": 4},
    },
}


# ---------------------------------------------------------------------------
# conf_09..conf_18 - the paraphrase pack
# ---------------------------------------------------------------------------
# WALKTHROUGH.md: hidden scenarios use paraphrases ("I need to get to Denver",
# "any seats to Denver Friday", "Denver, Friday, book it") and "keyword lists
# will not survive". The kit's generator varies only cities and timings, so
# phrasing robustness was unmeasured. Every city and name here is absent from
# the kit AND from our code, and each scenario isolates one structural shape.
# A failure here gets a structural fix - never its phrase added to a list.
def _flight_ids(city):
    code = "".join(c for c in city if c.isalpha())[:3].upper()
    return ["fl-" + code.lower()]


def _search_scenario(sid, description, events, city, extra_checkpoints=(),
                     end_index=None, date_alias=None):
    checkpoints = [
        {"id": "searched_city", "type": "tool_called", "tool": "flight_search",
         "args_subset": dict({"destination": [city.lower()]},
                             **({"date": date_alias} if date_alias else {})),
         "must_complete": True, "weight": 0.5},
        {"id": "final_grounded", "type": "final_response_contains",
         "any_of": [city.lower()] + _flight_ids(city), "weight": 0.25},
        {"id": "state_destination", "type": "state_snapshot",
         "path": "slots.destination", "any_of": [city.lower()], "weight": 0.25},
    ] + list(extra_checkpoints)
    # Weights sum to exactly 1.0 by convention (checked by
    # test_all_conformance_scenarios_are_wellformed), extras included.
    total = sum(cp["weight"] for cp in checkpoints)
    for cp in checkpoints:
        cp["weight"] = round(cp["weight"] / total, 3)
    checkpoints[0]["weight"] = round(
        1.0 - sum(cp["weight"] for cp in checkpoints[1:]), 3)
    return {
        "scenario_id": sid,
        "metadata": {"modality": "text", "difficulty": "L2", "description": description},
        "tool_overrides": {"flight_search": [{"call_index": 0, "delay_ms": 1500}]},
        "events": events,
        "ground_truth": {
            "checkpoints": checkpoints,
            "latency": latency(len(events) - 1 if end_index is None else end_index),
        },
    }


CONF_09 = _search_scenario(
    "conf_09_fragments", "Telegraphic fragments, no verb, no preposition.",
    [chunk(100, "Recife. Friday. "), chunk(700, "Two people.", end=True)], "Recife")

CONF_10 = _search_scenario(
    "conf_10_question_form", "Indirect question with an unusual verb ('flies').",
    [chunk(100, "Could you see what flies "), chunk(800, "to Porto on Friday?", end=True)],
    "Porto")

CONF_11 = _search_scenario(
    "conf_11_need_to_get_to", "Need statement, no search verb at all.",
    [chunk(100, "I need to get to Tucson "), chunk(700, "by Friday.", end=True)], "Tucson")

CONF_12 = _search_scenario(
    "conf_12_self_repair_date", "Self-repair of the DATE inside one turn.",
    [chunk(100, "Find flights to Kyoto on Friday, "),
     chunk(900, "no wait, Saturday.", end=True)],
    "Kyoto", date_alias=["saturday"],
    extra_checkpoints=[
        {"id": "abandoned_date_not_searched", "type": "tool_not_called",
         "tool": "flight_search", "args_subset": {"date": ["friday"]}, "weight": 0.3},
    ])

CONF_13 = _search_scenario(
    "conf_13_politeness", "Apology and politeness padding ('sorry' is also a repair cue).",
    [chunk(100, "Sorry to bother you, but would you mind "),
     chunk(900, "checking flights to Nairobi for me?", end=True)], "Nairobi")

CONF_14 = _search_scenario(
    "conf_14_disfluent_text", "Fillers splitting the preposition from its place.",
    [chunk(100, "I want to, uh, go to, um, "), chunk(900, "Oslo I think, Friday.", end=True)],
    "Oslo")

CONF_15 = _search_scenario(
    "conf_15_lowercase_multiword", "Lowercase two-word city, as raw ASR would give it.",
    [chunk(100, "find flights to santa fe "), chunk(700, "tomorrow", end=True)], "Santa Fe")

CONF_16 = _search_scenario(
    "conf_16_any_seats", "'Any seats' phrasing with city and day run together.",
    [chunk(100, "Any seats to Halifax "), chunk(700, "Friday for Priya?", end=True)],
    "Halifax")

# conf_17 - an unseen tool whose required argument is a NUMBER, said as a word.
# docs/TOOLS.md lists `number` among the argument types every hidden tool may
# use; "for two nights", "a party of four" is how people say numbers.
CONF_17 = {
    "scenario_id": "conf_17_number_words",
    "metadata": {"modality": "text", "difficulty": "L3",
                 "description": "Unseen tool with a required number argument spoken as a word."},
    "tool_manifest": {
        "hotel_booking_quote": {
            "kind": "read_only", "delay_range_ms": [900, 1500],
            "description": "Quote a hotel stay in a city for a number of nights.",
            "args": {
                "city": {"type": "string", "required": True, "description": "City name."},
                "nights": {"type": "number", "required": True,
                           "description": "Number of nights to stay."}},
            "default_result": {"quote_id": "HQ-3301", "total_usd": 412,
                               "hotel": "Quayside Rooms"}},
    },
    "tool_overrides": {"hotel_booking_quote": [{"call_index": 0, "delay_ms": 1100}]},
    "events": [chunk(100, "How much would a hotel in Porto cost "),
               chunk(800, "for three nights?", end=True)],
    "ground_truth": {
        "checkpoints": [
            {"id": "quoted_with_nights", "type": "tool_called",
             "tool": "hotel_booking_quote",
             "args_subset": {"city": ["porto"], "nights": [3, "3"]},
             "must_complete": True, "weight": 0.5},
            {"id": "not_forced_into_flights", "type": "tool_not_called",
             "tool": "flight_search", "weight": 0.15},
            {"id": "grounded", "type": "final_response_contains",
             "any_of": ["412", "hq-3301", "quayside"], "weight": 0.35},
        ],
        "latency": latency(1),
    },
}

# conf_18 - an unseen STATE-MODIFYING tool, reached from a paraphrase, with a
# number word and a name that is not a person's. The duplicate rule applies to
# it through its kind tag alone.
CONF_18 = {
    "scenario_id": "conf_18_unseen_commit",
    "metadata": {"modality": "text", "difficulty": "L3",
                 "description": "Unseen state-modifying tool: restaurant name, party size "
                                "as a word, commit phrasing."},
    "tool_manifest": {
        "reserve_table": {
            "kind": "state_modifying", "delay_range_ms": [800, 1400],
            "description": "Reserve a table at a restaurant.",
            "args": {
                "restaurant": {"type": "string", "required": True,
                               "description": "Restaurant name."},
                "party_size": {"type": "number", "required": True,
                               "description": "How many people."},
                "time": {"type": "string", "required": False,
                         "description": "Requested time, free-form."}},
            "default_result": {"reservation_id": "RS-0042"}},
    },
    "tool_overrides": {"reserve_table": [{"call_index": 0, "delay_ms": 1000}]},
    "events": [chunk(100, "Could you get us a table at Casa Lume "),
               chunk(800, "for four at 7 PM?", end=True)],
    "ground_truth": {
        "checkpoints": [
            {"id": "reserved", "type": "tool_called", "tool": "reserve_table",
             "args_subset": {"restaurant": ["casa lume"], "party_size": [4, "4"]},
             "must_complete": True, "weight": 0.55},
            {"id": "exactly_once", "type": "tool_called", "tool": "reserve_table",
             "min_count": 1, "weight": 0.1},
            {"id": "grounded", "type": "final_response_contains",
             "any_of": ["rs-0042"], "weight": 0.35},
        ],
        "latency": latency(1),
        "safety": {"state_modifying_tools": ["reserve_table"]},
    },
}


# ---------------------------------------------------------------------------
# conf_19..conf_23 - adversarial timing
# ---------------------------------------------------------------------------
# WALKTHROUGH.md: "interruptions during chained calls, during a tool that is
# about to return, twice in one scenario". Each delay below is pinned so the
# failure being tested is forced, and the margin is written next to it.

# conf_19 - interruption 30-45 ms before the stale search returns.
# search(Recife) issued ~800, pinned 1130 -> returns ~1930-1945.
# Interruption at 1900. Whether the cancel or the result wins the race, the
# stale result must never be spoken: FL-REC after 1900 is the failure.
CONF_19 = {
    "scenario_id": "conf_19_interrupt_just_before_result",
    "metadata": {"modality": "text", "difficulty": "L3",
                 "description": "Interruption lands ~40ms before the stale call returns."},
    "_design_notes": {"stale_returns_ms": 1935, "interrupt_ms": 1900},
    "tool_overrides": {"flight_search": [{"call_index": 0, "delay_ms": 1130},
                                         {"call_index": 1, "delay_ms": 1200}]},
    "events": [chunk(100, "Can you find flights to "),
               chunk(800, "Recife on Monday?", end=True),
               interrupt(1900, "Actually, make it Lagos.")],
    "ground_truth": {
        "checkpoints": [
            {"id": "searched_new_city", "type": "tool_called", "tool": "flight_search",
             "args_subset": {"destination": ["lagos"]}, "after_ms": 1900,
             "must_complete": True, "weight": 0.4},
            {"id": "final_new_city", "type": "final_response_contains",
             "any_of": ["lagos", "fl-lag"], "weight": 0.25},
            {"id": "stale_result_never_spoken", "type": "spoken_not_contains",
             "any_of": ["fl-rec"], "after_ms": 1900, "weight": 0.35},
        ],
        "recovery": {"interrupt_at_ms": 1900,
                     "invalidated_calls": [{"tool": "flight_search",
                                            "args_subset": {"destination": ["recife"]},
                                            "invalid_after_ms": 1900}],
                     "required_state_after_interrupt": {"slots.destination": ["lagos"]}},
        "latency": latency(1, 2),
    },
}

# conf_20 - interruption 15 ms after the turn ends: inside the 20 ms speech
# floor, before the turn's own acknowledgment has been spoken.
# search(Porto) issued ~800, pinned 1800 -> ~2600 > 815 + 800 grace.
# The turn's pending "Looking up flights to Porto" must be dropped, not
# spoken after the user has already changed it.
CONF_20 = {
    "scenario_id": "conf_20_interrupt_inside_speech_floor",
    "metadata": {"modality": "text", "difficulty": "L3",
                 "description": "Interruption 15ms after the turn ends, before its ack."},
    "_design_notes": {"stale_completes_ms": 2600, "deadline_ms": 1615},
    "tool_overrides": {"flight_search": [{"call_index": 0, "delay_ms": 1800},
                                         {"call_index": 1, "delay_ms": 1400}]},
    "events": [chunk(100, "Find flights to "),
               chunk(800, "Porto for Sunday.", end=True),
               interrupt(815, "No wait, Oslo.")],
    "ground_truth": {
        "checkpoints": [
            {"id": "searched_new_city", "type": "tool_called", "tool": "flight_search",
             "args_subset": {"destination": ["oslo"]}, "after_ms": 815,
             "must_complete": True, "weight": 0.35},
            {"id": "final_new_city", "type": "final_response_contains",
             "any_of": ["oslo", "fl-osl"], "weight": 0.25},
            {"id": "no_reissue_old_city", "type": "tool_not_called", "tool": "flight_search",
             "args_subset": {"destination": ["porto"]}, "after_ms": 815, "weight": 0.25},
            {"id": "stale_ack_dropped", "type": "spoken_not_contains",
             "any_of": ["flights to porto"], "after_ms": 815, "weight": 0.15},
        ],
        "recovery": {"interrupt_at_ms": 815,
                     "invalidated_calls": [{"tool": "flight_search",
                                            "args_subset": {"destination": ["porto"]},
                                            "invalid_after_ms": 815}],
                     "required_state_after_interrupt": {"slots.destination": ["oslo"]}},
        "latency": latency(1, 2),
    },
}

# conf_21 - retraction while a BOOKING is in flight.
# search issued ~900, pinned 1300 -> ~2200; book(2PM, Omar) issued ~2200,
# pinned 1400 -> would complete ~3600 > 2500 + 800 = 3300. An uncancelled
# booking completes after the grace window AND books a flight the user just
# declined. "Don't book anything" is a negated command, not a new request.
CONF_21 = {
    "scenario_id": "conf_21_retract_during_booking",
    "metadata": {"modality": "text", "difficulty": "L4",
                 "description": "Retraction while the state-modifying call is in flight."},
    "_design_notes": {"stale_booking_completes_ms": 3600, "deadline_ms": 3300},
    "tool_overrides": {"flight_search": [{"call_index": 0, "delay_ms": 1300}],
                       "book_flight": [{"call_index": 0, "delay_ms": 1400}]},
    "events": [chunk(100, "Find a flight to Lima and "),
               chunk(900, "book the 2 PM one for Omar.", end=True),
               interrupt(2500, "Actually, never mind. Don't book anything.")],
    "ground_truth": {
        "checkpoints": [
            {"id": "no_booking_after_retraction", "type": "tool_not_called",
             "tool": "book_flight", "after_ms": 2500, "weight": 0.3},
            {"id": "acknowledged", "type": "spoken_contains",
             "any_of": ["no problem", "okay", "sure", "understood", "fine", "dropping",
                        "leave it", "stopping", "cancel"],
             "after_ms": 2500, "before_ms": 3700, "weight": 0.3},
            {"id": "never_claims_booking", "type": "spoken_not_contains",
             "any_of": ["booked", "all set", "bk-"], "after_ms": 2500, "weight": 0.4},
        ],
        "recovery": {"interrupt_at_ms": 2500,
                     "invalidated_calls": [{"tool": "book_flight",
                                            "args_subset": {"flight_id": ["fl-lim-2pm"]},
                                            "invalid_after_ms": 2500}]},
        "latency": latency(1, 2),
        "safety": {"max_fillers": 4},
    },
}

# conf_22 - three corrections in 800 ms.
# Each search pinned 1600 so every one is still in flight at the next
# correction: Accra ~2300 > 2100, Lagos ~2920 > 2500, Dakar ~3320 > 2900.
# Four acknowledgments (the turn + three) exactly meet the filler budget.
CONF_22 = {
    "scenario_id": "conf_22_three_corrections",
    "metadata": {"modality": "text", "difficulty": "L4",
                 "description": "Three corrections 400ms apart; each supersedes the last."},
    "tool_overrides": {"flight_search": [{"call_index": 0, "delay_ms": 1600},
                                         {"call_index": 1, "delay_ms": 1600},
                                         {"call_index": 2, "delay_ms": 1600},
                                         {"call_index": 3, "delay_ms": 1200}]},
    "events": [chunk(100, "Flights to Accra "),
               chunk(700, "for Tuesday please.", end=True),
               interrupt(1300, "Make it Lagos."),
               interrupt(1700, "No wait, Dakar."),
               interrupt(2100, "Actually, Nairobi.")],
    "ground_truth": {
        "checkpoints": [
            {"id": "searched_final_city", "type": "tool_called", "tool": "flight_search",
             "args_subset": {"destination": ["nairobi"]}, "after_ms": 2100,
             "must_complete": True, "weight": 0.35},
            {"id": "final_names_final_city", "type": "final_response_contains",
             "any_of": ["nairobi", "fl-nai"], "weight": 0.2},
            {"id": "no_accra_after_first", "type": "tool_not_called", "tool": "flight_search",
             "args_subset": {"destination": ["accra"]}, "after_ms": 1300, "weight": 0.15},
            {"id": "no_lagos_after_second", "type": "tool_not_called", "tool": "flight_search",
             "args_subset": {"destination": ["lagos"]}, "after_ms": 1700, "weight": 0.15},
            {"id": "no_dakar_after_third", "type": "tool_not_called", "tool": "flight_search",
             "args_subset": {"destination": ["dakar"]}, "after_ms": 2100, "weight": 0.15},
        ],
        "recovery": {"interrupt_at_ms": 2100,
                     "invalidated_calls": [
                         {"tool": "flight_search", "args_subset": {"destination": ["accra"]},
                          "invalid_after_ms": 1300},
                         {"tool": "flight_search", "args_subset": {"destination": ["lagos"]},
                          "invalid_after_ms": 1700},
                         {"tool": "flight_search", "args_subset": {"destination": ["dakar"]},
                          "invalid_after_ms": 2100}],
                     "required_state_after_interrupt": {"slots.destination": ["nairobi"]}},
        "latency": latency(1, 2, 3, 4),
        "safety": {"max_fillers": 4},
    },
}

# conf_23 - a second correction INSIDE the commit quiet window.
# search ~900 pinned 1200 -> ~2100; book(8AM, Alice) ~2100 pinned 1500 ->
# would complete ~3600 > 2500 + 800. The first correction (2500) holds the
# 2PM booking for 250 ms; the second (2650) lands inside that hold, so the
# held booking for ALICE must be dropped and only 2PM for PRIYA issued.
CONF_23 = {
    "scenario_id": "conf_23_correction_inside_commit_hold",
    "metadata": {"modality": "text", "difficulty": "L4",
                 "description": "Second correction arrives while the corrected booking is "
                                "still held behind the commitment gate."},
    "_design_notes": {"stale_booking_completes_ms": 3600, "hold_ends_ms": 2770},
    "tool_overrides": {"flight_search": [{"call_index": 0, "delay_ms": 1200}],
                       "book_flight": [{"call_index": 0, "delay_ms": 1500},
                                       {"call_index": 1, "delay_ms": 1000}]},
    "events": [chunk(100, "Find a flight to Denver and "),
               chunk(900, "book the 8 AM one for Alice.", end=True),
               interrupt(2500, "Wait, make it the 2 PM flight instead."),
               interrupt(2650, "Oh, and it's for Priya, not Alice.")],
    "ground_truth": {
        "checkpoints": [
            {"id": "booked_final_version", "type": "tool_called", "tool": "book_flight",
             "args_subset": {"flight_id": ["fl-den-2pm"], "passenger_name": ["priya"]},
             "must_complete": True, "weight": 0.4},
            {"id": "held_booking_dropped", "type": "tool_not_called", "tool": "book_flight",
             "args_subset": {"flight_id": ["fl-den-2pm"], "passenger_name": ["alice"]},
             "weight": 0.25},
            {"id": "no_8am_after_correction", "type": "tool_not_called", "tool": "book_flight",
             "args_subset": {"flight_id": ["fl-den-8am"]}, "after_ms": 2500, "weight": 0.15},
            {"id": "final_confirms", "type": "final_response_contains",
             "any_of": ["bk-", "priya"], "weight": 0.2},
        ],
        "recovery": {"interrupt_at_ms": 2650,
                     "invalidated_calls": [{"tool": "book_flight",
                                            "args_subset": {"flight_id": ["fl-den-8am"]},
                                            "invalid_after_ms": 2500}],
                     "required_state_after_interrupt": {
                         "slots.passenger_name": ["priya"],
                         "slots.flight_id": ["fl-den-2pm"]}},
        "latency": latency(1, 2, 3),
        "safety": {"max_fillers": 4},
    },
}


ALL = [CONF_01, CONF_02, CONF_03, CONF_04, CONF_05, CONF_06, CONF_07, CONF_08,
       CONF_09, CONF_10, CONF_11, CONF_12, CONF_13, CONF_14, CONF_15, CONF_16,
       CONF_17, CONF_18, CONF_19, CONF_20, CONF_21, CONF_22, CONF_23]


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    for scenario in ALL:
        path = os.path.join(OUT_DIR, scenario["scenario_id"] + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(scenario, fh, indent=2)
            fh.write("\n")
        print("wrote " + os.path.relpath(path, ROOT))
    print(str(len(ALL)) + " conformance scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
