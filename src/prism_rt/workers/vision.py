"""VisionAnalyzer worker: a frame + question targets -> claims.

Like `workers/interpreter.py`/`planner.py`/`composer.py`, `run_vision` only
talks to the gateway and returns the raw dict; parsing into typed
`PerceptionClaim`s is kernel-side (`kernel/proposals.py`).

The prompt always carries the frame by reference (`obs_id`/targets) —
never pixel data — matching every test in this project (100%
`ScriptedProvider`, per "Mock first, live second"; the kernel/store layer
never touches bytes, by design). Phase 3 (`docs/post_v4_implementation_plan.md`)
adds a `media` passthrough for live use: `workers/runner.py`'s optional
`blob_resolver` resolves `view["frame_id"]` (the harness-given reference,
not this project's own internal `obs_id`) into real bytes *outside* the
kernel, and hands them to `run_vision` as an ordinary function argument —
this module still never reads or resolves a blob itself."""

from __future__ import annotations

from prism_rt.workers.gateway import MediaPart, ModelGateway

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


def run_vision(gateway: ModelGateway, view: dict, *, media: list[MediaPart] | None = None) -> dict:
    return gateway.complete_json("vision", build_prompt(view), VISION_SCHEMA, media=media)
