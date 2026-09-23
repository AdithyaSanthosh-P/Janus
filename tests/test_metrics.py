"""T-04 (`docs/theme05_implementation_blueprint.md` §10.3, line 2520):
`observability/metrics.py`'s deterministic aggregation over a run's
`StepReport` trace.

Two fixture styles, matching this project's own established split:
- Full `SimHarness` scenarios (via `conftest`'s helpers) for realistic
  "normal", "interruption/cancellation", and "multiple turns" cases --
  the same style every acceptance test in this suite already uses.
- Hand-built, minimal `StepReport`-shaped fixtures (same pattern
  `tests/test_checker_cs03.py`/`test_checker_cs27.py` established) for
  precise control over empty traces, miss cases, and percentile math.

The `test_ttfs_matches_the_documented_speculative_interpretation_numbers`
test is a real cross-check, not just internal self-consistency: it
reproduces the exact scenario `docs/measurements.md`'s Phase 6 entry
describes by hand ("INTERPRET latency 300ms, chunk spacing 150ms... Flag
off: 300,000 µs... Flag on: 0 µs") and asserts this module's `ttfs()`
reproduces those exact, already-published numbers independently.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from conftest import SEARCH_FLIGHTS_TOOL, chunk_event, drain, eot_event, interruption_event, manifest_event, tool_result_event

from prism_rt.config import Config
from prism_rt.kernel.emission import EmittedRecord, EmitReport
from prism_rt.model.actions import CancelBody, FinalBody, SpeakBody, ToolCallBody
from prism_rt.model.events import Envelope
from prism_rt.model.types import EMPTY_READ_SET, ActionType, EventClass, JobKind
from prism_rt.observability import metrics
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

# --- hand-built StepReport fixtures (unit-level control) --------------------


def _env(payload_type: str, ts_us: int, event_class: EventClass, *, seq: int = 0, payload=None) -> Envelope:
    return Envelope(ts_us=ts_us, event_class=event_class, seq=seq, event_id=f"e{seq}-{ts_us}", payload_type=payload_type, payload=payload)


def _action(action_type: ActionType, ts_us: int, body) -> SimpleNamespace:
    return SimpleNamespace(action_type=action_type, ts_us=ts_us, body=body)


def _report(step_no: int, *, batch=(), emitted=(), invalidated_call_ids=()) -> SimpleNamespace:
    return SimpleNamespace(
        step_no=step_no,
        batch=tuple(batch),
        invalidation=SimpleNamespace(invalidated_call_ids=tuple(invalidated_call_ids)),
        emit_report=EmitReport(emitted=tuple(EmittedRecord(action=a) for a in emitted), rejected=()),
    )


# --- empty trace: every function must handle it without error ---------------


def test_every_metric_handles_an_empty_trace():
    assert metrics.ttfs([]) == metrics.TTFSResult(
        per_trigger={
            t: metrics.TTFSTriggerResult(0, 0, metrics.LatencyStats(0, None, None, None))
            for t in ("end_of_turn", "interruption", "first_chunk")
        }
    )
    assert metrics.tool_latency([]) == {}
    assert metrics.interruption_to_cancellation_latency([]) == metrics.CancelLatencyResult(
        metrics.LatencyStats(0, None, None, None), 0
    )
    assert metrics.end_to_end_latency([]) == []
    assert metrics.substantive_actions([]) == []


# --- percentile / stats correctness (known, hand-computed inputs) -----------


def test_percentile_nearest_rank_on_a_known_dataset():
    # 5 sorted samples: nearest-rank p50 -> ceil(0.5*5)=3rd (1-indexed) = 30;
    # p95 -> ceil(0.95*5)=ceil(4.75)=5th = 50.
    stats = metrics._stats([50, 10, 40, 20, 30])
    assert stats == metrics.LatencyStats(count=5, p50=30, p95=50, max=50)


def test_stats_single_sample():
    assert metrics._stats([42]) == metrics.LatencyStats(count=1, p50=42, p95=42, max=42)


# --- substantive-action classification ---------------------------------------


def test_substantive_actions_classifies_correctly():
    reports = [
        _report(
            1,
            emitted=[
                _action(ActionType.SPEAK, 100, SpeakBody(text="ok", kind="ack")),
                _action(ActionType.SPEAK, 100, SpeakBody(text="ok", kind="inform")),
                _action(ActionType.CLARIFY, 100, SpeakBody(text="which one?", kind="clarify")),
                _action(ActionType.FINAL, 100, FinalBody(text="done")),
                _action(ActionType.CANCEL, 100, CancelBody(target_call_id="c1", reason="x")),
                _action(ActionType.TOOL_CALL, 100, ToolCallBody(call_id="c1", tool_name="t", arguments={})),
            ],
        )
    ]
    kinds = sorted((a.action_type, a.kind) for a in metrics.substantive_actions(reports))
    assert kinds == sorted(
        [
            ("speak", "ack"),
            ("speak", "inform"),
            ("clarify", "clarify"),
            ("final", None),
        ]
    )
    # CANCEL and TOOL_CALL never count, regardless of anything else.
    assert all(a.action_type not in ("cancel", "tool_call") for a in metrics.substantive_actions(reports))


# --- TTFS: hand-built windowing edge cases ------------------------------------


def test_ttfs_finds_the_nearest_substantive_action_after_the_trigger():
    reports = [
        _report(1, batch=[_env("end_of_turn", 1000, EventClass.END_OF_TURN)]),
        _report(2, emitted=[_action(ActionType.SPEAK, 1500, SpeakBody(text="ok", kind="ack"))]),
        _report(3, emitted=[_action(ActionType.FINAL, 5000, FinalBody(text="done"))]),
    ]
    result = metrics.ttfs(reports)
    eot = result.per_trigger["end_of_turn"]
    assert eot.trigger_count == 1
    assert eot.miss_count == 0
    assert eot.stats.p50 == 500  # nearest one (ACK at 1500), not the FINAL at 5000


def test_ttfs_is_a_miss_when_no_substantive_action_follows_the_trigger():
    reports = [_report(1, batch=[_env("interruption", 1000, EventClass.INTERRUPTION)])]
    result = metrics.ttfs(reports)
    interruption = result.per_trigger["interruption"]
    assert interruption.trigger_count == 1
    assert interruption.miss_count == 1
    assert interruption.stats.count == 0


def test_ttfs_windows_by_the_next_trigger_of_the_same_type():
    """An action after the *second* end_of_turn must never count toward
    the *first* one's TTFS, even though it's numerically the nearest
    substantive action in the whole trace."""
    reports = [
        _report(1, batch=[_env("end_of_turn", 1000, EventClass.END_OF_TURN)]),
        _report(2, batch=[_env("end_of_turn", 2000, EventClass.END_OF_TURN)]),
        _report(3, emitted=[_action(ActionType.FINAL, 2100, FinalBody(text="done"))]),
    ]
    result = metrics.ttfs(reports)
    eot = result.per_trigger["end_of_turn"]
    assert eot.trigger_count == 2
    assert eot.miss_count == 1  # the first EOT has nothing before the second one
    assert eot.stats.count == 1
    assert eot.stats.p50 == 100  # only the second EOT's window catches the FINAL


def test_ttfs_zero_latency_same_step_promotion():
    """The exact shape `docs/measurements.md`'s Phase 6 entry measured by
    hand: a substantive action emitted in the *same step*, same `ts_us`,
    as its trigger -- 0, not a miss."""
    reports = [
        _report(
            1,
            batch=[_env("end_of_turn", 9000, EventClass.END_OF_TURN)],
            emitted=[_action(ActionType.FINAL, 9000, FinalBody(text="done"))],
        )
    ]
    eot = metrics.ttfs(reports).per_trigger["end_of_turn"]
    assert eot.stats.p50 == 0
    assert eot.miss_count == 0


# --- tool latency --------------------------------------------------------------


def test_tool_latency_matches_result_minus_emitted_ts():
    reports = [
        _report(1, emitted=[_action(ActionType.TOOL_CALL, 1000, ToolCallBody(call_id="c1", tool_name="search_flights", arguments={}))]),
        _report(2, batch=[_env("tool_result", 1400, EventClass.TOOL_RESULT, payload=SimpleNamespace(call_id="c1", status="ok"))]),
    ]
    result = metrics.tool_latency(reports)
    key = metrics.ToolLatencyKey(tool="search_flights", status="ok")
    assert result[key] == metrics.LatencyStats(count=1, p50=400, p95=400, max=400)


def test_tool_latency_groups_separately_by_status():
    reports = [
        _report(1, emitted=[
            _action(ActionType.TOOL_CALL, 0, ToolCallBody(call_id="c1", tool_name="t", arguments={})),
            _action(ActionType.TOOL_CALL, 0, ToolCallBody(call_id="c2", tool_name="t", arguments={})),
        ]),
        _report(2, batch=[
            _env("tool_result", 100, EventClass.TOOL_RESULT, seq=1, payload=SimpleNamespace(call_id="c1", status="ok")),
            _env("tool_result", 200, EventClass.TOOL_RESULT, seq=2, payload=SimpleNamespace(call_id="c2", status="error")),
        ]),
    ]
    result = metrics.tool_latency(reports)
    assert result[metrics.ToolLatencyKey("t", "ok")].max == 100
    assert result[metrics.ToolLatencyKey("t", "error")].max == 200


def test_tool_latency_ignores_a_result_for_a_call_never_dispatched():
    """Boundary case: a tool_result envelope whose call_id was never seen
    as a TOOL_CALL emission (malformed/incomplete trace) must be silently
    excluded, not crash or invent a latency."""
    reports = [_report(1, batch=[_env("tool_result", 100, EventClass.TOOL_RESULT, payload=SimpleNamespace(call_id="ghost", status="ok"))])]
    assert metrics.tool_latency(reports) == {}


# --- interruption-to-cancellation latency -------------------------------------


def test_cancel_latency_signal_anchored():
    reports = [
        _report(1, batch=[_env("interruption", 1000, EventClass.INTERRUPTION)]),
        _report(2, emitted=[_action(ActionType.CANCEL, 1300, CancelBody(target_call_id="c1", reason="x"))]),
    ]
    result = metrics.interruption_to_cancellation_latency(reports)
    assert result.signal_anchored == metrics.LatencyStats(count=1, p50=300, p95=300, max=300)


def test_cancel_with_no_preceding_interruption_excluded_from_signal_anchored_stat():
    reports = [_report(1, emitted=[_action(ActionType.CANCEL, 500, CancelBody(target_call_id="c1", reason="x"))])]
    result = metrics.interruption_to_cancellation_latency(reports)
    assert result.signal_anchored.count == 0


def test_invalidated_never_cancelled_safety_count():
    reports = [
        _report(1, invalidated_call_ids=["c1", "c2"], emitted=[_action(ActionType.CANCEL, 100, CancelBody(target_call_id="c1", reason="x"))]),
    ]
    result = metrics.interruption_to_cancellation_latency(reports)
    assert result.invalidated_never_cancelled_count == 1  # c2 invalidated, never cancelled


def test_invalidated_never_cancelled_count_is_zero_when_every_invalidation_is_cancelled():
    reports = [
        _report(1, invalidated_call_ids=["c1"], emitted=[_action(ActionType.CANCEL, 100, CancelBody(target_call_id="c1", reason="x"))]),
    ]
    assert metrics.interruption_to_cancellation_latency(reports).invalidated_never_cancelled_count == 0


# --- end-to-end latency ---------------------------------------------------------


def test_end_to_end_latency_both_definitions():
    reports = [
        _report(1, batch=[_env("text_chunk", 1000, EventClass.USER_CONTENT)]),
        _report(2, batch=[_env("end_of_turn", 2000, EventClass.END_OF_TURN)]),
        _report(3, emitted=[_action(ActionType.FINAL, 2500, FinalBody(text="done"))]),
    ]
    results = metrics.end_to_end_latency(reports)
    assert len(results) == 1
    assert results[0].since_first_chunk_us == 1500
    assert results[0].since_end_of_turn_us == 500


def test_end_to_end_latency_none_when_no_prior_chunk_or_eot():
    reports = [_report(1, emitted=[_action(ActionType.FINAL, 100, FinalBody(text="done"))])]
    results = metrics.end_to_end_latency(reports)
    assert results[0].since_first_chunk_us is None
    assert results[0].since_end_of_turn_us is None


def test_end_to_end_latency_multiple_finals_each_anchor_to_their_own_preceding_turn():
    reports = [
        _report(1, batch=[_env("text_chunk", 0, EventClass.USER_CONTENT)]),
        _report(2, batch=[_env("end_of_turn", 100, EventClass.END_OF_TURN)]),
        _report(3, emitted=[_action(ActionType.FINAL, 200, FinalBody(text="first"))]),
        _report(4, batch=[_env("text_chunk", 1000, EventClass.USER_CONTENT)]),
        _report(5, batch=[_env("end_of_turn", 1100, EventClass.END_OF_TURN)]),
        _report(6, emitted=[_action(ActionType.FINAL, 1200, FinalBody(text="second"))]),
    ]
    results = metrics.end_to_end_latency(reports)
    assert len(results) == 2
    assert results[0].since_end_of_turn_us == 100
    assert results[1].since_end_of_turn_us == 100  # anchored to the *second* turn's own EOT, not the first


# --- non-mutation and reproducibility ----------------------------------------


def test_aggregation_does_not_mutate_its_input():
    reports = [
        _report(1, batch=[_env("end_of_turn", 1000, EventClass.END_OF_TURN)], invalidated_call_ids=["c1"]),
        _report(2, emitted=[_action(ActionType.FINAL, 1500, FinalBody(text="done"))]),
    ]
    before = [replace(r) if hasattr(r, "__dataclass_fields__") else (r.step_no, tuple(r.batch), tuple(r.emit_report.emitted)) for r in reports]

    metrics.ttfs(reports)
    metrics.tool_latency(reports)
    metrics.interruption_to_cancellation_latency(reports)
    metrics.end_to_end_latency(reports)
    metrics.substantive_actions(reports)

    after = [(r.step_no, tuple(r.batch), tuple(r.emit_report.emitted)) for r in reports]
    assert after == [(r.step_no, tuple(r.batch), tuple(r.emit_report.emitted)) for r in reports]
    assert len(reports) == 2  # nothing appended/removed


def test_repeated_aggregation_is_identical():
    reports = [
        _report(1, batch=[_env("end_of_turn", 1000, EventClass.END_OF_TURN), _env("interruption", 1000, EventClass.INTERRUPTION)]),
        _report(2, emitted=[
            _action(ActionType.CANCEL, 1200, CancelBody(target_call_id="c1", reason="x")),
            _action(ActionType.FINAL, 1200, FinalBody(text="done")),
        ]),
    ]
    assert metrics.ttfs(reports) == metrics.ttfs(reports)
    assert metrics.tool_latency(reports) == metrics.tool_latency(reports)
    assert metrics.interruption_to_cancellation_latency(reports) == metrics.interruption_to_cancellation_latency(reports)
    assert metrics.end_to_end_latency(reports) == metrics.end_to_end_latency(reports)


# --- realistic SimHarness scenarios ------------------------------------------


def _basic_provider() -> ScriptedProvider:
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
    provider.register("compose", "s1", {"text": "Pune flights found.", "claims": ["result:s1"]})
    return provider


def _harness(config: Config, *, latency_ms: int = 400) -> SimHarness:
    return SimHarness(
        config,
        seed=1,
        provider=_basic_provider(),
        tools={"search_flights": {"latency_ms": latency_ms, "response": {"flight_id": "AI-1"}}},
        worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000},
    )


def test_normal_scenario_produces_sane_metrics():
    h = _harness(Config())
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 1_000_000, stop_on_final=False)

    reports = h.run_log.reports
    tool_stats = metrics.tool_latency(reports)
    key = metrics.ToolLatencyKey(tool="search_flights", status="ok")
    assert tool_stats[key].max == 400_000  # exactly the configured latency_ms

    eot_stats = metrics.ttfs(reports).per_trigger["end_of_turn"]
    assert eot_stats.miss_count == 0
    assert eot_stats.stats.p50 is not None and eot_stats.stats.p50 >= 0

    e2e = metrics.end_to_end_latency(reports)
    assert len(e2e) == 1
    assert e2e[0].since_end_of_turn_us is not None and e2e[0].since_end_of_turn_us > 400_000  # at least the tool call itself


def test_interruption_scenario_produces_cancel_metrics():
    provider = _basic_provider()
    provider.register("interpret", "actually never mind", {"act": "abort", "slot_deltas": []})
    h = SimHarness(
        Config(),
        seed=1,
        provider=provider,
        tools={"search_flights": {"latency_ms": 800, "response": {"flight_id": "AI-1"}}},
        worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000},
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 300_000, stop_on_final=False)

    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("actually never mind")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])
    drain(h, h.clock.now_us() + 2_000_000, stop_on_final=False)

    reports = h.run_log.reports
    cancel_stats = metrics.interruption_to_cancellation_latency(reports)
    assert cancel_stats.signal_anchored.count >= 1
    assert cancel_stats.signal_anchored.max is not None and cancel_stats.signal_anchored.max >= 0
    assert cancel_stats.invalidated_never_cancelled_count == 0  # the one in-flight call really was cancelled

    interruption_stats = metrics.ttfs(reports).per_trigger["interruption"]
    assert interruption_stats.trigger_count == 1


def test_multi_turn_scenario_end_to_end_latency_has_one_entry_per_final():
    provider = _basic_provider()
    provider.register(
        "interpret",
        "Find flights to Mumbai",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]},
    )
    h = SimHarness(
        Config(),
        seed=1,
        provider=provider,
        tools={"search_flights": {"latency_ms": 50, "response": {"flight_id": "AI-1"}}},
        worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000},
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 1_000_000, stop_on_final=True)

    t = h.clock.now_us()
    h.send(t + 100_000, [chunk_event("Find flights to Mumbai")])
    h.send(t + 150_000, [eot_event()])
    drain(h, t + 1_000_000, stop_on_final=True)

    e2e = metrics.end_to_end_latency(h.run_log.reports)
    assert len(e2e) == 2


def test_ttfs_matches_the_documented_speculative_interpretation_numbers():
    """Cross-check against `docs/measurements.md`'s Phase 6 entry, which
    computed this exact scenario by hand in a one-off script: "INTERPRET
    latency 300ms, chunk spacing 150ms... Flag off: 300,000 µs... Flag
    on: 0 µs... a 100% reduction." Reproduced here independently, through
    this module, against the real kernel -- not re-derived from the
    documented number itself."""
    enum_tool = {
        "name": "search_flights",
        "mutability": "read_only",
        "parameters": {"type": "object", "properties": {"destination": {"type": "string", "enum": ["Pune", "Mumbai"]}}, "required": ["destination"]},
    }

    def run(speculative: bool) -> int:
        config = Config(
            transitive_invalidation=False,
            settle_barrier_enabled=False,
            absence_read_sets=False,
            claim_grades_enabled=False,
            rebinder_enabled=False,
            speculative_interpretation_enabled=speculative,
        )
        h = SimHarness(
            config,
            seed=1,
            provider=_basic_provider(),
            tools={"search_flights": {"latency_ms": 50, "response": {"flight_id": "AI-1"}}},
            worker_latency_us={JobKind.INTERPRET: 300_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000},
        )
        h.send(0, [manifest_event([enum_tool])])
        h.send(100_000, [chunk_event("Find flights to Pune")])
        eot_t = 100_000 + 4 * 150_000  # the turn stays open >= 300ms (4x150ms) before EOT
        h.send(eot_t, [eot_event()])
        drain(h, eot_t + 2_000_000, stop_on_final=False)
        stats = metrics.ttfs(h.run_log.reports).per_trigger["end_of_turn"].stats
        assert stats.p50 is not None
        return stats.p50

    assert run(False) == 300_000
    assert run(True) == 0
