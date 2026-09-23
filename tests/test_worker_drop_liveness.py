"""Regression tests for dropped VISION/ASR jobs leaving their scheduler
bookkeeping stuck -- found while building M-09 (Phase 7), by direct
reproduction against the real kernel.

`kernel/reducers.py._apply_worker_result` drops a worker result that errored
or went stale (K4) before any job-kind-specific handler runs. That's correct
for INTERPRET/PLAN/COMPOSE (TaskStateMachine re-requests them from state on
its own), but VISION and ASR track "a job is in flight" on their own records
(`Question.pending_job_id`, `Observation.asr_job_id`) -- nothing ever cleared
those on a drop:
- VISION: the question stayed pending forever; even a new frame was never
  analyzed.
- ASR: the clip was never retried -- an audio-only utterance vanished
  silently after one failure (e.g. a live 429 surviving the provider's own
  retries), with no output at all.

A failure is simulated with a callable ScriptedProvider response that
raises; `workers/runner.py` reports that as `status="error"`, the same path a
real exhausted-retry provider error takes.
"""

from __future__ import annotations

from conftest import (
    FAST_WORKER_LATENCY,
    SEARCH_FLIGHTS_TOOL,
    audio_clip_event,
    chunk_event,
    drain,
    eot_event,
    frame_event,
    manifest_event,
)

from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

CHECK_STATUS_TOOL = {
    "name": "check_status",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"indicator_state": {"type": "string"}}, "required": ["indicator_state"]},
}


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def _failing_then(fail_times: int, response: dict):
    calls = {"n": 0}

    def respond(prompt: str) -> dict:
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise RuntimeError("simulated provider failure after its own retries")
        return dict(response)

    return respond, calls


def test_failed_vision_job_releases_question_so_new_frame_is_analyzed():
    respond, calls = _failing_then(1, {"claims": [{"name": "indicator_state", "value": "green", "confidence": "high"}]})
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "what is this light doing",
        {
            "act": "new_goal",
            "intent": "device_help",
            "visual_reference": "current_state",
            "visual_candidates": [{"name": "indicator_state", "description": "the indicator's state"}],
        },
    )
    provider.register(
        "plan",
        "goal_intent: device_help",
        {"steps": [{"local_id": "s1", "tool": "check_status", "kind": "read", "bindings": {"indicator_state": {"type": "fact", "key": "slot.$G.indicator_state"}}, "after": []}]},
    )
    provider.register("vision", "indicator_state", respond)
    provider.register("compose", "s1", {"text": "The light is green, all good.", "claims": ["result:s1"]})
    latency = dict(FAST_WORKER_LATENCY)
    latency[JobKind.VISION] = 10_000
    h = SimHarness(Config(vision_enabled=True), seed=1, provider=provider, worker_latency_us=latency, tools={"check_status": {"latency_ms": 50, "response": {"ok": True}}})
    h.send(0, [manifest_event([CHECK_STATUS_TOOL])])
    h.send(50_000, [frame_event("f1")])
    h.send(100_000, [chunk_event("what is this light doing")])
    h.send(150_000, [eot_event()])
    drain(h, 1_000_000, stop_on_final=False)

    question = h.store.evidence.all_questions()[0]
    assert question.pending_job_id is None  # released, not stuck on the failed job
    assert calls["n"] == 1  # the failed frame is not hot-retried against a failing provider

    h.send(1_010_000, [frame_event("f2")])  # new evidence re-opens analysis
    actions = drain(h, 3_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1 and finals[0].body.text == "The light is green, all good."
    assert calls["n"] == 2
    assert_clean(h)


def _audio_harness(fail_times: int):
    respond, calls = _failing_then(
        fail_times,
        {"segments": [{"text": "Find flights to Pune", "offset_us": 0, "end_us": 900_000}], "end_of_utterance": True},
    )
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register(
        "plan",
        "goal_intent: search_flights",
        {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]},
    )
    provider.register("compose", "s1", {"text": "Found flights to Pune.", "claims": ["result:s1"]})
    provider.register("asr", "o-0001", respond)
    latency = dict(FAST_WORKER_LATENCY)
    latency[JobKind.ASR] = 10_000
    h = SimHarness(
        Config(asr_enabled=True, audio_mode="audio_only", asr_closes_turn=True),
        seed=1,
        provider=provider,
        worker_latency_us=latency,
        tools={"search_flights": {"latency_ms": 50, "response": {"flight_id": "AI-1"}}},
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [audio_clip_event("clip-1")])
    return h, calls


def test_transient_asr_failure_is_retried_and_the_audio_request_still_completes():
    h, calls = _audio_harness(fail_times=1)
    actions = drain(h, 2_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1 and finals[0].body.text == "Found flights to Pune."
    assert calls["n"] == 2
    assert h.store.evidence.observations()[0].asr_done is True
    assert_clean(h)


def test_persistent_asr_failure_is_bounded_and_recorded_not_retried_forever():
    h, calls = _audio_harness(fail_times=10_000)
    drain(h, 2_000_000, stop_on_final=False)
    obs = h.store.evidence.observations()[0]
    max_attempts = h.config.max_read_retries + 1
    assert calls["n"] == max_attempts
    assert obs.asr_done is True and obs.asr_job_id is None
    failed = h.store.facts.get(f"asr.{obs.obs_id}.failed")
    assert failed is not None and failed.value == max_attempts
    assert_clean(h)
