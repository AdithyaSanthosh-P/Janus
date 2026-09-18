"""ASR worker: an audio clip -> transcript segments.

Same `ScriptedProvider`-mocked pattern as every other worker — no test in
this repository calls a real speech model. `AsrScheduler`
(`kernel/audio.py`) is the sole caller of `run_asr`'s result; parsing the
raw response into typed segments happens there rather than here, mirroring
`workers/interpreter.py`'s "parsing is kernel-side" convention.

Like `workers/vision.py`, the clip is referenced by id only — the
`Provider` protocol is text-only, so no audio bytes are ever sent through
this path (`docs/post_v4_implementation_plan.md` Phase 3 covers a real,
image/audio-aware live provider; not built yet).
"""

from __future__ import annotations

from prism_rt.workers.gateway import ModelGateway

ASR_SCHEMA = {
    "type": "object",
    "required": ["segments"],
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text", "offset_us", "end_us"],
                "properties": {
                    "text": {"type": "string"},
                    "offset_us": {"type": "integer"},
                    "end_us": {"type": "integer"},
                },
            },
        },
        "end_of_utterance": {"type": "boolean"},
    },
}


def build_prompt(view: dict) -> str:
    return (
        f"clip: {view.get('obs_id')}\n"
        "Transcribe the audio clip and return JSON matching the schema: an "
        "ordered list of segments (text, offset_us, end_us relative to the "
        "clip start), and whether the clip ends mid-utterance or at one "
        "(end_of_utterance). Preserve hesitations and self-repairs verbatim "
        "— do not clean up disfluencies."
    )


def run_asr(gateway: ModelGateway, view: dict) -> dict:
    return gateway.complete_json("asr", build_prompt(view), ASR_SCHEMA)
