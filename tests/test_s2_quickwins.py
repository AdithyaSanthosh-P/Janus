"""S2 quick-win pack (`docs-personal/private-docs/win_plan_2026-09-27.md`
§6.2) -- Q1, Q5, Q6a, Q6b, Q7, Q8, Q10. Every flag defaults off (checked
by the untouched 328-test suite passing unchanged); these tests turn each
one on individually and prove the specific behavior it adds, plus a
flag-off negative control where the bug being fixed would otherwise
reproduce.

Q4's own two real, confirmed root-cause scenarios (housing_11/housing_13:
BACKCHANNEL/SMALLTALK/UNCLEAR with no active goal -> total silence) are
reproduced here directly rather than against the live model, since the
live decision logs saved under run_output/stall_debug_housing_11/ and
_13/ already pinned the exact fact-change shape (`session.pending_
interpretation_turn` retracted, nothing else) -- that shape *is* the
`gid is None` branch this file's Q6a tests exercise.
"""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.kernel.interpret_apply import canonicalize_spoken_id
from prism_rt.model.types import ActionType, JobKind
from prism_rt.observability import speechlint
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

SEARCH_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"destination": {"type": "string"}}, "required": ["destination"]},
}

BOOK_TOOL = {
    "name": "book_flight",
    "mutability": "state_changing",
    "parameters": {
        "type": "object",
        "properties": {"destination": {"type": "string"}, "confirmation_code": {"type": "string"}},
        "required": ["destination"],
    },
}

FAST_LATENCY = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000, JobKind.EXTRACT: 10_000}


def flat_plan(tool: str, *, param: str = "destination") -> dict:
    return {"steps": [{"local_id": "s1", "tool": tool, "kind": "read", "bindings": {param: {"type": "fact", "key": f"slot.$G.{param}"}}, "after": []}]}


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


# ---------------------------------------------------------------------------
# Q1: spoken-ID canonicaliser
# ---------------------------------------------------------------------------

def test_canonicalize_spoken_id_joins_single_char_tokens():
    assert canonicalize_spoken_id("X-Y-Z-8-8") == "XYZ88"
    assert canonicalize_spoken_id("A, B, C") == "ABC"


def test_canonicalize_spoken_id_leaves_structured_codes_alone():
    # A multi-character token anywhere means this is a real structured
    # code with separators, not a spelled-out identifier.
    assert canonicalize_spoken_id("FL-DEN-8AM") == "FL-DEN-8AM"
    assert canonicalize_spoken_id("Pune") == "Pune"


def test_canonicalize_spoken_id_ignores_non_strings():
    assert canonicalize_spoken_id(42) == 42
    assert canonicalize_spoken_id(None) is None


def test_normalize_spoken_ids_flag_applies_at_slot_write():
    config = Config(normalize_spoken_ids=True)
    provider = ScriptedProvider()
    provider.register(
        "interpret", "confirmation", {"act": "new_goal", "intent": "book_flight",
         "slot_deltas": [{"name": "confirmation_code", "scope": "goal", "op": "set", "value": "X-Y-Z-8-8"}]},
    )
    provider.register("plan", "book_flight", flat_plan("book_flight", param="confirmation_code"))
    provider.register("compose", "s1", {"text": "Done.", "claims": ["result:s1"]})
    h = SimHarness(config, seed=1, provider=provider, tools={"book_flight": {"latency_ms": 50, "response": {"ok": True}}}, worker_latency_us=FAST_LATENCY)
    h.send(0, [manifest_event([BOOK_TOOL])])
    h.send(100_000, [chunk_event("my confirmation code is X-Y-Z-8-8")])
    h.send(150_000, [eot_event()])
    drain(h, 500_000)
    fact = h.store.facts.by_prefix("slot.")
    values = [f.value for f in fact.values()]
    assert "XYZ88" in values
    assert_clean(h)


def test_normalize_spoken_ids_flag_off_leaves_value_verbatim():
    config = Config(normalize_spoken_ids=False)
    provider = ScriptedProvider()
    provider.register(
        "interpret", "confirmation", {"act": "new_goal", "intent": "book_flight",
         "slot_deltas": [{"name": "confirmation_code", "scope": "goal", "op": "set", "value": "X-Y-Z-8-8"}]},
    )
    provider.register("plan", "book_flight", flat_plan("book_flight", param="confirmation_code"))
    provider.register("compose", "s1", {"text": "Done.", "claims": ["result:s1"]})
    h = SimHarness(config, seed=1, provider=provider, tools={"book_flight": {"latency_ms": 50, "response": {"ok": True}}}, worker_latency_us=FAST_LATENCY)
    h.send(0, [manifest_event([BOOK_TOOL])])
    h.send(100_000, [chunk_event("my confirmation code is X-Y-Z-8-8")])
    h.send(150_000, [eot_event()])
    drain(h, 500_000)
    values = [f.value for f in h.store.facts.by_prefix("slot.").values()]
    assert "X-Y-Z-8-8" in values


# ---------------------------------------------------------------------------
# Q6a: never-silent UNCLEAR-with-no-goal (the confirmed housing_11/
# housing_13 root cause)
# ---------------------------------------------------------------------------

def test_never_silent_unclear_speaks_when_no_goal_exists():
    config = Config(never_silent_unclear_enabled=True)
    provider = ScriptedProvider()
    # Exactly the housing_11/housing_13 shape: the live model gave up and
    # classified a real (if disfluent) utterance as UNCLEAR, with no
    # active goal for it to attach to.
    provider.register("interpret", "well", {"act": "unclear"})
    h = SimHarness(config, seed=1, provider=provider, tools={}, worker_latency_us=FAST_LATENCY)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("Uh... well... i was thinking a 1-bedroom")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 400_000, stop_on_final=False)
    speaks = [a for a in actions if a.action_type == ActionType.SPEAK]
    assert len(speaks) == 1
    assert "didn't quite catch" in speaks[0].body.text
    assert_clean(h)


def test_never_silent_unclear_flag_off_stays_silent():
    """Negative control: flag off reproduces the exact silent-stall bug
    (Q4's own confirmed finding) -- zero output for the whole scenario."""
    config = Config(never_silent_unclear_enabled=False)
    provider = ScriptedProvider()
    provider.register("interpret", "well", {"act": "unclear"})
    h = SimHarness(config, seed=1, provider=provider, tools={}, worker_latency_us=FAST_LATENCY)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("Uh... well... i was thinking a 1-bedroom")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 400_000, stop_on_final=False)
    assert actions == []


def test_never_silent_unclear_does_not_fire_with_an_active_goal():
    """A BACKCHANNEL/SMALLTALK/UNCLEAR turn *while a goal is active* is
    unaffected -- only the no-goal case is a bug."""
    config = Config(never_silent_unclear_enabled=True)
    provider = ScriptedProvider()
    provider.register("interpret", "Find flights to Pune", {"act": "new_goal", "intent": "search_flights",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]})
    provider.register("interpret", "mm-hmm", {"act": "backchannel"})
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found flights.", "claims": ["result:s1"]})
    h = SimHarness(config, seed=1, provider=provider, tools={"search_flights": {"latency_ms": 2000, "response": {"flight_id": "AI-1"}}}, worker_latency_us=FAST_LATENCY)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 300_000, stop_on_final=False)
    h.send(h.clock.now_us() + 10_000, [chunk_event("mm-hmm")])
    r = h.send(h.clock.now_us() + 5_000, [eot_event()])
    rest = drain(h, h.clock.now_us() + 300_000, stop_on_final=False)
    all_actions = [er.action for er in r.emit_report.emitted] + rest
    unclear_speaks = [a for a in all_actions if a.action_type == ActionType.SPEAK and "didn't quite catch" in a.body.text]
    assert unclear_speaks == []
    assert_clean(h)


# ---------------------------------------------------------------------------
# Q6b: turn-stall salvage
# ---------------------------------------------------------------------------

def test_turn_stall_salvage_fires_when_a_goal_never_produces_final():
    """No 'plan' rule is registered for the goal's intent at all -- PLAN
    keeps erroring and re-dispatching forever (ScriptedProvider raises
    LookupError -> a status="error" WorkerResultPayload -> dropped ->
    re-requested next step), the same "stuck goal, zero progress"
    shape a genuinely hung live call would produce. turn_stall_salvage_ms
    must still force an honest FINAL."""
    config = Config(turn_stall_salvage_ms=2_000)
    provider = ScriptedProvider()
    provider.register("interpret", "Pune", {"act": "new_goal", "intent": "search_flights",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]})
    h = SimHarness(config, seed=1, provider=provider, tools={}, worker_latency_us=FAST_LATENCY)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 5_000_000, stop_on_final=False)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is False
    assert_clean(h)


def test_turn_stall_salvage_disabled_by_default():
    config = Config(turn_stall_salvage_ms=0)
    provider = ScriptedProvider()
    provider.register("interpret", "Pune", {"act": "new_goal", "intent": "search_flights",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]})
    h = SimHarness(config, seed=1, provider=provider, tools={}, worker_latency_us=FAST_LATENCY)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 5_000_000, stop_on_final=False)
    assert [a for a in actions if a.action_type == ActionType.FINAL] == []


def test_turn_stall_salvage_does_not_clobber_a_real_final():
    config = Config(turn_stall_salvage_ms=2_000)
    provider = ScriptedProvider()
    provider.register("interpret", "Pune", {"act": "new_goal", "intent": "search_flights",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]})
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found flights to Pune.", "claims": ["result:s1"]})
    h = SimHarness(config, seed=1, provider=provider, tools={"search_flights": {"latency_ms": 500, "response": {"flight_id": "AI-1"}}}, worker_latency_us=FAST_LATENCY)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 5_000_000, stop_on_final=False)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is True
    assert finals[0].body.text == "Found flights to Pune."
    assert_clean(h)


# ---------------------------------------------------------------------------
# Q7: echo ACK
# ---------------------------------------------------------------------------

def test_echo_ack_speaks_the_interpreter_supplied_phrase():
    config = Config(echo_ack_enabled=True)
    provider = ScriptedProvider()
    provider.register("interpret", "Pune", {"act": "new_goal", "intent": "search_flights",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
        "ack_phrase": "I'll search flights to Pune for you."})
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found flights.", "claims": ["result:s1"]})
    h = SimHarness(config, seed=1, provider=provider, tools={"search_flights": {"latency_ms": 2000, "response": {"flight_id": "AI-1"}}}, worker_latency_us=FAST_LATENCY)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 400_000, stop_on_final=False)
    speaks = [a for a in actions if a.action_type == ActionType.SPEAK]
    assert len(speaks) == 1
    assert speaks[0].body.text == "I'll search flights to Pune for you."
    assert_clean(h)


def test_echo_ack_falls_back_to_generic_when_interpreter_gives_none():
    config = Config(echo_ack_enabled=True)
    provider = ScriptedProvider()
    provider.register("interpret", "Pune", {"act": "new_goal", "intent": "search_flights",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]})
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found flights.", "claims": ["result:s1"]})
    h = SimHarness(config, seed=1, provider=provider, tools={"search_flights": {"latency_ms": 2000, "response": {"flight_id": "AI-1"}}}, worker_latency_us=FAST_LATENCY)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 400_000, stop_on_final=False)
    speaks = [a for a in actions if a.action_type == ActionType.SPEAK]
    assert len(speaks) == 1
    assert speaks[0].body.text == "Got it, working on it."


# ---------------------------------------------------------------------------
# Q8: SpeechLint
# ---------------------------------------------------------------------------

def test_speechlint_humanizes_leaked_jargon():
    result = speechlint.lint("What should indicator_state be?", "clarify")
    assert result.text == "What should indicator state be?"
    assert any(v.startswith("jargon:") for v in result.violations)


def test_speechlint_flags_internal_mechanism_words():
    result = speechlint.lint("Step 1 was successful, the effect is confirmed.", "final")
    assert any(v.startswith("internal_jargon:") for v in result.violations)


def test_speechlint_rejects_premature_completion_claim_in_ack_only():
    ack = speechlint.lint("Your flight has been booked.", "ack")
    assert ack.text is None
    final = speechlint.lint("Your flight has been booked.", "final")
    assert final.text == "Your flight has been booked."


def test_speechlint_leaves_clean_text_untouched():
    result = speechlint.lint("Got it, working on it.", "ack")
    assert result.text == "Got it, working on it."
    assert result.violations == ()


def test_speechlint_wired_into_emission_humanizes_a_clarify():
    config = Config(speechlint_enabled=True)
    provider = ScriptedProvider()
    provider.register("interpret", "Delhi", {"act": "new_goal", "intent": "search_flights", "slot_deltas": []})
    provider.register("plan", "search_flights", flat_plan("search_flights", param="indicator_state"))
    h = SimHarness(config, seed=1, provider=provider, tools={}, worker_latency_us=FAST_LATENCY)
    h.send(0, [manifest_event([{
        "name": "search_flights", "mutability": "read_only",
        "parameters": {"type": "object", "properties": {"indicator_state": {"type": "string"}}, "required": ["indicator_state"]},
    }])])
    h.send(100_000, [chunk_event("what's going on with Delhi")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 400_000, stop_on_final=False)
    clarifies = [a for a in actions if a.action_type == ActionType.CLARIFY]
    assert len(clarifies) == 1
    assert "indicator_state" not in clarifies[0].body.text
    assert "indicator state" in clarifies[0].body.text


# ---------------------------------------------------------------------------
# Q5: re-extract before clarifying
# ---------------------------------------------------------------------------

def test_clarify_reextract_finds_a_value_and_never_asks():
    config = Config(clarify_reextract_enabled=True)
    provider = ScriptedProvider()
    provider.register("interpret", "somewhere warm", {"act": "new_goal", "intent": "search_flights", "slot_deltas": []})
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("extract", "somewhere warm", {"value": "Goa"})
    provider.register("compose", "s1", {"text": "Found flights to Goa.", "claims": ["result:s1"]})
    h = SimHarness(config, seed=1, provider=provider, tools={"search_flights": {"latency_ms": 500, "response": {"flight_id": "AI-1"}}}, worker_latency_us=FAST_LATENCY)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("I want to go somewhere warm")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 1_500_000, stop_on_final=False)
    clarifies = [a for a in actions if a.action_type == ActionType.CLARIFY]
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert clarifies == []
    assert len(finals) == 1
    values = [f.value for f in h.store.facts.by_prefix("slot.").values()]
    assert "Goa" in values
    assert_clean(h)


def test_clarify_reextract_falls_through_to_ordinary_clarify_on_null():
    config = Config(clarify_reextract_enabled=True)
    provider = ScriptedProvider()
    provider.register("interpret", "book me a flight", {"act": "new_goal", "intent": "search_flights", "slot_deltas": []})
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("extract", "book me a flight", {"value": None})
    h = SimHarness(config, seed=1, provider=provider, tools={}, worker_latency_us=FAST_LATENCY)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("book me a flight")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 700_000, stop_on_final=False)
    clarifies = [a for a in actions if a.action_type == ActionType.CLARIFY]
    assert len(clarifies) == 1
    assert "destination" in clarifies[0].body.text
    assert_clean(h)


def test_clarify_reextract_disabled_by_default_asks_immediately():
    config = Config(clarify_reextract_enabled=False)
    provider = ScriptedProvider()
    provider.register("interpret", "book me a flight", {"act": "new_goal", "intent": "search_flights", "slot_deltas": []})
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    h = SimHarness(config, seed=1, provider=provider, tools={}, worker_latency_us=FAST_LATENCY)
    h.send(0, [manifest_event([SEARCH_TOOL])])
    h.send(100_000, [chunk_event("book me a flight")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 400_000, stop_on_final=False)
    clarifies = [a for a in actions if a.action_type == ActionType.CLARIFY]
    assert len(clarifies) == 1


# ---------------------------------------------------------------------------
# Q10: segmented transcript splitting (pure function)
# ---------------------------------------------------------------------------

def test_segment_transcript_splits_on_pause_markers():
    from prism_rt.adapters.transcript_segmenter import segment_transcript

    pieces = segment_transcript("Uh... well... i was thinking a 1-bedroom under 1200... but actually, no wait — 2-bedroom.")
    assert pieces == [
        "Uh",
        "well",
        "i was thinking a 1-bedroom under 1200",
        "but actually",
        "no wait",
        "2-bedroom.",
    ]


def test_segment_transcript_no_markers_returns_whole_string():
    from prism_rt.adapters.transcript_segmenter import segment_transcript

    assert segment_transcript("Find flights to Pune") == ["Find flights to Pune"]


# ---------------------------------------------------------------------------
# Replay identity with the full S2 pack on
# ---------------------------------------------------------------------------

def test_s2_pack_replay_identity():
    def run_once() -> list[dict]:
        config = Config(
            normalize_spoken_ids=True, strict_value_rules_enabled=True,
            never_silent_unclear_enabled=True, turn_stall_salvage_ms=5_000,
            clarify_reextract_enabled=True, speechlint_enabled=True, echo_ack_enabled=True,
        )
        provider = ScriptedProvider()
        provider.register("interpret", "Pune", {"act": "new_goal", "intent": "search_flights",
            "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
            "ack_phrase": "I'll search flights to Pune."})
        provider.register("plan", "search_flights", flat_plan("search_flights"))
        provider.register("compose", "s1", {"text": "Found flights.", "claims": ["result:s1"]})
        h = SimHarness(config, seed=1, provider=provider, tools={"search_flights": {"latency_ms": 300, "response": {"flight_id": "AI-1"}}}, worker_latency_us=FAST_LATENCY)
        h.send(0, [manifest_event([SEARCH_TOOL])])
        h.send(100_000, [chunk_event("Find flights to Pune")])
        h.send(150_000, [eot_event()])
        drain(h, 700_000)
        assert_clean(h)
        return [
            {"step_no": r.step_no, "emitted": [(er.action.action_type.value, er.action.body.text if hasattr(er.action.body, "text") else None) for er in r.emit_report.emitted]}
            for r in h.run_log.reports
        ]

    first = run_once()
    second = run_once()
    assert first == second
