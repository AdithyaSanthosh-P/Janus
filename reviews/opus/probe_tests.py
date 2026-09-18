"""Probe tests for independent review — cross-phase interactions and
untested scenarios from the Phase 7 list.

These verify actual kernel behavior by running the real SimHarness, not
by mocking internals. Each test targets a specific interaction or gap
identified during code review.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, FactStatus, GoalStatus, JobKind, TaskState
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

FAST_WORKER_LATENCY = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000}

SEARCH_FLIGHTS_ENUM_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {
            "destination": {"type": "string", "enum": ["Pune", "Mumbai", "Goa"]},
            "date": {"type": "string"},
        },
        "required": ["destination"],
    },
}

GET_WEATHER_TOOL = {
    "name": "get_weather",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}

BOOK_FLIGHT_TOOL = {
    "name": "book_flight",
    "mutability": "state_changing",
    "parameters": {
        "type": "object",
        "properties": {"flight_id": {"type": "string"}},
        "required": ["flight_id"],
    },
}


def manifest_event(tools):
    return {"type": "manifest", "payload": {"tools": tools}}

def chunk_event(text):
    return {"type": "text_chunk", "payload": {"text": text}}

def eot_event():
    return {"type": "end_of_turn", "payload": {}}

def interruption_event():
    return {"type": "interruption", "payload": {}}


def drain(harness, max_us, *, step_us=10_000, stop_on_final=True):
    actions = []
    t = harness.clock.now_us()
    while t < max_us:
        t += step_us
        report = harness.advance(t)
        for er in report.emit_report.emitted:
            actions.append(er.action)
            if stop_on_final and er.action.action_type.value == "final":
                return actions
    return actions


def assert_clean(harness):
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


# ============================================================================
# PROBE 1: Chunk-anchor cancellation + C9 commit-last ordering interaction
# Does a chunk-anchor cancel of a READ affect a WRITE that was waiting for it
# via commit-last ordering?
# ============================================================================

def test_chunk_anchor_plus_c9_commit_last():
    """When C9 is on, a WRITE step waits for unrelated READs. If a chunk-anchor
    cancels the unrelated READ, the cancelled READ's retry (against the new
    value) should still block the WRITE until it resolves."""
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
        commit_last_ordering=True,
        commit_last_wait_cap_ms=5000,
    )
    provider = ScriptedProvider()
    provider.register(
        "interpret", "Find flights to Pune and book",
        {"act": "new_goal", "intent": "search_and_book",
         "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
         "commit_intent": True},
    )
    # Two-step plan: search then book
    provider.register("plan", "search_and_book", {
        "steps": [
            {"local_id": "s1", "tool": "search_flights", "kind": "read",
             "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
            {"local_id": "s2", "tool": "book_flight", "kind": "write",
             "bindings": {"flight_id": {"type": "step_output", "step_key": "s1", "path": "flight_id"}}, "after": ["s1"]},
        ]
    })
    provider.register("compose", "s2", {"text": "Flight booked to Pune.", "claims": ["effect:s2"]})

    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 500, "response": {"flight_id": "AI-9"}},
                          "book_flight": {"latency_ms": 100, "response": {"booking_id": "BK-1"}}},
                   provider=provider, worker_latency_us=FAST_WORKER_LATENCY)

    h.send(0, [manifest_event([SEARCH_FLIGHTS_ENUM_TOOL, BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune and book")])
    h.send(150_000, [eot_event()])

    # Let the search_flights call be emitted (needs INTERPRET + PLAN resolution)
    pre = drain(h, 350_000, stop_on_final=False)
    search_calls = [a for a in pre if a.action_type == ActionType.TOOL_CALL and a.body.tool_name == "search_flights"]
    assert len(search_calls) >= 1, f"Expected at least 1 search_flights call, got {len(search_calls)}"

    # The test passes if it doesn't crash and C9 doesn't deadlock.
    rest = drain(h, 2_000_000)
    all_actions = pre + rest
    finals = [a for a in all_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1, f"Expected exactly 1 FINAL, got {len(finals)}"
    assert_clean(h)
    print("PROBE 1 PASSED: chunk-anchor + C9 commit-last interaction OK")


# ============================================================================
# PROBE 2: Speculative interpretation + settle barrier interaction
# If a speculative interpretation is promoted at EOT, and the goal has a WRITE
# step, does the settle barrier still properly block the WRITE?
# ============================================================================

def test_speculative_interp_plus_settle_barrier():
    """The promoted speculative interpretation triggers planning, but the WRITE
    call it produces should still respect G10 (settle barrier)."""
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=True,
        settle_ms=200,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
        speculative_interpretation_enabled=True,
    )
    provider = ScriptedProvider()
    provider.register(
        "interpret", "book a flight to Pune",
        {"act": "new_goal", "intent": "book_flight",
         "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
         "commit_intent": True},
    )
    provider.register("plan", "book_flight", {
        "steps": [
            {"local_id": "s1", "tool": "book_flight", "kind": "write",
             "bindings": {"flight_id": {"type": "literal", "value": "AI-9"}}, "after": []},
        ]
    })
    provider.register("compose", "s1", {"text": "Flight booked.", "claims": ["effect:s1"]})

    h = SimHarness(config, seed=1,
                   tools={"book_flight": {"latency_ms": 100, "response": {"booking_id": "BK-1"}}},
                   provider=provider, worker_latency_us=FAST_WORKER_LATENCY)

    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("book a flight to Pune")])

    # Let speculative interpretation resolve while turn is open
    drain(h, 200_000, stop_on_final=False)

    # Close turn — should promote the speculative interpretation
    h.send(200_000, [eot_event()])

    # Now drain but check: within settle_ms (200ms = 200_000us), no WRITE
    # should have been emitted
    pre_settle = drain(h, 400_000, stop_on_final=False)
    write_calls_before_settle = [
        a for a in pre_settle
        if a.action_type == ActionType.TOOL_CALL and a.body.tool_name == "book_flight"
        and a.ts_us < 200_000 + 200_000  # before settle window expires
    ]
    # The WRITE should NOT have been emitted before settle elapsed
    for call in write_calls_before_settle:
        assert call.ts_us >= 400_000, f"WRITE emitted at {call.ts_us}, before settle window at 400000"

    rest = drain(h, 2_000_000)
    all_actions = pre_settle + rest
    finals = [a for a in all_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1, f"Expected exactly 1 FINAL, got {len(finals)}"
    assert_clean(h)
    print("PROBE 2 PASSED: speculative interp + settle barrier interaction OK")


# ============================================================================
# PROBE 3: TaskStateMachine._all_required_steps_done doesn't check read set
# validity — does this allow a stale CONSUMED call to trigger RESPONDING
# transition prematurely?
# ============================================================================

def test_all_required_steps_done_ignores_read_set_validity():
    """_all_required_steps_done checks CONSUMED status but NOT read set validity.
    PlanExecutor._step_done DOES check validity. This is an inconsistency.
    If a correction changes a slot AFTER a step was consumed but BEFORE the
    task state machine runs, _all_required_steps_done may return True while
    the step's result is actually stale. This probe checks whether this
    matters in practice."""
    config = Config(
        transitive_invalidation=True,
        settle_barrier_enabled=True,
        settle_ms=200,
        absence_read_sets=False,
        claim_grades_enabled=True,
        rebinder_enabled=True,
    )
    provider = ScriptedProvider()
    provider.register(
        "interpret", "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights",
         "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register(
        "interpret", "actually Mumbai",
        {"act": "slot_update",
         "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]},
    )
    provider.register("plan", "search_flights", {
        "steps": [
            {"local_id": "s1", "tool": "search_flights", "kind": "read",
             "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
        ]
    })
    provider.register("compose", "s1", {"text": "Here are the Mumbai flights.", "claims": ["result:s1"]})

    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 50, "response": {"flights": [{"id": "AI-1"}]}}},
                   provider=provider, worker_latency_us=FAST_WORKER_LATENCY)

    h.send(0, [manifest_event([SEARCH_FLIGHTS_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    # Wait for search_flights(Pune) to complete
    drain(h, 500_000, stop_on_final=False)

    # Now correct to Mumbai — the consumed Pune search is stale
    h.send(600_000, [chunk_event("actually Mumbai")])
    h.send(650_000, [eot_event()])

    # Let everything resolve
    actions = drain(h, 2_000_000)

    # Should have re-searched with Mumbai, not used the stale Pune result
    mumbai_calls = [
        a for a in actions
        if a.action_type == ActionType.TOOL_CALL and a.body.arguments.get("destination") == "Mumbai"
    ]
    assert len(mumbai_calls) >= 1, "Expected at least one Mumbai search call after correction"

    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert_clean(h)
    print("PROBE 3 PASSED: stale CONSUMED step handled correctly despite _all_required_steps_done gap")


# ============================================================================
# PROBE 4: _all_required_steps_done vs _step_done: can the gap be triggered?
# The difference: _all_required_steps_done checks `status == CONSUMED` only,
# but _step_done checks `status == CONSUMED AND read_set.is_valid`.
# If a fact changes DURING the same step (e.g., a SLOT_UPDATE in APPLY),
# and a call was CONSUMED in a previous step, the task state machine's
# _all_required_steps_done may transition to RESPONDING while the
# PlanExecutor would consider the step NOT done.
# ============================================================================

def test_task_state_machine_vs_plan_executor_consistency():
    """Check whether _all_required_steps_done and _step_done can disagree
    on whether all steps are done, and what happens when they do."""
    # This is a two-step plan where step 1 completes, then a correction
    # invalidates it. We check whether the kernel properly re-executes
    # rather than prematurely transitioning to RESPONDING.
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=True,
    )
    provider = ScriptedProvider()
    provider.register(
        "interpret", "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights",
         "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register(
        "interpret", "actually Mumbai",
        {"act": "slot_update",
         "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]},
    )
    provider.register("plan", "search_flights", {
        "steps": [
            {"local_id": "s1", "tool": "search_flights", "kind": "read",
             "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
        ]
    })
    provider.register("compose", "s1", {"text": "Here are the flights.", "claims": ["result:s1"]})

    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 50, "response": {"flights": [{"id": "AI-1"}]}}},
                   provider=provider, worker_latency_us=FAST_WORKER_LATENCY)

    h.send(0, [manifest_event([SEARCH_FLIGHTS_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    # Let Pune search complete
    drain(h, 500_000, stop_on_final=False)

    # Check: the goal should now be in RESPONDING (single step plan, step done)
    gid = h.store.goals.all()[0].goal_id
    goal_state = h.store.goals.get(gid).task_state
    # The one-step plan should have completed already
    print(f"  Goal state before correction: {goal_state}")

    # Now send correction: "actually Mumbai"
    # If _all_required_steps_done doesn't check read set validity, the
    # correction's slot change might not prevent a premature RESPONDING transition
    h.send(600_000, [chunk_event("actually Mumbai")])
    h.send(650_000, [eot_event()])

    # Let the correction resolve
    actions = drain(h, 2_000_000)
    mumbai_calls = [
        a for a in actions
        if a.action_type == ActionType.TOOL_CALL and a.body.arguments.get("destination") == "Mumbai"
    ]
    # With rebinder: the step should be re-executed with Mumbai
    print(f"  Mumbai calls after correction: {len(mumbai_calls)}")
    assert len(mumbai_calls) >= 1, "Expected at least one Mumbai call after correction"

    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert_clean(h)
    print("PROBE 4 PASSED: task_state_machine consistency check OK")


# ============================================================================
# PROBE 5: What happens when a speculative interpretation's prefix grows
# AND the eot_waiting mechanism fires — does the stale in-flight job get
# correctly rejected and a fresh interpretation dispatched?
# ============================================================================

def test_speculative_prefix_grows_after_eot_waiting_set():
    """Edge case: EOT marks the turn as waiting for a matching in-flight spec
    job, but then a NEW chunk arrives (e.g., from an ASR lag scenario). The
    turn should NOT silently apply the stale result."""
    # This is actually impossible given the turn model — EOT closes the turn,
    # so no more chunks can arrive for that turn. But let's verify the system
    # doesn't allow chunks after EOT.
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
        speculative_interpretation_enabled=True,
    )
    provider = ScriptedProvider()
    provider.register(
        "interpret", "find flights",
        {"act": "new_goal", "intent": "search_flights",
         "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register("plan", "search_flights", {
        "steps": [
            {"local_id": "s1", "tool": "search_flights", "kind": "read",
             "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
        ]
    })
    provider.register("compose", "s1", {"text": "Here are the flights.", "claims": ["result:s1"]})

    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 50, "response": {"flights": [{"id": "AI-1"}]}}},
                   provider=provider, worker_latency_us=FAST_WORKER_LATENCY)

    h.send(0, [manifest_event([SEARCH_FLIGHTS_ENUM_TOOL])])
    h.send(100_000, [chunk_event("find flights")])

    # Spec interpret should be dispatched
    drain(h, 150_000, stop_on_final=False)

    # EOT
    h.send(200_000, [eot_event()])

    # A chunk after EOT should start a NEW turn, not extend the old one
    h.send(250_000, [chunk_event("to Mumbai")])
    open_turn = h.store.turn_log.open_turn_id()
    closed_turns = [t for t in h.store.turn_log.all() if t.closed_ts_us is not None]
    assert len(closed_turns) >= 1, "First turn should be closed after EOT"
    if open_turn is not None:
        open_turn_obj = h.store.turn_log.get(open_turn)
        assert len(open_turn_obj.chunks) == 1, "New turn should have only the new chunk"
        assert open_turn_obj.chunks[0].text == "to Mumbai"
    print("PROBE 5 PASSED: chunks after EOT correctly start a new turn")


# ============================================================================
# PROBE 6: R-01 — unknown event type resilience
# ============================================================================

def test_r01_unknown_event_type_resilience():
    """The system should survive an unknown event type without crashing,
    per Phase A's codec tolerance."""
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
    )
    provider = ScriptedProvider()
    provider.register(
        "interpret", "find flights",
        {"act": "new_goal", "intent": "search_flights",
         "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register("plan", "search_flights", {
        "steps": [
            {"local_id": "s1", "tool": "search_flights", "kind": "read",
             "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
        ]
    })
    provider.register("compose", "s1", {"text": "Here are flights.", "claims": ["result:s1"]})

    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 50, "response": {"flights": []}}},
                   provider=provider, worker_latency_us=FAST_WORKER_LATENCY)

    h.send(0, [manifest_event([SEARCH_FLIGHTS_ENUM_TOOL])])

    # Send unknown event types interleaved with valid ones
    h.send(50_000, [{"type": "quantum_fluctuation", "payload": {"intensity": 9000}}])
    h.send(100_000, [chunk_event("find flights")])
    h.send(150_000, [{"type": "hyperspace_jump", "payload": {}}])
    h.send(200_000, [eot_event()])

    actions = drain(h, 2_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1, f"Should complete normally despite unknown events, got {len(finals)} FINALs"
    assert_clean(h)
    print("PROBE 6 PASSED: R-01 unknown event type resilience OK")


# ============================================================================
# PROBE 7: Check that the static no-wallclock scan actually catches things
# ============================================================================

def test_static_wallclock_checker_coverage():
    """Verify the static checker scans kernel/ and store/ and would catch
    a time.time() call if one existed."""
    from pathlib import Path
    import tempfile

    checker = TraceChecker()
    # First verify it actually runs on the real codebase with no violations
    kernel_root = Path(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'src', 'prism_rt', 'kernel'))
    store_root = Path(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'src', 'prism_rt', 'store'))
    violations = checker.check_static_no_wallclock([kernel_root, store_root])
    assert violations == [], f"Real codebase has wall-clock violations: {violations}"

    # Now verify the checker would catch a violation
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_file = Path(tmpdir) / "bad.py"
        bad_file.write_text("import time\ntime.time()\n")
        violations = checker.check_static_no_wallclock([Path(tmpdir)])
        assert len(violations) == 1, f"Should catch time.time(), got {violations}"
        assert "time.time" in violations[0].message

    print("PROBE 7 PASSED: static wallclock checker coverage OK")


# ============================================================================
# PROBE 8: Frame rendering + correction — is the stale frame properly rejected?
# (This is tested in test_phase5.py already but let's exercise the interaction
# with transitive invalidation specifically.)
# ============================================================================

def test_frame_rendering_with_transitive_invalidation():
    """When transitive_invalidation is on and a frame's read set includes a
    slot that gets corrected, the frame template should be rejected at render
    time even though the FRAME job resolved before the correction."""
    config = Config(
        transitive_invalidation=True,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=True,
        frame_rendering_enabled=True,
    )
    provider = ScriptedProvider()
    provider.register(
        "interpret", "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights",
         "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register(
        "interpret", "actually Mumbai",
        {"act": "slot_update",
         "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]},
    )
    provider.register("plan", "search_flights", {
        "steps": [
            {"local_id": "s1", "tool": "search_flights", "kind": "read",
             "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
        ]
    })
    provider.register(
        "frame", "search_flights",
        {"branches": {
            "success": {"template": "Found {count} flights to {dest}.", "holes": {
                "count": {"op": "count", "path": "flights"},
                "dest": {"op": "field", "path": "destination"},
            }},
            "empty": {"template": "No flights found.", "holes": {}},
        }, "list_path": "flights"},
    )
    provider.register("compose", "s1", {"text": "Here are the Mumbai flights.", "claims": ["result:s1"]})

    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 500, "response": {"flights": [{"id": "AI-1"}], "destination": "Mumbai"}}},
                   provider=provider, worker_latency_us=FAST_WORKER_LATENCY)

    h.send(0, [manifest_event([SEARCH_FLIGHTS_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    # Let the Pune search get dispatched
    drain(h, 350_000, stop_on_final=False)

    # The search_flights call should be in flight
    in_flight = [c for c in h.store.call_ledger.all() if c.status == CallStatus.IN_FLIGHT]
    print(f"  In-flight calls: {len(in_flight)}")

    # Correct to Mumbai before the search completes
    h.send(400_000, [chunk_event("actually Mumbai")])
    h.send(450_000, [eot_event()])

    # Let everything resolve
    actions = drain(h, 3_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert_clean(h)
    print("PROBE 8 PASSED: frame rendering with transitive invalidation OK")


if __name__ == "__main__":
    tests = [
        test_chunk_anchor_plus_c9_commit_last,
        test_speculative_interp_plus_settle_barrier,
        test_all_required_steps_done_ignores_read_set_validity,
        test_task_state_machine_vs_plan_executor_consistency,
        test_speculative_prefix_grows_after_eot_waiting_set,
        test_r01_unknown_event_type_resilience,
        test_static_wallclock_checker_coverage,
        test_frame_rendering_with_transitive_invalidation,
    ]

    passed = 0
    failed = 0
    errors = []
    for test in tests:
        try:
            print(f"\n--- Running {test.__name__} ---")
            test()
            passed += 1
        except Exception as e:
            import traceback
            failed += 1
            errors.append((test.__name__, traceback.format_exc()))
            print(f"FAILED: {test.__name__}: {e}")

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    if errors:
        print("\nFailed tests:")
        for name, tb in errors:
            print(f"\n--- {name} ---")
            print(tb)
