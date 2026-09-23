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
    frame_event,
    manifest_event,
)

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, GoalStatus, JobKind, JobStatus
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


def test_r04_delayed_cancellation_delivers_anyway_without_deadlock():
    """R-04 (blueprint): "Delayed cancellation | Mock cancel_semantics:
    deliver_anyway, extra delay 5s | cancel_ack too_late present/absent |
    completed_after_cancel; no consumption; no deadlock | CS-09".

    Two parts of this scenario don't map onto anything this codebase
    builds, worth stating precisely rather than glossing over: there is no
    `cancel_ack` event type at all (grepped -- never added, same wire-format
    scope trim documented elsewhere for this project), so the "too_late"
    field can't be checked; and "deliver_anyway" isn't a configurable mock
    option, it's `MockToolRegistry`'s *only* behavior -- its own docstring
    states this directly: "Cancelling a call does not un-schedule its
    result." So every interruption scenario in this suite already exercises
    deliver-anyway semantics incidentally; what's missing is a scenario
    that names R-04 explicitly and checks its three stated outcomes
    together in one place: `completed_after_cancel` (not silently dropped,
    not treated as a fresh success), no consumption (no `result.<call_id>`
    fact from the stale call -- the corrected goal's own result must come
    from the *new* call), and no deadlock (the goal still reaches a real
    FINAL despite the noise).

    The correction lands while the original call is still IN_FLIGHT (5s
    mock latency, corrected well before that elapses) -- not already
    CONSUMED, which is the shape `test_correction_race_regression.py`
    exercises instead. `rebinder_enabled=False` forces a full replan on the
    slot correction (same choice that test makes), the surest way to get a
    real CANCEL rather than an in-place rebind. Uses the plain
    (non-`enum`) `SEARCH_FLIGHTS_TOOL` deliberately, not an enum schema --
    an enum or already-seen session value makes `QuickDetector.detect_
    value`'s chunk-anchor (Phase 4) fire HIGH-confidence *before* EOT,
    which cancels-and-immediately-retries against the still-committed
    (pre-correction) slot value first (a real, already-documented,
    harmless extra hop -- `tests/test_phase4.py`'s own first test), adding
    a second stale call this scenario doesn't need to make its point.
    """
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
    )
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register(
        "interpret",
        "actually Mumbai",
        {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]},
    )
    provider.register(
        "plan",
        "search_flights",
        {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]},
    )
    provider.register("compose", "s1", {"text": "Found flights.", "claims": ["result:s1"]})

    h = SimHarness(
        config,
        seed=1,
        provider=provider,
        tools={"search_flights": {"latency_ms": 5_000, "response": {"flights": [{"id": "AI-1"}]}}},
        worker_latency_us=FAST_WORKER_LATENCY,
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])

    early_actions = drain(h, 250_000, stop_on_final=False)
    tool_calls = [a for a in early_actions if a.action_type == ActionType.TOOL_CALL]
    assert len(tool_calls) == 1
    pune_call_id = tool_calls[0].body.call_id
    pune_call = h.store.call_ledger.get(pune_call_id)
    assert pune_call.args.get("destination") == "Pune"
    assert pune_call.status == CallStatus.IN_FLIGHT  # well before the 5s mock latency

    # Correct while it's still in flight -- 5s is a long way off yet.
    h.send(300_000, [chunk_event("actually Mumbai")])
    h.send(310_000, [eot_event()])

    mid_actions = drain(h, 600_000, stop_on_final=False)
    cancels = [a for a in mid_actions if a.action_type == ActionType.CANCEL]
    assert len(cancels) == 1
    assert cancels[0].body.target_call_id == pune_call_id
    assert h.store.call_ledger.get(pune_call_id).status == CallStatus.CANCEL_REQUESTED

    mumbai_calls = [a for a in mid_actions if a.action_type == ActionType.TOOL_CALL and a.body.call_id != pune_call_id]
    assert len(mumbai_calls) == 1
    mumbai_call_id = mumbai_calls[0].body.call_id

    # Drain well past both the original's 5s mock delivery (deliver-anyway,
    # arriving even though it was cancelled) and the corrected call's own
    # 5s delivery -- the goal must still reach a real completion.
    late_actions = drain(h, 6_500_000)
    finals = [a for a in late_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.task_completed is True

    # completed_after_cancel, not dropped and not a false success.
    assert h.store.call_ledger.get(pune_call_id).status == CallStatus.COMPLETED_AFTER_CANCEL
    # no consumption: the stale call never produced a result fact.
    assert h.store.facts.get(f"result.{pune_call_id}") is None
    # the corrected call is what actually resolved the goal.
    assert h.store.call_ledger.get(mumbai_call_id).status == CallStatus.CONSUMED
    assert h.store.facts.get(f"result.{mumbai_call_id}") is not None
    assert_clean(h)


WEATHER_TOOL = {
    "name": "get_weather",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
}


def test_r07_stale_worker_result_after_goal_replacement_is_rejected():
    """R-07 (blueprint): "Stale worker result | Interpretation for prefix
    dispatched; goal replaced before return | -- | rejected_stale; no facts
    from it | CS-04, 19".

    Exercised with a PLAN job rather than an INTERPRET job: the goal-
    replacement trigger the blueprint names only has something to
    invalidate once a goal-scoped job exists, and PLAN is the first one.
    g1's PLAN job is still in flight (400ms) when the user starts g2;
    `goal.active` changes, so when g1's plan finally resolves, K4
    (`_apply_worker_result`'s read-set check) drops it -- no plan is
    created for g1, its tool is never called, and g2 completes normally.
    Note for anyone extending this: PLAN rules are keyed on the
    `goal_intent:` line, not the bare tool name -- every PLAN prompt
    carries the whole tool catalog, so a bare `"search_flights"` rule
    would also match g2's prompt (found while reproducing this).
    """
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register(
        "interpret",
        "weather in Goa",
        {"act": "new_goal", "intent": "get_weather", "slot_deltas": [{"name": "city", "scope": "goal", "op": "set", "value": "Goa"}]},
    )
    provider.register(
        "plan",
        "goal_intent: search_flights",
        {"steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read", "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]},
    )
    provider.register(
        "plan",
        "goal_intent: get_weather",
        {"steps": [{"local_id": "s1", "tool": "get_weather", "kind": "read", "bindings": {"city": {"type": "fact", "key": "slot.$G.city"}}, "after": []}]},
    )
    provider.register("compose", "s1", {"text": "Sunny in Goa.", "claims": ["result:s1"]})

    h = SimHarness(
        Config(),
        seed=1,
        provider=provider,
        worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 400_000, JobKind.COMPOSE: 10_000},
        tools={"search_flights": {"latency_ms": 50, "response": {"flights": []}}, "get_weather": {"latency_ms": 50, "response": {"t": "sunny"}}},
    )
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL, WEATHER_TOOL])])
    h.send(100_000, [chunk_event("flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 250_000, stop_on_final=False)
    stale_plan = next(j for j in h.store.jobs._jobs.values() if j.kind == JobKind.PLAN)
    assert stale_plan.goal_id == "g-0001" and stale_plan.status == JobStatus.RUNNING

    h.send(260_000, [chunk_event("weather in Goa")])
    h.send(270_000, [eot_event()])
    actions = drain(h, 2_000_000)

    assert h.store.jobs.get(stale_plan.job_id).status == JobStatus.DONE  # it did resolve...
    assert h.store.plans.current("g-0001") is None  # ...and was never applied
    failing = {entry[0] for entry in h.store.facts.is_valid(stale_plan.read_set).failing}
    assert "goal.active" in failing
    tool_calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert [c.body.tool_name for c in tool_calls] == ["get_weather"]  # g1's tool never called
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1 and finals[0].body.text == "Sunny in Goa."
    assert h.store.goals.get("g-0002").status == GoalStatus.COMPLETED
    assert_clean(h)


CHECK_STATUS_TOOL = {
    "name": "check_status",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"indicator_state": {"type": "string"}}, "required": ["indicator_state"]},
}


def test_m09_low_confidence_perception_clarifies_without_lookup_or_reanalysis_loop():
    """M-09 (blueprint): "Ambiguous perception | Vision returns low for
    required indicator | -- | CLARIFY; no lookup".

    The visible outcome already held before this test existed, but
    building it surfaced a real bug: a LOW claim leaves the question OPEN,
    and `PerceptionScheduler.decide` re-selected the *same* anchor-aligned
    frame and re-dispatched VISION every single step -- 285 jobs in 3
    simulated seconds, each one a real paid image call against a live
    provider. Fixed via `Question.analyzed_obs_id`: an already-analyzed
    frame is never re-sent; only new evidence, a retarget, or a lease
    renewal re-opens analysis. This test pins both halves.
    """
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "what is this light doing",
        {
            "act": "new_goal",
            "intent": "device_help",
            "visual_reference": "at_utterance",
            "visual_candidates": [{"name": "indicator_state", "description": "the indicator's state"}],
        },
    )
    provider.register(
        "plan",
        "goal_intent: device_help",
        {"steps": [{"local_id": "s1", "tool": "check_status", "kind": "read", "bindings": {"indicator_state": {"type": "fact", "key": "slot.$G.indicator_state"}}, "after": []}]},
    )
    provider.register("vision", "indicator_state", {"claims": [{"name": "indicator_state", "value": "amber?", "confidence": "low"}]})
    latency = dict(FAST_WORKER_LATENCY)
    latency[JobKind.VISION] = 10_000
    h = SimHarness(
        Config(vision_enabled=True),
        seed=1,
        provider=provider,
        worker_latency_us=latency,
        tools={"check_status": {"latency_ms": 50, "response": {"ok": True}}},
    )
    h.send(0, [manifest_event([CHECK_STATUS_TOOL])])
    h.send(50_000, [frame_event("f1")])
    h.send(100_000, [chunk_event("what is this light doing")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 3_000_000, stop_on_final=False)

    clarifies = [a for a in actions if a.action_type == ActionType.CLARIFY]
    assert len(clarifies) == 1
    assert [a for a in actions if a.action_type == ActionType.TOOL_CALL] == []  # no lookup
    assert h.store.facts.get("slot.g-0001.indicator_state") is None  # LOW never promoted
    assert h.store.facts.get("claim.q-0001.indicator_state") is None  # LOW never a claim fact either
    vision_jobs = [j for j in h.store.jobs._jobs.values() if j.kind == JobKind.VISION]
    assert len(vision_jobs) == 1  # the frame was analyzed once, not re-sent every step
    assert_clean(h)


MANUAL_LOOKUP_TOOL = {
    "name": "manual_lookup",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"device_model": {"type": "string"}, "visible_state": {"type": "string"}},
        "required": ["device_model", "visible_state"],
    },
}


def _device_help_harness(*, vision_latency_us: int, mode: str = "at_utterance"):
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "what does this light mean",
        {
            "act": "new_goal",
            "intent": "device_help",
            "visual_reference": mode,
            "visual_candidates": [
                {"name": "device_model", "description": "the device's model"},
                {"name": "visible_state", "description": "the light's state"},
            ],
        },
    )
    provider.register(
        "plan",
        "goal_intent: device_help",
        {
            "steps": [
                {
                    "local_id": "s1",
                    "tool": "manual_lookup",
                    "kind": "read",
                    "bindings": {
                        "device_model": {"type": "fact", "key": "slot.$G.device_model"},
                        "visible_state": {"type": "fact", "key": "slot.$G.visible_state"},
                    },
                    "after": [],
                }
            ]
        },
    )
    provider.register(
        "vision",
        "device_model",
        {
            "claims": [
                {"name": "device_model", "value": "Tab S9", "confidence": "high"},
                {"name": "visible_state", "value": "blinking amber", "confidence": "high"},
            ]
        },
    )
    provider.register("compose", "s1", {"text": "That blinking amber light means low battery.", "claims": ["result:s1"]})
    latency = dict(FAST_WORKER_LATENCY)
    latency[JobKind.VISION] = vision_latency_us
    return SimHarness(
        Config(vision_enabled=True),
        seed=1,
        provider=provider,
        worker_latency_us=latency,
        tools={"manual_lookup": {"latency_ms": 50, "response": {"meaning": "low battery"}}},
    )


def test_n05_vision_slower_than_plan_waits_instead_of_asking_the_user():
    """Regression, found while fixing M-09's dropped-job paths: with VISION
    slower than PLAN (normal for a live image call), a step bound through
    `slot.$G.<name>` counted as missing *user* input -- the agent asked
    "what should device_model be?" about the very thing it was looking at,
    then stuck in CLARIFYING forever, since a perception-filled slot never
    cleared `clarify_target`. N-05 itself only passed because its VISION
    latency happened to beat PLAN's. Reproduced on the pre-fix code: 300ms
    VISION -> CLARIFY, no FINAL after 5 simulated seconds."""
    h = _device_help_harness(vision_latency_us=300_000)
    h.send(0, [manifest_event([MANUAL_LOOKUP_TOOL])])
    h.send(50_000, [frame_event("f1")])
    h.send(100_000, [chunk_event("what does this light mean")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 5_000_000)
    assert [a for a in actions if a.action_type == ActionType.CLARIFY] == []
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1 and finals[0].body.text == "That blinking amber light means low battery."
    assert_clean(h)


def _run_m04(frame_delay_after_plan_us: int):
    h = _device_help_harness(vision_latency_us=10_000, mode="current_state")
    h.send(0, [manifest_event([MANUAL_LOOKUP_TOOL])])
    h.send(100_000, [chunk_event("what does this light mean")])
    h.send(150_000, [eot_event()])
    early = drain(h, 400_000, stop_on_final=False)  # INTERPRET + PLAN done by here; no frame yet
    assert h.store.plans.current("g-0001") is not None
    plan_ready_us = h.clock.now_us()
    frame_at = plan_ready_us + frame_delay_after_plan_us
    early += drain(h, frame_at - 10_000, stop_on_final=False)
    h.send(frame_at, [frame_event("f-late")])
    late = drain(h, frame_at + 2_000_000)
    return h, early, late, frame_at


def test_m04_frame_after_reasoning_lookup_waits_for_claim():
    """M-04 (blueprint): "Frame after reasoning | Plan needs indicator from
    frames; first frame arrives after planning | Frame 500/2000 ms after plan
    | Lookup emitted only after claim accepted | CS-25". Both of the
    blueprint's timings. Before the slot-exemption fix above, the plan step
    asked the user instead of waiting (no frame -> nothing analyzed yet ->
    treated as missing user input)."""
    for delay_us in (500_000, 2_000_000):
        h, early, late, frame_at = _run_m04(delay_us)
        assert [a for a in early if a.action_type in (ActionType.TOOL_CALL, ActionType.CLARIFY)] == [], delay_us
        tool_calls = [a for a in late if a.action_type == ActionType.TOOL_CALL]
        assert len(tool_calls) == 1, delay_us
        assert tool_calls[0].body.arguments == {"device_model": "Tab S9", "visible_state": "blinking amber"}
        claim = h.store.facts.get("slot.g-0001.visible_state")
        assert claim is not None and claim.provenance.source == "perception"
        assert claim.provenance.ts_us >= frame_at  # lookup only after the claim was accepted
        finals = [a for a in late if a.action_type == ActionType.FINAL]
        assert len(finals) == 1, delay_us
        assert_clean(h)


TICKET_TOOL = {
    "name": "create_ticket",
    "mutability": "state_changing",
    "parameters": {"type": "object", "properties": {"indicator_state": {"type": "string"}}, "required": ["indicator_state"]},
}


def _m05_harness():
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "open a ticket for this light",
        {
            "act": "new_goal",
            "intent": "report_issue",
            "commit_intent": True,
            "visual_reference": "current_state",
            "visual_candidates": [{"name": "indicator_state", "description": "the indicator's current state"}],
        },
    )
    provider.register(
        "interpret",
        "it's blinking red",
        {"act": "answer_clarification", "slot_deltas": [{"name": "indicator_state", "scope": "goal", "op": "set", "value": "blinking red"}]},
    )
    provider.register(
        "plan",
        "goal_intent: report_issue",
        {"steps": [{"local_id": "s1", "tool": "create_ticket", "kind": "write", "bindings": {"indicator_state": {"type": "fact", "key": "slot.$G.indicator_state"}}, "after": []}]},
    )
    provider.register("vision", "indicator_state", {"claims": [{"name": "indicator_state", "value": "blinking red", "confidence": "high"}]})
    provider.register("compose", "s1", {"text": "Ticket filed.", "claims": ["result:s1"]})
    latency = dict(FAST_WORKER_LATENCY)
    latency[JobKind.VISION] = 10_000
    h = SimHarness(
        Config(vision_enabled=True),
        seed=1,
        provider=provider,
        worker_latency_us=latency,
        tools={"create_ticket": {"latency_ms": 50, "response": {"ticket_id": "T-1"}}},
    )
    h.send(0, [manifest_event([TICKET_TOOL])])
    h.send(50_000, [frame_event("f1")])  # the only frame for the next 5 seconds
    return h


def _vision_jobs(h):
    return [j for j in h.store.jobs._jobs.values() if j.kind == JobKind.VISION]


def test_m05_frame_gap_before_write_requests_fresh_view_instead_of_writing():
    """M-05 (blueprint): "Frame gap before write | Ticket creation needs
    current_state device state; no frames for 5 s | -- | CLARIFY request for
    fresh view; no write | CS-25, 26". Exact contract, `docs/prompt 2.txt`
    §10 "Freshness": a frame gap longer than gap_ms doesn't invalidate a
    CURRENT_STATE claim, but before a WRITE it "triggers a request for a
    fresh view instead of assuming persistence."

    Before the fix, the ticket was filed from a 5s-old frame: the rule was
    never implemented (no gap_ms setting existed), and a claim's lease
    clock starts when the frame is *analyzed*, so a stale frame analyzed
    just now looked fresh. Now `PlanExecutor._stale_current_state_input`
    checks the latest frame's *capture* time before any perception-fed
    WRITE (`Config.frame_gap_ms`, default 3000 per §10), and asks instead.
    """
    h = _m05_harness()
    h.send(5_100_000, [chunk_event("open a ticket for this light")])
    h.send(5_150_000, [eot_event()])
    actions = drain(h, 7_000_000, stop_on_final=False)

    assert [a for a in actions if a.action_type == ActionType.TOOL_CALL] == []  # no write
    clarifies = [a for a in actions if a.action_type == ActionType.CLARIFY]
    assert len(clarifies) == 1
    assert "show it to me again" in clarifies[0].body.text
    assert "indicator_state" not in clarifies[0].body.text  # no raw param name for this ask
    # The claim is still valid for reads (§10: a gap doesn't invalidate it)...
    slot = h.store.facts.get("slot.g-0001.indicator_state")
    assert slot is not None and slot.provenance.source == "perception"
    # ...and the stale frame is never re-sent while waiting.
    assert len(_vision_jobs(h)) == 1

    # A fresh frame is what unblocks it: analyzed once, then the write goes out.
    h.send(7_010_000, [frame_event("f2")])
    later = drain(h, 9_000_000)
    tool_calls = [a for a in later if a.action_type == ActionType.TOOL_CALL]
    assert [c.body.tool_name for c in tool_calls] == ["create_ticket"]
    assert len([a for a in later if a.action_type == ActionType.FINAL]) == 1
    assert len(_vision_jobs(h)) == 2
    assert_clean(h)


def test_m05_user_stated_state_is_not_gated_by_frame_gap():
    """The gap rule gates *perception-sourced* inputs only: if the user just
    tells us the state instead of showing it, that's rank-1 evidence
    (§9.1), not a stale camera claim."""
    h = _m05_harness()
    h.send(5_100_000, [chunk_event("open a ticket for this light")])
    h.send(5_150_000, [eot_event()])
    drain(h, 6_000_000, stop_on_final=False)
    h.send(6_100_000, [chunk_event("it's blinking red")])
    h.send(6_150_000, [eot_event()])
    actions = drain(h, 8_000_000)
    tool_calls = [a for a in actions if a.action_type == ActionType.TOOL_CALL]
    assert [c.body.tool_name for c in tool_calls] == ["create_ticket"]
    assert h.store.facts.get("slot.g-0001.indicator_state").provenance.source == "user"
    assert len([a for a in actions if a.action_type == ActionType.FINAL]) == 1
    assert_clean(h)


def test_m05_control_fresh_frame_writes_immediately():
    h = _m05_harness()
    h.send(200_000, [chunk_event("open a ticket for this light")])
    h.send(250_000, [eot_event()])
    actions = drain(h, 2_000_000)
    assert [a for a in actions if a.action_type == ActionType.CLARIFY] == []
    assert [a.body.tool_name for a in actions if a.action_type == ActionType.TOOL_CALL] == ["create_ticket"]
    assert_clean(h)
