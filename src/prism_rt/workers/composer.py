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
    # Day 2 WP3 (docs/fdb_v3_day2_plan.md): found live -- without being
    # told which tools actually ran, the Composer wrote "your flight has
    # been successfully confirmed" from a search_flights result alone
    # (book_flight was never planned/run for that turn). `executed_calls`
    # and `not_done` are additive (absent for any caller that predates
    # this, e.g. every ScriptedProvider-driven test) -- when present,
    # the instruction below is explicit that only what's listed as
    # executed may be reported as done.
    executed_calls = view.get("executed_calls")
    not_done = view.get("not_done")
    honesty_block = ""
    if executed_calls is not None:
        honesty_block = (
            f"\nexecuted_calls (the ONLY actions that actually ran): {executed_calls}\n"
            "Report only what executed_calls shows -- never say something was "
            "booked, added, confirmed, updated, or tracked unless the matching "
            "tool is actually in that list. State the concrete values from "
            "facts/effects (IDs, prices, dates) for what did run."
        )
        if not_done:
            honesty_block += f" Plainly say that {not_done} was not completed.\n"
        else:
            honesty_block += "\n"
        skipped = view.get("skipped")
        if skipped:
            honesty_block += (
                f"skipped (deliberately NOT run, because the condition the user set did not hold): {skipped}. "
                "Say briefly that each was not done and why, from the results (e.g. \"the earliest table is at 9, "
                "so I didn't book it\"); if the condition was the user's own call, offer to do it if they want.\n"
            )
    return (
        f"facts: {view.get('facts')}\n"
        f"effects: {view.get('effects')}\n"
        f"open_questions: {view.get('open_questions')}\n"
        f"{honesty_block}"
        "Write a short, grounded response and return JSON matching the schema. "
        "It is spoken aloud by a voice agent: one or two plain sentences, no "
        "markdown, bullets, lists or headings, and only the key result of each "
        "action (found live: a bulleted, bolded paragraph was read out and cut "
        "off mid-sentence)."
    )


def run_compose(gateway: ModelGateway, view: dict) -> dict:
    return gateway.complete_json("compose", build_prompt(view), COMPOSE_SCHEMA)
