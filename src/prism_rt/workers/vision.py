"""VisionAnalyzer worker: a frame + question targets -> claims.

Like `workers/interpreter.py`/`planner.py`/`composer.py`, `run_vision` only
talks to the gateway and returns the raw dict; parsing into typed
`PerceptionClaim`s is kernel-side (`kernel/proposals.py`).

The existing `Provider.complete_json(kind, prompt, schema) -> dict` protocol
(`workers/gateway.py`) is text-only — no caller in this project passes image
bytes through it (`GeminiProvider`, added earlier for live text-worker
validation, is scoped the same way). So this worker's prompt carries the
frame only by reference (`obs_id`/targets), never pixel data: a real
image-grounded live call would need a second, image-aware Provider method,
which is out of scope here (V3's tests are 100% `ScriptedProvider`, matching
`docs/sonnet_implementation_plan.md`'s "Mock first, live second" — live
vision has never been exercised, same disclosure as `currentStatus.md`'s
Known Risks already carries for `AnthropicProvider`)."""

from __future__ import annotations

from prism_rt.workers.gateway import ModelGateway

VISION_SCHEMA = {
    "type": "object",
    "required": ["claims"],
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "value", "confidence"],
                "properties": {
                    "name": {"type": "string"},
                    "value": {},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
            },
        }
    },
}


def build_prompt(view: dict) -> str:
    return (
        f"frame: {view.get('obs_id')}\n"
        f"question_mode: {view.get('mode')}\n"
        f"targets: {view.get('targets')}\n"
        "Analyze the frame for the listed targets and return JSON matching the "
        "schema: one claim per target with name/value/confidence (high, medium, or low)."
    )


def run_vision(gateway: ModelGateway, view: dict) -> dict:
    return gateway.complete_json("vision", build_prompt(view), VISION_SCHEMA)
