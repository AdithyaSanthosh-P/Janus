"""Interpreter worker: transcript + context -> TurnInterpretation.

`run_interpret` only talks to the gateway and returns the raw dict. Parsing
that into the typed, validated `TurnInterpretation` lives on the kernel
side (`kernel/proposals.py`) so the acceptance/rejection decision can live
next to read-set validation, without this module needing to import store
or kernel (package boundary, `docs/prompt 2.txt` §21.1).
"""

from __future__ import annotations

from prism_rt.model.types import InterpretAct
from prism_rt.workers.gateway import ModelGateway

INTERPRET_SCHEMA = {
    "type": "object",
    "required": ["act"],
    "properties": {
        "act": {"type": "string", "enum": [a.value for a in InterpretAct]},
        "intent": {"type": ["string", "null"]},
        "slot_deltas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "scope": {"type": "string", "enum": ["goal", "session"]},
                    "op": {"type": "string", "enum": ["set", "clear"]},
                    "value": {},
                },
            },
        },
        "commit_intent": {"type": "boolean"},
        "resume_goal_id": {"type": ["string", "null"]},
        "ack_phrase": {"type": ["string", "null"]},
        "visual_reference": {"type": "string", "enum": ["none", "at_utterance", "current_state"]},
        "visual_candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
            },
        },
    },
}


def build_prompt(view: dict) -> str:
    # `tools` (added alongside a live-model reliability fix): every
    # slot_deltas[].name a live model emits must exactly match one of
    # these tool parameter names, or it binds to nothing and the kernel
    # asks a spurious clarifying question instead of proceeding -- see
    # `kernel/task.py._build_interpret_request`'s docstring for the full
    # story. Not present for ScriptedProvider-driven tests (canned
    # responses don't read the prompt), so this is additive and doesn't
    # change any existing test's matched substring.
    tools = view.get("tools") or []
    tools_block = ""
    if tools:
        lines = "\n".join(f"  - {t['name']}: requires {t['required_params']}" for t in tools)
        tools_block = (
            "\nAvailable tools (a slot_deltas[].name MUST exactly match one of "
            f"these required_params, never a synonym):\n{lines}\n"
            "Example: for a tool requiring [\"destination\"], \"book me a flight to "
            "Pune\" -> slot_deltas: [{\"name\": \"destination\", \"scope\": \"goal\", "
            "\"op\": \"set\", \"value\": \"Pune\"}], not {\"name\": \"city\", ...} or "
            "{\"name\": \"to\", ...}.\n"
        )
    return (
        f"transcript: {view.get('transcript', '')!r}\n"
        f"active_intent: {view.get('active_intent')}\n"
        f"active_slots: {view.get('active_slots')}\n"
        f"suspended_goals: {view.get('suspended_goals')}\n"
        f"pending_clarification: {view.get('pending_clarification')}\n"
        f"{tools_block}"
        "Classify the act and return JSON matching the schema."
    )


def run_interpret(gateway: ModelGateway, view: dict) -> dict:
    return gateway.complete_json("interpret", build_prompt(view), INTERPRET_SCHEMA)
