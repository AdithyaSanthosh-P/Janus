"""Parse raw worker-proposal dicts into validated model records.

This lives on the kernel side of the package boundary (`docs/prompt 2.txt`
§21.1: kernel must not import workers) even though it mirrors the prompt-
building code in `workers/interpreter.py`, `workers/planner.py`,
`workers/composer.py` — accepting/rejecting an untrusted proposal is a
kernel responsibility (§5.1: "WORKER_RESULT ... Validate proposal read
set; accept, hold, or reject"), the prompt-building half is not.
"""

from __future__ import annotations

import dataclasses
import re

from prism_rt.model.types import (
    ActionRef,
    ActionSpec,
    AsrSegment,
    Binding,
    BindingKind,
    Confidence,
    InterpretAct,
    PerceptionClaim,
    Plan,
    PlanStep,
    SlotDelta,
    SlotOp,
    StepKind,
    ToolMutability,
    TurnInterpretation,
    VisualCandidate,
)


def _parse_actions(raw_actions) -> tuple[ActionSpec, ...]:
    """S3: all-or-nothing -- one malformed entry empties the whole list, so
    the kernel falls back to the ordinary PLAN path rather than silently
    running a plan with one of the user's actions missing. Catches the
    TypeError/AttributeError family itself: `_apply_worker_result` only
    catches KeyError/ValueError, and a bad `actions` alone must never
    drop the rest of an otherwise-good interpretation."""
    if not raw_actions or not isinstance(raw_actions, (list, tuple)):
        return ()
    try:
        parsed = []
        for raw in raw_actions:
            tool = raw["tool"]
            args = raw.get("args") or {}
            if not isinstance(tool, str) or not tool or not isinstance(args, dict):
                return ()
            refs = []
            for raw_ref in raw.get("refs") or ():
                source = raw_ref["from"]
                if isinstance(source, bool):
                    return ()
                refs.append(
                    ActionRef(
                        param=str(raw_ref["param"]),
                        source=int(source),
                        field=raw_ref.get("field") or None,
                        select=str(raw_ref.get("select") or ""),
                    )
                )
            assumed = tuple(str(p) for p in (raw.get("assumed") or ()))
            parsed.append(ActionSpec(tool=tool, args={str(k): v for k, v in args.items()}, refs=tuple(refs), assumed=assumed))
        return tuple(parsed)
    except (KeyError, TypeError, ValueError, AttributeError):
        return ()


def parse_interpretation(raw: dict, *, turn_id: str, input_digest: str) -> TurnInterpretation:
    try:
        act = InterpretAct(raw["act"])
    except ValueError:
        # Found live: a model that filled `unsupported`/`status_question`
        # sometimes also invents a matching act ("unsupported"). The fields
        # carry the meaning; don't drop the whole interpretation over it.
        if not (isinstance(raw.get("unsupported"), str) or raw.get("status_question") is True):
            raise
        act = InterpretAct.UNCLEAR
    slot_deltas = tuple(
        SlotDelta(
            name=delta["name"],
            scope=delta.get("scope", "goal"),
            op=SlotOp(delta.get("op", "set")),
            value=delta.get("value"),
        )
        for delta in raw.get("slot_deltas") or ()
    )
    visual_candidates = tuple(
        VisualCandidate(name=c["name"], description=c.get("description", ""))
        for c in raw.get("visual_candidates") or ()
    )
    requested_actions = tuple(a for a in (raw.get("requested_actions") or ()) if isinstance(a, str))
    return TurnInterpretation(
        turn_id=turn_id,
        input_digest=input_digest,
        act=act,
        intent=raw.get("intent"),
        slot_deltas=slot_deltas,
        commit_intent=bool(raw.get("commit_intent", False)),
        resume_goal_id=raw.get("resume_goal_id"),
        ack_phrase=raw.get("ack_phrase"),
        visual_reference=raw.get("visual_reference") or "none",
        visual_candidates=visual_candidates,
        requested_actions=requested_actions,
        actions=_parse_actions(raw.get("actions")),
        unsupported=raw["unsupported"].strip() or None if isinstance(raw.get("unsupported"), str) else None,
        status_question=raw.get("status_question") is True,
    )


def fix_step_kind(step, catalog):
    """`step.kind` is copied from the catalog at plan acceptance
    (`docs/prompt 2.txt` §4.3) — the model's own claim is provisional only,
    never trusted for mutability (W5: unknown -> WRITE, the safe default).
    Lives here (re-exported by kernel/reducers.py as `_fix_step_kind`) so
    kernel/action_plans.py can apply the same rule to a compiled plan
    without importing reducers."""
    spec = catalog.get(step.tool)
    kind = StepKind.READ if spec is not None and spec.mutability == ToolMutability.READ_ONLY else StepKind.WRITE
    return dataclasses.replace(step, kind=kind, requires_commit_intent=(kind == StepKind.WRITE))


_ACTION_SLOT_KEY = re.compile(r"^a\d+\.\w+$")


def _normalize_fact_key(raw_key: str | None) -> str | None:
    """Day 1 (docs/fdb_v3_implementation_plan.md, found live: a real
    PLAN response from gemini-3.6-flash with thinking off): the schema's
    own instruction and example (`workers/planner.py.PLAN_SCHEMA`:
    `{"type": "fact", "key": "slot.$G.destination"}`) is not always
    enough -- a live model can return the bare slot name ("query")
    instead of the fully-qualified key ("slot.$G.query"). `_bind` then
    looks up a fact that will never exist, and the step blocks on a
    clarify forever, even though the Interpreter correctly committed
    the slot moments earlier (confirmed by direct reproduction: the
    INTERPRET job set `slot.<gid>.query`; the PLAN job's own binding
    was the bare string "query", not "slot.$G.query").

    Every legitimate fact-key scheme this codebase uses (`slot.`,
    `derived.`, `claim.`, `goal.`, `hyp.`, `stepout.`, `result.`) is
    always dotted, so a bare name with no "." at all is unambiguous --
    there is no other sensible reading of it than "this goal's own slot
    of that name," the same convention `slot_deltas[].name` already
    uses. A key that already has a "." (even if malformed some other
    way) is left untouched rather than guessed at -- except S3's per-action
    slot name ("a0.city", `kernel/action_plans.py`), which a fallback PLAN
    job sees in its `facts` view and may bind by that bare name."""
    if raw_key is None:
        return raw_key
    if "." in raw_key and not _ACTION_SLOT_KEY.match(raw_key):
        return raw_key
    return f"slot.$G.{raw_key}"


def parse_plan(raw: dict, *, goal_id: str, plan_rev: int) -> Plan:
    """`step.kind` here is provisional; the caller (kernel/reducers.py)
    overwrites it from the authoritative ToolCatalog mutability right after
    this returns — see that module's `_fix_step_kind`."""
    steps = []
    for raw_step in raw.get("steps") or ():
        tool_name = raw_step["tool"]
        kind = StepKind(raw_step["kind"]) if "kind" in raw_step else StepKind.WRITE

        bindings: dict[str, Binding] = {}
        for param, raw_binding in (raw_step.get("bindings") or {}).items():
            binding_kind = BindingKind(raw_binding["type"])
            bindings[param] = Binding(
                kind=binding_kind,
                fact_key=_normalize_fact_key(raw_binding.get("key")),
                value=raw_binding.get("value"),
                step_key=raw_binding.get("step_key"),
                path=raw_binding.get("path"),
            )

        steps.append(
            PlanStep(
                step_key=raw_step["local_id"],
                tool=tool_name,
                kind=kind,
                bindings=bindings,
                after=tuple(raw_step.get("after") or ()),
                output_map=dict(raw_step.get("output_map") or {}),
                requires_commit_intent=(kind == StepKind.WRITE),
                structure_depends_on=tuple(raw_step.get("structure_depends_on") or ()),
                absence_keys=tuple(raw_step.get("absence_keys") or ()),
            )
        )
    return Plan(goal_id=goal_id, plan_rev=plan_rev, steps=tuple(steps))


def parse_compose(raw: dict) -> tuple[str, tuple[str, ...]]:
    text = str(raw.get("text", "")).strip()
    claims = tuple(raw.get("claims") or ())
    return text, claims


def parse_perception(raw: dict) -> tuple[PerceptionClaim, ...]:
    return tuple(
        PerceptionClaim(name=c["name"], value=c.get("value"), confidence=Confidence(c["confidence"]))
        for c in raw.get("claims") or ()
    )


def parse_asr(raw: dict) -> tuple[tuple[AsrSegment, ...], bool]:
    segments = tuple(
        AsrSegment(text=s["text"], offset_us=int(s.get("offset_us", 0)), end_us=int(s.get("end_us", 0)))
        for s in raw.get("segments") or ()
    )
    return segments, bool(raw.get("end_of_utterance", False))


_FRAME_HOLE_OPS = {"field", "count", "min", "max", "first_k", "exists"}


def parse_frame(raw: dict) -> dict:
    """C2 (`workers/frame.py`'s module docstring, Phase 5 of
    `docs/post_v4_implementation_plan.md`). Returns a plain, JSON-safe dict
    (not a dataclass) so it can be stored directly as a `FactStore` value —
    `{"list_path": str | None, "branches": {"success"|"empty"|"error":
    {"template": str, "holes": {name: {"op", "path", "field", "k"}}}}}`.
    Raises `KeyError`/`ValueError` on anything malformed, caught by the
    same `_apply_worker_result` pattern every other proposal type uses."""
    branches_raw = raw["branches"]
    branches: dict = {}
    for name in ("success", "empty", "error"):
        branch_raw = branches_raw[name]
        template = str(branch_raw["template"])
        holes: dict = {}
        for hole_name, spec in (branch_raw.get("holes") or {}).items():
            op = spec["op"]
            if op not in _FRAME_HOLE_OPS:
                raise ValueError(f"unknown frame hole op: {op!r}")
            holes[hole_name] = {
                "op": op,
                "path": spec.get("path"),
                "field": spec.get("field"),
                "k": int(spec["k"]) if "k" in spec and spec["k"] is not None else None,
            }
        branches[name] = {"template": template, "holes": holes}
    return {"list_path": raw.get("list_path"), "branches": branches}
