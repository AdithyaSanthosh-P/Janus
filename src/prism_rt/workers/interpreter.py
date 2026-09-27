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
        "requested_actions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "resume_goal_id": {"type": ["string", "null"]},
        "ack_phrase": {"type": ["string", "null"]},
        "visual_reference": {"type": "string", "enum": ["none", "at_utterance", "current_state"]},
        "visual_candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
            },
        },
    },
}


def build_prompt(view: dict) -> str:
    # `tools` (added alongside a live-model reliability fix): every
    # slot_deltas[].name a live model emits must exactly match one of
    # these tool parameter names, or it binds to nothing and the kernel
    # asks a spurious clarifying question instead of proceeding -- see
    # `kernel/task.py._build_interpret_request`'s docstring for the full
    # story. Not present for ScriptedProvider-driven tests (canned
    # responses don't read the prompt), so this is additive and doesn't
    # change any existing test's matched substring.
    tools = view.get("tools") or []
    tools_block = ""
    if tools:
        # Day 1 (docs/fdb_v3_implementation_plan.md §4.1, §9): `params`
        # (type/description/optional/default per parameter, added
        # alongside `kernel/task.py._build_interpret_request`'s own
        # change) is rendered here when present -- absent for any older
        # or test-constructed `view` that only sets `required_params`
        # (e.g. ScriptedProvider-driven tests), so this stays additive
        # and doesn't change any existing test's matched substring.
        def _tool_line(t: dict) -> str:
            line = f"  - {t['name']}: requires {t['required_params']}"
            params = t.get("params") or {}
            if params:
                bits = []
                for pname, pinfo in params.items():
                    bit = pname
                    if pinfo.get("type"):
                        bit += f" ({pinfo['type']})"
                    if not pinfo.get("required", True):
                        default = pinfo.get("default")
                        bit += " [optional" + ("" if default is None else f", default={default!r}") + "]"
                    if pinfo.get("description"):
                        bit += f": {pinfo['description']}"
                    bits.append(bit)
                line += "\n      params: " + "; ".join(bits)
            return line

        lines = "\n".join(_tool_line(t) for t in tools)
        tools_block = (
            "\nAvailable tools (a slot_deltas[].name MUST exactly match one of "
            f"these required_params, never a synonym):\n{lines}\n"
            "Example: for a tool requiring [\"destination\"], \"book me a flight to "
            "Pune\" -> slot_deltas: [{\"name\": \"destination\", \"scope\": \"goal\", "
            "\"op\": \"set\", \"value\": \"Pune\"}], not {\"name\": \"city\", ...} or "
            "{\"name\": \"to\", ...}. An optional parameter's slot name is legal too "
            "(same rule) -- don't skip it just because it isn't required.\n"
        )
    # V3 (found by directly running the live multimodal demo, not by
    # design review): `visual_candidates` had zero prompt guidance --
    # a live model correctly set `visual_reference` on a vision-grounded
    # question but left `visual_candidates` empty, since nothing told it
    # what to put there. Empty candidates make `kernel/perception.py`
    # fall back to `_GENERIC_TARGETS` (device_model/visible_state -- built
    # for this project's own tested device-diagnostic scenario, N-05),
    # which is wrong for any other visual question and gets a legitimately
    # empty claim result back from VISION. Gated on `vision_enabled`
    # (absent/False for every non-V3 test and any harness that never turns
    # vision on) so this doesn't change the prompt at all for the common
    # case -- doesn't change any existing ScriptedProvider test's matched
    # substring either way.
    visual_block = ""
    if view.get("vision_enabled"):
        visual_block = (
            "\nIf answering needs looking at something (an image/video frame "
            "was provided or the user references what they're pointing a "
            "camera at), set visual_reference to \"at_utterance\" (a one-time "
            "look) or \"current_state\" (an ongoing/ambient state, e.g. \"is the "
            "light still on\"), and name what to look for in visual_candidates "
            "using short snake_case names taken from what's literally being "
            "asked -- never leave visual_candidates empty when visual_reference "
            "isn't \"none\". If one of the available tools listed above will "
            "need the answer as a parameter, name the candidate to match that "
            "parameter exactly (same rule as slot_deltas above) instead of "
            "inventing a new name. Example: \"what colors do you see, top and "
            "bottom\" -> visual_candidates: [{\"name\": \"top_half_color\", "
            "\"description\": \"the color of the top half\"}, {\"name\": "
            "\"bottom_half_color\", \"description\": \"the color of the bottom "
            "half\"}].\n"
        )
    # Day 2 WP2 (docs/fdb_v3_day2_plan.md): found live (gemini-3.5-flash-
    # lite, a real 3-action FDB recording) that the Interpreter only ever
    # populates `intent`/`slot_deltas` for the *first* action a turn
    # asks for -- "search X, then add it to my cart, and also track
    # order Y" produced only the search's slot. `requested_actions`
    # (added alongside `intent`, which keeps naming the first/primary
    # action exactly as before -- no existing test's matched substring
    # changes) tells the Planner every tool the turn actually asked for,
    # in order, so it can plan one step per action instead of one step
    # total. Gated on `multi_action_enabled` so this is a no-op prompt
    # change unless Day 2's profile turns it on.
    multi_action_block = ""
    if view.get("multi_action_enabled"):
        multi_action_block = (
            "\nThis turn may ask for more than one action (e.g. \"search for X, "
            "then add it to my cart, and also track order Y\"). List EVERY tool "
            "name the turn asks for, in the order asked, in requested_actions -- "
            "intent should be the *first* one (same value, not a summary). "
            "Extract slot_deltas for *every* action's parameters, not just the "
            "first -- if two different actions need the same parameter name "
            "with two different values (e.g. two order lookups), name the "
            "second occurrence with a \"_2\" suffix (e.g. \"order_id_2\") so "
            "they don't collide. A parameter whose value must come from an "
            "earlier action's own result (a chained argument, e.g. \"the "
            "cheapest one you just found\") is NOT a slot_delta -- leave it "
            "out; the planner resolves it from the prior step's result.\n"
        )
    # Q2 (win_plan §6.2): value-copying rules found necessary against real
    # FDB-v3 recordings (self-corrections, unstated optional params), plus
    # explicit guidance against the confirmed root cause of the
    # housing_11/housing_13 silent stalls (Q4) -- a heavily disfluent,
    # self-correcting first utterance classified as BACKCHANNEL/SMALLTALK/
    # UNCLEAR instead of NEW_GOAL, with no active_intent to fall back on
    # and nothing left to say for the rest of the scenario. Additive,
    # gated on strict_value_rules_enabled so it doesn't touch any existing
    # test's matched substring.
    value_rules_block = ""
    if view.get("strict_value_rules_enabled"):
        value_rules_block = (
            "\nValue rules:\n"
            "- Copy slot values in the user's own words; never reformat, "
            "reorder, or convert them (dates, IDs, names stay exactly as "
            "spoken).\n"
            "- Never invent a year, unit, or any value the user didn't "
            "say.\n"
            "- If the user corrects themselves mid-turn (\"a 1-bedroom... "
            "actually no, 2-bedroom\"), only the FINAL corrected value is "
            "a slot_delta -- never emit the rejected earlier value.\n"
            "- Never add a slot_delta for an optional parameter the user "
            "did not state.\n"
            "- If a required parameter has no stated value at all, still "
            "extract everything else and leave that parameter unbound "
            "rather than inventing one -- the system will ask.\n"
            "- Disfluency (\"uh\", \"you know\", false starts, "
            "self-corrections) is never a reason to classify the turn as "
            "backchannel, smalltalk, or unclear when active_intent is "
            "null and the transcript states or implies a real request -- "
            "extract new_goal from whatever real content is present. "
            "backchannel/smalltalk/unclear are for turns with no active "
            "goal to attach to AND no actionable content at all (e.g. "
            "\"ok\", \"hello\", pure noise) -- not for a genuine request "
            "that happens to be spoken haltingly.\n"
        )
    # Q7 (win_plan §6.2): tells the model `ack_phrase` exists and what it's
    # for -- the field was already in the schema and already parsed onto
    # `TurnInterpretation.ack_phrase`, but nothing ever told a live model
    # to fill it, so it was always null in practice. Gated on
    # echo_ack_enabled so the prompt (and therefore every ScriptedProvider
    # test's matched substring) is unchanged unless this is on.
    ack_phrase_block = ""
    if view.get("echo_ack_enabled"):
        ack_phrase_block = (
            "\nAlso set ack_phrase: one future-tense sentence naming EVERY "
            "action this turn asks for and its key values, to speak back "
            "to the user before any of it runs (e.g. \"I'll search "
            "flights to Dubai for April 10th and book the cheapest one "
            "for Casey Lee.\"). Never claim anything is already done, "
            "booked, or confirmed -- only what you're about to do. Null "
            "if there's nothing to acknowledge yet (a clarifying "
            "question, backchannel, etc.).\n"
        )
    return (
        f"transcript: {view.get('transcript', '')!r}\n"
        f"active_intent: {view.get('active_intent')}\n"
        f"active_slots: {view.get('active_slots')}\n"
        f"suspended_goals: {view.get('suspended_goals')}\n"
        f"pending_clarification: {view.get('pending_clarification')}\n"
        f"{tools_block}"
        f"{visual_block}"
        f"{multi_action_block}"
        f"{value_rules_block}"
        f"{ack_phrase_block}"
        "Classify the act and return JSON matching the schema."
    )


def run_interpret(gateway: ModelGateway, view: dict) -> dict:
    return gateway.complete_json("interpret", build_prompt(view), INTERPRET_SCHEMA)
