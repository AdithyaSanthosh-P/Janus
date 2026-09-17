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
    VISION = "vision"
    # V3 (not implemented): ASR = "asr"


class Confidence(str, Enum):
    """Ordinal, not calibrated probability — VisionAnalyzer's verbalized
    band, mapped (`docs/prompt 2.txt` §10.5)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class QuestionMode(str, Enum):
    AT_UTTERANCE = "at_utterance"
    CURRENT_STATE = "current_state"


class QuestionStatus(str, Enum):
    OPEN = "open"
    ANSWERED = "answered"
    VOID = "void"


class ConflictStatus(str, Enum):
    OPEN = "open"
    VOID = "void"


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
    # Current task state while ACTIVE; frozen at whatever it was the moment
    # the goal is SUSPENDED, so RETURN_TO_GOAL resumes from here with no
    # separate "suspended" copy needed (§4.2: "Last task state, for resume
    # after suspension").
    task_state: TaskState = TaskState.IDLE


@dataclass(frozen=True)
class Snapshot:
    intent: str | None
    slots: dict[str, Any]
    revision: int = 0
    digest: str = ""


def fingerprint_for(tool: str, args: dict) -> str:
    """Digest of (tool name, canonical args) — CommitGate duplicate detection."""
    return compute_digest({"tool": tool, "args": args})


# ---------------------------------------------------------------------------
# V1: turns, interpretation, plans, worker proposals
#
# Not enumerated as their own files in docs/sonnet_implementation_plan.md's
# V1 file list (only kernel/store/workers additions are listed there) — these
# records are added directly to model/types.py, consistent with where V0's
# core records already live, since `model` is the only package every other
# V1 package (kernel, workers) is allowed to depend on for shared shapes.
# ---------------------------------------------------------------------------


class SlotOp(str, Enum):
    SET = "set"
    CLEAR = "clear"


@dataclass(frozen=True)
class SlotDelta:
    name: str
    scope: str  # "goal" | "session"
    op: SlotOp
    value: Any = None


@dataclass(frozen=True)
class VisualCandidate:
    """A named target the Interpreter wants a video frame analyzed for
    (V3, `docs/prompt 2.txt` §10.4 "Demand" trigger)."""

    name: str
    description: str = ""


@dataclass(frozen=True)
class TurnInterpretation:
    """Interpreter worker output (`docs/prompt 2.txt` §4.6). `ambiguities`
    (multiple candidate interpretations) remains out of scope; V3 adds
    visual_reference/visual_candidates only."""

    turn_id: str
    input_digest: str
    act: InterpretAct
    intent: str | None = None
    slot_deltas: tuple[SlotDelta, ...] = ()
    commit_intent: bool = False
    resume_goal_id: str | None = None
    ack_phrase: str | None = None
    visual_reference: str = "none"  # V3: "none" | "at_utterance" | "current_state"
    visual_candidates: tuple[VisualCandidate, ...] = ()


class BindingKind(str, Enum):
    FACT = "fact"
    LITERAL = "literal"
    STEP_OUTPUT = "step_output"


@dataclass(frozen=True)
class Binding:
    kind: BindingKind
    fact_key: str | None = None  # FACT
    value: Any = None  # LITERAL
    step_key: str | None = None  # STEP_OUTPUT
    path: str | None = None  # STEP_OUTPUT: dotted path into that step's result


@dataclass(frozen=True)
class PlanStep:
    step_key: str  # digest of (goal_id, tool, binding template) — stable across revisions
    tool: str
    kind: StepKind
    bindings: dict  # param name -> Binding
    after: tuple[str, ...] = ()  # step_keys that must be CONSUMED first
    output_map: dict = field(default_factory=dict)  # result path -> derived.<gid>.<name>
    requires_commit_intent: bool = False
    structure_depends_on: tuple[str, ...] = ()  # slot names whose change forces replan, not rebind
    absence_keys: tuple[str, ...] = ()  # V2: fact keys (with $G) the step's validity implicitly assumes are absent


@dataclass(frozen=True)
class Plan:
    goal_id: str
    plan_rev: int
    steps: tuple[PlanStep, ...]


@dataclass(frozen=True)
class InterpretationProposal:
    interpretation: TurnInterpretation
    read_set: ReadSet


@dataclass(frozen=True)
class PlanProposal:
    plan: Plan
    read_set: ReadSet


@dataclass(frozen=True)
class ComposeProposal:
    text: str
    claims: tuple  # V1: opaque strings naming what the text asserts; graded in V2
    read_set: ReadSet


@dataclass(frozen=True)
class ChunkRecord:
    ts_us: int
    text: str


@dataclass(frozen=True)
class Turn:
    turn_id: str
    opened_ts_us: int
    closed_ts_us: int | None = None
    chunks: tuple[ChunkRecord, ...] = ()
    prefix_digest: str = ""
    is_interruption: bool = False
    during_goal: str | None = None


class JobStatus(str, Enum):
    RUNNING = "running"
    DONE = "done"
    ABORTED = "aborted"


@dataclass(frozen=True)
class JobRecord:
    """Bookkeeping for a dispatched worker job — lets TaskStateMachine avoid
    re-dispatching (e.g.) a second PLAN job for a goal that already has one
    in flight. Correctness of *accepting* the eventual result never depends
    on this table — that's read_set validity (K4), same as everything else.
    """

    job_id: str
    kind: JobKind
    goal_id: str | None
    turn_id: str | None
    read_set: ReadSet
    status: JobStatus = JobStatus.RUNNING
    dispatched_step: int = 0


# ---------------------------------------------------------------------------
# V3: multimodal evidence (`docs/prompt 2.txt` §10, `docs/theme05_
# implementation_blueprint.md` §2.10/§9). Simplified per
# `docs/sonnet_implementation_plan.md`'s V3 "SIMPLIFIED" scope: leases are a
# flat timeout (no frame-count tracking), perception scheduling dispatches
# the first available aligned frame (no coalescing/watch-mode re-analysis),
# and only one open question is tracked per goal (a new demand retargets it
# rather than the full multi-question bookkeeping the blueprint describes).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """A stored video frame — analyzed only on demand (§10.4), never on
    arrival. `frame_id` is the harness-given reference; no blob is held
    here (see `workers/vision.py`'s module docstring for why)."""

    obs_id: str
    frame_id: str
    capture_ts_us: int
    arrival_step: int
    modality_seq: int  # 1-based


@dataclass(frozen=True)
class PerceptionClaim:
    name: str
    value: Any
    confidence: Confidence


@dataclass(frozen=True)
class QuestionTarget:
    name: str
    description: str = ""


@dataclass(frozen=True)
class Question:
    question_id: str
    goal_id: str
    targets: tuple[QuestionTarget, ...]
    mode: QuestionMode
    anchor_ts_us: int
    created_by: str  # "demand" | "renewal" (cue/plan sources not implemented — see kernel/perception.py)
    status: QuestionStatus = QuestionStatus.OPEN
    pending_job_id: str | None = None  # VISION job currently analyzing a frame for this question
    pending_obs_id: str | None = None  # which observation that job is analyzing


@dataclass(frozen=True)
class Conflict:
    conflict_id: str
    goal_id: str
    name: str  # slot/target name, not a fact key
    candidates: tuple[dict, ...]  # [{"value", "source": "user"|"perception", "confidence", "ref"}, ...]
    status: ConflictStatus = ConflictStatus.OPEN
