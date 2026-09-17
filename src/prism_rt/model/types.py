"""Enumerations and small immutable records shared across the kernel.

This module imports nothing from the rest of `prism_rt` (see the package
boundary table in `docs/prompt 2.txt` §21.1: `model` may import nothing) so
every other package can depend on it without risk of a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any

from prism_rt.canonical import ABSENT, compute_digest


class EventClass(IntEnum):
    """Ordering class for batch sorting. Lower = higher priority.

    At equal ts_us, a lower class is applied first — facts that can
    invalidate work (manifests, user input) are ordered ahead of facts that
    are subject to invalidation (tool results, worker outputs). See
    `docs/prompt 2.txt` §5.1.
    """

    SETUP = 0  # scenario_start, manifest
    INTERRUPTION = 1
    USER_CONTENT = 2  # text_chunk, audio_clip, video_frame
    END_OF_TURN = 3
    TOOL_RESULT = 4  # tool_result, cancel_ack
    WORKER_RESULT = 5
    TIMER = 6  # timer_fired, watchdog
    INTERNAL = 7  # emit_rejected, malformed_input, scenario_end


class ActionType(str, Enum):
    CANCEL = "cancel"
    SPEAK = "speak"
    TOOL_CALL = "tool_call"
    CLARIFY = "clarify"
    FINAL = "final"


class CallStatus(str, Enum):
    PROPOSED = "proposed"
    IN_FLIGHT = "in_flight"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    COMPLETED_AFTER_CANCEL = "completed_after_cancel"
    CONSUMED = "consumed"
    STALE = "stale"
    RETAINED = "retained"
    FAILED = "failed"
    INVALIDATED = "invalidated"
    DISCARDED = "discarded"


TERMINAL_CALL_STATUSES = frozenset(
    {
        CallStatus.CANCELLED,
        CallStatus.COMPLETED_AFTER_CANCEL,
        CallStatus.CONSUMED,
        CallStatus.STALE,
        CallStatus.RETAINED,
        CallStatus.FAILED,
        CallStatus.DISCARDED,
    }
)


class EffectStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"


# Effect states that block a new write with the same fingerprint/lineage
# (CommitGate G5/G6). FAILED does not block — it permits a retry.
BLOCKING_EFFECT_STATUSES = frozenset(
    {EffectStatus.PENDING, EffectStatus.CONFIRMED, EffectStatus.UNKNOWN}
)


class GoalStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class TaskState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    EXECUTING = "executing"
    RESPONDING = "responding"
    CLARIFYING = "clarifying"
    TRIAGE = "triage"
    COMPLETED = "completed"
    FAILED = "failed"


class FloorState(str, Enum):
    USER_TURN_OPEN = "user_turn_open"
    USER_TURN_CLOSED = "user_turn_closed"


class FactStatus(str, Enum):
    COMMITTED = "committed"
    RETRACTED = "retracted"
    DERIVED = "derived"  # V2: for transitive invalidation
    HYPOTHESIS = "hypothesis"


class ToolMutability(str, Enum):
    READ_ONLY = "read_only"
    STATE_CHANGING = "state_changing"


class StepKind(str, Enum):
    READ = "read"
    WRITE = "write"


class InterpretAct(str, Enum):
    NEW_GOAL = "new_goal"
    SLOT_UPDATE = "slot_update"
    ADDITION = "addition"
    CONFIRM = "confirm"
    DENY = "deny"
    ANSWER_CLARIFICATION = "answer_clarification"
    BACKCHANNEL = "backchannel"
    ABORT = "abort"
    RETURN_TO_GOAL = "return_to_goal"
    SMALLTALK = "smalltalk"
    UNCLEAR = "unclear"


class ClaimGrade(str, Enum):
    UNDERSTOOD = "understood"
    INTENDED = "intended"
    IN_PROGRESS = "in_progress"
    RESULT = "result"
    EFFECT_DONE = "effect_done"


class JobKind(str, Enum):
    INTERPRET = "interpret"
    PLAN = "plan"
    COMPOSE = "compose"
    # V3: VISION = "vision", ASR = "asr", FRAME = "frame"


# ---------------------------------------------------------------------------
# Core records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Provenance:
    source: str  # "user", "tool", "system", "carry"
    event_id: str | None = None
    turn_id: str | None = None
    call_id: str | None = None
    step_no: int = 0
    ts_us: int = 0
    derivation_read_set: "ReadSet | None" = None  # V2: for transitive invalidation


@dataclass(frozen=True)
class ReadSetEntry:
    key: str
    version: int  # 0 if absent
    digest: str  # ABSENT if absent


@dataclass(frozen=True)
class ReadSet:
    entries: tuple[ReadSetEntry, ...] = ()  # sorted by key
    digest: str = ""  # SHA-256 of canonical entries

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(e.key for e in self.entries)


EMPTY_READ_SET = ReadSet()


def make_read_set(entries: "list[ReadSetEntry] | tuple[ReadSetEntry, ...]") -> ReadSet:
    """Build a ReadSet with entries sorted by key and a stable digest."""
    ordered = tuple(sorted(entries, key=lambda e: e.key))
    digest = compute_digest(
        [{"key": e.key, "version": e.version, "digest": e.digest} for e in ordered]
    )
    return ReadSet(entries=ordered, digest=digest)


@dataclass(frozen=True)
class Fact:
    key: str
    value: Any
    digest: str  # digest of canonical value
    ver: int  # session revision at last change
    status: FactStatus = FactStatus.COMMITTED
    provenance: Provenance = field(default_factory=lambda: Provenance(source="system"))


@dataclass(frozen=True)
class ChangeSet:
    step_no: int
    changes: tuple  # (key, old_digest, new_digest, old_status, new_status, ver)
    new_revision: int


@dataclass(frozen=True)
class ValidityResult:
    is_valid: bool
    failing: tuple = ()  # (key, expected_digest, actual_digest, reason)


@dataclass(frozen=True)
class CallRecord:
    call_id: str
    goal_id: str
    step_key: str
    tool: str
    kind: StepKind
    args: dict
    fingerprint: str  # digest({"tool": tool, "args": args})
    read_set: ReadSet
    status: CallStatus = CallStatus.PROPOSED
    attempt: int = 1
    created_step: int = 0
    emitted_ts_us: int | None = None
    cancel_reason: str | None = None


@dataclass(frozen=True)
class EffectRecord:
    fingerprint: str
    lineage: str  # "{goal_id}:{step_key}"
    call_id: str
    status: EffectStatus = EffectStatus.PENDING


@dataclass(frozen=True)
class GoalRecord:
    goal_id: str
    status: GoalStatus = GoalStatus.ACTIVE
    created_turn: str = ""
    created_step: int = 0
    replaced_by: str | None = None
    suspended_task_state: TaskState | None = None


@dataclass(frozen=True)
class Snapshot:
    intent: str | None
    slots: dict[str, Any]
    revision: int = 0
    digest: str = ""


def fingerprint_for(tool: str, args: dict) -> str:
    """Digest of (tool name, canonical args) — CommitGate duplicate detection."""
    return compute_digest({"tool": tool, "args": args})
