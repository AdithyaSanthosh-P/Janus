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
                    "bindings": {
                        "type": "object",
                        "description": (
                            "Map of tool parameter name -> binding object. Each "
                            "binding object is one of: "
                            '{"type": "fact", "key": "<fact key, e.g. slot.$G.destination>"}, '
                            '{"type": "literal", "value": <any>}, '
                            '{"type": "step_output", "step_key": "<local_id of an earlier step>", "path": "<field name>"}.'
                        ),
                    },
                    "after": {"type": "array", "items": {"type": "string"}},
                    "output_map": {"type": "object"},
                },
            },
        }
    },
}


def build_prompt(view: dict) -> str:
    # V3 (added alongside a live-model reliability fix): a pending visual
    # question's targets don't exist as facts *yet* -- nothing else in
    # this prompt tells a live model that binding to one of them is even
    # legal, so it either invents a literal from the transcript or leaves
    # the parameter unbound instead of waiting on the real answer. Not
    # present when no vision question is pending (`kernel/task.py.
    # _build_plan_request`'s docstring), so this is additive and doesn't
    # change any existing ScriptedProvider test's matched substring.
    pending_visual_targets = view.get("pending_visual_targets") or []
    visual_block = ""
    if pending_visual_targets:
        lines = "\n".join(f"  - {t['name']}: {t['description']}" for t in pending_visual_targets)
        visual_block = (
            "\nA visual question is pending -- these facts don't exist yet "
            f"but WILL once it's answered (a step bound to one just waits "
            f"until then, same as any other fact):\n{lines}\n"
            'Bind a parameter needing one of these to {"type": "fact", '
            '"key": "slot.$G.<name>"}, exactly like a committed_facts slot.\n'
        )
    return (
        f"goal_intent: {view.get('intent')}\n"
        f"committed_facts: {view.get('facts')}\n"
        f"catalog: {view.get('catalog')}\n"
        f"change_context: {view.get('change_context')}\n"
        f"{visual_block}"
        "Return JSON matching the schema: an ordered list of steps."
    )


def run_plan(gateway: ModelGateway, view: dict) -> dict:
    return gateway.complete_json("plan", build_prompt(view), PLAN_SCHEMA)
