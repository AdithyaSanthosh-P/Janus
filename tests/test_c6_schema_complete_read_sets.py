"""C6 regression tests: schema-complete read sets (`docs/prompt 3.txt`
C6, tracked as partially built in `docs/post_v4_implementation_plan.md`
line 57: "absence entries built (V2); read set still only covers bound
params").

Before this fix, a call's read set only ever covered params the plan
step's own `bindings` named, plus whatever the Planner happened to
explicitly declare via `step.absence_keys` (V2, I-12, `test_v2.py`). An
optional tool-schema parameter the plan simply never mentioned at all got
zero read-set representation -- so a result stayed valid even after the
user later stated a value for it, the "omission escape" bug class
(`docs/prompt 3.txt` C6 point 1: "a result stays valid after the user
adds a constraint the call never read", L2).

`PlanExecutor._schema_complete_absence_keys` (called from `_bind`, gated
behind `Config.absence_read_sets` like the rest of the V2 absence
mechanism) now also tracks every OPTIONAL schema parameter not already
bound, at the ordinary `slot.$G.<name>` key -- `store.facts.build_read_set`
resolves each to present-or-absent, whichever it currently is, so this
reuses the exact mechanism I-12 already established, just applied
completely instead of only where the Planner happened to declare it.
Required parameters are excluded (already guaranteed bound by the time a
call exists, `validate_args` refuses to create one missing a required
param). A slot name currently targeted by the goal's tracked perception
question is also excluded (absence_sensitivity, policy default per the
architecture doc: user-sourced facts only) -- see
`PlanExecutor._schema_complete_absence_keys`'s own docstring for why.
"""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, frame_event, interruption_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

LOOKUP_TOOL = {
    "name": "lookup_tool",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "date": {"type": "string"},
            "cabin_class": {"type": "string"},
        },
        "required": ["query"],
    },
}

STATUS_TOOL = {
    "name": "status_tool",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"ticket_id": {"type": "string"}, "device_model": {"type": "string"}},
        "required": ["ticket_id"],
    },
}

_FAST_LATENCY = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000}


def c6_config(**overrides) -> Config:
    return Config(
        absence_read_sets=overrides.pop("absence_read_sets", True),
        vision_enabled=overrides.pop("vision_enabled", False),
        **overrides,
    )


def new_harness(config, tools=None, provider=None, worker_latency_us=None, **kwargs):
    latency = dict(_FAST_LATENCY)
    latency[JobKind.VISION] = 10_000
    if worker_latency_us:
        latency.update(worker_latency_us)
    return SimHarness(config, seed=1, tools=tools, provider=provider, worker_latency_us=latency, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def _lookup_plan(bindings: dict) -> dict:
    return {"steps": [{"local_id": "s1", "tool": "lookup_tool", "kind": "read", "bindings": bindings, "after": []}]}


def _register_lookup_dispatch(provider: ScriptedProvider) -> None:
    provider.register(
        "interpret",
        "look up widget",
        {
            "act": "new_goal",
            "intent": "lookup",
            "slot_deltas": [{"name": "query", "scope": "goal", "op": "set", "value": "widget"}],
        },
    )
    provider.register(
        "plan", "goal_intent: lookup", _lookup_plan({"query": {"type": "fact", "key": "slot.$G.query"}})
    )
    provider.register("compose", "s1", {"text": "Found it.", "claims": ["result:s1"]})


def _drive_until_cancelled(h: SimHarness, call_id: str, steps: int = 40) -> bool:
    t = h.clock.now_us()
    for _ in range(steps):
        t += 10_000
        report = h.advance(t)
        if any(
            er.action.action_type == ActionType.CANCEL and er.action.body.target_call_id == call_id
            for er in report.emit_report.emitted
        ):
            return True
    return False


# --- core fix: unbound optional params are tracked --------------------------


def test_unbound_optional_param_is_tracked_in_the_read_set():
    provider = ScriptedProvider()
    _register_lookup_dispatch(provider)
    h = new_harness(c6_config(), tools={"lookup_tool": {"latency_ms": 800, "response": {"ok": True}}}, provider=provider)
    h.send(0, [manifest_event([LOOKUP_TOOL])])
    h.send(100_000, [chunk_event("look up widget")])
    h.send(150_000, [eot_event()])
    drain(h, 250_000, stop_on_final=False)

    call = next(c for c in h.store.call_ledger.all() if c.tool == "lookup_tool")
    assert call.status == CallStatus.IN_FLIGHT
    keys = {e.key for e in call.read_set.entries}
    # bound (existing behavior): the required param the plan step actually used
    assert "slot.g-0001.query" in keys
    # new: optional params the plan step never mentioned at all -- not even
    # via an explicit absence_keys declaration
    assert "slot.g-0001.date" in keys
    assert "slot.g-0001.cabin_class" in keys
    assert_clean(h)


def test_later_setting_a_previously_absent_optional_param_invalidates_the_call():
    provider = ScriptedProvider()
    _register_lookup_dispatch(provider)
    provider.register(
        "interpret",
        "for next Tuesday",
        {"act": "addition", "slot_deltas": [{"name": "date", "scope": "goal", "op": "set", "value": "2026-09-29"}]},
    )
    h = new_harness(c6_config(), tools={"lookup_tool": {"latency_ms": 800, "response": {"ok": True}}}, provider=provider)
    h.send(0, [manifest_event([LOOKUP_TOOL])])
    h.send(100_000, [chunk_event("look up widget")])
    h.send(150_000, [eot_event()])
    drain(h, 250_000, stop_on_final=False)

    call = next(c for c in h.store.call_ledger.all() if c.tool == "lookup_tool")
    assert call.status == CallStatus.IN_FLIGHT

    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("for next Tuesday")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])

    cancelled = _drive_until_cancelled(h, call.call_id)
    assert cancelled, (
        "setting a previously-absent optional tool param should invalidate the "
        "in-flight call, even though the plan never bound or declared it"
    )
    assert_clean(h)


def test_unrelated_fact_mutation_does_not_invalidate():
    """Negative control (required by the C6 task spec): a fact entirely
    outside the tool's schema must never be tracked and must never
    invalidate the call -- this is not "maximal invalidation."""
    provider = ScriptedProvider()
    _register_lookup_dispatch(provider)
    provider.register(
        "interpret",
        "by the way I like tea",
        {"act": "addition", "slot_deltas": [{"name": "beverage_preference", "scope": "goal", "op": "set", "value": "tea"}]},
    )
    h = new_harness(c6_config(), tools={"lookup_tool": {"latency_ms": 800, "response": {"ok": True}}}, provider=provider)
    h.send(0, [manifest_event([LOOKUP_TOOL])])
    h.send(100_000, [chunk_event("look up widget")])
    h.send(150_000, [eot_event()])
    drain(h, 250_000, stop_on_final=False)

    call = next(c for c in h.store.call_ledger.all() if c.tool == "lookup_tool")
    assert call.status == CallStatus.IN_FLIGHT
    keys = {e.key for e in call.read_set.entries}
    assert "slot.g-0001.beverage_preference" not in keys  # not a schema param -- correctly excluded

    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("by the way I like tea")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])

    cancelled = _drive_until_cancelled(h, call.call_id)
    assert not cancelled, "a fact entirely outside the tool's schema must never invalidate the call"
    assert_clean(h)


def test_bound_param_changing_still_invalidates_as_before():
    """Sanity: the pre-existing behavior (a bound FACT param changing
    invalidates the call) must survive this change unweakened."""
    provider = ScriptedProvider()
    _register_lookup_dispatch(provider)
    provider.register(
        "interpret",
        "actually gadget",
        {"act": "slot_update", "slot_deltas": [{"name": "query", "scope": "goal", "op": "set", "value": "gadget"}]},
    )
    h = new_harness(c6_config(), tools={"lookup_tool": {"latency_ms": 800, "response": {"ok": True}}}, provider=provider)
    h.send(0, [manifest_event([LOOKUP_TOOL])])
    h.send(100_000, [chunk_event("look up widget")])
    h.send(150_000, [eot_event()])
    drain(h, 250_000, stop_on_final=False)

    call = next(c for c in h.store.call_ledger.all() if c.tool == "lookup_tool")
    assert call.status == CallStatus.IN_FLIGHT

    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("actually gadget")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])

    cancelled = _drive_until_cancelled(h, call.call_id)
    assert cancelled, "a bound param changing must still invalidate the call"
    assert_clean(h)


# --- absence_sensitivity: perception-governed names are exempted -----------


def test_perception_governed_slot_is_not_auto_tracked_and_does_not_invalidate():
    """V3 (`vision_enabled=True`): an optional schema param that happens
    to also be the target of the goal's open perception question must be
    excluded from the new schema-complete tracking -- V3's own claim-
    acceptance path already governs it, and without this exemption a
    later HIGH-confidence vision claim would retroactively invalidate
    already-dispatched, unrelated work purely because no call ever bound
    that name (`docs/prompt 3.txt` C6 point 2: "Policy absence_sensitivity
    (default: user-sourced facts only) prevents a later perception claim
    for an optional parameter from retroactively invalidating completed
    work")."""
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "check my ticket",
        {
            "act": "new_goal",
            "intent": "status_check",
            "slot_deltas": [{"name": "ticket_id", "scope": "goal", "op": "set", "value": "T-100"}],
            "visual_reference": "at_utterance",
            "visual_candidates": [{"name": "device_model", "description": "the device shown"}],
        },
    )
    provider.register(
        "plan",
        "goal_intent: status_check",
        {
            "steps": [
                {
                    "local_id": "s1",
                    "tool": "status_tool",
                    "kind": "read",
                    "bindings": {"ticket_id": {"type": "fact", "key": "slot.$G.ticket_id"}},
                    "after": [],
                }
            ]
        },
    )
    provider.register("compose", "s1", {"text": "Ticket found.", "claims": ["result:s1"]})
    provider.register(
        "vision",
        "device_model",
        {"claims": [{"name": "device_model", "value": "Tab S9", "confidence": "high"}]},
    )

    config = c6_config(vision_enabled=True)
    h = new_harness(config, tools={"status_tool": {"latency_ms": 800, "response": {"status": "ok"}}}, provider=provider)
    h.send(0, [manifest_event([STATUS_TOOL])])
    h.send(50_000, [frame_event("f1")])
    h.send(100_000, [chunk_event("check my ticket")])
    h.send(150_000, [eot_event()])
    drain(h, 250_000, stop_on_final=False)

    call = next(c for c in h.store.call_ledger.all() if c.tool == "status_tool")
    assert call.status == CallStatus.IN_FLIGHT
    keys = {e.key for e in call.read_set.entries}
    # "device_model" is an optional schema param the step never bound --
    # ordinarily schema-complete tracking would add it, but it's also the
    # open question's target, so it must be excluded
    assert "slot.g-0001.device_model" not in keys

    # let the vision claim land (HIGH confidence -> writes slot.g-0001.device_model)
    cancelled = _drive_until_cancelled(h, call.call_id, steps=60)
    assert not cancelled, "a perception claim on a question-governed slot must not invalidate an unrelated in-flight call"
    assert h.store.facts.get("slot.g-0001.device_model") is not None  # the claim really did land
    assert_clean(h)


# --- multiple absent optional params ----------------------------------------


def test_multiple_absent_optional_params_are_all_tracked_independently():
    provider = ScriptedProvider()
    _register_lookup_dispatch(provider)
    provider.register(
        "interpret",
        "business class",
        {"act": "addition", "slot_deltas": [{"name": "cabin_class", "scope": "goal", "op": "set", "value": "business"}]},
    )
    h = new_harness(c6_config(), tools={"lookup_tool": {"latency_ms": 800, "response": {"ok": True}}}, provider=provider)
    h.send(0, [manifest_event([LOOKUP_TOOL])])
    h.send(100_000, [chunk_event("look up widget")])
    h.send(150_000, [eot_event()])
    drain(h, 250_000, stop_on_final=False)

    call = next(c for c in h.store.call_ledger.all() if c.tool == "lookup_tool")
    keys = {e.key for e in call.read_set.entries}
    assert "slot.g-0001.date" in keys and "slot.g-0001.cabin_class" in keys

    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("business class")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])

    cancelled = _drive_until_cancelled(h, call.call_id)
    assert cancelled, "either previously-absent optional param becoming present must invalidate the call"
    assert_clean(h)


# --- FRAME inherits the same completeness (second instance of the pattern) --


def test_frame_read_set_inherits_the_pending_calls_schema_complete_entries():
    """`kernel/frames.py.FrameScheduler.decide` now folds the pending
    call's own `read_set.entries` into the dispatched FRAME job's read
    set -- the same pattern `kernel/task.py._build_compose_request`
    already established (`v10-1-correction-race-fix`). This is what lets
    a frame automatically inherit `_bind`'s schema completeness instead of
    needing its own separate schema walk: a call's read set now already
    carries an absence entry for every unbound optional tool param, so
    folding it into the frame's own read set is enough."""
    from prism_rt.ids import IdGenerator
    from prism_rt.kernel.frames import FrameScheduler
    from prism_rt.model.types import (
        CallRecord,
        CallStatus,
        FactStatus,
        GoalRecord,
        GoalStatus,
        Plan,
        PlanStep,
        Provenance,
        ReadSet,
        ReadSetEntry,
        StepKind,
        TaskState,
        fingerprint_for,
        make_read_set,
    )
    from prism_rt.store.session import SessionStore

    config = c6_config(frame_rendering_enabled=True)
    store = SessionStore.new(config, IdGenerator(1))

    with store.begin_txn(1) as txn:
        txn.catalog.parse_manifest([LOOKUP_TOOL])
        txn.facts.set("goal.active", "g1", FactStatus.COMMITTED, Provenance(source="system"), rule="test")
        txn.goals.create(GoalRecord(goal_id="g1", status=GoalStatus.ACTIVE, task_state=TaskState.EXECUTING))
        txn.facts.set("slot.g1.query", "widget", FactStatus.COMMITTED, Provenance(source="user"), rule="test")
        step = PlanStep(
            step_key="s1",
            tool="lookup_tool",
            kind=StepKind.READ,
            bindings={"query": {"kind": "fact"}},
        )
        txn.plans.create(Plan(goal_id="g1", plan_rev=1, steps=(step,)))

        # Simulates what a post-fix `_bind` now produces: a read set that
        # already carries a schema-complete absence entry ("date") for a
        # param the plan step never bound at all.
        call_read_set = make_read_set(
            [
                ReadSetEntry(key="goal.active", version=1, digest=txn.facts.get("goal.active").digest),
                ReadSetEntry(key="slot.g1.query", version=1, digest=txn.facts.get("slot.g1.query").digest),
                ReadSetEntry(key="slot.g1.date", version=0, digest="__ABSENT__"),
            ]
        )
        args = {"query": "widget"}
        call = CallRecord(
            call_id="c1",
            goal_id="g1",
            step_key="s1",
            tool="lookup_tool",
            kind=StepKind.READ,
            args=args,
            fingerprint=fingerprint_for("lookup_tool", args),
            read_set=call_read_set,
            attempt=1,
            created_step=1,
            status=CallStatus.IN_FLIGHT,
        )
        txn.store.call_ledger.create(call)

        requests = FrameScheduler().decide(txn.store, 0, 1)
        assert len(requests) == 1
        frame_read_keys = {e.key for e in requests[0].read_set.entries}
        # the frame's own read set must include the key the call itself
        # tracked as absent -- not just the slots the frame's own "facts"
        # snapshot happened to contain (query only; date is absent, so it
        # would never appear there on its own)
        assert "slot.g1.date" in frame_read_keys


# --- replay identity ---------------------------------------------------------


def test_c6_replay_identity():
    def run_once() -> list[dict]:
        provider = ScriptedProvider()
        _register_lookup_dispatch(provider)
        provider.register(
            "interpret",
            "for next Tuesday",
            {"act": "addition", "slot_deltas": [{"name": "date", "scope": "goal", "op": "set", "value": "2026-09-29"}]},
        )
        h = new_harness(c6_config(), tools={"lookup_tool": {"latency_ms": 800, "response": {"ok": True}}}, provider=provider)
        h.send(0, [manifest_event([LOOKUP_TOOL])])
        h.send(100_000, [chunk_event("look up widget")])
        h.send(150_000, [eot_event()])
        drain(h, 250_000, stop_on_final=False)
        h.send(h.clock.now_us() + 10_000, [interruption_event()])
        h.send(h.clock.now_us() + 5_000, [chunk_event("for next Tuesday")])
        h.send(h.clock.now_us() + 5_000, [eot_event()])
        drain(h, h.clock.now_us() + 2_000_000)
        return h.log.records

    violations = TraceChecker().check_replay(run_once, times=5)
    assert violations == []
