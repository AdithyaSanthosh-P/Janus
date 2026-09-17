"""Planner worker: goal intent + facts + catalog -> Plan.

`run_plan` only talks to the gateway and returns the raw dict; parsing into
`Plan`/`PlanStep` is kernel-side (`kernel/proposals.py`) — see
`workers/interpreter.py`'s module docstring for why.
"""

from __future__ import annotations

from prism_rt.workers.gateway import ModelGateway

PLAN_SCHEMA = {
    "type": "object",
    "required": ["steps"],
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["local_id", "tool"],
                "properties": {
                    "local_id": {"type": "string"},
                    "tool": {"type": "string"},
                    "kind": {"type": "string", "enum": ["read", "write"]},
                    "bindings": {"type": "object"},
                    "after": {"type": "array", "items": {"type": "string"}},
                    "output_map": {"type": "object"},
                },
            },
        }
    },
}


def build_prompt(view: dict) -> str:
    return (
        f"goal_intent: {view.get('intent')}\n"
        f"committed_facts: {view.get('facts')}\n"
        f"catalog: {view.get('catalog')}\n"
        f"change_context: {view.get('change_context')}\n"
        "Return JSON matching the schema: an ordered list of steps."
    )


def run_plan(gateway: ModelGateway, view: dict) -> dict:
    return gateway.complete_json("plan", build_prompt(view), PLAN_SCHEMA)
