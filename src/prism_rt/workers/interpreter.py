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


# S3 (kernel/action_plans.py): the same schema plus per-action tool calls.
# A separate object, so INTERPRET_SCHEMA itself stays byte-identical when
# Config.action_plans_enabled is off.
INTERPRET_SCHEMA_S3 = {
    **INTERPRET_SCHEMA,
    "properties": {
        **INTERPRET_SCHEMA["properties"],
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["tool"],
                "properties": {
                    "tool": {"type": "string"},
                    "args": {"type": "object"},
                    "refs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "param": {"type": "string"},
                                "from": {"type": "integer"},
                                "field": {"type": ["string", "null"]},
                                "select": {"type": "string"},
                            },
                        },
                    },
                    "assumed": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "unsupported": {"type": ["string", "null"]},
        "status_question": {"type": "boolean"},
    },
}


def _action_plans_block(view: dict) -> str:
    """S3: per-action tool calls. Examples use made-up tools only -- never a
    benchmark's own tool names or items."""
    block = (
        "\nAlso fill actions: one entry per tool call this turn asks for, in "
        "the order they should run (the order the user said them, unless they "
        "say otherwise): {\"tool\": <tool name>, \"args\": {<param>: <value>}, "
        "\"refs\": [...], \"assumed\": [...]}.\n"
        "- args: every value the user stated for THAT call, under the tool's "
        "own parameter names. Every value rule above applies to args exactly "
        "as to slot_deltas -- a date drops its ordinal suffix (\"March 22\", "
        "never \"March 22nd\"), and a value is the thing itself without filler "
        "words around it (\"a desk\" -> \"desk\") -- OPTIONAL "
        "parameters included whenever the user said them (a stated travel "
        "mode, quantity or filter value belongs in args even though the "
        "parameter has a default; only a parameter the user never mentioned "
        "is left out). The same tool asked for twice is two entries, each "
        "with its own args -- never a \"_2\" suffix.\n"
        "- When active_intent is null there is no current task to update: a "
        "request to set, change, raise or add something is act \"new_goal\" "
        "with actions filled, never \"slot_update\" or \"addition\".\n"
        "- A value the user clearly implies counts as stated (\"my new work "
        "phone is 555-0101\" gives both the phone type, work, and the number) "
        "-- fill it rather than leave a required parameter empty. A value said "
        "as a verb or adjective is stated too: \"walk there\" states a mode of "
        "walking, \"ship it overnight\" a shipping speed. Examples in "
        "a parameter's description are illustrations, not the only allowed "
        "values: when the user names a kind that isn't listed, use their "
        "words.\n"
        "- A value stated anywhere in this request counts as stated, even if "
        "it was said for another action (\"set my budget to 900, then search "
        "within that budget\" -> both calls get 900).\n"
        "- refs: a parameter whose value must come from an EARLIER action's "
        "result (\"the cheapest one\", \"from there\", \"whatever you find\") "
        "is not an arg. Add {\"param\": <name>, \"from\": <that earlier "
        "action's 0-based index>, \"field\": <the result field holding it, if "
        "obvious, else null>, \"select\": <which item, in a few words>}.\n"
        "- If the user makes an action conditional (\"if it's under 50, do X, "
        "otherwise do Y\"), still list every action they mention, in order.\n"
        "- Leave out an action the user explicitly called off.\n"
        "- If the user asks to book, buy, send or change something, include "
        "that action even when details are missing (leave them unbound -- the "
        "system will ask); never replace it with only a search. Booking "
        "something that must first be found is TWO actions: the search, then "
        "the booking.\n"
        "- Only use a tool whose purpose is what was asked -- a similar-sounding "
        "tool for a different thing is not a match.\n"
        "Example (made-up tools): \"find pizza places near the park, uh, and "
        "book a table for two at the first one\" -> actions: [{\"tool\": "
        "\"find_restaurants\", \"args\": {\"cuisine\": \"pizza\", \"near\": "
        "\"the park\"}, \"refs\": [], \"assumed\": []}, {\"tool\": "
        "\"reserve_table\", \"args\": {\"party_size\": 2}, \"refs\": "
        "[{\"param\": \"restaurant_id\", \"from\": 0, \"field\": "
        "\"restaurant_id\", \"select\": \"first result\"}], \"assumed\": []}]. "
        "\"check orders 12 and 40\" -> two check_order entries, one with "
        "{\"order_id\": \"12\"}, one with {\"order_id\": \"40\"}.\n"
        "With actions filled, slot_deltas may be left empty; keep intent and "
        "requested_actions filled as usual.\n"
    )
    if view.get("conversational_replies_enabled"):
        block += (
            "- If the user asks for something none of the listed tools can do "
            "(a kind of booking, search or change no tool handles), do NOT map "
            "it onto a different tool: leave it out of actions and describe it "
            "in a few words in unsupported (e.g. \"reserve a table\"). Otherwise "
            "unsupported is null. act stays one of the listed act values. A "
            "change to the previous request (\"no, make it X\", \"I meant Y\") "
            "is never unsupported -- it is a slot_update with slot_deltas.\n"
            "- A value only ever goes into a parameter whose meaning it matches: "
            "never put an amount, count or date into a name or ID parameter. If "
            "the user asks for something no parameter can express (e.g. a "
            "colour when no tool takes a colour), do not put it anywhere -- "
            "describe it in unsupported (e.g. \"choose by colour\"), and still "
            "return the rest of the request as usual.\n"
            "- If the user only asks how an earlier request went (\"did that go "
            "through?\") or about something an earlier result already told "
            "them (a name, a time, a reference number), set status_question to "
            "true and leave actions empty -- that is never unsupported.\n"
        )
    last = view.get("last_task") or []
    if last:
        rendered = "; ".join(f"{a['tool']} {a['args']}" for a in last)
        block += (
            f"Just finished: {rendered}. If the user now changes one of those "
            "values (\"make it X instead\", \"what about Y\"), that is a "
            "slot_update with slot_deltas naming the parameter -- not a status "
            "question and not unsupported.\n"
        )
        results = [a for a in last if a.get("result")]
        if results:
            block += (
                "What it returned: "
                + "; ".join(f"{a['tool']} -> {a['result']}" for a in results)
                + ". If the user now asks to do something with one of those "
                "results (\"add the first one to my cart\", \"book it\", "
                "\"how far is it from there\"), that is a new_goal whose args "
                "take the value straight from these results -- never ask for "
                "a value shown here.\n"
            )
    active = view.get("active_actions") or []
    if active:
        rendered = "; ".join(f"{a['action']}: {a['tool']} {a['args']}" for a in active)
        block += (
            f"Current actions: {rendered}. A correction to one of them is a "
            "slot_delta named \"<action>.<param>\" (e.g. \"a1.party_size\"). "
            "If the user adds a new action, set act to \"addition\" and return "
            "actions with the COMPLETE list: the current ones first, unchanged "
            "and in the same order, then the new ones.\n"
        )
    return block


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
                        if view.get("action_plans_enabled"):
                            # S3: found live -- "[optional, default='driving']"
                            # read as already filled, and a stated "transit"
                            # was dropped from the action's args.
                            bit += " [optional: include it whenever the user states it" + (
                                "" if default is None else f"; only if unstated does {default!r} apply"
                            ) + "]"
                        else:
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
        if view.get("camera_available"):
            visual_block += (
                "\nA live camera frame of what the user is showing is available right now. "
                "When the user asks about a device, light, screen or object they can show, "
                "and a tool parameter's value is something VISIBLE that they did not say "
                "aloud (a light's colour, which light, a code on a display), do not ask them "
                "for it: set visual_reference to \"at_utterance\" and name a visual_candidate "
                "after each such parameter exactly. Anything they did say aloud stays in "
                "slot_deltas / the action's args.\n"
            )
    commit_block = ""
    if view.get("commit_intent_guidance"):
        commit_block = (
            "\ncommit_intent: set true when the user is telling you to DO something that "
            "changes the world (book, order, cancel, open a ticket), and keep it true when "
            "they then change a detail of that same request (\"no wait, make it Friday\"). "
            "Set false for questions, look-ups and exploring options.\n"
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
            + (
                # With per-action args (below) the same parameter twice is two
                # actions, never a "_2" slot: telling the model both was a
                # contradiction in the FDB profile's prompt (1 Oct audit).
                "Give every action's parameters in its own actions[] entry, not just the first. "
                if view.get("action_plans_enabled")
                else "Extract slot_deltas for *every* action's parameters, not just the "
                "first -- if two different actions need the same parameter name "
                "with two different values (e.g. two order lookups), name the "
                "second occurrence with a \"_2\" suffix (e.g. \"order_id_2\") so "
                "they don't collide. "
            )
            + "A parameter whose value must come from an "
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
            "spoken) -- with specific exceptions, found live against real "
            "recordings: for a date, drop a trailing ordinal suffix on the "
            "day number (say \"15\" not \"15th\", \"July 15\" not \"July "
            "15th\", \"March 22\" not \"March 22nd\") -- keep everything "
            "else about how the user said it; for a currency, use its "
            "standard 3-letter code when the user names the currency in "
            "words (\"US dollars\" -> \"USD\", \"Canadian dollars\" -> "
            "\"CAD\", \"euros\" -> \"EUR\") -- this is translation, not "
            "reformatting, since the tool parameter expects a code; for an "
            "account/category type, drop a generic trailing word like "
            "\"account\" that isn't part of the type name itself "
            "(\"savings account\" -> \"savings\", \"checking account\" -> "
            "\"checking\"). Do NOT drop or add articles (\"the\"/\"a\"/"
            "\"an\") on a place/venue name either way -- tried live and "
            "reverted: which form is expected is genuinely inconsistent "
            "across real recordings (a named venue like \"Gym\"/"
            "\"University\" drops it, a generic destination like \"the "
            "office\"/\"the gym\" keeps it), so guessing either way nets "
            "out worse than just copying exactly what was said.\n"
            "- Never invent a year, unit, or any value the user didn't "
            "say.\n"
            "- When a parameter's description shows its example values as "
            "identifiers (lowercase words joined by underscores, e.g. "
            "'id_card'), write the kind the user names in that same form "
            "(\"work permit\" -> \"work_permit\"). When a parameter names a "
            "field or setting (a filter key, a field name), use the matching "
            "parameter name from the tool list when one exists (a \"minimum "
            "rating\" filter -> \"min_rating\" if some tool has a min_rating "
            "parameter).\n"
            "- If the user corrects themselves mid-turn (\"a 1-bedroom... "
            "actually no, 2-bedroom\"), only the FINAL corrected value is "
            "a slot_delta -- never emit the rejected earlier value.\n"
            "- Never add a slot_delta for an optional parameter the user "
            "did not state.\n"
            + (
                # S3 (Config.fill_unstated_required_enabled): the user can't
                # be asked in every setting (a single-turn recording never
                # answers), so a required count/size/budget/yes-no gets a
                # stated assumption instead. Free text is still left unbound.
                "- If a REQUIRED parameter has no stated value at all: when it "
                "is a number or yes/no (a count, size, quantity, budget, "
                "true/false), put a sensible value that does NOT narrow the "
                "request in that action's args -- 1 for a count of rooms, "
                "guests or items; for a maximum (a price cap, a budget) a "
                "generous high value, never 0 -- (a value is required: naming "
                "it alone is not enough), ALSO list its name in that action's "
                "\"assumed\", and say the assumption in ack_phrase (e.g. \"..., "
                "assuming 1 guest\"). Any "
                "other kind (a place, name, date, ID, free text) stays unbound "
                "-- the system will ask. Never assume an optional parameter.\n"
                if view.get("fill_unstated_required_enabled")
                else "- If a required parameter has no stated value at all, still "
                "extract everything else and leave that parameter unbound "
                "rather than inventing one -- the system will ask.\n"
            )
            +
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
    action_plans_block = _action_plans_block(view) if view.get("action_plans_enabled") else ""
    return (
        f"transcript: {view.get('transcript', '')!r}\n"
        f"active_intent: {view.get('active_intent')}\n"
        f"active_slots: {view.get('active_slots')}\n"
        f"suspended_goals: {view.get('suspended_goals')}\n"
        f"pending_clarification: {view.get('pending_clarification')}\n"
        f"{tools_block}"
        f"{visual_block}"
        f"{commit_block}"
        f"{multi_action_block}"
        f"{value_rules_block}"
        f"{action_plans_block}"
        f"{ack_phrase_block}"
        "Classify the act and return JSON matching the schema."
    )


def run_interpret(gateway: ModelGateway, view: dict) -> dict:
    schema = INTERPRET_SCHEMA_S3 if view.get("action_plans_enabled") else INTERPRET_SCHEMA
    return gateway.complete_json("interpret", build_prompt(view), schema)
