"""V3 acceptance tests: 5 multimodal scenarios from
`docs/sonnet_implementation_plan.md` §4 VERSION 3 (N-05, M-06, M-07, M-08,
M-03), driven through ScriptedProvider + SimHarness exactly like V1/V2 —
`workers/vision.py`'s ScriptedProvider rules stand in for the real
vision-capable API the same way every other worker is mocked (see that
module's docstring for why no test here touches the network).

Every test builds its own `Config` with `vision_enabled=True` plus only the
V3-adjacent flags it needs — same isolation pattern `test_v2.py` established
(`v2_config`), so `test_v0.py`/`test_v1.py`/`test_v2.py` stay unaffected by
this file existing.
"""

from __future__ import annotations

from conftest import (
    FAST_WORKER_LATENCY,
    chunk_event,
    drain,
    eot_event,
    frame_event,
    interruption_event,
    manifest_event,
)

from prism_rt.config import Config
from prism_rt.model.types import ActionType, GoalStatus, JobKind, QuestionStatus
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

MANUAL_LOOKUP_TOOL = {
    "name": "manual_lookup",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"device_model": {"type": "string"}, "visible_state": {"type": "string"}},
        "required": ["device_model", "visible_state"],
    },
}

CHECK_STATUS_TOOL = {
    "name": "check_status",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"indicator_state": {"type": "string"}}, "required": ["indicator_state"]},
}

IDENTIFY_COMPONENT_TOOL = {
    "name": "identify_component",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"component": {"type": "string"}}, "required": ["component"]},
}

SEARCH_FLIGHTS_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"destination": {"type": "string"}}, "required": ["destination"]},
}


def v3_config(**overrides) -> Config:
    return Config(
        transitive_invalidation=overrides.pop("transitive_invalidation", False),
        settle_barrier_enabled=overrides.pop("settle_barrier_enabled", False),
        absence_read_sets=overrides.pop("absence_read_sets", False),
        claim_grades_enabled=overrides.pop("claim_grades_enabled", False),
        rebinder_enabled=overrides.pop("rebinder_enabled", False),
        vision_enabled=overrides.pop("vision_enabled", True),
        **overrides,
    )


def new_harness(config, tools=None, provider=None, worker_latency_us=None, **kwargs):
    latency = dict(FAST_WORKER_LATENCY)
    latency[JobKind.VISION] = 10_000
    if worker_latency_us:
        latency.update(worker_latency_us)
    return SimHarness(config, seed=1, tools=tools, provider=provider, worker_latency_us=latency, **kwargs)


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def slot_plan(tool: str, bindings: dict) -> dict:
    return {"steps": [{"local_id": "s1", "tool": tool, "kind": "read", "bindings": bindings, "after": []}]}


# --- N-05: visual lookup happy path -------------------------------------


def test_n05_visual_lookup_happy_path():
    config = v3_config()
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "what does this light mean",
        {"act": "new_goal", "intent": "device_help", "visual_reference": "at_utterance"},
    )
    provider.register(
        "plan",
        "device_help",
        slot_plan(
            "manual_lookup",
            {
                "device_model": {"type": "fact", "key": "slot.$G.device_model"},
                "visible_state": {"type": "fact", "key": "slot.$G.visible_state"},
            },
        ),
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

    h = new_harness(
        config,
        tools={"manual_lookup": {"latency_ms": 50, "response": {"meaning": "low battery"}}},
        provider=provider,
    )
    h.send(0, [manifest_event([MANUAL_LOOKUP_TOOL])])
    h.send(50_000, [frame_event("f1")])
    h.send(100_000, [chunk_event("what does this light mean")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 3_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.text == "That blinking amber light means low battery."
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED
    q = h.store.evidence.get_question("q-0001")
    assert q is not None and q.status == QuestionStatus.ANSWERED
    assert_clean(h)


# --- M-06: conflicting visual/text evidence ------------------------------


def test_m06_conflicting_visual_text():
    config = v3_config()
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "light on my Tab A",
        {
            "act": "new_goal",
            "intent": "device_help",
            "slot_deltas": [{"name": "device_model", "scope": "goal", "op": "set", "value": "Tab A"}],
            "visual_reference": "at_utterance",
        },
    )
    provider.register(
        "plan",
        "device_help",
        slot_plan(
            "manual_lookup",
            {
                "device_model": {"type": "fact", "key": "slot.$G.device_model"},
                "visible_state": {"type": "fact", "key": "slot.$G.visible_state"},
            },
        ),
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

    h = new_harness(
        config,
        tools={"manual_lookup": {"latency_ms": 50, "response": {"meaning": "low battery"}}},
        provider=provider,
    )
    h.send(0, [manifest_event([MANUAL_LOOKUP_TOOL])])
    h.send(50_000, [frame_event("f1")])
    h.send(100_000, [chunk_event("light on my Tab A")])
    h.send(150_000, [eot_event()])

    actions = drain(h, 2_000_000, stop_on_final=False)
    clarifies = [a for a in actions if a.action_type == ActionType.CLARIFY]
    assert len(clarifies) == 1
    assert "Tab S9" in clarifies[0].body.text and "Tab A" in clarifies[0].body.text
    assert ActionType.TOOL_CALL not in [a.action_type for a in actions]
    assert ActionType.FINAL not in [a.action_type for a in actions]
    assert h.store.goals.all()[0].status == GoalStatus.ACTIVE
    conflict = h.store.evidence.get_conflict("g-0001", "device_model")
    assert conflict is not None and conflict.status.value == "open"
    # slot value untouched by the (unaccepted) perception claim
    assert h.store.facts.get("slot.g-0001.device_model").value == "Tab A"
    assert_clean(h)


# --- M-07: multimodal cancellation (goal replacement) --------------------


def test_m07_multimodal_cancellation():
    config = v3_config()
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "what does this light mean",
        {"act": "new_goal", "intent": "device_help", "visual_reference": "at_utterance"},
    )
    provider.register(
        "interpret",
        "search flights to Delhi instead",
        {
            "act": "new_goal",
            "intent": "search_flights",
            "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}],
        },
    )
    provider.register(
        "plan",
        "search_flights",
        slot_plan("search_flights", {"destination": {"type": "fact", "key": "slot.$G.destination"}}),
    )
    provider.register(
        "vision",
        "device_model",
        {"claims": [{"name": "device_model", "value": "Tab S9", "confidence": "high"}]},
    )
    provider.register("compose", "s1", {"text": "Found a flight to Delhi.", "claims": ["result:s1"]})

    h = new_harness(
        config,
        tools={"search_flights": {"latency_ms": 50, "response": {"flight_id": "AI-1"}}},
        provider=provider,
        worker_latency_us={JobKind.VISION: 30_000},  # resolves after the goal has already been replaced
    )
    h.send(0, [manifest_event([MANUAL_LOOKUP_TOOL, SEARCH_FLIGHTS_TOOL])])
    h.send(50_000, [frame_event("f1")])
    h.send(100_000, [chunk_event("what does this light mean")])
    h.send(150_000, [eot_event()])  # -> g-0001 device_help, INTERPRET due 160_000

    h.send(161_000, [interruption_event()])
    h.send(162_000, [chunk_event("search flights to Delhi instead")])
    h.send(163_000, [eot_event()])  # -> INTERPRET#2 due 173_000, replaces goal.active before VISION (due 190_000) resolves

    actions = drain(h, 3_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.text == "Found a flight to Delhi."

    q1 = h.store.evidence.get_question("q-0001")
    assert q1 is not None and q1.status == QuestionStatus.OPEN  # never answered — result arrived after goal replacement
    assert h.store.facts.get("claim.q-0001.device_model") is None
    assert h.store.jobs.get("j-0002").status.value == "done"  # VISION job settled (dropped), not left running
    assert_clean(h)


# --- M-08: deictic alignment (frame nearest to the cue) -------------------


def test_m08_deictic_alignment():
    config = v3_config()
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "what's wrong with this one",
        {
            "act": "new_goal",
            "intent": "component_help",
            "visual_reference": "at_utterance",
            "visual_candidates": [{"name": "component", "description": "the referenced component"}],
        },
    )
    provider.register(
        "plan",
        "component_help",
        slot_plan("identify_component", {"component": {"type": "fact", "key": "slot.$G.component"}}),
    )
    # Only the SECOND frame (o-0002, sent just before end-of-turn) should
    # ever be analyzed — o-0001 (well before the turn) and o-0003 (after
    # end-of-turn) have no rule and would raise if selected, cleanly
    # failing the test via a missing FINAL instead of a wrong pick.
    provider.register("vision", "o-0002", {"claims": [{"name": "component", "value": "power button", "confidence": "high"}]})
    provider.register("compose", "s1", {"text": "That's the power button.", "claims": ["result:s1"]})

    h = new_harness(
        config,
        tools={"identify_component": {"latency_ms": 50, "response": {"function": "power"}}},
        provider=provider,
    )
    h.send(0, [manifest_event([IDENTIFY_COMPONENT_TOOL])])
    h.send(20_000, [frame_event("fA")])  # o-0001: well before the turn
    h.send(90_000, [frame_event("fB")])  # o-0002: shortly before end-of-turn — the one that should be picked
    h.send(100_000, [chunk_event("what's wrong with this one")])
    h.send(120_000, [eot_event()])  # anchor_ts_us = 120_000
    h.send(125_000, [frame_event("fC")])  # o-0003: arrives after the turn closed — must be ignored

    actions = drain(h, 2_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.text == "That's the power button."
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED
    assert_clean(h)


# --- M-03: stale frame — lease expiry then renewal before FINAL -----------


def test_m03_stale_frame_renewal():
    config = v3_config(evidence_lease_ms=80)
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "the light keeps blinking",
        {
            "act": "new_goal",
            "intent": "device_watch",
            "visual_reference": "current_state",
            "visual_candidates": [{"name": "indicator_state", "description": "the indicator's current state"}],
        },
    )
    provider.register(
        "plan",
        "device_watch",
        slot_plan("check_status", {"indicator_state": {"type": "fact", "key": "claim.$Q.indicator_state"}}),
    )
    provider.register(
        "vision",
        "indicator_state",
        {"claims": [{"name": "indicator_state", "value": "blinking amber", "confidence": "high"}]},
    )
    provider.register("compose", "s1", {"text": "It's blinking amber.", "claims": ["result:s1"]})

    h = new_harness(
        config,
        tools={"check_status": {"latency_ms": 10, "response": {"ok": True}}},
        provider=provider,
        # PLAN deliberately slow: the first vision claim (accepted ~170_000,
        # 80ms lease -> expires ~250_000) goes stale and gets renewed before
        # the plan even exists to bind against it, so there is no in-flight
        # call to cancel — just a clean "renew, then proceed" sequence.
        worker_latency_us={JobKind.PLAN: 150_000, JobKind.VISION: 10_000},
    )
    h.send(0, [manifest_event([CHECK_STATUS_TOOL])])
    h.send(50_000, [frame_event("f1")])
    h.send(100_000, [chunk_event("the light keeps blinking")])
    h.send(150_000, [eot_event()])  # INTERPRET due 160_000

    actions = drain(h, 3_000_000, step_us=10_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1
    assert finals[0].body.text == "It's blinking amber."
    assert h.store.goals.all()[0].status == GoalStatus.COMPLETED

    q = h.store.evidence.get_question("q-0001")
    assert q is not None and q.created_by == "renewal"  # proves expire_leases reopened it at least once
    assert_clean(h)


# --- replay identity (C1) --------------------------------------------------


def test_v3_replay_identity():
    def run_once() -> list[dict]:
        config = v3_config()
        provider = ScriptedProvider()
        provider.register(
            "interpret",
            "what does this light mean",
            {"act": "new_goal", "intent": "device_help", "visual_reference": "at_utterance"},
        )
        provider.register(
            "plan",
            "device_help",
            slot_plan(
                "manual_lookup",
                {
                    "device_model": {"type": "fact", "key": "slot.$G.device_model"},
                    "visible_state": {"type": "fact", "key": "slot.$G.visible_state"},
                },
            ),
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

        h = new_harness(
            config,
            tools={"manual_lookup": {"latency_ms": 50, "response": {"meaning": "low battery"}}},
            provider=provider,
        )
        h.send(0, [manifest_event([MANUAL_LOOKUP_TOOL])])
        h.send(50_000, [frame_event("f1")])
        h.send(100_000, [chunk_event("what does this light mean")])
        h.send(150_000, [eot_event()])
        drain(h, 3_000_000)
        return h.log.records

    violations = TraceChecker().check_replay(run_once, times=5)
    assert violations == []
