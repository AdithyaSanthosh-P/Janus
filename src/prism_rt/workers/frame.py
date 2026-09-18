"""FrameWriter worker: writes a response *template* ahead of the result.

C2 (`docs/prompt 3.txt`, Phase 5 of `docs/post_v4_implementation_plan.md`).
Dispatched once the plan's last required call is *emitted* (in flight),
not once it resolves — the model has nothing but the tool's declared
output schema and the goal's facts to go on, and writes three branch
templates (success, empty, error) with typed holes bound to result paths
via a fixed operator set (`field`, `count`, `min`/`max` by field,
`first_k`, `exists`). No result data reaches the model here; grounding
comes from the kernel evaluating the holes against the *real* result once
it lands (`kernel/frames.py`) — the model can invent wording, never
values.

Same `ScriptedProvider`-mocked pattern as every other worker; parsing the
raw response is kernel-side (`kernel/proposals.py.parse_frame`).
"""

from __future__ import annotations

from prism_rt.workers.gateway import ModelGateway

_HOLE_SCHEMA = {
    "type": "object",
    "required": ["op"],
    "properties": {
        "op": {"type": "string", "enum": ["field", "count", "min", "max", "first_k", "exists"]},
        "path": {"type": "string"},
        "field": {"type": "string"},
        "k": {"type": "integer"},
    },
}

_BRANCH_SCHEMA = {
    "type": "object",
    "required": ["template"],
    "properties": {
        "template": {"type": "string"},
        "holes": {"type": "object", "additionalProperties": _HOLE_SCHEMA},
    },
}

FRAME_SCHEMA = {
    "type": "object",
    "required": ["branches"],
    "properties": {
        "list_path": {"type": "string"},
        "branches": {
            "type": "object",
            "required": ["success", "empty", "error"],
            "properties": {"success": _BRANCH_SCHEMA, "empty": _BRANCH_SCHEMA, "error": _BRANCH_SCHEMA},
        },
    },
}


def build_prompt(view: dict) -> str:
    return (
        f"intent: {view.get('intent')}\n"
        f"facts: {view.get('facts')}\n"
        f"tool: {view.get('tool')}\n"
        f"tool_output_schema: {view.get('output_schema')}\n"
        "Write a response FRAME in advance, before the tool's result exists: three branch "
        "templates (success, empty, error), each a short sentence with {hole_name} placeholders. "
        "Every hole must use one of these operators against the tool's declared output schema: "
        "field (a scalar path), count (list length at path), min/max (by field, across a list at "
        "path), first_k (first k values of field from a list at path), exists (boolean at path). "
        "Optionally declare list_path: the path to the primary list this response is about, used "
        "to pick the empty branch when that list resolves to []. Return JSON matching the schema."
    )


def run_frame(gateway: ModelGateway, view: dict) -> dict:
    return gateway.complete_json("frame", build_prompt(view), FRAME_SCHEMA)
