"""End-to-end through the evaluation kit's own harness and scorer.

Drives the real `agent.agent.ParticipantAgent` (KitCodec + Janus's async
entry point) with the kit's `harness.runner.EvaluationHarness` and scores
the trace with `harness.scorer.score_scenario`. Only the model provider is
replaced, by a ScriptedProvider keyed to the synthetic utterances below --
test fixture data, not the public scenarios, and nothing in the submission
path. The harness runs in scaled real time, so assertions are structural
(protocol-clean, stale work handled, corrected state reported), never
exact timings.
"""

from __future__ import annotations

import asyncio
import os

import agent.agent as agent_module
from agent.agent import ParticipantAgent
from harness.runner import EvaluationHarness
from harness.scorer import score_scenario

from prism_rt.workers.gateway import ScriptedProvider

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SEARCH_PLAN = {
    "steps": [
        {
            "local_id": "s1",
            "tool": "flight_search",
            "kind": "read",
            "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}},
            "after": [],
        }
    ]
}


def _scripted() -> ScriptedProvider:
    p = ScriptedProvider()
    p.register(
        "interpret",
        "switch it to Lima",
        {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Lima"}]},
    )
    p.register(
        "interpret",
        "flights to Oslo",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Oslo"}]},
    )
    p.register("plan", "flight_search", _SEARCH_PLAN)
    p.register("compose", "s1", {"text": "Here are the flights I found.", "claims": ["result:s1"]})
    return p


def _run(scenario: dict, monkeypatch, *, time_scale: float = 4.0) -> list:
    monkeypatch.setattr(agent_module, "_provider", _scripted)

    async def go():
        h = EvaluationHarness(scenario, lambda a, b: ParticipantAgent(a, b), time_scale=time_scale, verbose=False, tail_ms=5000)
        await h.prepare()
        return await h.run()

    return asyncio.run(go())


def _kinds(trace, kind):
    return [e for e in trace if e.get("kind") == kind]


def _actions(trace, name):
    return [e for e in trace if e.get("kind") == "action" and e.get("action") == name]


def test_simple_search_is_protocol_clean_and_grounded(monkeypatch):
    scenario = {
        "scenario_id": "janus_kit_simple",
        "events": [
            {"timestamp_ms": 100, "event_type": "user_speech_chunk", "payload": {"text": "Find me ", "end_of_turn": False}},
            {"timestamp_ms": 500, "event_type": "user_speech_chunk", "payload": {"text": "flights to Oslo", "end_of_turn": True}},
        ],
        "ground_truth": {
            "checkpoints": [
                {"id": "search", "type": "tool_called", "tool": "flight_search", "args_subset": {"destination": ["oslo"]}, "must_complete": True},
                {"id": "state", "type": "state_snapshot", "path": "slots.destination", "any_of": ["oslo"]},
            ]
        },
    }
    trace = _run(scenario, monkeypatch)

    assert _kinds(trace, "protocol_error") == [] and _kinds(trace, "agent_crash") == []
    assert _actions(trace, "filler_speech"), "no acknowledgement spoken"
    [call] = _actions(trace, "tool_call")
    assert call["api_name"] == "flight_search" and call["args"] == {"destination": "Oslo"}
    finals = _actions(trace, "final_response")
    assert len(finals) == 1 and finals[0]["state_snapshot"]["slots"] == {"destination": "Oslo"}
    task = score_scenario(scenario, trace)["breakdown"]["task"]
    assert task["fraction"] == 1.0, task


def test_interruption_cancels_stale_work_and_reports_corrected_state(monkeypatch):
    scenario = {
        "scenario_id": "janus_kit_interrupt",
        "events": [
            {"timestamp_ms": 100, "event_type": "user_speech_chunk", "payload": {"text": "Find me flights to Oslo", "end_of_turn": True}},
            {"timestamp_ms": 900, "event_type": "interruption", "payload": {"text": "Actually, switch it to Lima."}},
        ],
        "ground_truth": {
            "checkpoints": [
                {"id": "lima", "type": "tool_called", "tool": "flight_search", "args_subset": {"destination": ["lima"]}, "after_ms": 900, "must_complete": True},
                {"id": "no_new_oslo", "type": "tool_not_called", "tool": "flight_search", "args_subset": {"destination": ["oslo"]}, "after_ms": 900},
            ],
            "recovery": {
                "interrupt_at_ms": 900,
                "invalidated_calls": [{"tool": "flight_search", "args_subset": {"destination": ["oslo"]}, "invalid_after_ms": 900}],
                "required_state_after_interrupt": {"slots.destination": ["lima"]},
            },
        },
    }
    trace = _run(scenario, monkeypatch)

    assert _kinds(trace, "protocol_error") == [] and _kinds(trace, "agent_crash") == []
    result = score_scenario(scenario, trace)
    assert result["breakdown"]["recovery"]["fraction"] == 1.0, result["breakdown"]["recovery"]
    assert result["breakdown"]["task"]["fraction"] == 1.0, result["breakdown"]["task"]
    # Every cancel names a call Janus itself issued.
    issued = {c["call_id"] for c in _actions(trace, "tool_call")}
    cancelled = {e["call_id"] for e in trace if e.get("kind") in ("tool_cancelled", "cancel_noop")}
    assert cancelled <= issued


def test_eval_submission_stages_1_and_2_accept_the_package():
    import eval_submission

    config, cls, errors = eval_submission.validate_submission(ROOT)
    assert errors == [], errors
    assert config["entry_point"] == "agent.agent:ParticipantAgent" and cls is ParticipantAgent
    assert eval_submission.contract_smoke_test(cls, setup_cap_s=10.0) == []
