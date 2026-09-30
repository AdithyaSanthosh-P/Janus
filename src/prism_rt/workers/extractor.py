"""Extractor worker: transcript + one parameter's schema -> {value}.

Q5 (win_plan §6.2, "re-read before asking"): before a generic "what
should X be" CLARIFY goes out, `kernel/task.py.TaskStateMachine`
dispatches one of these -- a narrow, single-parameter question over the
full conversation so far, since the user may already have said the value
under different phrasing than whatever the broader INTERPRET pass bound.
Same `run_*`/`build_prompt` split every other worker module uses, so
`kernel/task.py`/`workers/runner.py` need no special-casing for this
job kind beyond dispatch.
"""

from __future__ import annotations

from prism_rt.workers.gateway import ModelGateway

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {"value": {}},
    "required": [],
}


def build_prompt(view: dict) -> str:
    return (
        f"transcript: {view.get('transcript', '')!r}\n"
        f"Does the transcript state a value for the parameter "
        f"{view.get('param_name')!r} ({view.get('param_description') or 'no description given'})? "
        "Return {\"value\": <the value, copied exactly as stated>} if the "
        "user did state one, anywhere in the conversation so far -- or "
        "{\"value\": null} if it genuinely was never stated. Never guess "
        "or invent a value; null is the correct answer when unsure."
    )


def run_extract(gateway: ModelGateway, view: dict) -> dict:
    return gateway.complete_json("extract", build_prompt(view), EXTRACT_SCHEMA)
