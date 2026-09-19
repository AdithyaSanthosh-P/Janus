"""Phase 7 (`docs/post_v4_implementation_plan.md`): closing named scenario
gaps from `docs/theme05_implementation_blueprint.md`'s scenario catalog
that weren't covered by V0-V4 or the post-V4 phases' own acceptance tests.

Each test names the scenario ID it covers in its docstring, quoting the
blueprint's own one-line description, so coverage can be audited against
the blueprint table directly rather than trusted from a comment.
"""

from __future__ import annotations

from conftest import (
    FAST_WORKER_LATENCY,
    GET_SEAT_MAP_TOOL,
    SEARCH_FLIGHTS_TOOL,
    chunk_event,
    drain,
    eot_event,
    manifest_event,
)

from prism_rt.config import Config
from prism_rt.model.types import ActionType
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def test_r03_out_of_order_results_join_correctly():
    """R-03 (blueprint): "Out-of-order results | Two parallel reads;
    second-emitted returns first; dependent step joins | -- | Join after
    both; correct args | CS-11".

    Two independent READ steps (neither depends on the other's output) are
    both dispatched in the same step. The second one (get_seat_map, bound
    from a user-given slot rather than the first step's result) is
    configured with a *shorter* tool latency than the first (search_flights)
    so its result is delivered first even though it was proposed second.
    The join (COMPOSE, which `_build_compose_request`'s read set makes wait
    on every consumed call's own result fact) must not fire until *both*
    results have arrived, and each tool call must carry its own arguments
    -- not the other call's -- regardless of arrival order. CS-11 ("results
    matched only by call_id") is what makes this safe by construction in
    this implementation: there is no positional/index-based result
    matching anywhere in `ResultRouter`, so this test pins that invariant
    with a real regression rather than trusting it from reading the code.
    """
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "search Delhi flight AI-1 seat map",
        {
            "act": "new_goal",
            "intent": "search_flights",
            "slot_deltas": [
                {"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"},
                {"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"},
            ],
        },
    )
    provider.register(
        "plan",
        "search_flights",
        {
            "steps": [
                {
                    "local_id": "s1",
                    "tool": "search_flights",
                    "kind": "read",
                    "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}},
                    "after": [],
                },
                {
                    "local_id": "s2",
                    "tool": "get_seat_map",
                    "kind": "read",
                    "bindings": {"flight_id": {"type": "fact", "key": "slot.$G.flight_id"}},
                    "after": [],
                },
            ]
        },
    )
    provider.register("compose", "s2", {"text": "Here you go.", "claims": ["result:s1", "result:s2"]})

    h = SimHarness(
        Config(),
        seed=1,
        provider=provider,
        worker_latency_us=FAST_WORKER_LATENCY,
        tools={
            # Proposed/emitted first (plan order), but resolves *slower* --
            # its result must not be mistaken for s2's, and the join must
            # wait for it even though s2 answers first.
            "search_flights": {"latency_ms": 300, "response": {"flights": [{"flight_id": "AI-1"}]}},
            # Proposed second, but resolves first.
            "get_seat_map": {"latency_ms": 50, "response": {"seats": ["1A", "1B"]}},
        },
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, GET_SEAT_MAP_TOOL])])
    h.send(100_000, [chunk_event("search Delhi flight AI-1 seat map")])
    h.send(150_000, [eot_event()])

    # Drain only up to just past get_seat_map's (faster) resolution, well
    # before search_flights's -- the join must not fire yet.
    early_actions = drain(h, 250_000, stop_on_final=False)
    tool_calls = [a for a in early_actions if a.action_type == ActionType.TOOL_CALL]
    assert [c.body.tool_name for c in tool_calls] == ["search_flights", "get_seat_map"]
    # Each call carries its own arguments, not the other's, regardless of
    # which one's result lands first.
    search_call = next(c for c in tool_calls if c.body.tool_name == "search_flights")
    seat_call = next(c for c in tool_calls if c.body.tool_name == "get_seat_map")
    assert search_call.body.arguments == {"destination": "Delhi"}
    assert seat_call.body.arguments == {"flight_id": "AI-1"}
    assert [a for a in early_actions if a.action_type == ActionType.FINAL] == []

    # Continue draining -- the join (COMPOSE) can only fire once the slower
    # call (search_flights) has also resolved.
    later_actions = drain(h, 3_000_000)
    finals = [a for a in later_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert_clean(h)


def test_s08_manifest_update_drops_pending_steps_tool_honestly():
    """S-08 (blueprint): "Manifest update | Second manifest removes
    get_seat_map while its step pending; one call in flight | -- | Step
    invalid; replan; in-flight result still routed | --".

    This project has no replan-on-manifest-change mechanism (nothing in
    `kernel/` re-plans an active goal after a later manifest arrives), so
    "replan" isn't the actual behavior -- honest failure is, via the same
    G1 pre-check `propose_ready_calls` already uses for a plan referencing
    an unknown tool (`kernel/executor.py`, the S-07 fix from bugfix round
    2; its own comment at the check site already anticipates exactly this
    trigger: "a planner referencing an unknown tool, or one a later
    manifest update dropped"). What's verified here, by direct
    reproduction rather than by reading that comment and trusting it:
    - `ToolCatalog.parse_manifest` *replaces* its tool set wholesale each
      call, so a second manifest that omits a previously-USABLE tool
      really does make `catalog.get(that_tool)` return `None` afterward.
    - The step depending on the now-missing tool fails the goal honestly
      (a FINAL admitting failure, `task_completed=False`) instead of
      silently livelocking forever.
    - The blueprint's "one call in flight ... in-flight result still
      routed" undersells what actually happens, discovered only by
      instrumented reproduction, not by reading the executor: EVERY call's
      read set includes `catalog.version` *by design*
      (`kernel/executor.py`'s own comment: "Every call's read set includes
      goal.active and catalog.version") -- so a manifest update, even one
      that doesn't touch the tool a given in-flight call uses, invalidates
      that call too. search_flights (unaffected by the manifest change)
      still gets CANCEL_REQUESTED, and `propose_ready_calls`'s
      CANCEL_REQUESTED branch immediately re-proposes a fresh call for the
      same step (no waiting on the stale one's result) -- so search_flights
      is actually dispatched *twice* (`c-0001` then `c-0002`), and the
      *retry's* result is what actually gets consumed and unblocks s2; the
      original's late result still gets reconciled (COMPLETED_AFTER_CANCEL)
      rather than silently dropped. This is safe for a READ (idempotent,
      no double side effect) and is the deliberate, conservative choice --
      any manifest change could in principle affect any tool's schema or
      mutability, so re-validating every in-flight call is the safe
      default -- but it does mean "in-flight result still routed" for the
      *original* dispatch isn't quite what happens; the retry's result is
      what the plan actually uses.
    """
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Delhi and the seat map",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}]},
    )
    provider.register(
        "plan",
        "search_flights",
        {
            "steps": [
                {"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
                {
                    "local_id": "s2",
                    "tool": "get_seat_map",
                    "kind": "read",
                    "bindings": {"flight_id": {"type": "step_output", "step_key": "s1", "path": "flights.0.flight_id"}},
                    "after": ["s1"],
                },
            ]
        },
    )

    h = SimHarness(
        Config(),
        seed=1,
        provider=provider,
        worker_latency_us=FAST_WORKER_LATENCY,
        tools={"search_flights": {"latency_ms": 300, "response": {"flights": [{"flight_id": "AI-1"}]}}},
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, GET_SEAT_MAP_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Delhi and the seat map")])
    h.send(150_000, [eot_event()])

    # s1 (search_flights) should be in flight by now (300ms latency,
    # dispatched well before this point); s2 hasn't even been proposed yet
    # (it depends on s1's output). Drop get_seat_map from the catalog while
    # s1 is still in flight.
    early_actions = drain(h, 300_000, stop_on_final=False)
    manifest_report = h.send(h.clock.now_us() + 1_000, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    manifest_actions = [er.action for er in manifest_report.emit_report.emitted]

    actions = early_actions + manifest_actions + drain(h, 3_000_000)

    tool_calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    # search_flights dispatched twice (original + cancel-triggered retry,
    # see docstring); get_seat_map never proposed at all -- it's blocked by
    # the missing tool before it ever gets a chance.
    assert [c.body.tool_name for c in tool_calls] == ["search_flights", "search_flights"]

    cancels = [a for a in actions if a.action_type == ActionType.CANCEL]
    assert len(cancels) == 1
    assert cancels[0].body.target_call_id == tool_calls[0].body.call_id

    # The retry's result is what actually gets consumed; the original's
    # late result is still reconciled, not silently dropped.
    original_call_id, retry_call_id = tool_calls[0].body.call_id, tool_calls[1].body.call_id
    assert h.store.call_ledger.get(original_call_id).status.value == "completed_after_cancel"
    assert h.store.call_ledger.get(retry_call_id).status.value == "consumed"
    assert h.store.facts.get(f"result.{retry_call_id}") is not None

    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is False
    assert "get_seat_map" in finals[0].body.text

    gid = h.store.goals.all()[0].goal_id
    assert h.store.goals.get(gid).status.value == "abandoned"
    assert_clean(h)
