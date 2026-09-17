"""Parse raw worker-proposal dicts into validated model records.

This lives on the kernel side of the package boundary (`docs/prompt 2.txt`
§21.1: kernel must not import workers) even though it mirrors the prompt-
building code in `workers/interpreter.py`, `workers/planner.py`,
`workers/composer.py` — accepting/rejecting an untrusted proposal is a
kernel responsibility (§5.1: "WORKER_RESULT ... Validate proposal read
set; accept, hold, or reject"), the prompt-building half is not.
"""

from __future__ import annotations

from prism_rt.model.types import (
    Binding,
    BindingKind,
    InterpretAct,
    Plan,
    PlanStep,
    SlotDelta,
    SlotOp,
    StepKind,
    TurnInterpretation,
)


def parse_interpretation(raw: dict, *, turn_id: str, input_digest: str) -> TurnInterpretation:
    act = InterpretAct(raw["act"])
    slot_deltas = tuple(
        SlotDelta(
            name=delta["name"],
            scope=delta.get("scope", "goal"),
            op=SlotOp(delta.get("op", "set")),
            value=delta.get("value"),
        )
        for delta in raw.get("slot_deltas") or ()
    )
    return TurnInterpretation(
        turn_id=turn_id,
        input_digest=input_digest,
        act=act,
        intent=raw.get("intent"),
        slot_deltas=slot_deltas,
        commit_intent=bool(raw.get("commit_intent", False)),
        resume_goal_id=raw.get("resume_goal_id"),
        ack_phrase=raw.get("ack_phrase"),
    )


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
                fact_key=raw_binding.get("key"),
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
            )
        )
    return Plan(goal_id=goal_id, plan_rev=plan_rev, steps=tuple(steps))


def parse_compose(raw: dict) -> tuple[str, tuple[str, ...]]:
    text = str(raw.get("text", "")).strip()
    claims = tuple(raw.get("claims") or ())
    return text, claims
