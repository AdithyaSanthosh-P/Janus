"""Our own scenario templates, for chaos runs beyond the kit's three.

The kit's generator (harness/scenario_gen.py) varies seven cities across a
simple search, a search-then-interrupt, and an unseen read-only tool. A
perfect score there says nothing about paraphrases, retractions, topic
changes, chained bookings, corrections during a booking, or unseen
state-modifying tools - which is most of what WALKTHROUGH.md says the hidden
set contains.

Every template draws its city, name, phrasing and timing from pools that
appear nowhere in duet/ (the invariant tests forbid gazetteers there), and
its ground truth follows the kit's own conventions. Used by tools/chaos.py:

    python tools/chaos.py --n 120 --seed 11 --templates all
"""

from __future__ import annotations

import random
from typing import Dict, List

CITIES = ["Recife", "Porto", "Tucson", "Kyoto", "Nairobi", "Oslo", "Halifax",
          "Lagos", "Lima", "Accra", "Dakar", "Quito", "Hanoi", "Perth", "Bergen",
          "Tallinn", "Cusco", "Muscat", "Valletta", "Seville", "Leeds", "Graz",
          "Cork", "Santa Fe", "San Juan", "Buenos Aires", "Kuala Lumpur",
          "Cape Town", "Las Vegas", "New Orleans"]
NAMES = ["Priya", "Omar", "Mei", "Tomas", "Aisha", "Kenji", "Lucia", "Femi",
         "Ingrid", "Rahul"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _code(city: str) -> str:
    return "fl-" + "".join(c for c in city if c.isalpha())[:3].lower()


def _chunk(t, text, end=False):
    return {"timestamp_ms": t, "event_type": "user_speech_chunk",
            "payload": {"text": text, "end_of_turn": end}}


def _interrupt(t, text):
    return {"timestamp_ms": t, "event_type": "interruption", "payload": {"text": text}}


def _latency(*idx):
    return {"respond_to": [{"event_index": i, "full_credit_ms": 800,
                            "zero_credit_ms": 2500} for i in idx]}


# ---------------------------------------------------------------------------
SEARCH_PHRASES = [
    ("Can you find flights to ", "{city} for {day}?"),
    ("I need to get to ", "{city} by {day}."),
    ("Any seats to ", "{city} on {day}?"),
    ("{city}. {day}. ", "Two people."),
    ("Could you see what flies ", "to {city} on {day}?"),
    ("I want to, uh, go to, um, ", "{city} I think, {day}."),
    ("Sorry to bother you, but would you mind ", "checking flights to {city}?"),
    ("find me something going to ", "{lower} on {lowday}"),
    ("Is there anything heading to ", "{city} this {day}?"),
    ("What have you got for ", "{city}, {day}?"),
]


def gen_paraphrase_search(rng: random.Random, idx: int) -> Dict:
    city, day = rng.choice(CITIES), rng.choice(DAYS)
    first, second = rng.choice(SEARCH_PHRASES)
    fill = {"city": city, "day": day, "lower": city.lower(), "lowday": day.lower()}
    t_end = rng.randint(600, 1100)
    return {
        "scenario_id": "ours_paraphrase_%03d" % idx,
        "metadata": {"modality": "text", "difficulty": "L2",
                     "description": "Paraphrased search: " + (first + second).format(**fill)},
        "events": [_chunk(100, first.format(**fill)),
                   _chunk(t_end, second.format(**fill), end=True)],
        "ground_truth": {
            "checkpoints": [
                {"id": "searched", "type": "tool_called", "tool": "flight_search",
                 "args_subset": {"destination": [city.lower()]}, "must_complete": True,
                 "weight": 0.5},
                {"id": "grounded", "type": "final_response_contains",
                 "any_of": [city.lower(), _code(city)], "weight": 0.25},
                {"id": "state", "type": "state_snapshot", "path": "slots.destination",
                 "any_of": [city.lower()], "weight": 0.25}],
            "latency": _latency(1)},
    }


RETRACTIONS = ["Actually, never mind.", "Forget it, thanks.", "Never mind, don't bother.",
               "Actually cancel that.", "No, leave it.", "Wait, never mind. Forget it."]


def gen_retraction(rng: random.Random, idx: int) -> Dict:
    city, day = rng.choice(CITIES), rng.choice(DAYS)
    t_end = rng.randint(600, 1000)
    t_int = t_end + rng.randint(500, 1000)
    return {
        "scenario_id": "ours_retraction_%03d" % idx,
        "metadata": {"modality": "text", "difficulty": "L2", "description": "Retraction mid-search."},
        "tool_overrides": {"flight_search": [{"call_index": 0, "delay_ms": 2400}]},
        "events": [_chunk(100, "Can you find flights to "),
                   _chunk(t_end, "%s on %s?" % (city, day), end=True),
                   _interrupt(t_int, rng.choice(RETRACTIONS))],
        "ground_truth": {
            "checkpoints": [
                {"id": "no_search_after", "type": "tool_not_called", "tool": "flight_search",
                 "after_ms": t_int, "weight": 0.3},
                {"id": "acknowledged", "type": "spoken_contains",
                 "any_of": ["no problem", "okay", "sure", "understood", "fine", "dropping",
                            "leave it", "stopping", "cancel"],
                 "after_ms": t_int, "before_ms": t_int + 1500, "weight": 0.3},
                {"id": "no_results_spoken", "type": "spoken_not_contains",
                 "any_of": ["i found", _code(city), "departing"], "after_ms": t_int,
                 "weight": 0.4}],
            "recovery": {"interrupt_at_ms": t_int,
                         "invalidated_calls": [{"tool": "flight_search",
                                                "args_subset": {"destination": [city.lower()]},
                                                "invalid_after_ms": t_int}]},
            "latency": _latency(1, 2)},
    }


PIVOTS = [("Forget the flight. My TV is showing a blinking red light.", ["led", "blinking", "light", "status"]),
          ("Never mind the flight, what does the drum error code on my washer mean?", ["drum", "error", "code"]),
          ("Scratch the trip - my phone won't charge through its port.", ["charging", "port", "page"])]


def gen_intent_change(rng: random.Random, idx: int) -> Dict:
    city = rng.choice(CITIES)
    text, needles = rng.choice(PIVOTS)
    t_end = rng.randint(600, 1000)
    t_int = t_end + rng.randint(500, 900)
    return {
        "scenario_id": "ours_pivot_%03d" % idx,
        "metadata": {"modality": "text", "difficulty": "L3", "description": "Full intent change."},
        "tool_overrides": {"flight_search": [{"call_index": 0, "delay_ms": 2400}]},
        "events": [_chunk(100, "I need a flight to "), _chunk(t_end, city + " please.", end=True),
                   _interrupt(t_int, text)],
        "ground_truth": {
            "checkpoints": [
                {"id": "pivoted", "type": "tool_called", "tool": "lookup_manual",
                 "args_present": ["query"], "after_ms": t_int, "must_complete": True,
                 "weight": 0.4},
                {"id": "no_flight_work", "type": "tool_not_called", "tool": "flight_search",
                 "after_ms": t_int, "weight": 0.2},
                {"id": "addresses_new_problem", "type": "final_response_contains",
                 "any_of": needles + ["manual"], "weight": 0.2},
                {"id": "old_city_not_leaked", "type": "spoken_not_contains",
                 "any_of": [_code(city)], "after_ms": t_int, "weight": 0.2}],
            "recovery": {"interrupt_at_ms": t_int,
                         "invalidated_calls": [{"tool": "flight_search",
                                                "args_subset": {"destination": [city.lower()]},
                                                "invalid_after_ms": t_int}]},
            "latency": _latency(1, 2)},
    }


SELECTORS = [("the 8 AM one", "8am"), ("the 2 PM one", "2pm"), ("the cheapest one", "2pm"),
             ("the earliest one", "8am"), ("the afternoon flight", "2pm")]


def gen_chained_booking(rng: random.Random, idx: int) -> Dict:
    city, name = rng.choice(CITIES), rng.choice(NAMES)
    phrase, slot = rng.choice(SELECTORS)
    fid = "fl-" + "".join(c for c in city if c.isalpha())[:3].lower() + "-" + slot
    t_end = rng.randint(700, 1100)
    return {
        "scenario_id": "ours_chain_%03d" % idx,
        "metadata": {"modality": "text", "difficulty": "L2",
                     "description": "Search then book %s for %s." % (phrase, name)},
        "events": [_chunk(100, "Find a flight to %s and " % city),
                   _chunk(t_end, "book %s for %s." % (phrase, name), end=True)],
        "ground_truth": {
            "checkpoints": [
                {"id": "searched", "type": "tool_called", "tool": "flight_search",
                 "args_subset": {"destination": [city.lower()]}, "must_complete": True,
                 "weight": 0.3},
                {"id": "booked_right_one", "type": "tool_called", "tool": "book_flight",
                 "args_subset": {"flight_id": [fid], "passenger_name": [name.lower()]},
                 "must_complete": True, "weight": 0.45},
                {"id": "confirms", "type": "final_response_contains",
                 "any_of": ["bk-", "book"], "weight": 0.25}],
            "latency": _latency(1),
            "safety": {"max_fillers": 3}},
    }


UNSEEN_COMMITS = [
    {"name": "reserve_table",
     "schema": {"kind": "state_modifying", "delay_range_ms": [800, 1400],
                "description": "Reserve a table at a restaurant.",
                "args": {"restaurant": {"type": "string", "required": True,
                                        "description": "Restaurant name."},
                         "party_size": {"type": "number", "required": True,
                                        "description": "How many people."}},
                "default_result": {"reservation_id": "RS-0042"}},
     "utterance": ("Could you get us a table at {venue} ", "for {word}, around 7 PM?"),
     "check": lambda venue, n: {"restaurant": [venue.lower()], "party_size": [n, str(n)]},
     "needle": "rs-0042"},
    {"name": "order_supplies",
     "schema": {"kind": "state_modifying", "delay_range_ms": [700, 1300],
                "description": "Place an order for office supplies.",
                "args": {"item": {"type": "string", "required": True,
                                  "description": "What to order."},
                         "quantity": {"type": "number", "required": True,
                                      "description": "How many units."}},
                "default_result": {"order_id": "OR-7710"}},
     "utterance": ("Please order ", "{word} boxes of printer paper."),
     "check": lambda venue, n: {"quantity": [n, str(n)]},
     "needle": "or-7710"},
]
VENUES = ["Casa Lume", "The Olive Room", "Kinfolk", "Bistro Verde", "Saffron House"]
WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def gen_unseen_commit(rng: random.Random, idx: int) -> Dict:
    tool = rng.choice(UNSEEN_COMMITS)
    venue = rng.choice(VENUES)
    n = rng.choice(sorted(WORDS))
    first, second = tool["utterance"]
    t_end = rng.randint(700, 1100)
    return {
        "scenario_id": "ours_unseen_commit_%03d" % idx,
        "metadata": {"modality": "text", "difficulty": "L3",
                     "description": "Unseen state-modifying tool " + tool["name"]},
        "tool_manifest": {tool["name"]: tool["schema"]},
        "events": [_chunk(100, first.format(venue=venue, word=WORDS[n])),
                   _chunk(t_end, second.format(venue=venue, word=WORDS[n]), end=True)],
        "ground_truth": {
            "checkpoints": [
                {"id": "committed", "type": "tool_called", "tool": tool["name"],
                 "args_subset": tool["check"](venue, n), "must_complete": True,
                 "weight": 0.55},
                {"id": "not_forced_into_flights", "type": "tool_not_called",
                 "tool": "flight_search", "weight": 0.1},
                {"id": "grounded", "type": "final_response_contains",
                 "any_of": [tool["needle"]], "weight": 0.35}],
            "latency": _latency(1)},
    }


TEMPLATES = {
    "ours_paraphrase": gen_paraphrase_search,
    "ours_retraction": gen_retraction,
    "ours_pivot": gen_intent_change,
    "ours_chain": gen_chained_booking,
    "ours_unseen_commit": gen_unseen_commit,
}
