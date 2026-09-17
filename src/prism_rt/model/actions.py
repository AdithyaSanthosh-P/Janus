"""Outbound action types, emitted only by EmissionGate inside a kernel step."""

from __future__ import annotations

from dataclasses import dataclass

from prism_rt.model.types import ActionType, ClaimGrade, ReadSet, Snapshot, ToolMutability


@dataclass(frozen=True)
class SpeakBody:
    text: str
    kind: str  # "ack", "progress", "hold", "final"
    claim_grade: ClaimGrade | None = None


@dataclass(frozen=True)
class ToolCallBody:
    call_id: str
    tool_name: str
    arguments: dict
    mutability: ToolMutability = ToolMutability.READ_ONLY


@dataclass(frozen=True)
class CancelBody:
    target_call_id: str
    reason: str


@dataclass(frozen=True)
class FinalBody:
    text: str
    task_completed: bool = True


@dataclass(frozen=True)
class Action:
    action_type: ActionType
    action_id: str
    ts_us: int
    body: SpeakBody | ToolCallBody | CancelBody | FinalBody
    read_set: ReadSet
    snapshot: Snapshot | None = None
    trigger_event_id: str = ""
    rule_id: str = ""


@dataclass(frozen=True)
class IntendedAction:
    """A decide-phase proposal, before EmissionGate assigns action_id/ts_us
    and validates it. Kernel decision components (CommitGate, FastResponder,
    CancellationPlanner, ...) produce these; only EmissionGate turns them
    into emitted `Action`s (or rejects them)."""

    action_type: ActionType
    body: SpeakBody | ToolCallBody | CancelBody | FinalBody
    read_set: ReadSet
    needs_snapshot: bool = False
    trigger_event_id: str = ""
    rule_id: str = ""
    goal_id: str | None = None  # V1: which goal a FINAL concludes, for EmissionGate's side effect
