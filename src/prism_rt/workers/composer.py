"""Composer worker: ResponsePlan (facts + effects + open questions) -> text.

V1 keeps `claims` as opaque strings naming what the text asserts, not the
full ClaimGrade-typed system — that grading/enforcement lands in V2
(`docs/sonnet_implementation_plan.md` §4 VERSION 2, "claim-typed
emission"). The template fallback that makes truth-check failures cost only
naturalness, never truthfulness, lives in kernel/responder.py. Parsing the
raw response is kernel-side (`kernel/proposals.py`) — see
`workers/interpreter.py`'s module docstring for why.
"""

from __future__ import annotations

from prism_rt.workers.gateway import ModelGateway

COMPOSE_SCHEMA = {
    "type": "object",
    "required": ["text"],
    "properties": {
        "text": {"type": "string"},
        "claims": {"type": "array", "items": {"type": "string"}},
    },
}


def build_prompt(view: dict) -> str:
    return (
        f"facts: {view.get('facts')}\n"
        f"effects: {view.get('effects')}\n"
        f"open_questions: {view.get('open_questions')}\n"
        "Write a short, grounded response and return JSON matching the schema."
    )


def run_compose(gateway: ModelGateway, view: dict) -> dict:
    return gateway.complete_json("compose", build_prompt(view), COMPOSE_SCHEMA)
