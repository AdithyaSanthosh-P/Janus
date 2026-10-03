"""S3: per-action interpretation compiled straight into a Plan.

`docs-personal/private-docs/s3_plan_2026-09-28.md` (win_plan §6.3),
behind `Config.action_plans_enabled`. With it on, the Interpreter returns
`actions[]` -- one entry per tool call the turn asked for, in order, each
with its own literal args -- and `compile_actions` builds the Plan in the
same kernel step the interpretation is applied, instead of dispatching a
PLAN job. Found against real FDB-v3 recordings: the Interpreter's own echo
ACK named an argument ("transit") that the Planner then dropped (the call
went out with the schema default); the same tool asked for twice collided
on one flat `slot.<gid>.<param>` name (a prompt-only "_2" suffix was the
only guard); and the PLAN round trip delays every first tool call.

Each action's arguments live at `slot.<gid>.a<i>.<param>` (step key and
`PlanStep.slot_prefix` "a<i>"), so a correction to one action's value
invalidates only that action's call through the ordinary DependencyIndex
path -- nothing here knows what is in flight. Every required parameter
gets a FACT binding even when no value was stated, so a missing one goes
through the existing clarify / re-extract path on its per-action key.

A compiled goal must never be sent back to PLANNING (`is_compiled`): a
Planner-written replacement gets new step keys, and CommitGate's G5/G6
would then block an already-confirmed write for the same action forever.
Anything the compiler can't handle returns None and the ordinary PLAN path
runs exactly as before.
"""

from __future__ import annotations

import dataclasses
import re

from prism_rt.canonical import canonicalize_spoken_id, normalize_value, to_canonical_json
from prism_rt.kernel.proposals import ACTION_SLOT_NAME, fix_step_kind  # noqa: F401 -- re-exported
from prism_rt.model.types import (
    ActionRef,
    ActionSpec,
    Binding,
    BindingKind,
    CallStatus,
    FactStatus,
    Plan,
    PlanStep,
    Provenance,
    SlotDelta,
    SlotOp,
    StepKind,
    TurnInterpretation,
)


# Only these may be assumed when unstated (Config.fill_unstated_required_
# enabled) -- a count, size, budget or yes/no has a least-restrictive value;
# a place, name, date or ID does not, and must still be asked.
_ASSUMABLE_TYPES = frozenset({"integer", "number", "boolean"})
_TRUE_WORDS = frozenset({"true", "yes", "y", "on"})
_FALSE_WORDS = frozenset({"false", "no", "n", "off"})
_LEADING_NUMBER = re.compile(r"^\s*\$?\s*(-?\d[\d,]*(?:\.\d+)?)")
# A whole value that is an amount as spoken: "$1,800 a month", "1800 dollars",
# "2,500 per month". Groups: currency sign, number, currency word, period.
_SPOKEN_AMOUNT = re.compile(
    r"^\s*([$€£₹])?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*"
    r"(dollars?|bucks|usd|euros?|eur|pounds?|gbp|rupees?|inr)?\s*"
    r"((?:a|an|per|/)\s*(?:month|week|night|day|year)|monthly|weekly|nightly|yearly)?\s*$",
    re.IGNORECASE,
)


def is_compiled(store, goal_id: str | None) -> bool:
    if goal_id is None:
        return False
    plan = store.plans.current(goal_id)
    return plan is not None and plan.origin == "compiled"


def _schema_type(prop: dict) -> str | None:
    t = prop.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    return t if isinstance(t, str) else None


_NUMBER_WORDS = {
    w: i
    for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve".split()
    )
}


def _parse_number(text: str):
    match = _LEADING_NUMBER.match(text)
    if match is None:
        # A small spoken count ("two", "three bedrooms") -- ASR spells these out.
        words = text.strip().lower().split()
        return _NUMBER_WORDS.get(words[0]) if words else None
    number = float(match.group(1).replace(",", ""))
    return int(number) if number.is_integer() else number


# A parameter whose description shows its values as snake_case words
# ("Type of document, e.g. 'passport' or 'id_card'") takes them in that form.
_SNAKE_EXAMPLE = re.compile(r"'[a-z]+(?:_[a-z]+)+'")
_CATEGORY_PHRASE = re.compile(r"^[A-Za-z][A-Za-z'\u2019 -]*$")


def _snake_case_like_examples(value: str, prop: dict) -> str:
    """"driver's license" -> "driver_license", "Credit Card" -> "credit_card",
    for a parameter whose own description gives snake_case example values.
    Found in the 3 Oct voice runs: the model wrote the spoken form of the
    document type in three recordings. Only a short phrase of letters is
    touched (never a name with digits, an ID or a sentence)."""
    text = value.strip()
    if (
        not _SNAKE_EXAMPLE.search(prop.get("description") or "")
        or not _CATEGORY_PHRASE.match(text)
        or len(text.split()) > 4
    ):
        return value
    lowered = re.sub(r"['\u2019]s\b", "", text.lower())  # possessive: "driver's" -> "driver"
    lowered = re.sub(r"['\u2019]", "", lowered)
    return "_".join(w for w in re.split(r"[\s-]+", lowered) if w)


def _drop_repeated_type_noun(value: str, name: str | None) -> str:
    """For a `<noun>_type` parameter, a value ending in that noun repeats
    it: "travel card" for `card_type` is the type "travel" (FDB's own
    descriptions show bare examples: 'platinum', 'gold'). The model wrote
    both forms in the 3 Oct runs, flipping recordings between runs. Only a
    multi-word value whose last word is the noun is changed."""
    if not name or not name.endswith("_type"):
        return value
    noun = name[: -len("_type")].rsplit("_", 1)[-1].lower()
    words = value.strip().split()
    if noun and len(words) > 1 and words[-1].lower().rstrip(".") == noun:
        return " ".join(words[:-1])
    return value


def coerce_to_schema(value, prop: dict, name: str | None = None):
    """Coerces a spoken value to the parameter's declared JSON type before
    it is written, so `ToolCatalog.validate_args` (G8) never rejects a value
    the user plainly stated: a number for a string parameter becomes "2500"
    (never "2500.0"), a spoken amount for a string parameter becomes the bare
    number ("$1,800 a month" -> "1800"; 2 Oct voice run, housing_03), "2" or
    2.0 becomes 2 for an integer, "$1,800" becomes 1800 for a number, "yes"
    becomes True, and an enum value matches
    case-insensitively. Anything that can't be coerced is returned as is
    (validation then decides)."""
    value = normalize_value(value)
    kind = _schema_type(prop)
    if kind == "string":
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            amount = _SPOKEN_AMOUNT.match(value)
            # Only with a currency or a period: a bare "1800" (or an ID made
            # of digits) is left exactly as said.
            if amount and (amount.group(1) or amount.group(4) or amount.group(5)):
                return amount.group(2).replace(",", "") + (amount.group(3) or "")
            value = _snake_case_like_examples(_drop_repeated_type_noun(value, name), prop)
    elif kind in ("integer", "number") and not isinstance(value, bool):
        if isinstance(value, str):
            parsed = _parse_number(value)
            if parsed is not None:
                value = parsed
        if kind == "integer" and isinstance(value, float) and value.is_integer():
            value = int(value)
    elif kind == "boolean" and isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_WORDS:
            return True
        if lowered in _FALSE_WORDS:
            return False
    enum = prop.get("enum")
    if isinstance(value, str) and isinstance(enum, list):
        for option in enum:
            if isinstance(option, str) and option.lower() == value.lower():
                return option
    return value


def bind_key(goal_id: str, step_key: str, param: str) -> str:
    """Where kernel/binder.py puts a LATE binding's chosen value."""
    return f"bind.{goal_id}.{step_key}.{param}"


def valid_bind_fact(store, key: str):
    """A bind decision counts only while it is live *and* still grounded:
    its derivation read set (the upstream results it was picked from) must
    still be valid -- an upstream re-run makes it stale even before the
    invalidation fixpoint retracts it (same rule as `grounded_compose_text`)."""
    fact = store.facts.get(key)
    if fact is None or fact.status == FactStatus.RETRACTED:
        return None
    grounding = fact.provenance.derivation_read_set
    if grounding is not None and not store.facts.is_valid(grounding).is_valid:
        return None
    return fact


def bind_failure_key(goal_id: str, step_key: str) -> str:
    return f"bindfail.{goal_id}.{step_key}"


def write_bind_facts(txn, goal_id: str, step_key: str, values: dict, read_set, *, rule: str, now_us: int, step_no: int, event_id: str | None = None) -> None:
    """DERIVED with the upstream grounding as `derivation_read_set`, so the
    invalidation fixpoint retracts them if an upstream result changes.
    Retracted first whenever the grounding differs: `FactStore.set` is a
    no-op for an unchanged value, which would keep a stale grounding and
    re-dispatch forever (the `set_grounded_compose_text` lesson)."""
    provenance = Provenance(source="system", event_id=event_id, step_no=step_no, ts_us=now_us, derivation_read_set=read_set)
    for param, value in values.items():
        key = bind_key(goal_id, step_key, param)
        existing = txn.facts.get(key)
        if existing is not None and existing.status != FactStatus.RETRACTED and existing.provenance.derivation_read_set != read_set:
            txn.facts.retract(key, rule=f"{rule}.regrounded")
        txn.facts.set(key, value, FactStatus.DERIVED, provenance, rule=rule)
    # A bind that landed clears the give-up budget (found by review: the
    # count otherwise carried over into a later re-bind of the same step).
    txn.facts.retract(bind_failure_key(goal_id, step_key), rule=f"{rule}.failures_cleared")


def tool_props(store, tool: str) -> tuple[dict, list]:
    return _tool_props(store, tool)


def _tool_props(store, tool: str) -> tuple[dict, list]:
    spec = store.catalog.get(tool)
    schema = (spec.params_schema if spec is not None else None) or {}
    props = schema.get("properties") or {}
    return props, list(schema.get("required") or ())


def coerce_slot_value(store, goal_id: str, slot_key: str, value):
    """Coerces a value about to be written to `slot_key` to the declared type
    of the tool parameter that FACT-binds it in the goal's current plan (a
    re-extracted "two" for an integer `bedrooms` becomes 2), so the rebind
    doesn't fail validation and clarify the same slot again. Returned as is
    when no plan step binds the key."""
    plan = store.plans.current(goal_id)
    if plan is None:
        return value
    template = slot_key.replace(f"slot.{goal_id}.", "slot.$G.", 1)
    for step in plan.steps:
        for param, binding in step.bindings.items():
            if binding.kind == BindingKind.FACT and binding.fact_key in (slot_key, template):
                prop = _tool_props(store, step.tool)[0].get(param)
                if isinstance(prop, dict):
                    return coerce_to_schema(value, prop, param)
    return value


def _usable(store, tool: str) -> bool:
    spec = store.catalog.get(tool)
    return spec is not None and spec.status == "USABLE"


def _refs_valid(actions, first_index: int, source_map) -> bool:
    """Every chained ref must point at an EARLIER action -- anything else
    (itself, a later action, an index that doesn't exist) means the
    interpretation is inconsistent and the ordinary PLAN path runs instead.
    Checked before anything is written."""
    for offset, action in enumerate(actions):
        index = first_index + offset
        for ref in action.refs:
            source = source_map(ref.source)
            if source is None or not 0 <= source < index:
                return False
    return True


def _build_steps(txn, goal_id: str, actions, first_index: int, now_us: int, step_no: int, *, event_id: str, turn_id: str | None, source_map) -> list[PlanStep]:
    """One step per action, strictly sequential in the order asked (FDB
    pairs same-name calls first-in-first-out, and every FDB tool is a WRITE
    -- user order is the safe order). Writes each action's own slot facts.
    A chained ref becomes a LATE binding (kernel/binder.py picks the value
    from the upstream step's real result once it exists) and wins over any
    literal the model also put in args for that parameter (a placeholder
    like "there" or "whatever you find"). `_refs_valid` has already been
    checked."""
    config = txn.store.config
    steps: list[PlanStep] = []
    for offset, action in enumerate(actions):
        index = first_index + offset
        prefix = f"a{index}"
        props, required = _tool_props(txn.store, action.tool)
        bindings: dict[str, Binding] = {}
        for ref in action.refs:
            if isinstance(props.get(ref.param), dict):
                bindings[ref.param] = Binding(
                    kind=BindingKind.LATE, step_key=f"a{source_map(ref.source)}", path=ref.field or None, hint=ref.select or None
                )
        for param, raw in action.args.items():
            prop = props.get(param)
            if not isinstance(prop, dict) or param in bindings:
                continue  # not a parameter of this tool (dropped), or chained (the ref wins)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                continue
            assumed = param in action.assumed
            if assumed and (not config.fill_unstated_required_enabled or _schema_type(prop) not in _ASSUMABLE_TYPES):
                continue  # an assumed free-text value is dropped -- still asked
            value = coerce_to_schema(raw, prop, param)
            if config.normalize_spoken_ids:
                value = canonicalize_spoken_id(value, param)
            txn.facts.set(
                f"slot.{goal_id}.{prefix}.{param}",
                value,
                FactStatus.COMMITTED,
                Provenance(source="assumed" if assumed else "user", event_id=event_id, turn_id=turn_id, step_no=step_no, ts_us=now_us),
                rule="action_plans.slot_set",
            )
            bindings[param] = Binding(kind=BindingKind.FACT, fact_key=f"slot.$G.{prefix}.{param}")
        for param in required:
            # Unstated: still FACT-bound, so the executor's ordinary missing-
            # key path asks (or Q5 re-extracts) on this action's own key.
            bindings.setdefault(param, Binding(kind=BindingKind.FACT, fact_key=f"slot.$G.{prefix}.{param}"))
        after = (f"a{index - 1}",) if index > 0 else ()
        step = PlanStep(step_key=prefix, tool=action.tool, kind=StepKind.WRITE, bindings=bindings, after=after, slot_prefix=prefix)
        steps.append(fix_step_kind(step, txn.store.catalog))
    return steps


def _dedupe(actions) -> tuple[list[ActionSpec], dict[int, int]]:
    """Identical actions (same tool, args and refs) collapse to one -- the
    second would be a duplicate write CommitGate's G5 blocks forever.
    Returns the unique actions and a map from each original index to its
    unique index, so a ref to a dropped duplicate points at the kept one."""
    seen: dict[str, int] = {}
    unique: list[ActionSpec] = []
    index_map: dict[int, int] = {}
    for original, action in enumerate(actions):
        key = to_canonical_json({
            "tool": action.tool,
            "args": action.args,
            "refs": [[r.param, r.source, r.field, r.select] for r in action.refs],
        })
        if key in seen:
            index_map[original] = seen[key]
            continue
        seen[key] = index_map[original] = len(unique)
        unique.append(action)
    return unique, index_map


def compile_actions(txn, goal_id: str, interp: TurnInterpretation, now_us: int, step_no: int, *, event_id: str) -> Plan | None:
    """Builds and stores the goal's Plan from `interp.actions`, or returns
    None (writing nothing) when the ordinary PLAN path must run instead:
    no actions, an unknown/quarantined tool, a ref that doesn't point at an
    earlier action, or vision (perception writes flat slot keys)."""
    actions = interp.actions
    if not actions:
        return None
    if any(not _usable(txn.store, a.tool) for a in actions):
        return None
    unique, index_map = _dedupe(actions)
    if not _refs_valid(unique, 0, index_map.get):
        return None
    steps = _build_steps(txn, goal_id, unique, 0, now_us, step_no, event_id=event_id, turn_id=interp.turn_id, source_map=index_map.get)
    plan = Plan(goal_id=goal_id, plan_rev=txn.store.plans.next_rev(goal_id), steps=tuple(steps), origin="compiled")
    txn.store.plans.create(plan)
    return plan


def extend_compiled_plan(txn, goal_id: str, interp: TurnInterpretation, now_us: int, step_no: int, *, event_id: str) -> bool:
    """ADDITION on a compiled goal ("...and also track my order"): the
    Interpreter returns the complete updated action list. Only a pure
    append is accepted -- the existing actions must come first, same tools,
    same order; their steps (and any calls already made for them) keep
    their step keys. Returns True iff the plan was extended."""
    plan = txn.store.plans.current(goal_id)
    if plan is None or plan.origin != "compiled":
        return False
    new = list(interp.actions)
    if len(new) <= len(plan.steps):
        return False
    if [a.tool for a in new[: len(plan.steps)]] != [s.tool for s in plan.steps]:
        return False
    added = new[len(plan.steps):]
    if any(not _usable(txn.store, a.tool) for a in added):
        return False
    identity = lambda i: i  # noqa: E731 -- the model's list is positionally aligned with the current a<i> steps
    if not _refs_valid(added, len(plan.steps), identity):
        return False
    extra = _build_steps(txn, goal_id, added, len(plan.steps), now_us, step_no, event_id=event_id, turn_id=interp.turn_id, source_map=identity)
    txn.store.plans.create(dataclasses.replace(plan, plan_rev=txn.store.plans.next_rev(goal_id), steps=plan.steps + tuple(extra)))
    return True


def map_deltas(store, goal_id: str, deltas) -> tuple[list[SlotDelta], list[SlotDelta]]:
    """A correction or clarify answer on a compiled goal, as (mapped,
    unmapped). A flat name is resolved to one action's own slot: the
    clarify target it answers first, else the only action whose tool takes
    that parameter. An already-scoped "a<i>.<param>" name is kept if that
    action exists and takes the parameter. Anything else is unmapped --
    ambiguous between actions, or no action takes it."""
    plan = store.plans.current(goal_id)
    steps = {s.slot_prefix: s for s in plan.steps if s.slot_prefix} if plan is not None else {}
    target_fact = store.facts.get(f"goal.{goal_id}.clarify_target")
    target = target_fact.value if target_fact is not None and target_fact.status != FactStatus.RETRACTED else None
    mapped: list[SlotDelta] = []
    unmapped: list[SlotDelta] = []
    for delta in deltas:
        if delta.scope == "session":
            mapped.append(delta)
            continue
        scoped = ACTION_SLOT_NAME.match(delta.name)
        if scoped:
            step = steps.get(scoped.group(1))
            if step is not None and scoped.group(2) in _tool_props(store, step.tool)[0]:
                mapped.append(delta)
            else:
                unmapped.append(delta)
            continue
        if isinstance(target, str) and target.startswith(f"slot.{goal_id}.a") and target.rsplit(".", 1)[-1] == delta.name:
            mapped.append(dataclasses.replace(delta, name=target[len(f"slot.{goal_id}."):]))
            continue
        candidates = [prefix for prefix, step in steps.items() if delta.name in _tool_props(store, step.tool)[0]]
        if len(candidates) == 1:
            mapped.append(dataclasses.replace(delta, name=f"{candidates[0]}.{delta.name}"))
        else:
            unmapped.append(delta)
    return mapped, unmapped


def coerce_deltas(store, goal_id: str, deltas) -> list[SlotDelta]:
    """Applies `coerce_to_schema` to mapped per-action SET deltas, so a
    corrected value is written in the same type the compile wrote."""
    plan = store.plans.current(goal_id)
    steps = {s.slot_prefix: s for s in plan.steps if s.slot_prefix} if plan is not None else {}
    out: list[SlotDelta] = []
    for delta in deltas:
        scoped = ACTION_SLOT_NAME.match(delta.name) if delta.scope != "session" else None
        step = steps.get(scoped.group(1)) if scoped else None
        if step is not None and delta.op == SlotOp.SET:
            prop = _tool_props(store, step.tool)[0].get(scoped.group(2))
            if isinstance(prop, dict):
                delta = dataclasses.replace(delta, value=coerce_to_schema(delta.value, prop, scoped.group(2)))
        out.append(delta)
    return out


def ensure_bindings(txn, goal_id: str, names) -> None:
    """A correction that states a parameter the action didn't have before
    ("make it walking" when no mode was given) writes `a<i>.<param>`, but
    the step has no binding reading it -- the call would re-run with the
    old arguments. Recompile in place: same step keys, the new FACT
    binding added, plan_rev + 1."""
    plan = txn.store.plans.current(goal_id)
    if plan is None or plan.origin != "compiled":
        return
    wanted: dict[str, set[str]] = {}
    for name in names:
        scoped = ACTION_SLOT_NAME.match(name)
        if scoped:
            wanted.setdefault(scoped.group(1), set()).add(scoped.group(2))
    changed = False
    steps = []
    for step in plan.steps:
        # A value the user now states outright also replaces a LATE (chained)
        # binding for that parameter -- they told us, no result-picking needed.
        missing = sorted(
            p for p in wanted.get(step.slot_prefix or "", ())
            if p not in step.bindings or step.bindings[p].kind == BindingKind.LATE
        )
        if missing:
            bindings = dict(step.bindings)
            for param in missing:
                bindings[param] = Binding(kind=BindingKind.FACT, fact_key=f"slot.$G.{step.slot_prefix}.{param}")
            step = dataclasses.replace(step, bindings=bindings)
            changed = True
        steps.append(step)
    if changed:
        txn.store.plans.create(dataclasses.replace(plan, plan_rev=txn.store.plans.next_rev(goal_id), steps=tuple(steps)))


def flat_deltas_from_actions(actions) -> tuple[SlotDelta, ...]:
    """Fallback only: when the Interpreter left `slot_deltas` empty (it may,
    once `actions` is filled) but the plan couldn't be compiled, the
    ordinary PLAN path still needs the stated values as flat slots -- the
    first use of a parameter name keeps it, repeats get "_2", "_3" (the
    existing multi-action convention). Chained refs have no value yet."""
    counts: dict[str, int] = {}
    deltas: list[SlotDelta] = []
    for action in actions:
        for param, value in action.args.items():
            if value is None:
                continue
            counts[param] = counts.get(param, 0) + 1
            name = param if counts[param] == 1 else f"{param}_{counts[param]}"
            deltas.append(SlotDelta(name=name, scope="goal", op=SlotOp.SET, value=value))
    return tuple(deltas)


def active_actions_view(store, goal_id: str | None) -> list[dict]:
    """The Interpreter's view of a compiled goal's actions, so a later
    correction can name "a<i>.<param>" directly."""
    if not is_compiled(store, goal_id):
        return []
    plan = store.plans.current(goal_id)
    view = []
    for step in plan.steps:
        args = {}
        for param, binding in step.bindings.items():
            if binding.kind == BindingKind.LATE:
                args[param] = f"<from {binding.step_key}'s result>"
                continue
            fact = store.facts.get(f"slot.{goal_id}.{step.slot_prefix}.{param}")
            if fact is not None and fact.status != FactStatus.RETRACTED:
                args[param] = fact.value
        view.append({"action": step.slot_prefix, "tool": step.tool, "args": args})
    return view


_RESULT_VIEW_CHARS = 600


def step_failed_key(goal_id: str, step_key: str) -> str:
    return f"step_failed.{goal_id}.{step_key}"


def step_failed(store, goal_id: str, step_key: str) -> bool:
    """Config.partial_failure_continues: this step failed on its own."""
    fact = store.facts.get(step_failed_key(goal_id, step_key))
    return fact is not None and fact.status != FactStatus.RETRACTED and bool(fact.value)


def finished_actions_view(store, goal_id: str | None) -> list[dict]:
    """`active_actions_view` plus each action's own result, compacted -- for
    the task that just finished, so a follow-up can act on what it found
    ("add one of them to the cart"). Found by the 1 Oct audit: the follow-up
    saw only the tools and arguments, and asked for an id the results held."""
    view = active_actions_view(store, goal_id)
    if not view:
        return view
    for entry in view:
        call = store.call_ledger.latest_by_step(goal_id, entry["action"])
        if call is None or call.status != CallStatus.CONSUMED:
            continue
        fact = store.facts.get(f"result.{call.call_id}")
        if fact is None or fact.status == FactStatus.RETRACTED or fact.value is None:
            continue
        text = to_canonical_json(fact.value)
        entry["result"] = text if len(text) <= _RESULT_VIEW_CHARS else text[: _RESULT_VIEW_CHARS - 1] + "\u2026"
    return view


def append_actions(txn, goal_id: str, actions, now_us: int, step_no: int, *, event_id: str, turn_id: str | None) -> bool:
    """A new request while a compiled goal is still running ("...and then
    buy me a coffee maker") -- found live: treated as a NEW_GOAL, it
    silently suspended the running task, which was never mentioned again.
    Appended instead, after the existing steps (refs in `actions` index
    within `actions` itself). Returns True iff the plan was extended."""
    plan = txn.store.plans.current(goal_id)
    if plan is None or plan.origin != "compiled" or not actions:
        return False
    if any(not _usable(txn.store, a.tool) for a in actions):
        return False
    offset = len(plan.steps)
    shift = lambda i: i + offset  # noqa: E731
    if not _refs_valid(actions, offset, shift):
        return False
    extra = _build_steps(txn, goal_id, list(actions), offset, now_us, step_no, event_id=event_id, turn_id=turn_id, source_map=shift)
    txn.store.plans.create(dataclasses.replace(plan, plan_rev=txn.store.plans.next_rev(goal_id), steps=plan.steps + tuple(extra)))
    return True


def redo_actions(store, goal_id: str, deltas) -> list[ActionSpec] | None:
    """"No, make it ten dollars" right after a task finished -- found live:
    with the goal already closed, the correction had nothing to attach to
    and was answered "I can't do that". Rebuilds the finished compiled
    plan's actions with the corrected values, to be run as a new request.
    None when there is nothing to redo or no delta maps onto an action."""
    plan = store.plans.current(goal_id)
    if plan is None or plan.origin != "compiled":
        return None
    specs: list[dict] = []
    for step in plan.steps:
        args, refs = {}, []
        for param, binding in step.bindings.items():
            if binding.kind == BindingKind.LATE:
                refs.append(ActionRef(param=param, source=int(binding.step_key[1:]), field=binding.path, select=binding.hint or ""))
                continue
            fact = store.facts.get(f"slot.{goal_id}.{step.slot_prefix}.{param}")
            if fact is not None and fact.status != FactStatus.RETRACTED:
                args[param] = fact.value
        specs.append({"tool": step.tool, "args": args, "refs": refs, "props": _tool_props(store, step.tool)[0]})
    applied = False
    for delta in deltas:
        if delta.scope == "session" or delta.op != SlotOp.SET:
            continue
        scoped = ACTION_SLOT_NAME.match(delta.name)
        name = scoped.group(2) if scoped else delta.name
        if scoped:
            targets = [s for i, s in enumerate(specs) if f"a{i}" == scoped.group(1)]
        else:
            holders = [s for s in specs if name in s["args"]]
            targets = holders if len(holders) == 1 else [s for s in specs if name in s["props"]]
        if len(targets) != 1 or name not in targets[0]["props"]:
            continue
        targets[0]["args"][name] = delta.value
        targets[0]["refs"] = [r for r in targets[0]["refs"] if r.param != name]
        applied = True
    if not applied:
        return None
    return [ActionSpec(tool=s["tool"], args=s["args"], refs=tuple(s["refs"])) for s in specs]
