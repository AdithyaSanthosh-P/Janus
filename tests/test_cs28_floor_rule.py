"""CS Safety-Check Audit: CS-28 (`docs/theme05_implementation_blueprint.md`
line 2325, `docs/prompt 2.txt` line 370 -- "Floor rule. No SPEAK, CLARIFY,
or FINAL is emitted while the floor region is USER_TURN_OPEN (policy
speak_during_open_turn, default OFF). Talking over the user harms
naturalness and usually refers to soon-to-change information.").

Before this fix, nothing in the kernel ever read `store.floor_state`
outside `CommitGate` G3 (which only gates *writes*) -- `kernel/
responder.py.FastResponder`, the sole source of every SPEAK/CLARIFY/FINAL
action in this codebase, spoke unconditionally the moment its own
per-action trigger condition became true, with no floor check at all.

Two concrete, ordinary (not contrived) races reproduce a real violation:
1. A goal's tool call takes real time to resolve; the user starts a new,
   unrelated utterance before it resolves (floor reopens); FINAL still
   fires the instant the tool result lands, talking over the user.
2. A write gets cancelled by a correction, but its real result confirms
   late anyway (`CallStatus.COMPLETED_AFTER_CANCEL`, I-15's own scenario);
   if the user starts a new utterance before that late result arrives,
   the reconciliation notice ("the earlier booking had already gone
   through...") still fires while they're mid-sentence.

Fixed with the smallest possible change: `FastResponder.decide` (the only
place these three action types originate) now returns `[]` immediately
when `floor_state == USER_TURN_OPEN` and `Config.speak_during_open_turn`
is off (the doc's own default) -- before any of its own "already handled"
facts get set, so a floor-blocked notice is deferred to the next step the
floor is closed, never silently dropped. New `Config.speak_during_open_turn`
(default `False`) makes the doc's named policy configurable, matching
every other named "unknown" in this codebase.
"""

from __future__ import annotations

from conftest import BOOK_FLIGHT_TOOL, SEARCH_FLIGHTS_TOOL, FAST_WORKER_LATENCY, chunk_event, drain, eot_event, interruption_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, FloorState, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider


def new_harness(config, tools=None, provider=None, **kwargs):
    kwargs.setdefault("worker_latency_us", FAST_WORKER_LATENCY)
    return SimHarness(config, seed=1, tools=tools, provider=provider, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def _flat_plan(tool: str, *, kind: str = "read", param: str = "destination") -> dict:
    return {"steps": [{"local_id": "s1", "tool": tool, "kind": kind, "bindings": {param: {"type": "fact", "key": f"slot.$G.{param}"}}, "after": []}]}


# --- FINAL must not fire while an unrelated new utterance is in progress ---


def _search_provider() -> ScriptedProvider:
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register("interpret", "what time is it", {"act": "addition", "slot_deltas": []})
    provider.register("plan", "goal_intent: search_flights", _flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Pune flights found.", "claims": ["result:s1"]})
    return provider


def test_final_is_never_emitted_while_the_floor_is_open():
    config = Config()  # speak_during_open_turn defaults False -- floor rule enforced
    h = new_harness(config, tools={"search_flights": {"latency_ms": 800, "response": {"flight_id": "AI-1"}}}, provider=_search_provider())
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 250_000, stop_on_final=False)

    # The user starts a second, unrelated utterance before the 800ms tool
    # call resolves -- the floor reopens and stays open (no EOT yet).
    h.send(h.clock.now_us() + 10_000, [chunk_event("what time is it")])

    seen_final = False
    t = h.clock.now_us()
    for _ in range(120):
        t += 10_000
        report = h.advance(t)
        for er in report.emit_report.emitted:
            if er.action.action_type == ActionType.FINAL:
                seen_final = True
                assert h.store.floor_state == FloorState.USER_TURN_CLOSED, (
                    "FINAL was emitted while the floor was open -- CS-28 violation"
                )
    # The tool result had well over 800ms to land in this window -- if
    # FINAL never appeared at all, this test isn't exercising the race.
    assert not seen_final, "with the floor never closing again in this window, FINAL must stay deferred, not silently vanish"


def test_final_is_deferred_not_dropped_and_fires_once_the_floor_closes():
    config = Config()
    h = new_harness(config, tools={"search_flights": {"latency_ms": 800, "response": {"flight_id": "AI-1"}}}, provider=_search_provider())
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 250_000, stop_on_final=False)

    h.send(h.clock.now_us() + 10_000, [chunk_event("what time is it")])
    # The second utterance finishes well after the first goal's tool
    # result would already have landed -- FINAL must have been waiting,
    # not lost. One more drain lets the (now-unblocked) COMPOSE round trip
    # actually complete -- FINAL isn't necessarily in this exact step's
    # own report, just no longer blocked from ever happening.
    h.send(h.clock.now_us() + 900_000, [eot_event()])
    actions = drain(h, h.clock.now_us() + 200_000, stop_on_final=False)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.text == "Pune flights found."
    assert h.store.goals.all()[0].status.value == "completed"
    assert_clean(h)


def test_final_fires_immediately_when_speak_during_open_turn_is_enabled():
    """Flag-off (permissive) parity check: the restriction is genuinely
    opt-out, not hard-coded -- setting the policy to its non-default value
    restores the old (unsafe) behavior exactly."""
    config = Config(speak_during_open_turn=True)
    h = new_harness(config, tools={"search_flights": {"latency_ms": 800, "response": {"flight_id": "AI-1"}}}, provider=_search_provider())
    h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 250_000, stop_on_final=False)

    h.send(h.clock.now_us() + 10_000, [chunk_event("what time is it")])
    seen_final = False
    t = h.clock.now_us()
    for _ in range(120):
        t += 10_000
        report = h.advance(t)
        for er in report.emit_report.emitted:
            if er.action.action_type == ActionType.FINAL:
                seen_final = True
    assert seen_final, "with the policy flag on, FINAL should fire immediately even while the floor is open"


# --- reconciliation notice (I-15) must not talk over a new utterance -------


def _reconcile_provider() -> ScriptedProvider:
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Book the flight",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True},
    )
    provider.register("interpret", "wait cancel that", {"act": "abort", "slot_deltas": []})
    provider.register("interpret", "actually never mind", {"act": "addition", "slot_deltas": []})
    provider.register("plan", "book_flight", _flat_plan("book_flight", kind="write", param="flight_id"))
    return provider


def _v2_config(**overrides) -> Config:
    """Matches `test_v2.py`'s own `v2_config()` exactly: every V2 flag
    starts False so this test is isolated from unrelated V2 behavior
    (in particular the settle barrier, which would otherwise shift every
    hand-picked timing offset below)."""
    return Config(
        transitive_invalidation=overrides.pop("transitive_invalidation", False),
        settle_barrier_enabled=overrides.pop("settle_barrier_enabled", False),
        absence_read_sets=overrides.pop("absence_read_sets", False),
        claim_grades_enabled=overrides.pop("claim_grades_enabled", False),
        rebinder_enabled=overrides.pop("rebinder_enabled", False),
        **overrides,
    )


def test_reconcile_inform_is_never_emitted_while_the_floor_is_open():
    config = _v2_config()
    h = new_harness(config, tools={"book_flight": {"latency_ms": 800, "response": {"confirmation": "XYZ"}}}, provider=_reconcile_provider())
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book the flight")])
    h.send(150_000, [eot_event()])
    drain(h, 300_000, stop_on_final=False)
    call = next(c for c in h.store.call_ledger.all() if c.status == CallStatus.IN_FLIGHT)

    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("wait cancel that")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])
    drain(h, h.clock.now_us() + 50_000, stop_on_final=False)

    # A new utterance starts before the cancelled write's late result
    # confirms -- the floor reopens and stays open.
    h.send(h.clock.now_us() + 10_000, [chunk_event("actually never mind")])

    reconcile_seen = False
    t = h.clock.now_us()
    for _ in range(150):
        t += 10_000
        report = h.advance(t)
        for er in report.emit_report.emitted:
            if er.action.action_type == ActionType.SPEAK and "gone through" in (er.action.body.text or ""):
                reconcile_seen = True
                assert h.store.floor_state == FloorState.USER_TURN_CLOSED
    assert h.store.call_ledger.get(call.call_id).status == CallStatus.COMPLETED_AFTER_CANCEL  # the race really did happen
    assert not reconcile_seen, "with the floor never closing again in this window, the reconcile notice must stay deferred"


def test_reconcile_inform_fires_once_the_floor_closes():
    config = _v2_config()
    h = new_harness(config, tools={"book_flight": {"latency_ms": 800, "response": {"confirmation": "XYZ"}}}, provider=_reconcile_provider())
    h.send(0, [manifest_event([BOOK_FLIGHT_TOOL])])
    h.send(100_000, [chunk_event("Book the flight")])
    h.send(150_000, [eot_event()])
    drain(h, 300_000, stop_on_final=False)

    h.send(h.clock.now_us() + 10_000, [interruption_event()])
    h.send(h.clock.now_us() + 5_000, [chunk_event("wait cancel that")])
    h.send(h.clock.now_us() + 5_000, [eot_event()])
    drain(h, h.clock.now_us() + 50_000, stop_on_final=False)

    h.send(h.clock.now_us() + 10_000, [chunk_event("actually never mind")])
    report = h.send(h.clock.now_us() + 900_000, [eot_event()])
    reconciles = [er.action for er in report.emit_report.emitted if er.action.action_type == ActionType.SPEAK and "gone through" in (er.action.body.text or "")]
    assert len(reconciles) == 1
    assert h.store.floor_state == FloorState.USER_TURN_CLOSED
    assert_clean(h)


def test_cs28_replay_identity():
    def run_once() -> list[dict]:
        config = Config()
        h = new_harness(config, tools={"search_flights": {"latency_ms": 800, "response": {"flight_id": "AI-1"}}}, provider=_search_provider())
        h.send(0, [manifest_event([SEARCH_FLIGHTS_TOOL])])
        h.send(100_000, [chunk_event("Find flights to Pune")])
        h.send(150_000, [eot_event()])
        drain(h, 250_000, stop_on_final=False)
        h.send(h.clock.now_us() + 10_000, [chunk_event("what time is it")])
        h.send(h.clock.now_us() + 900_000, [eot_event()])
        drain(h, h.clock.now_us() + 500_000, stop_on_final=False)
        return h.log.records

    violations = TraceChecker().check_replay(run_once, times=5)
    assert violations == []
