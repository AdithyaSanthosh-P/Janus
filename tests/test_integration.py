"""Integration-surface tests: Phase A of `docs/post_v4_implementation_plan.md`.

Covers what verifying that plan found broken: the codec is now tolerant of
wire-format variance instead of raising (P-01, P-02), the watchdog salvage
path (T-07) is deterministic and directly testable despite its *trigger*
being real wall time, and `audio_clip` degrades to "observed, ignored"
instead of "unparseable". None of this depends on the real evaluation kit
— it's insurance against our own guessed wire format being wrong, and
against a real entry point that had never been exercised before this.
"""

from __future__ import annotations

from conftest import (
    FAST_WORKER_LATENCY,
    SEARCH_FLIGHTS_TOOL,
    chunk_event,
    drain,
    eot_event,
    frame_event,
    manifest_event,
)

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, GoalStatus
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

BOOK_FLIGHT_TOOL = {
    "name": "book_flight",
    "mutability": "state_changing",
    "parameters": {"type": "object", "properties": {"flight_id": {"type": "string"}}, "required": ["flight_id"]},
}


def new_harness(config=None, tools=None, provider=None, **kwargs):
    kwargs.setdefault("worker_latency_us", FAST_WORKER_LATENCY)
    return SimHarness(config or Config(), seed=1, tools=tools, provider=provider, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def watchdog_event(wall_elapsed_s: float = 105.0) -> dict:
    return {"type": "watchdog", "payload": {"wall_elapsed_s": wall_elapsed_s}}


def _search_provider() -> ScriptedProvider:
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register(
        "plan",
        "search_flights",
        {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]},
    )
    provider.register("compose", "s1", {"text": "Found flights to Pune.", "claims": ["result:s1"]})
    return provider


# --- T-07: watchdog salvage --------------------------------------------


def test_t07_watchdog_cancels_inflight_and_emits_honest_final():
    h = new_harness(tools={"search_flights": {"latency_ms": 5000, "response": {"flight_id": "AI-1"}}}, provider=_search_provider())
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 250_000, stop_on_final=False)

    call = next(c for c in h.store.call_ledger.all() if c.tool == "search_flights")
    assert call.status == CallStatus.IN_FLIGHT

    report = h.send(300_000, [watchdog_event()])
    actions = [er.action for er in report.emit_report.emitted]
    cancels = [a for a in actions if a.action_type == ActionType.CANCEL]
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(cancels) == 1
    assert cancels[0].body.target_call_id == call.call_id
    assert len(finals) == 1
    assert finals[0].body.task_completed is False
    assert "wasn't able to finish" in finals[0].body.text

    assert h.store.call_ledger.get(call.call_id).status == CallStatus.CANCEL_REQUESTED
    assert_clean(h)


def test_t07_watchdog_before_any_call_still_emits_honest_final():
    h = new_harness(tools={"search_flights": {"latency_ms": 5000, "response": {"flight_id": "AI-1"}}}, provider=_search_provider())
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    h.advance(160_000)  # interpretation resolves -> goal exists, task_state PLANNING
    assert h.store.goals.all()[0].status == GoalStatus.ACTIVE
    assert h.store.call_ledger.all() == []
    # Fire before PLAN resolves (10ms worker latency) -> no call exists yet.
    report = h.send(165_000, [watchdog_event()])
    finals = [er.action for er in report.emit_report.emitted if er.action.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is False
    assert_clean(h)


def test_t07_watchdog_blocks_future_writes():
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book flight AI-1",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True},
    )
    provider.register(
        "plan",
        "book_flight",
        {"steps": [{"local_id": "s1", "tool": "book_flight", "kind": "write", "bindings": {"flight_id": {"type": "fact", "key": "slot.$G.flight_id"}}, "after": []}]},
    )
    h = new_harness(tools={"book_flight": {"latency_ms": 50, "response": {"confirmation": "OK"}}}, provider=provider)
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book flight AI-1")])
    h.send(150_000, [eot_event()])
    # Fire before PLAN resolves (10ms worker latency) -> the write must
    # never be proposed, let alone admitted.
    h.send(160_000, [watchdog_event()])
    drain(h, 400_000, stop_on_final=False)

    all_actions = h.run_log.emitted_actions
    assert ActionType.TOOL_CALL not in [a.action_type for a in all_actions]
    assert h.store.call_ledger.all() == []
    assert_clean(h)


def test_t07_watchdog_does_not_clobber_a_final_ready_the_same_step():
    """A COMPOSE result and a watchdog event batched in the same step: the
    real FINAL wins (WORKER_RESULT is ordered before TIMER — model/types.py
    EventClass — so the reducer applies compose's text before the watchdog
    reducer checks whether text already exists)."""
    h = new_harness(tools={"search_flights": {"latency_ms": 10, "response": {"flight_id": "AI-1"}}}, provider=_search_provider())
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    h.advance(160_000)  # INTERPRET resolves -> PLANNING
    h.advance(170_000)  # PLAN resolves -> EXECUTING, search_flights call emitted
    h.advance(180_000)  # tool result consumed -> RESPONDING, COMPOSE dispatched
    goal = h.store.goals.all()[0]
    assert goal.task_state.value == "responding"

    # COMPOSE resolves at 190_000 (10ms latency from dispatch) — same step
    # as the watchdog event below, auto-delivered by SimHarness alongside it.
    report = h.send(190_000, [watchdog_event()])
    finals = [er.action for er in report.emit_report.emitted if er.action.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.text == "Found flights to Pune."
    assert finals[0].body.task_completed is True
    assert_clean(h)


# --- P-01/P-02: malformed input never ends a scenario --------------------


def test_p02_unknown_event_type_is_ignored_not_fatal():
    h = new_harness(provider=_search_provider())
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(50_000, [{"type": "some_future_event_type", "payload": {"whatever": 1}}])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 2_000_000)
    assert any(a.action_type == ActionType.FINAL for a in actions)
    assert_clean(h)


def test_p02_malformed_event_midstream_does_not_end_the_run():
    h = new_harness(provider=_search_provider())
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(50_000, [{"type": "text_chunk"}])  # missing required "text" -> dropped, not raised
    h.send(60_000, [{"type": "tool_result", "payload": {"status": "ok"}}])  # missing call_id -> dropped
    h.send(70_000, [{"type": "text_chunk", "payload": {"text": 12345}}])  # wrong type -> dropped
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 2_000_000)
    assert any(a.action_type == ActionType.FINAL for a in actions)
    assert_clean(h)


def test_p02_completely_malformed_raw_event_is_ignored_by_the_codec():
    """Codec-level: something that isn't even an event object at all must
    decode to nothing rather than raise — this is what makes a real
    harness's genuinely garbled event survivable at the point where it
    actually gets decoded (entry.py calls codec.decode directly)."""
    from prism_rt.adapters.codec import HarnessCodec

    codec = HarnessCodec()
    assert codec.decode("not even a dict", seq=1) == []
    assert codec.decode(None, seq=1) == []
    assert codec.decode(42, seq=1) == []
    assert codec.decode({"type": "text_chunk", "payload": "not a dict either"}, seq=1) == []


def test_p02_audio_clip_stored_not_fatal():
    h = new_harness(provider=_search_provider())
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(50_000, [{"type": "audio_clip", "payload": {"clip_id": "clip-1"}}])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 2_000_000)
    assert any(a.action_type == ActionType.FINAL for a in actions)
    assert len(h.store.evidence.observations()) == 1
    assert h.store.evidence.observations()[0].frame_id == "clip-1"
    assert_clean(h)


# --- format-variant dialects: same scenario, several plausible shapes ----


def test_format_variant_camelcase_and_ts_ms_and_flat_payload():
    h = new_harness(provider=_search_provider())
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    # ts_ms instead of ts_us, plus an entirely unrecognized extra field.
    h.send(0, [{"type": "text_chunk", "ts_ms": 100, "payload": {"text": "Find flights to Pune", "speaker": "user", "confidence": 0.94}}])
    h.send(0, [{"type": "end_of_turn", "ts_ms": 150, "payload": {}}])
    actions = drain(h, 2_000_000)
    assert any(a.action_type == ActionType.FINAL for a in actions)
    assert_clean(h)


def test_format_variant_field_aliases_and_flat_shape():
    h = new_harness(provider=_search_provider())
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    # "content" alias for "text", flat shape (no nested "payload" key).
    h.send(100_000, [{"type": "text_chunk", "content": "Find flights to Pune"}])
    h.send(150_000, [eot_event()])
    actions = drain(h, 2_000_000)
    assert any(a.action_type == ActionType.FINAL for a in actions)
    assert_clean(h)


def test_format_variant_field_aliases_decode_correctly():
    """Codec-level: the alias map resolves to the correct canonical field
    on the decoded payload, for every aliased event type."""
    from prism_rt.adapters.codec import HarnessCodec
    from prism_rt.model.events import AudioClipPayload, ManifestPayload, ToolResultPayload, VideoFramePayload, WorkerResultPayload

    codec = HarnessCodec()

    envs = codec.decode({"type": "tool_result", "ts_us": 0, "payload": {"id": "c-0001", "status": "ok", "result": {}}}, seq=1)
    assert len(envs) == 1 and isinstance(envs[0].payload, ToolResultPayload) and envs[0].payload.call_id == "c-0001"

    envs = codec.decode({"type": "worker_result", "ts_us": 0, "payload": {"id": "j-0001", "kind": "interpret", "status": "ok"}}, seq=1)
    assert len(envs) == 1 and isinstance(envs[0].payload, WorkerResultPayload) and envs[0].payload.job_id == "j-0001"

    envs = codec.decode({"type": "manifest", "ts_us": 0, "payload": {"manifest": [{"name": "x"}]}}, seq=1)
    assert len(envs) == 1 and isinstance(envs[0].payload, ManifestPayload) and envs[0].payload.tools == [{"name": "x"}]

    envs = codec.decode({"type": "frame", "ts_us": 0, "payload": {"id": "f1"}}, seq=1)  # "frame" -> video_frame type alias
    assert len(envs) == 1 and isinstance(envs[0].payload, VideoFramePayload) and envs[0].payload.frame_id == "f1"

    envs = codec.decode({"type": "audio", "ts_us": 0, "payload": {"id": "clip1"}}, seq=1)  # "audio" -> audio_clip type alias
    assert len(envs) == 1 and isinstance(envs[0].payload, AudioClipPayload) and envs[0].payload.clip_id == "clip1"
