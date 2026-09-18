"""Phase 2 (`docs/post_v4_implementation_plan.md`) audio/ASR tests: N-06,
M-01, M-10 from the blueprint's scenario catalog.

Same pattern as every other version's tests: `ScriptedProvider` mocks the
ASR worker exactly like it mocks Interpreter/Planner/Composer/Vision — no
test here calls a real speech model. `kernel/audio.py`'s module docstring
documents the one real scope trim (simplified `auto`-mode dedupe, no
timer-window machinery) these tests are written against.
"""

from __future__ import annotations

from conftest import (
    FAST_WORKER_LATENCY,
    SEARCH_FLIGHTS_TOOL,
    audio_clip_event,
    chunk_event,
    drain,
    eot_event,
    manifest_event,
)

from prism_rt.config import Config
from prism_rt.model.types import ActionType, GoalStatus, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider


def audio_config(**overrides) -> Config:
    overrides.setdefault("asr_enabled", True)
    return Config(
        transitive_invalidation=overrides.pop("transitive_invalidation", False),
        settle_barrier_enabled=overrides.pop("settle_barrier_enabled", False),
        absence_read_sets=overrides.pop("absence_read_sets", False),
        claim_grades_enabled=overrides.pop("claim_grades_enabled", False),
        rebinder_enabled=overrides.pop("rebinder_enabled", False),
        vision_enabled=overrides.pop("vision_enabled", False),
        reference_bound_identifiers=overrides.pop("reference_bound_identifiers", False),
        **overrides,
    )


def new_harness(config, tools=None, provider=None, **kwargs):
    latency = dict(FAST_WORKER_LATENCY)
    latency[JobKind.ASR] = kwargs.pop("asr_latency_us", 10_000)
    kwargs.setdefault("worker_latency_us", latency)
    return SimHarness(config, seed=1, tools=tools, provider=provider, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def flat_plan(tool: str, *, kind: str = "read", param: str = "destination") -> dict:
    return {"steps": [{"local_id": "s1", "tool": tool, "kind": kind, "bindings": {param: {"type": "fact", "key": f"slot.$G.{param}"}}, "after": []}]}


# --- N-06: audio-only request completes end-to-end -----------------------


def test_n06_audio_only_request_completes_end_to_end():
    config = audio_config(audio_mode="audio_only", asr_closes_turn=True)
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found flights to Pune.", "claims": ["result:s1"]})
    provider.register("asr", "o-0001", {"segments": [{"text": "Find flights to Pune", "offset_us": 0, "end_us": 900_000}], "end_of_utterance": True})

    h = new_harness(config, tools={"search_flights": {"latency_ms": 50, "response": {"flight_id": "AI-1"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    # No text_chunk/end_of_turn anywhere in this scenario — audio is the
    # only input, and the turn must still close and complete on its own.
    h.send(100_000, [audio_clip_event("clip-1")])
    actions = drain(h, 2_000_000)

    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.text == "Found flights to Pune."
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED
    obs = h.store.evidence.observations()[0]
    assert obs.modality == "audio" and obs.asr_done is True
    assert_clean(h)


def test_transcript_primary_mode_never_dispatches_asr():
    config = audio_config(audio_mode="transcript_primary")
    provider = ScriptedProvider()
    h = new_harness(config, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [audio_clip_event("clip-1")])
    drain(h, 500_000, stop_on_final=False)
    obs = h.store.evidence.observations()[0]
    assert obs.asr_job_id is None and obs.asr_done is False  # stored, never analyzed
    assert_clean(h)


# --- M-01: audio/transcript disagreement ----------------------------------


def test_m01_audio_transcript_disagreement_discards_asr_in_favor_of_text():
    config = audio_config(audio_mode="auto")
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found flights to Pune.", "claims": ["result:s1"]})
    # The ASR guess is deliberately a *different* destination — if it were
    # wrongly appended alongside the real transcript, no interpret rule
    # would match the combined text (LookupError -> dropped -> no FINAL),
    # so a passing FINAL here is itself proof the ASR text never landed.
    provider.register("asr", "o-0001", {"segments": [{"text": "Find flights to Mumbai", "offset_us": 0, "end_us": 900_000}], "end_of_utterance": True})

    # ASR latency long enough that it resolves *after* the real text_chunk
    # has already opened the turn with content.
    h = new_harness(
        config,
        tools={"search_flights": {"latency_ms": 50, "response": {"flight_id": "AI-1"}}},
        provider=provider,
        asr_latency_us=100_000,
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(50_000, [audio_clip_event("clip-1")])  # ASR dispatched ~50_000, resolves ~150_000
    h.send(100_000, [chunk_event("Find flights to Pune")])  # opens the turn first
    h.advance(150_000)  # ASR result lands while the turn is still open with real content -> discarded
    h.send(160_000, [eot_event()])
    actions = drain(h, 2_000_000)

    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.text == "Found flights to Pune."
    assert h.store.facts.get("slot.g-0001.destination").value == "Pune"
    disagreement = h.store.facts.get("asr.o-0001.disagreement")
    assert disagreement is not None and disagreement.value is True
    assert_clean(h)


# --- M-10: audio self-repair preserved verbatim ---------------------------


def test_m10_audio_self_repair_preserved_verbatim():
    config = audio_config(audio_mode="audio_only", asr_closes_turn=True)
    repaired_text = "Find flights to Pune, sorry, i mean Mumbai"
    provider = ScriptedProvider()
    # Registered on the *exact* disfluent text — if anything stripped or
    # normalized "sorry, i mean", this substring match would fail and no
    # interpretation would ever be dispatched correctly.
    provider.register(
        "interpret",
        repaired_text,
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]},
    )
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found flights to Mumbai.", "claims": ["result:s1"]})
    provider.register("asr", "o-0001", {"segments": [{"text": repaired_text, "offset_us": 0, "end_us": 900_000}], "end_of_utterance": True})

    h = new_harness(config, tools={"search_flights": {"latency_ms": 50, "response": {"flight_id": "AI-9"}}}, provider=provider)
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [audio_clip_event("clip-1")])
    actions = drain(h, 2_000_000)

    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert h.store.facts.get("slot.g-0001.destination").value == "Mumbai"
    assert_clean(h)


# --- replay identity (C1) --------------------------------------------------


def test_audio_replay_identity():
    def run_once() -> list[dict]:
        config = audio_config(audio_mode="audio_only", asr_closes_turn=True)
        provider = ScriptedProvider()
        provider.register(
            "interpret",
            "Find flights to Pune",
            {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
        )
        provider.register("plan", "search_flights", flat_plan("search_flights"))
        provider.register("compose", "s1", {"text": "Found flights to Pune.", "claims": ["result:s1"]})
        provider.register("asr", "o-0001", {"segments": [{"text": "Find flights to Pune", "offset_us": 0, "end_us": 900_000}], "end_of_utterance": True})

        h = new_harness(config, tools={"search_flights": {"latency_ms": 50, "response": {"flight_id": "AI-1"}}}, provider=provider)
        h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
        h.send(100_000, [audio_clip_event("clip-1")])
        drain(h, 2_000_000)
        return h.log.records

    violations = TraceChecker().check_replay(run_once, times=5)
    assert violations == []
