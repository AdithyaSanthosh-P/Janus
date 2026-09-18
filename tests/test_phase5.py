"""Phase 5 (`docs/post_v4_implementation_plan.md`) task-completion depth
tests: C9 commit-last ordering (N-02b) and C2 response frames.

C9 (`kernel/executor.py.PlanExecutor._commit_last_blocked`): a WRITE step,
even once its own `after` dependencies are satisfied, waits for every READ
step in the plan that isn't downstream of it (a sibling/independent read)
to resolve first — bounded by `Config.commit_last_wait_cap_ms` so one slow
unrelated read can't block the write forever. Gated behind
`Config.commit_last_ordering` (default `False`), same pattern every
version's new behavior uses — V0-V4 and Phase A-4 tests are unaffected.

C2 (`kernel/frames.py.FrameScheduler`): a FRAME job writes a response
template — typed holes bound to result paths, no result data involved —
the moment the plan's last call is emitted; when that call's real result
lands, the kernel renders FINAL from the template deterministically, no
COMPOSE round-trip. Gated behind `Config.frame_rendering_enabled` (default
`False`). Composer is always the fallback: these tests also cover the
paths where the flag is on but no usable frame ever forms (no provider
rule, a hole whose path doesn't resolve, a correction invalidating the
frame's read set) and confirm Composer still runs.
"""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

SEARCH_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"destination": {"type": "string"}}, "required": ["destination"]},
}
WEATHER_TOOL = {
    "name": "get_weather",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"destination": {"type": "string"}}, "required": ["destination"]},
}
BOOK_TOOL = {
    "name": "book_flight",
    "mutability": "state_changing",
    "parameters": {"type": "object", "properties": {"flight_id": {"type": "string"}}, "required": ["flight_id"]},
}

N02B_PLAN = {
    "steps": [
        {"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
        {"local_id": "s2", "tool": "get_weather", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
        {
            "local_id": "s3",
            "tool": "book_flight",
            "kind": "write",
            "bindings": {"flight_id": {"type": "step_output", "step_key": "s1", "path": "flight_id"}},
            "after": ["s1"],
        },
    ]
}


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def _config(**overrides) -> Config:
    overrides.setdefault("transitive_invalidation", False)
    overrides.setdefault("settle_barrier_enabled", False)
    overrides.setdefault("absence_read_sets", False)
    overrides.setdefault("claim_grades_enabled", False)
    overrides.setdefault("rebinder_enabled", False)
    return Config(**overrides)


def _n02b_harness(config, *, weather_latency_ms: int) -> SimHarness:
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "book the flight to Pune",
        {
            "act": "new_goal",
            "intent": "book_flight",
            "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
            "commit_intent": True,
        },
    )
    provider.register("plan", "book_flight", N02B_PLAN)
    provider.register("compose", "s3", {"text": "Booked and checked the weather.", "claims": ["result:s3"]})
    return SimHarness(
        config,
        seed=1,
        provider=provider,
        tools={
            "search_flights": {"latency_ms": 50, "response": {"flight_id": "AI-1"}},
            "get_weather": {"latency_ms": weather_latency_ms, "response": {"forecast": "clear"}},
            "book_flight": {"latency_ms": 50, "response": {"status": "confirmed"}},
        },
        worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000},
    )


def _book_call_step(h, actions):
    return next((a for a in actions if a.action_type == ActionType.TOOL_CALL and a.body.tool_name == "book_flight"), None)


def test_n02b_write_waits_for_unrelated_read_when_commit_last_enabled():
    config = _config(commit_last_ordering=True, commit_last_wait_cap_ms=3000)
    h = _n02b_harness(config, weather_latency_ms=2000)
    h.send(0, [manifest_event([SEARCH_TOOL, WEATHER_TOOL, BOOK_TOOL])])
    h.send(100_000, [chunk_event("book the flight to Pune")])
    h.send(150_000, [eot_event()])

    # Both reads must be emitted promptly (independent, no reason to wait).
    early = drain(h, 300_000, stop_on_final=False)
    reads = [a for a in early if a.action_type == ActionType.TOOL_CALL and a.body.tool_name in ("search_flights", "get_weather")]
    assert {a.body.tool_name for a in reads} == {"search_flights", "get_weather"}
    assert _book_call_step(h, early) is None  # not yet — weather (2000ms) hasn't resolved

    rest = drain(h, 3_000_000)
    all_actions = early + rest
    book_call = _book_call_step(h, all_actions)
    assert book_call is not None
    # book_flight must not be emitted before get_weather's own call resolves.
    weather_call_ts = next(a.ts_us for a in all_actions if a.action_type == ActionType.TOOL_CALL and a.body.tool_name == "get_weather")
    assert book_call.ts_us >= weather_call_ts + 2_000_000
    finals = [a for a in all_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1


def test_n02b_write_proceeds_without_commit_last_flag():
    """Baseline: with the flag off (the default), book_flight is emitted
    as soon as its own `after` dependency (search) resolves — proving the
    flag, not something else, is what changes the ordering."""
    config = _config(commit_last_ordering=False)
    h = _n02b_harness(config, weather_latency_ms=2000)
    h.send(0, [manifest_event([SEARCH_TOOL, WEATHER_TOOL, BOOK_TOOL])])
    h.send(100_000, [chunk_event("book the flight to Pune")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 500_000, stop_on_final=False)
    assert _book_call_step(h, actions) is not None  # emitted well before weather's 2000ms latency elapses


def test_n02b_wait_cap_bounds_the_block():
    """An unrelated read slower than commit_last_wait_cap_ms must not
    block the write forever."""
    config = _config(commit_last_ordering=True, commit_last_wait_cap_ms=500)
    h = _n02b_harness(config, weather_latency_ms=20_000)  # far beyond the 500ms cap
    h.send(0, [manifest_event([SEARCH_TOOL, WEATHER_TOOL, BOOK_TOOL])])
    h.send(100_000, [chunk_event("book the flight to Pune")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 1_500_000, stop_on_final=False)
    book_call = _book_call_step(h, actions)
    assert book_call is not None  # the cap released it, weather still not in
    assert book_call.ts_us < 20_000_000


def test_phase5_c9_replay_identity():
    def run_once() -> list[dict]:
        config = _config(commit_last_ordering=True, commit_last_wait_cap_ms=3000)
        h = _n02b_harness(config, weather_latency_ms=2000)
        h.send(0, [manifest_event([SEARCH_TOOL, WEATHER_TOOL, BOOK_TOOL])])
        h.send(100_000, [chunk_event("book the flight to Pune")])
        h.send(150_000, [eot_event()])
        drain(h, 3_000_000)
        return h.log.records

    violations = TraceChecker().check_replay(run_once, times=5)
    assert violations == []


# --- C2: response frames ----------------------------------------------------

SEARCH_WITH_SCHEMA_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"destination": {"type": "string"}}, "required": ["destination"]},
    "output_schema": {
        "type": "object",
        "properties": {"flights": {"type": "array", "items": {"type": "object", "properties": {"price": {"type": "number"}}}}},
    },
}

FRAME_PLAN = {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]}

SUCCESS_FRAME = {
    "list_path": "flights",
    "branches": {
        "success": {
            "template": "Found {count} flights, cheapest at {min_price}.",
            "holes": {"count": {"op": "count", "path": "flights"}, "min_price": {"op": "min", "path": "flights", "field": "price"}},
        },
        "empty": {"template": "No flights found for that route.", "holes": {}},
        "error": {"template": "I could not search for flights.", "holes": {}},
    },
}

BROKEN_HOLE_FRAME = {
    "branches": {
        "success": {"template": "Found {bogus} flights.", "holes": {"bogus": {"op": "field", "path": "does.not.exist"}}},
        "empty": {"template": "No flights found.", "holes": {}},
        "error": {"template": "Search failed.", "holes": {}},
    },
}


def _frame_config(**overrides) -> Config:
    overrides.setdefault("frame_rendering_enabled", True)
    return _config(**overrides)


def _frame_harness(config, *, search_response, frame_response=None):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register("plan", "search_flights", FRAME_PLAN)
    provider.register("compose", "s1", {"text": "Composer fallback response.", "claims": ["result:s1"]})
    if frame_response is not None:
        provider.register("frame", "search_flights", frame_response)
    return SimHarness(
        config,
        seed=1,
        provider=provider,
        tools={"search_flights": {"latency_ms": 200, "response": search_response}},
        worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000, JobKind.FRAME: 10_000},
    )


def _finals(actions):
    return [a for a in actions if a.action_type == ActionType.FINAL]


def test_frame_renders_final_grounded_in_real_result_without_compose():
    config = _frame_config()
    h = _frame_harness(config, search_response={"flights": [{"price": 300}, {"price": 150}, {"price": 500}]}, frame_response=SUCCESS_FRAME)
    h.send(0, [manifest_event([SEARCH_WITH_SCHEMA_TOOL])])
    h.send(100_000, [chunk_event("find flights to Pune")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 1_000_000)
    finals = _finals(actions)
    assert len(finals) == 1
    # CS-32-style check: every dynamic value in the text is exactly what
    # the holes evaluated against the real result, nothing invented.
    assert finals[0].body.text == "Found 3 flights, cheapest at 150."
    assert not any(j.kind == JobKind.COMPOSE for j in h.store.jobs._jobs.values())  # never dispatched at all
    assert_clean(h)


def test_frame_selects_empty_branch_when_list_is_empty():
    config = _frame_config()
    h = _frame_harness(config, search_response={"flights": []}, frame_response=SUCCESS_FRAME)
    h.send(0, [manifest_event([SEARCH_WITH_SCHEMA_TOOL])])
    h.send(100_000, [chunk_event("find flights to Pune")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 1_000_000)
    finals = _finals(actions)
    assert len(finals) == 1
    assert finals[0].body.text == "No flights found for that route."
    assert not any(j.kind == JobKind.COMPOSE for j in h.store.jobs._jobs.values())


def test_frame_falls_back_to_compose_when_no_frame_rule_registered():
    config = _frame_config()
    h = _frame_harness(config, search_response={"flights": [{"price": 300}]}, frame_response=None)
    h.send(0, [manifest_event([SEARCH_WITH_SCHEMA_TOOL])])
    h.send(100_000, [chunk_event("find flights to Pune")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 1_000_000)
    finals = _finals(actions)
    assert len(finals) == 1
    assert finals[0].body.text == "Composer fallback response."
    assert any(j.kind == JobKind.COMPOSE for j in h.store.jobs._jobs.values())


def test_frame_falls_back_to_compose_when_a_hole_fails_to_evaluate():
    config = _frame_config()
    h = _frame_harness(config, search_response={"flights": [{"price": 300}]}, frame_response=BROKEN_HOLE_FRAME)
    h.send(0, [manifest_event([SEARCH_WITH_SCHEMA_TOOL])])
    h.send(100_000, [chunk_event("find flights to Pune")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 1_000_000)
    finals = _finals(actions)
    assert len(finals) == 1
    assert finals[0].body.text == "Composer fallback response."


def test_frame_rendering_disabled_by_default_behaves_like_before():
    config = _config()  # frame_rendering_enabled defaults False
    h = _frame_harness(config, search_response={"flights": [{"price": 300}]}, frame_response=SUCCESS_FRAME)
    h.send(0, [manifest_event([SEARCH_WITH_SCHEMA_TOOL])])
    h.send(100_000, [chunk_event("find flights to Pune")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 1_000_000)
    finals = _finals(actions)
    assert len(finals) == 1
    assert finals[0].body.text == "Composer fallback response."
    assert not any(j.kind == JobKind.FRAME for j in h.store.jobs._jobs.values())


def test_frame_invalidated_by_correction_is_rejected_at_render_time():
    """The frame's read set includes the goal's slot facts at dispatch
    time, so a correction (a real fact change) must invalidate it —
    `try_render` rejects a stale template rather than ever rendering text
    grounded in facts that no longer hold."""
    from prism_rt.ids import IdGenerator
    from prism_rt.kernel.frames import FrameScheduler
    from prism_rt.model.types import CallRecord, CallStatus, FactStatus, Provenance, StepKind, fingerprint_for
    from prism_rt.store.session import SessionStore

    config = _frame_config()
    store = SessionStore.new(config, IdGenerator(1))
    scheduler = FrameScheduler()

    with store.begin_txn(1) as txn:
        txn.catalog.parse_manifest([SEARCH_WITH_SCHEMA_TOOL])
        txn.facts.set("goal.active", "g1", FactStatus.COMMITTED, Provenance(source="system"), rule="test")
        txn.facts.set("slot.g1.destination", "Pune", FactStatus.COMMITTED, Provenance(source="user"), rule="test")
        frame_read_set = txn.facts.build_read_set(["goal.active", "catalog.version", "slot.g1.destination"])
        txn.facts.set(
            "frame.g1.template",
            SUCCESS_FRAME,
            FactStatus.DERIVED,
            Provenance(source="system", derivation_read_set=frame_read_set),
            rule="test",
        )
        args = {"destination": "Pune"}
        call = CallRecord(
            call_id="c1",
            goal_id="g1",
            step_key="s1",
            tool="search_flights",
            kind=StepKind.READ,
            args=args,
            fingerprint=fingerprint_for("search_flights", args),
            read_set=frame_read_set,
            attempt=1,
            created_step=1,
            status=CallStatus.CONSUMED,
        )
        txn.store.call_ledger.create(call)
        txn.facts.set("result.c1", {"flights": [{"price": 300}]}, FactStatus.COMMITTED, Provenance(source="tool"), rule="test")

        assert scheduler.try_render(txn, call, 0, 1, event_id="e1") is True
        assert txn.facts.get("compose.g1.text").value == "Found 1 flights, cheapest at 300."

        # A correction changes the slot the frame was written against.
        txn.facts.set("slot.g1.destination", "Mumbai", FactStatus.COMMITTED, Provenance(source="user"), rule="test")
        txn.facts.retract("compose.g1.text", rule="test")

        assert scheduler.try_render(txn, call, 0, 2, event_id="e2") is False
        stale_text = txn.facts.get("compose.g1.text")
        assert stale_text is None or stale_text.status == FactStatus.RETRACTED


def test_phase5_c2_replay_identity():
    def run_once() -> list[dict]:
        config = _frame_config()
        h = _frame_harness(config, search_response={"flights": [{"price": 300}, {"price": 150}]}, frame_response=SUCCESS_FRAME)
        h.send(0, [manifest_event([SEARCH_WITH_SCHEMA_TOOL])])
        h.send(100_000, [chunk_event("find flights to Pune")])
        h.send(150_000, [eot_event()])
        drain(h, 1_000_000)
        return h.log.records

    violations = TraceChecker().check_replay(run_once, times=5)
    assert violations == []
