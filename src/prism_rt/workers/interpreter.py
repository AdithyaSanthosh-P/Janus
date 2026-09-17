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
    },
}


def build_prompt(view: dict) -> str:
    return (
        f"transcript: {view.get('transcript', '')!r}\n"
        f"active_intent: {view.get('active_intent')}\n"
        f"active_slots: {view.get('active_slots')}\n"
        f"suspended_goals: {view.get('suspended_goals')}\n"
        f"pending_clarification: {view.get('pending_clarification')}\n"
        "Classify the act and return JSON matching the schema."
    )


def run_interpret(gateway: ModelGateway, view: dict) -> dict:
    return gateway.complete_json("interpret", build_prompt(view), INTERPRET_SCHEMA)
