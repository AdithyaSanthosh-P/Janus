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
from prism_rt.kernel.proposals import fix_step_kind
from prism_rt.model.types import (
    ActionSpec,
    Binding,
    BindingKind,
    FactStatus,
    Plan,
    PlanStep,
    Provenance,
    SlotDelta,
    SlotOp,
    StepKind,
    TurnInterpretation,
)

ACTION_SLOT_NAME = re.compile(r"^(a\d+)\.(\w+)$")

# Only these may be assumed when unstated (Config.fill_unstated_required_
# enabled) -- a count, size, budget or yes/no has a least-restrictive value;
# a place, name, date or ID does not, and must still be asked.
_ASSUMABLE_TYPES = frozenset({"integer", "number", "boolean"})
_TRUE_WORDS = frozenset({"true", "yes", "y", "on"})
_FALSE_WORDS = frozenset({"false", "no", "n", "off"})
_LEADING_NUMBER = re.compile(r"^\s*\$?\s*(-?\d[\d,]*(?:\.\d+)?)")


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


def _parse_number(text: str):
    match = _LEADING_NUMBER.match(text)
    if match is None:
        return None
    number = float(match.group(1).replace(",", ""))
    return int(number) if number.is_integer() else number


def coerce_to_schema(value, prop: dict):
    """Coerces a spoken value to the parameter's declared JSON type before
    it is written, so `ToolCatalog.validate_args` (G8) never rejects a value
    the user plainly stated: a number for a string parameter becomes "2500"
    (never "2500.0"), "2" or 2.0 becomes 2 for an integer, "$1,800" becomes
    1800 for a number, "yes" becomes True, and an enum value matches
    case-insensitively. Anything that can't be coerced is returned as is
    (validation then decides)."""
    value = normalize_value(value)
    kind = _schema_type(prop)
    if kind == "string":
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
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


def tool_props(store, tool: str) -> tuple[dict, list]:
    return _tool_props(store, tool)


def _tool_props(store, tool: str) -> tuple[dict, list]:
    spec = store.catalog.get(tool)
    schema = (spec.params_schema if spec is not None else None) or {}
    props = schema.get("properties") or {}
    return props, list(schema.get("required") or ())


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
            value = coerce_to_schema(raw, prop)
            if config.normalize_spoken_ids:
                value = canonicalize_spoken_id(value)
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
    if txn.store.config.vision_enabled and interp.visual_reference != "none":
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
                delta = dataclasses.replace(delta, value=coerce_to_schema(delta.value, prop))
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
