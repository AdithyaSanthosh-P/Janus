# Theme 05: Interruptible Real-Time Agents — Implementation Plan

Samsung PRISM GenAI Hackathon, 3rd Edition.

This document is the executable coding plan. A coding agent should be able to read this document top-to-bottom and implement every version sequentially, without making major architectural decisions.

**Source documents treated as ground truth:**
1. `prompt1.txt` — Pre-Architecture Problem Analysis
2. `prompt 2.txt` — System Architecture
3. `prompt 3.txt` — Technical Innovation Analysis
4. `theme05_implementation_blueprint.md` — Full Implementation Blueprint
5. `prototype_version_plan.md` — Staged Prototype Strategy

---

## 0. Guiding Principles

1. **Every version is independently runnable and submittable.** If V(N+1) breaks, `git checkout release/vN` is the submission.
2. **Single-writer kernel.** All state mutation goes through `StoreTxn` inside synchronous `Kernel.step()`. No exceptions.
3. **No wall-clock reads in core.** All timing via `ClockPort.now_us()`. Only `watchdog.py`, `workers/runner.py` (transport timeouts), and `sim/` may read real time.
4. **Additive evolution.** Never break V(N) interfaces when building V(N+1). Guard new features behind `Config` flags.
5. **Mock first, live second.** All tests run against `ScriptedProvider` before live LLM validation.
6. **Deterministic replay.** Same event sequence + same scripted provider = identical decision logs and actions.

---

## 1. Repository Structure

```
prism-theme05/
├── README.md
├── pyproject.toml
├── Dockerfile
├── config/
│   ├── defaults.py           # Policy constants (not YAML — hackathon simplification)
│   ├── lexicons.py           # Cue words, inert words, stop words
│   └── templates.py          # Utterance templates by kind
├── src/prism_rt/
│   ├── __init__.py
│   ├── entry.py              # setup() + Runtime.run_scenario()
│   ├── config.py             # Config frozen dataclass with feature flags
│   ├── ids.py                # Deterministic ID generation
│   ├── canonical.py          # Canonical JSON, digest, normalize, ABSENT
│   ├── errors.py             # Typed exceptions
│   ├── model/
│   │   ├── __init__.py
│   │   ├── types.py          # All enums + small frozen records
│   │   ├── events.py         # Envelope, all payload types
│   │   └── actions.py        # Action, body types, Snapshot
│   ├── store/
│   │   ├── __init__.py
│   │   ├── session.py        # SessionStore aggregate + StoreTxn
│   │   ├── facts.py          # FactStore + DependencyIndex
│   │   ├── catalog.py        # ToolCatalog, manifest parser
│   │   └── ledgers.py        # CallLedger, EffectLedger, GoalRegistry,
│   │                         # PlanStore, TurnLog, OutputLedger, TimerWheel
│   ├── kernel/
│   │   ├── __init__.py
│   │   ├── step.py           # Kernel + 7-phase step engine
│   │   ├── ordering.py       # Batch ordering by (ts_us, class, seq)
│   │   ├── reducers.py       # Event → state mutations
│   │   ├── invalidation.py   # Dependency marking + cancellation (V2: transitive)
│   │   ├── turns.py          # TurnManager + prefix digest
│   │   ├── detector.py       # QuickDetector
│   │   ├── interpret_apply.py # Interpretation → fact mutations
│   │   ├── task.py           # TaskStateMachine + GoalLifecycle + Triage
│   │   ├── executor.py       # PlanExecutor + binder + fingerprint
│   │   ├── commit.py         # CommitGate G1-G9 (V2: settle G10-G11)
│   │   ├── results.py        # ResultRouter (7 branches)
│   │   ├── responder.py      # FastResponder + UtteranceBudget
│   │   ├── snapshot.py       # SnapshotProjector
│   │   └── emission.py       # EmissionGate + schema validation + ordering
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── runner.py         # WorkerRunner protocol + AsyncWorkerRunner
│   │   ├── gateway.py        # ModelGateway + ScriptedProvider
│   │   ├── interpreter.py    # Interpreter worker
│   │   ├── planner.py        # Planner worker
│   │   └── composer.py       # Composer worker + template fallback
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── clock.py          # ClockPort protocol + SteppedClock
│   │   ├── codec.py          # ICW encode/decode
│   │   └── output_writer.py  # Synchronous write protocol
│   ├── observability/
│   │   ├── __init__.py
│   │   ├── decision_log.py   # Structured JSONL per step
│   │   └── watchdog.py       # Wall-clock budget supervisor
│   └── sim/
│       ├── __init__.py
│       ├── harness.py        # Deterministic test harness
│       └── checker.py        # Trace invariant checker
├── tests/
│   ├── conftest.py           # Shared fixtures
│   ├── scenarios/            # YAML scenario files
│   ├── test_v0.py            # V0 acceptance tests
│   ├── test_v1.py            # V1 scenario tests
│   ├── test_v2.py            # V2 innovation tests
│   └── test_v3.py            # V3 multimodal tests
└── docs/
    ├── architecture.md       # Reference copy
    └── measurements.md       # Results table (filled after tests)
```

### 1.1 File Specifications

| File | Purpose | Dependencies | Public Interfaces | Version |
|---|---|---|---|---|
| `canonical.py` | Canonical JSON, digest (SHA-256 of canonical JSON), value normalization, `ABSENT` sentinel | stdlib only | `to_canonical_json(v) -> str`, `compute_digest(v) -> str`, `normalize_value(v, schema) -> JSON`, `ABSENT = "__ABSENT__"` | V0 |
| `ids.py` | Deterministic counter-based ID generation, seeded per session | stdlib only | `IdGenerator(seed)`, `.next(kind: str) -> str` for kinds: event, step, turn, goal, plan, call, job, action, utt, timer | V0 |
| `config.py` | Central frozen dataclass with timing policies, limits, feature flags | `model/types.py` | `Config`, `DEFAULT_CONFIG` | V0 |
| `errors.py` | Typed exceptions | stdlib only | `PrismError`, `StoreMutationOutsideStep`, `InvariantViolation`, `CommitGateRejected`, `StaleReadSet`, `CodecError` | V0 |
| `model/types.py` | All enums (`EventClass`, `ActionType`, `CallStatus`, `EffectStatus`, `GoalStatus`, `TaskState`, `FactStatus`, `ClaimGrade`, `ToolMutability`, `StepKind`, `StepStatus`, `FloorState`, `InterpretAct`, `JobKind`) + frozen records (`Provenance`, `ReadSetEntry`, `ReadSet`, `ChangeSet`, `ValidityResult`, `Fact`) | stdlib, `dataclasses` | All enums and records | V0 |
| `model/events.py` | `Envelope` + all payload dataclasses (`ManifestPayload`, `TextChunkPayload`, `EndOfTurnPayload`, `InterruptionPayload`, `ToolResultPayload`, `TimerFiredPayload`, `WorkerResultPayload`, `WatchdogPayload`) | `model/types.py` | All payload classes, `Envelope` | V0 |
| `model/actions.py` | `Action`, `SpeakBody`, `ToolCallBody`, `CancelBody`, `FinalBody`, `Snapshot` | `model/types.py` | All action classes | V0 |
| `store/facts.py` | `FactStore` (versioned KV store), `DependencyIndex` (reverse index from fact key to dependents) | `model/types.py`, `canonical.py` | `FactStore.get/set/retract/is_valid/revision/snapshot_committed`, `DependencyIndex.register/unregister/dependents` | V0 |
| `store/catalog.py` | `ToolCatalog` — manifest parsing, mutability detection, quarantine | `model/types.py`, `canonical.py`, `config.py` | `ToolCatalog.parse_manifest/get/usable_tools/validate_args` | V0 |
| `store/ledgers.py` | `CallLedger`, `EffectLedger`, `GoalRegistry`, `PlanStore`, `TurnLog`, `OutputLedger`, `TimerWheel` | `model/types.py`, `model/events.py`, `model/actions.py` | All ledger classes | V0 (extended V1) |
| `store/session.py` | `SessionStore` aggregate + `StoreTxn` mutation guard | all `store/*`, `config.py`, `ids.py` | `SessionStore.new()`, `.begin_txn()`, `.facts`, `.catalog`, `.call_ledger`, `.effect_ledger`, `.goals`, `.plans`, `.turns`, `.timers`; `StoreTxn.*` | V0 |
| `adapters/clock.py` | `ClockPort` protocol + `SteppedClock` (Model B) | stdlib | `ClockPort.now_us()`, `.advance_to(ts_us)`, `.busy(flag)` ; `SteppedClock` | V0 |
| `adapters/codec.py` | Encode/decode between wire JSON and internal `Envelope`/`Action` | `model/*`, `canonical.py` | `HarnessCodec.decode(raw) -> list[Envelope]`, `.encode(action) -> dict` | V0 |
| `adapters/output_writer.py` | Synchronous write to harness | `model/actions.py`, `adapters/codec.py` | `OutputWriter.write(action) -> WriteResult` | V0 |
| `kernel/ordering.py` | Batch sort by `(ts_us, class, seq)` | `model/events.py` | `order_batch(envs) -> list[Envelope]`, `EVENT_CLASS` mapping | V0 |
| `kernel/reducers.py` | Event to state mutations. Dispatch by event type. | `store/*`, `kernel/turns.py`, `kernel/detector.py` | `apply(env, txn, ctx)` | V0 (extended V1) |
| `kernel/invalidation.py` | Compute dependents of changed facts, mark calls INVALIDATED, emit CANCEL | `store/*` | `InvalidationEngine.invalidate(changes, txn) -> InvalidationReport` | V0 (V2: transitive) |
| `kernel/step.py` | `Kernel` object + 7-phase `step(batch)` | all `kernel/*`, `store/*`, `adapters/*`, `observability/*` | `Kernel(config, store, clock, writer, runner, log)`, `.step(batch) -> StepReport` | V0 (extended V1) |
| `kernel/results.py` | Route tool results through 7 branches | `store/*` | `ResultRouter.route(result_env, txn, ctx) -> RouteOutcome` | V0 |
| `kernel/commit.py` | CommitGate conditions G1-G9 | `store/*` | `CommitGate.evaluate(step, bind_result, ctx) -> GateDecision` | V0 (V2: settle G10-G11) |
| `kernel/snapshot.py` | Compute snapshot from committed facts at emission | `store/*` | `SnapshotProjector.project(ctx) -> Snapshot` | V0 |
| `kernel/emission.py` | Validate + order + emit actions. Order: CANCEL, SPEAK, TOOL_CALL, CLARIFY, FINAL | `store/*`, `adapters/output_writer.py` | `EmissionGate.emit(intended, txn, ctx) -> EmitReport` | V0 |
| `kernel/turns.py` | Turn assembly, chunk buffering, prefix digest | `store/*`, `canonical.py` | `TurnManager.on_chunk()`, `.on_eot()`, `.prefix_digest()` | V1 |
| `kernel/detector.py` | QuickDetector — regex/lexicon extraction of HIGH-confidence values and cues | `config/lexicons.py`, `store/catalog.py` | `QuickDetector.detect(chunk, turn_text, ctx) -> ChunkSignals` | V1 |
| `kernel/interpret_apply.py` | Convert `TurnInterpretation` to fact mutations | `store/*`, `canonical.py` | `apply_interpretation(interp, turn, txn, ctx) -> ApplyReport` | V1 |
| `kernel/task.py` | Task state machine + goal lifecycle + triage | `store/*` | `TaskStateMachine` with states: IDLE, LISTENING, UNDERSTANDING, PLANNING, EXECUTING, RESPONDING, CLARIFYING, TRIAGE, COMPLETED, FAILED | V1 |
| `kernel/executor.py` | PlanExecutor: bind args, create calls, commit-last for writes | `store/*`, `kernel/commit.py` | `PlanExecutor.decide(ctx) -> list[IntendedAction]` | V1 |
| `kernel/responder.py` | FastResponder: templated ACK, PROGRESS, HOLD, FINAL | `store/*`, `config/templates.py` | `FastResponder.decide(ctx) -> list[IntendedAction]` | V1 |
| `workers/runner.py` | `WorkerRunner` protocol + `AsyncWorkerRunner` + `ScriptedRunner` (for tests) | `model/*` | `WorkerRunner.submit(job)`, `.abort(job_id)`, `.running()` | V1 |
| `workers/gateway.py` | LLM provider interface + `ScriptedProvider` | `model/*` | `ModelGateway.complete_json(kind, prompt, schema) -> dict`, `ScriptedProvider` | V1 |
| `workers/interpreter.py` | Build prompt + call gateway, validate output to `TurnInterpretation` | `workers/gateway.py` | `run_interpret(view) -> InterpretationProposal` | V1 |
| `workers/planner.py` | Build prompt + call gateway, validate output to `Plan` | `workers/gateway.py` | `run_plan(view) -> PlanProposal` | V1 |
| `workers/composer.py` | Build prompt + call gateway, validate output to response text with claims | `workers/gateway.py` | `run_compose(view) -> ComposeProposal` | V1 |
| `observability/decision_log.py` | Structured JSONL per step | `model/*` | `DecisionLogger.begin()`, `.note()`, `.end() -> DecisionRecord` | V0 |
| `observability/watchdog.py` | Wall-clock 105s supervisor | stdlib | `ScenarioWatchdog(budget_s, callback)` | V1 |
| `sim/harness.py` | Deterministic event replay: feeds events, runs mock tools, drives kernel | all | `SimHarness(scenario, kernel, clock)`, `.run() -> RunResult` | V0 |
| `sim/checker.py` | Trace invariant checker | `model/*` | `TraceChecker.check(run_dir) -> list[Violation]` | V0 |
| `entry.py` | Runtime setup + `run_scenario()` | all | `setup(config_dir) -> Runtime`, `Runtime.run_scenario(io, meta) -> RunSummary` | V1 |

### 1.2 Files DEFERRED or CUT

| Blueprint Item | Status | Reason |
|---|---|---|
| `kernel/perception.py` | DEFERRED to V3 | Multimodal only needed for visual scenarios |
| `kernel/frames.py` (response frames) | DEFERRED to V3 | Demo value, not required for text scoring |
| `kernel/retry.py` | SIMPLIFIED | Inline in `results.py`: reads retry at most 2, writes retry once if retryable |
| `kernel/reconcile.py` | SIMPLIFIED | Inline in `task.py`: INFORM truthfully, no compensation |
| `kernel/clarify.py` | SIMPLIFIED | Inline in `task.py`: one pending clarification per goal |
| `kernel/scheduler.py` | SIMPLIFIED | Inline in `step.py`: direct dispatch in phase 7 |
| `kernel/monitors.py` | MERGED into `sim/checker.py` | No separate online monitors for hackathon |
| `workers/vision.py` | DEFERRED to V3 | Multimodal |
| `workers/asr.py` | DEFERRED to V3 | Multimodal |
| `workers/framer.py` | DEFERRED to V3 | Response frames |
| `store/evidence.py` | DEFERRED to V3 | Perception evidence |
| `store/holds.py` | MERGED into `ledgers.py` | Small enough to colocate |
| `store/blobs.py` | DEFERRED to V3 | Binary storage for PNG/WAV |
| `sim/explorer.py` | CUT | Full adversarial PCT is too expensive |
| `sim/minimize.py` | CUT | Delta-debugging is luxury |
| `sim/report.py` | CUT | Simple markdown suffices |
| `observability/metrics.py` | CUT | Decision log suffices |
| `observability/telemetry.py` | CUT | Decision log suffices |
| `observability/run_writer.py` | MERGED into `decision_log.py` | Single JSONL writer |
| `adapters/harness_io.py` | MERGED into sim harness | No separate real harness adapter until kit |
| `adapters/ingress.py` | INLINED into driver | One less async task |
| `tools/` directory | CUT | Manual CLI tools not needed |
| Multiple clock models | CUT | Build Model B only; adapt on kit arrival |
| `workers/providers/` | SIMPLIFIED | `ScriptedProvider` in `gateway.py`; add Anthropic provider inline |
| `workers/schemas.py` | MERGED into worker files | JSON schemas inline |
| `workers/prompts/` | INLINED into worker files | Prompt strings as constants |

---

## 2. Core Data Models and Interfaces

### 2.1 Enumerations (`model/types.py`)

```python
from enum import IntEnum, Enum
from dataclasses import dataclass, field
from typing import Any

class EventClass(IntEnum):
    """Ordering class for batch sorting. Lower = higher priority."""
    SETUP = 0       # scenario_start, manifest
    INTERRUPTION = 1
    USER_CONTENT = 2  # text_chunk, audio_clip, video_frame
    END_OF_TURN = 3
    TOOL_RESULT = 4   # tool_result, cancel_ack
    WORKER_RESULT = 5
    TIMER = 6          # timer_fired, watchdog
    INTERNAL = 7       # emit_rejected, malformed_input, scenario_end

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

class EffectStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"

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
    DERIVED = "derived"    # V2: for transitive invalidation
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
```

### 2.2 Core Records (`model/types.py`)

```python
@dataclass(frozen=True)
class Provenance:
    source: str             # "user", "tool", "system", "carry"
    event_id: str | None = None
    turn_id: str | None = None
    call_id: str | None = None
    step_no: int = 0
    ts_us: int = 0
    derivation_read_set: 'ReadSet | None' = None  # V2: for transitive

@dataclass(frozen=True)
class ReadSetEntry:
    key: str
    version: int            # 0 if absent
    digest: str             # "ABSENT" if absent

@dataclass(frozen=True)
class ReadSet:
    entries: tuple[ReadSetEntry, ...] = ()  # sorted by key
    digest: str = ""        # SHA-256 of canonical entries

@dataclass(frozen=True)
class Fact:
    key: str
    value: Any
    digest: str             # digest of canonical value
    ver: int                # session revision at last change
    status: FactStatus = FactStatus.COMMITTED
    provenance: Provenance = field(default_factory=lambda: Provenance(source="system"))

@dataclass(frozen=True)
class ChangeSet:
    step_no: int
    changes: tuple          # (key, old_digest, new_digest, old_status, new_status, ver)
    new_revision: int

@dataclass(frozen=True)
class ValidityResult:
    is_valid: bool
    failing: tuple = ()     # (key, expected_digest, actual_digest, reason)

@dataclass(frozen=True)
class CallRecord:
    call_id: str
    goal_id: str
    step_key: str
    tool: str
    kind: StepKind
    args: dict
    fingerprint: str        # digest({"tool": tool, "args": args})
    read_set: ReadSet
    status: CallStatus = CallStatus.PROPOSED
    attempt: int = 1
    created_step: int = 0
    emitted_ts_us: int | None = None
    cancel_reason: str | None = None

@dataclass(frozen=True)
class EffectRecord:
    fingerprint: str
    lineage: str            # "{goal_id}:{step_key}"
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
```

### 2.3 Events (`model/events.py`)

```python
@dataclass(frozen=True)
class Envelope:
    ts_us: int
    event_class: EventClass
    seq: int
    event_id: str
    payload_type: str
    payload: Any

    @property
    def ordering_key(self) -> tuple[int, int, int]:
        return (self.ts_us, int(self.event_class), self.seq)

# All payload dataclasses are frozen
@dataclass(frozen=True)
class ManifestPayload:
    tools: list  # list[dict]

@dataclass(frozen=True)
class TextChunkPayload:
    text: str
    turn_id: str | None = None
    is_final: bool = False

@dataclass(frozen=True)
class EndOfTurnPayload:
    turn_id: str | None = None

@dataclass(frozen=True)
class InterruptionPayload:
    reason: str | None = None

@dataclass(frozen=True)
class ToolResultPayload:
    call_id: str
    status: str             # "ok" or "error"
    result: Any = None
    error: dict | None = None

@dataclass(frozen=True)
class WorkerResultPayload:
    job_id: str
    kind: str
    status: str             # "ok" or "error"
    proposal: dict | None = None

@dataclass(frozen=True)
class TimerFiredPayload:
    timer_id: str
    timer_kind: str
    ref: str | None = None
    liveness_fire: bool = False

@dataclass(frozen=True)
class WatchdogPayload:
    wall_elapsed_s: float
```

### 2.4 Actions (`model/actions.py`)

```python
@dataclass(frozen=True)
class SpeakBody:
    text: str
    kind: str               # "ack", "progress", "hold", "final"
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
```

---

## 3. Concurrency and Race Safety

The entire design prevents races through a single mechanism: **the kernel is synchronous and single-threaded**. Every event goes into a mailbox. The kernel drains the mailbox in batches and processes each batch in one `step()` call. Workers run asynchronously but cannot mutate state — they post results to the mailbox.

### 3.1 Race Prevention Matrix

| Race Condition | Event Order | Prevention Rule |
|---|---|---|
| **Stale worker commits** | Slot changes, then worker finishes with old slot values | Worker result carries `read_set` captured at dispatch. `ResultRouter` checks `facts.is_valid(read_set)`. If stale then `rejected_stale`, result discarded. |
| **Duplicate writes** | User says "book it" twice | `CommitGate.G5`: check `EffectLedger.blocking(fingerprint, lineage)`. If an effect with same fingerprint exists and is not FAILED then block. |
| **Old goal results applied** | Goal replaced, then old tool result arrives | `ResultRouter` checks `call.goal_id == store.goals.active_id`. If different then route to `retained` (for potential goal return) or discard. |
| **Cancelled work emitted** | Call invalidated, then result arrives in same batch | Ordering: `INTERRUPTION(1) < TOOL_RESULT(4)`. Interruption applies first, marking call INVALIDATED. When result applies, `ResultRouter` sees CANCEL_REQUESTED then `completed_after_cancel`. |
| **Tool results after goal change** | Goal replaced, then tool result from old goal | `ResultRouter` checks `goal_id` match. Mismatch then `retained` or discarded. |
| **Interruption races** | Correction chunk + tool result at same ts_us | Ordering: `USER_CONTENT(2) < TOOL_RESULT(4)`. Chunk applies first, triggers invalidation, then result is routed as stale/cancelled. |
| **Simultaneous state mutation** | Two events mutate same fact | Impossible: single-writer `StoreTxn`. Events in a batch apply sequentially within phase 2. |
| **Speculative work causing effects** | LLM plans a write speculatively | `CommitGate.G4`: `commit_intent == true` required. Speculative plans never have commit intent. |
| **Retrying unsafe writes** | Write fails with unknown outcome | `CommitGate.G7`: if `EffectLedger` has status UNKNOWN for same lineage then block. Only retry if error is explicitly `retryable`. |
| **Final response from stale state** | Facts change after FINAL is prepared | `EmissionGate` re-validates read sets at emission time (phase 5). If stale then emission rejected. |

### 3.2 Kernel Step Phases

```
Phase 1: ORDER     — Sort batch by (ts_us, class, seq). Read clock once: now_us.
Phase 2: APPLY     — Begin StoreTxn. For each envelope: apply reducer.
Phase 3: INVALIDATE — For each changed fact: find dependents, mark INVALIDATED.
Phase 4: DECIDE    — Each component proposes IntendedActions:
                     (1) CancellationPlanner (emit CANCELs for INVALIDATED calls)
                     (2) TaskStateMachine (state transitions, triage)
                     (3) PlanExecutor (bind args, create calls)
                     (4) CommitGate (evaluate write candidates)
                     (5) FastResponder (ACK, PROGRESS, HOLD)
                     (6) SnapshotProjector (attach to actions)
Phase 5: EMIT      — EmissionGate validates, orders, emits via OutputWriter.
Phase 6: COMMIT    — txn.commit() produces ChangeSet.
Phase 7: DISPATCH  — Submit new worker jobs; abort cancelled jobs.
Phase 8: LOG       — Write DecisionRecord.
```

### 3.3 Invariants That MUST Hold

| ID | Invariant | Enforcement |
|---|---|---|
| T1 | All timestamps in actions/decisions come from `clock.now_us()` | No `time.time()` in kernel or store modules |
| P1 | Every emitted action has a valid read set at emission | `EmissionGate` re-validates before write |
| P3 | Dependent in-flight calls cancelled within same step as fact mutation | `InvalidationEngine` in phase 3 then `CancellationPlanner` in phase 4 |
| W1 | Zero duplicate state-changing calls for same intent/args | `CommitGate.G5` + `EffectLedger` fingerprint check |
| S3 | Emitted snapshot digest matches committed facts at emission step | `SnapshotProjector` computes at emission time, never cached |
| C1 | Same event sequence produces identical decision logs (deterministic replay) | `IdGenerator` seeded per session; `SteppedClock`; `ScriptedProvider` |
| K1 | `step()` is synchronous; no awaits inside | By construction |
| K2 | Phases run in fixed order | Hardcoded sequence in `step()` |
| K3 | `StoreTxn` active only during phases 2-6 | Guard flag checked on every mutation |

---

## 4. Version-by-Version Plan

---

### VERSION 0: Foundation + Deterministic Skeleton

#### Goal
Prove the event-driven kernel works: events go in, facts change, actions come out. No LLM. All plans injected by test scripts.

#### Prerequisites
None — this is the foundation.

#### Files to Create
1. `pyproject.toml` — project metadata, dependencies (pydantic, pyyaml, pytest, pytest-asyncio)
2. `src/prism_rt/__init__.py`
3. `src/prism_rt/canonical.py`
4. `src/prism_rt/ids.py`
5. `src/prism_rt/config.py`
6. `src/prism_rt/errors.py`
7. `src/prism_rt/model/__init__.py`
8. `src/prism_rt/model/types.py`
9. `src/prism_rt/model/events.py`
10. `src/prism_rt/model/actions.py`
11. `src/prism_rt/store/__init__.py`
12. `src/prism_rt/store/facts.py`
13. `src/prism_rt/store/catalog.py`
14. `src/prism_rt/store/ledgers.py`
15. `src/prism_rt/store/session.py`
16. `src/prism_rt/adapters/__init__.py`
17. `src/prism_rt/adapters/clock.py`
18. `src/prism_rt/adapters/codec.py`
19. `src/prism_rt/adapters/output_writer.py`
20. `src/prism_rt/kernel/__init__.py`
21. `src/prism_rt/kernel/ordering.py`
22. `src/prism_rt/kernel/reducers.py`
23. `src/prism_rt/kernel/invalidation.py`
24. `src/prism_rt/kernel/commit.py`
25. `src/prism_rt/kernel/results.py`
26. `src/prism_rt/kernel/snapshot.py`
27. `src/prism_rt/kernel/emission.py`
28. `src/prism_rt/kernel/step.py`
29. `src/prism_rt/observability/__init__.py`
30. `src/prism_rt/observability/decision_log.py`
31. `src/prism_rt/sim/__init__.py`
32. `src/prism_rt/sim/harness.py`
33. `src/prism_rt/sim/checker.py`
34. `tests/conftest.py`
35. `tests/test_v0.py`

#### Execution Flow (V0)

In V0, the kernel runs without LLM workers. Test scripts inject plans and interpretations directly via `StoreTxn`.

```
SimHarness loads YAML scenario
  -> Creates SteppedClock, SessionStore, Kernel
  -> For each event group at time T:
      clock.advance_to(T)
      kernel.step([envelopes])
      -> ORDER: sort envelopes
      -> APPLY: reducers process each (manifest->catalog, chunk->hypothesis facts, tool_result->consume/reject)
      -> INVALIDATE: changed facts -> mark dependent calls
      -> DECIDE: cancellation planner emits CANCELs; CommitGate evaluates writes
      -> EMIT: EmissionGate validates, orders, writes to OutputWriter
      -> COMMIT: txn.commit()
      -> LOG: record DecisionRecord
  -> Checker verifies trace invariants
```

#### Key Interfaces (V0)

**FactStore:**
```python
class FactStore:
    def get(self, key: str) -> Fact | None
    def set(self, key: str, value: Any, status: FactStatus,
            provenance: Provenance, *, rule: str) -> bool
        # Returns True if actually changed. Normalizes value, computes digest.
        # If digest and status unchanged -> no-op (no version bump).
        # Otherwise revision += 1, ver = revision.
    def retract(self, key: str, *, rule: str) -> bool
    def is_valid(self, read_set: ReadSet) -> ValidityResult
    def revision(self) -> int
    def snapshot_committed(self) -> dict[str, Any]
    def build_read_set(self, keys: list[str]) -> ReadSet
```

**DependencyIndex:**
```python
class DependencyIndex:
    def register(self, dep_id: str, read_set: ReadSet) -> None
    def unregister(self, dep_id: str) -> None
    def dependents(self, changed_keys: set[str]) -> set[str]
```

**CommitGate:**
```python
class CommitGate:
    def evaluate(self, call_record: CallRecord, store: SessionStore,
                 now_us: int) -> GateDecision
    # GateDecision has: allowed (bool), blocked_reason (str), rule_id (str)
    # Conditions checked:
    # G1: tool exists and is usable
    # G2: read set is valid
    # G3: floor is closed (user_turn_closed)
    # G4: commit_intent is True for writes
    # G5: no existing effect with same fingerprint (non-FAILED)
    # G6: no existing effect with same lineage (non-FAILED)
    # G7: no UNKNOWN effect for same lineage
    # G8: tool args pass schema validation
    # G9: read set valid at emission time
```

**ResultRouter:**
```python
class ResultRouter:
    def route(self, result: ToolResultPayload, store: SessionStore,
              now_us: int) -> RouteOutcome
    # 7 Branches:
    # 1. UNKNOWN_CALL: call_id not in ledger -> ignore, log
    # 2. DUPLICATE: result already received -> ignore
    # 3. MALFORMED: result fails output schema validation -> treat as error
    # 4. CANCEL_REQUESTED: call was cancel_requested -> completed_after_cancel
    # 5. WRONG_GOAL: call.goal_id != active goal -> retained
    # 6. STALE_READ_SET: call.read_set is invalid -> stale
    # 7. CONSUMED: valid -> consume result, write to facts
```

**EmissionGate:**
```python
class EmissionGate:
    def emit(self, intended: list, txn, ctx) -> EmitReport
    # Order: CANCEL -> SPEAK -> TOOL_CALL -> CLARIFY -> FINAL
    # For each action:
    #   1. Validate read set (is_valid at current revision)
    #   2. Validate schema (action body matches expected shape)
    #   3. For TOOL_CALL: create CallRecord, register deps, write to effect ledger if write
    #   4. For SPEAK/FINAL: attach snapshot if needed
    #   5. Write via OutputWriter
    #   6. Log emission result
```

#### Tests (V0) — 7 scenarios with injected plans

| # | Scenario | What It Tests |
|---|---|---|
| 1 | Manifest parsed, read tool call emitted, result consumed, snapshot emitted | Full happy path with injected plan |
| 2 | Slot changes, dependent in-flight call cancelled in same step, new call emitted | Same-step invalidation + cancellation |
| 3 | Write passes CommitGate, result consumed, effect confirmed, no duplicate | CommitGate + EffectLedger |
| 4 | Result arrives for cancelled call -> `completed_after_cancel`, not consumed | ResultRouter branch 4 |
| 5 | Unknown call_id -> ignored, no crash | ResultRouter branch 1 |
| 6 | Duplicate result delivery -> ignored | ResultRouter branch 2 |
| 7 | Malformed manifest -> tools quarantined; valid tools usable | Catalog quarantine |

#### Acceptance Criteria
- All 7 scenarios pass with injected plans
- `TraceChecker` reports 0 violations for T1, P1, P3, W1, S3, C1
- Replay: 10 runs of same scenario = identical decision logs
- Static check: no `time.time()`, `time.monotonic()`, `datetime.now()` in `kernel/` or `store/`

#### Git Checkpoint
```bash
git add . && git commit -m "feat(v0): foundation skeleton with deterministic kernel"
git tag v0-skeleton
```

#### Rollback Procedure
V0 is the base. If it fails, the architecture is wrong — debug, do not proceed.

#### Known Limitations
- No LLM workers (plans injected)
- No speech output
- No turn management
- No task state machine
- No goal management

#### Next-Version Extension Points
- Add `kernel/turns.py` and `kernel/detector.py` for turn assembly
- Add `kernel/task.py` for state machine
- Add `workers/` for LLM integration
- Add `kernel/responder.py` for speech

---

### VERSION 1: Interruptible Text Agent (MVP)

#### Goal
A complete text-only agent with multi-turn conversations, slot corrections, goal changes. **First submission-capable version.**

#### Prerequisites
- V0 frozen and all tests passing

#### Files to Create
1. `src/prism_rt/kernel/turns.py`
2. `src/prism_rt/kernel/detector.py`
3. `src/prism_rt/kernel/interpret_apply.py`
4. `src/prism_rt/kernel/task.py`
5. `src/prism_rt/kernel/executor.py`
6. `src/prism_rt/kernel/responder.py`
7. `src/prism_rt/workers/__init__.py`
8. `src/prism_rt/workers/runner.py`
9. `src/prism_rt/workers/gateway.py`
10. `src/prism_rt/workers/interpreter.py`
11. `src/prism_rt/workers/planner.py`
12. `src/prism_rt/workers/composer.py`
13. `src/prism_rt/observability/watchdog.py`
14. `src/prism_rt/entry.py`
15. `config/defaults.py`
16. `config/lexicons.py`
17. `config/templates.py`
18. `tests/test_v1.py`
19. `tests/scenarios/*.yaml` (15 scenario files)

#### Files to Modify
- `kernel/step.py` — integrate new decide components (task, executor, responder)
- `kernel/reducers.py` — add turn/detector integration, worker_result handling
- `store/ledgers.py` — add GoalRegistry, PlanStore, TurnLog
- `store/session.py` — expose new sub-stores
- `config.py` — add V1 config fields

#### Key New Interfaces (V1)

**TurnManager:**
```python
class TurnManager:
    def on_chunk(self, chunk: TextChunkPayload, env: Envelope,
                 txn: StoreTxn) -> None
        # Append chunk to open turn (or open a new turn)
        # Update prefix digest
    def on_eot(self, env: Envelope, txn: StoreTxn) -> None
        # Close turn, compute final prefix digest
    def on_interruption(self, env: Envelope, txn: StoreTxn) -> None
        # Mark turn as interruption, open new turn if needed
    def prefix_digest(self, turn_id: str) -> str
```

**QuickDetector:**
```python
class QuickDetector:
    def detect(self, chunk_text: str, turn_text: str,
               ctx) -> ChunkSignals
    # ChunkSignals contains:
    #   high_values: list of (param_name, value) — HIGH-confidence slot values from regex
    #   cues: list of str — "stop", "wait", "no", "change", "abort", "never_mind"
    #   is_correction: bool — detected correction language
    #   is_backchannel: bool — "mm-hmm", "ok", "sure", "yeah"

    def is_inert_tail(self, tail: str) -> bool
        # "please", "thanks", etc. — V4 uses this for promotion
```

**TaskStateMachine:**
```python
class TaskStateMachine:
    # States: IDLE -> LISTENING -> UNDERSTANDING -> PLANNING -> EXECUTING
    #      -> RESPONDING -> COMPLETED
    # Plus: CLARIFYING, TRIAGE, FAILED

    def on_content(self, env, txn, ctx) -> None
        # IDLE/COMPLETED -> LISTENING
    def on_eot(self, env, txn, ctx) -> None
        # LISTENING -> UNDERSTANDING (dispatch interpreter)
    def on_interruption(self, env, txn, ctx) -> None
        # If EXECUTING/RESPONDING -> TRIAGE
    def on_interpretation(self, interp, txn, ctx) -> None
        # UNDERSTANDING -> PLANNING or EXECUTING
    def on_plan(self, plan, txn, ctx) -> None
        # PLANNING -> EXECUTING
    def on_all_calls_done(self, txn, ctx) -> None
        # EXECUTING -> RESPONDING (dispatch composer)
    def on_composition(self, text, txn, ctx) -> None
        # RESPONDING -> COMPLETED (emit FINAL)
    def on_abort(self, txn, ctx) -> None
        # -> COMPLETED (cancel all, emit FINAL)
    def on_goal_replace(self, new_intent, txn, ctx) -> None
        # Suspend current goal, create new goal -> PLANNING
    def on_goal_return(self, goal_id, txn, ctx) -> None
        # Reactivate suspended goal -> EXECUTING (reuse retained results)

    def resolve_triage(self, signals, interp, txn, ctx) -> None
        # Determine: backchannel (resume), slot_update (invalidate+replan),
        # addition (invalidate+replan), replace (new goal), return, abort
```

**PlanExecutor:**
```python
class PlanExecutor:
    def decide(self, ctx) -> list:
        # For each step in active plan:
        #   If status == PENDING and all dependencies met:
        #     Bind args from facts + read set
        #     If write: check CommitGate
        #     Create IntendedAction(TOOL_CALL)
        # Commit-last: defer writes until all independent reads complete

    def bind_args(self, step, goal_id, ctx):
        # Resolve each binding:
        #   fact -> read from FactStore
        #   literal -> use value directly
        #   step_output -> read from result facts
        #   derived -> read from derived facts
        # Build ReadSet capturing all read versions
        # Compute fingerprint: digest({"tool": tool, "args": canonical_args})
```

**FastResponder:**
```python
class FastResponder:
    def decide(self, ctx) -> list:
        # Rules (in priority order):
        # 1. If correction detected + call cancelled -> ACK "Changed to X. Searching now."
        # 2. If EOT + understanding -> ACK "Got it."
        # 3. If call completed + more calls pending -> PROGRESS "Found flights..."
        # 4. If triage + held -> HOLD "One moment..."
        # 5. If all calls done -> trigger RESPONDING state
        #
        # Budget constraints:
        # - No speech during open turn (floor = user_turn_open)
        # - Min gap between utterances: 400ms harness time
        # - Max 1 ACK per turn
        # - Max 3 consecutive HOLDs
```

#### Execution Flow (V1)

```
1. User speaks -> text_chunk events arrive
2. TurnManager buffers chunks, QuickDetector extracts signals
3. On end_of_turn:
   - TaskStateMachine transitions: LISTENING -> UNDERSTANDING
   - Dispatch InterpreterWorker (async)
4. Interpreter returns -> worker_result event
   - Validate proposal read set
   - apply_interpretation: set facts (intent, slots)
   - Invalidation: cancel stale calls
   - TaskStateMachine: UNDERSTANDING -> PLANNING
   - Dispatch PlannerWorker (async)
5. Planner returns -> accept plan
   - PlanExecutor: bind args, create calls
   - TaskStateMachine: PLANNING -> EXECUTING
   - Emit TOOL_CALL actions
   - FastResponder: ACK "Searching for flights..."
6. Tool result arrives:
   - ResultRouter: validate, consume if valid
   - Write results to facts
   - PlanExecutor: check if more steps ready
   - If all done: EXECUTING -> RESPONDING -> dispatch Composer
7. Composer returns -> emit FINAL with snapshot
8. ON INTERRUPTION (at any point):
   - QuickDetector detects correction ("actually Mumbai")
   - Slot fact changes -> Invalidation -> cancel stale calls
   - TaskStateMachine: -> TRIAGE -> resolve -> re-plan
   - FastResponder: ACK "Mumbai instead. Searching now."
```

#### Tests (V1) — 15 scenarios

| # | ID | Scenario | Scoring Category |
|---|---|---|---|
| 1 | N-01 | Simple read (search flights -> result -> FINAL) | TC |
| 2 | N-02 | Chained calls (search -> get_seat_map -> FINAL) | TC |
| 3 | N-03 | Clarification (missing date -> CLARIFY -> answer -> complete) | TC |
| 4 | N-04 | Successful write (search -> "book it" -> confirmed -> FINAL) | TC, Safety |
| 5 | I-01 | Interrupt before planning (correction before interpreter returns) | IR |
| 6 | I-03 | Interrupt during execution (destination change mid-search -> cancel -> re-search) | IR |
| 7 | I-07 | Repeated rapid interruptions (3 corrections during tool latency) | IR |
| 8 | I-08 | Goal replacement (flights -> "create a ticket") | IR |
| 9 | I-09 | Goal replacement + return (flights -> ticket -> "back to flights") | IR |
| 10 | I-13 | Abort ("never mind" -> cancel all -> FINAL) | IR |
| 11 | S-01 | State-changing retry (booking error, retryable -> retry once) | Safety |
| 12 | S-02 | Write timeout (booking no result -> INFORM truthfully) | Safety |
| 13 | S-03 | Duplicate request (user re-requests existing booking -> INFORM) | Safety |
| 14 | R-02 | Duplicate tool result delivery -> ignored | Safety |
| 15 | R-06 | Simultaneous events (same-ts pairs -> deterministic order) | Safety |

All tests use `ScriptedProvider` with canned LLM responses. Each test verifies:
- Correct action sequence
- Correct snapshot values
- TraceChecker 0 violations

#### Acceptance Criteria
- All 15 scenarios pass with ScriptedProvider
- TraceChecker reports 0 violations
- No false completion claims in emitted speech
- Watchdog fires and produces valid FINAL on slow-planner test
- Replay identity: 10 runs = identical traces

#### Git Checkpoint
```bash
git add . && git commit -m "feat(v1): interruptible text agent MVP"
git tag v1-text-agent
git branch release/v1
```

#### Rollback Procedure
```bash
git checkout release/v1
git tag PRISM_GENAI_HACKATHON_Y2026
```

#### Known Limitations
- Non-transitive invalidation (only direct dependents cancelled)
- No settle barrier (writes execute immediately on EOT)
- No absence entries in read sets
- No claim-typed emission grades
- No multimodal
- Full replan on every correction (no rebinder)

#### Next-Version Extension Points
- `invalidation.py`: add transitive retraction
- `commit.py`: add settle barrier (G10, G11)
- `executor.py`: add rebinder for localized corrections
- `emission.py`: add two-phase emission with claim grades

---

### VERSION 2: Robust Recovery + Innovation Core

#### Goal
Add the architectural innovations that most directly improve evaluation scores: transitive invalidation, settle barrier, claim-typed emission.

#### Prerequisites
- V1 frozen and all 15 scenarios passing

#### Files to Create
- `tests/test_v2.py`

#### Files to Modify
- `kernel/invalidation.py` — add transitive retraction with provenance chains
- `kernel/commit.py` — add settle barrier (G10, G11), commit-last ordering
- `kernel/emission.py` — add two-phase emission with claim grades
- `kernel/executor.py` — add rebinder for localized corrections
- `kernel/responder.py` — templates keyed by claim grade
- `store/facts.py` — support `derivation_read_set` on derived facts
- `config.py` — add V2 feature flags (all default `False` for V1 compatibility)

#### New Capabilities

**Transitive Invalidation (INNOV C5):**
```
When fact F changes:
  1. Find derived facts whose derivation_read_set references F
  2. If derivation_read_set invalid -> retract derived fact
  3. Add retracted derived fact to changed set
  4. Repeat until fixpoint (max 8 rounds)

Then: find all in-flight calls whose read_set references ANY changed fact
      -> mark INVALIDATED -> emit CANCEL

Example:
  search_flights(Pune) -> result consumed -> derived.selected_flight = "AI-505"
  get_seat_map("AI-505") in flight (read_set includes derived.selected_flight)
  User says "Mumbai" -> slot.destination changes
  -> search_flights invalidated (direct)
  -> derived.selected_flight retracted (transitive: provenance)
  -> get_seat_map invalidated (its read_set includes derived.selected_flight)
  -> Both calls cancelled in SAME STEP
```

**Settle Barrier (INNOV C9):**
```
When a write (state-changing call) is ready:
  CommitGate adds condition G10: "settle timer"
  G10: ts_since_eot >= settle_ms (default 300ms) AND no new user event since EOT

Purpose: catch corrections like "book the 6pm one" -> 200ms -> "wait, the 8pm one"
  Without settle: booking emitted immediately at EOT
  With settle: booking held for 300ms; correction arrives -> slot change -> cancel

Timer liveness rule:
  If Model B (stepped clock, time only advances on events):
    Schedule a liveness timer at EOT + settle_ms
    If timer fires with no intervening user events -> write proceeds
    This prevents deadlock: clock won't advance without an event, but we need time to pass
```

**Claim-Typed Emission (INNOV C8):**
```
Two-phase emission:
  Phase A: validate entire batch of intended actions (check all read sets)
  Phase B: write entire batch atomically

Claim grades on speech:
  UNDERSTOOD — "I'll search for flights to Mumbai" (just acknowledged intent)
  INTENDED — "Booking the 6pm flight" (write is pending, not yet emitted)
  IN_PROGRESS — "Searching now" (call is in flight)
  RESULT — "Found 3 flights" (result consumed)
  EFFECT_DONE — "Booked flight AI-505" (write confirmed)

Rule: never claim IN_PROGRESS for a call that hasn't been emitted yet.
       never claim EFFECT_DONE for a write that hasn't been confirmed.
```

#### Tests (V2) — 12 additional scenarios

| # | ID | Scenario | Tests |
|---|---|---|---|
| 1 | I-04 | Interrupt during chain (transitive cancel of downstream seat_map) | Transitive invalidation |
| 2 | I-05 | Interrupt immediately before completion (correction at t-1ms) | Edge timing |
| 3 | I-10 | Localized slot correction (only changed slot's dependents re-run) | Rebinder |
| 4 | I-11 | Backchannel interruption ("mm-hmm" -> no cancel) | Triage accuracy |
| 5 | I-12 | Optional constraint addition ("only direct flights" -> cancel) | Absence entries |
| 6 | I-14 | Interruption during write settle (correction at +150ms) | Settle barrier |
| 7 | I-15 | Interruption during write in flight (cancel, reconcile) | Write safety |
| 8 | S-04 | Paraphrase duplicate detection | Fingerprint check |
| 9 | S-10 | Settle catches correction (INTENDED grade for waiting write) | Claim grades |
| 10 | R-05 | Completion/cancellation race (same-ts result and correction) | Ordering |
| 11 | T-05 | Timer liveness under Model B (no deadlock) | Liveness rule |
| 12 | I-04-chain | 3-step chain: search -> select -> book, interrupt at step 2 | Deep transitive |

#### Acceptance Criteria
- All V1 + V2 scenarios pass
- Transitive retraction: I-04 cancels downstream in same step
- Settle sweep: I-14 with offsets from -100 to +600ms: zero wrong bookings for offsets <= settle_ms
- Claim grade check: no IN_PROGRESS for unemitted calls
- Timer liveness: T-05 passes (no deadlock)

#### Git Checkpoint
```bash
git add . && git commit -m "feat(v2): transitive invalidation + settle barrier + claim grades"
git tag v2-robust-recovery
git branch release/v2
```

#### Rollback Procedure
```bash
# If V2 is unstable:
git checkout release/v1
git tag PRISM_GENAI_HACKATHON_Y2026
# Or soft rollback:
# Config(transitive_invalidation=False, settle_barrier_enabled=False, claim_grades_enabled=False)
```

#### Known Limitations
- No multimodal (no visual scenarios)
- No response frames
- No ASR

---

### VERSION 3: Multimodal Grounding

#### Goal
Add visual scenario support. Visual scenarios carry 1.5x weight in the hidden test set.

#### Prerequisites
- V2 frozen

#### Files to Create
1. `src/prism_rt/kernel/perception.py` — simplified perception scheduler
2. `src/prism_rt/workers/vision.py` — vision analyzer (LLM-based frame analysis)
3. `tests/test_v3.py`
4. `tests/scenarios/multimodal/*.yaml`

#### Files to Modify
- `store/ledgers.py` — add EvidenceStore (observations, questions, conflicts)
- `store/session.py` — expose evidence store
- `kernel/step.py` — integrate perception.decide
- `kernel/reducers.py` — handle video_frame events
- `config.py` — V3 feature flags

#### Multimodal Strategy

**What is REAL:**
- Frame analysis via LLM vision API (Anthropic Claude with image input)
- Conflict detection between user-stated values and vision-perceived values
- CLARIFY action when visual evidence contradicts user statement

**What is MOCKED in tests:**
- Frame images: PNG fixtures in `tests/scenarios/fixtures/`
- Vision responses: ScriptedProvider returns canned claims
- ASR: NOT implemented in V3 — use text transcripts only

**What is SIMPLIFIED:**
- Evidence leases: simple timeout (lease_ms), not frame-count based
- Perception scheduling: analyze first available frame for open question
- No continuous watch mode — single-shot analysis per question

#### Tests (V3) — 5 multimodal scenarios

| # | ID | Scenario |
|---|---|---|
| 1 | N-05 | Visual lookup (frame -> vision -> manual_lookup tool -> FINAL) |
| 2 | M-06 | Conflicting visual/text ("Tab A" spoken, "Tab S9" in frame -> CLARIFY) |
| 3 | M-07 | Multimodal cancellation (vision job aborted on goal replacement) |
| 4 | M-08 | Deictic alignment ("this one" -> frame nearest to cue chunk) |
| 5 | M-03 | Stale frame (lease expiry -> renewal before FINAL) |

#### Git Checkpoint
```bash
git tag v3-multimodal
git branch release/v3
```

---

### VERSION 4: Hardening + Packaging

#### Goal
Kit integration readiness, Docker, adversarial timing, demo video, final submission.

#### Prerequisites
- V3 frozen (or V2 if V3 failed)

#### Files to Create/Modify
- `Dockerfile`
- `README.md` — reproducible setup
- `docs/measurements.md` — results table
- Timing sweep tests in `tests/test_v4.py`
- `adapters/codec.py` — kit wire mapping if kit available

#### Tasks
1. Docker image builds and runs all tests
2. Targeted timing sweeps on I-03, I-14, R-05 (+-10ms offsets)
3. Full regression run across all versions
4. Demo recording (5 min)
5. PPT preparation
6. Final freeze

#### Git Checkpoint
```bash
git tag v4-hardened
git tag PRISM_GENAI_HACKATHON_Y2026
```

---

## 5. Exact Coding Order

### Phase 1: Project Bootstrap (V0, Step 1)
**Why first:** Everything depends on project structure and dependencies.

```
1. Create pyproject.toml with:
   - name = "prism-theme05"
   - python = ">=3.10,<3.13"
   - dependencies = [pydantic>=2.6, PyYAML>=6]
   - dev dependencies = [pytest, pytest-asyncio]
2. Create directory structure (all __init__.py files)
3. Create config/defaults.py, config/lexicons.py, config/templates.py (empty stubs)
```

### Phase 2: Core Primitives (V0, Step 2)
**Why second:** Every other module imports these.

```
4. canonical.py — to_canonical_json, compute_digest, normalize_value, ABSENT
5. ids.py — IdGenerator with deterministic counters
6. errors.py — exception hierarchy
7. config.py — Config dataclass with all feature flags (V2/V3/V4 flags default False)
```

### Phase 3: Domain Models (V0, Step 3)
**Why third:** Store and kernel depend on model types.

```
8. model/types.py — all enums + frozen record dataclasses
9. model/events.py — Envelope + all payload dataclasses
10. model/actions.py — Action + body dataclasses + Snapshot
```

### Phase 4: Storage Layer (V0, Step 4)
**Why fourth:** Kernel depends on stores.

```
11. store/facts.py — FactStore + DependencyIndex
12. store/catalog.py — ToolCatalog (manifest parsing, mutability, quarantine)
13. store/ledgers.py — CallLedger, EffectLedger (V0 subset: no goals/plans/turns yet)
14. store/session.py — SessionStore + StoreTxn
```

### Phase 5: Adapters (V0, Step 5)
**Why fifth:** Kernel uses clock and output writer.

```
15. adapters/clock.py — ClockPort protocol + SteppedClock
16. adapters/codec.py — HarnessCodec (encode/decode)
17. adapters/output_writer.py — OutputWriter protocol + BufferOutputWriter
```

### Phase 6: Kernel Core (V0, Step 6)
**Why sixth:** This is the coordination heart.

```
18. kernel/ordering.py — order_batch, EVENT_CLASS
19. kernel/reducers.py — apply() dispatch: manifest->catalog, tool_result->facts (V0 subset)
20. kernel/invalidation.py — InvalidationEngine (non-transitive: direct dependents only)
21. kernel/commit.py — CommitGate G1-G9
22. kernel/results.py — ResultRouter (all 7 branches)
23. kernel/snapshot.py — SnapshotProjector
24. kernel/emission.py — EmissionGate (single-phase for V0)
25. kernel/step.py — Kernel + 7-phase step()
```

### Phase 7: Observability (V0, Step 7)
**Why seventh:** Kernel LOG phase needs the decision logger.

```
26. observability/decision_log.py — DecisionLogger (JSONL)
```

### Phase 8: Simulator + Checker (V0, Step 8)
**Why eighth:** Tests depend on these.

```
27. sim/harness.py — SimHarness (event replay, mock tools, stepped clock)
28. sim/checker.py — TraceChecker (T1, P1, P3, W1, S3, C1)
```

### Phase 9: V0 Tests (V0, Step 9)
```
29. tests/conftest.py — shared fixtures
30. tests/test_v0.py — 7 scenarios with injected plans
31. Run tests, fix failures
32. V0 FREEZE: git tag v0-skeleton
```

### Phase 10: Turn Management (V1, Step 1)
**Why next:** LLM integration needs turn assembly to produce interpreter inputs.

```
33. kernel/turns.py — TurnManager
34. kernel/detector.py — QuickDetector
35. config/lexicons.py — populate with cue words, correction patterns
```

### Phase 11: Task State Machine (V1, Step 2)
**Why next:** Orchestrates the full dialogue lifecycle.

```
36. kernel/task.py — TaskStateMachine + GoalLifecycle + simplified triage + clarification
37. store/ledgers.py — extend with GoalRegistry, PlanStore, TurnLog
38. store/session.py — expose new stores
```

### Phase 12: LLM Workers (V1, Step 3)
**Why next:** Task machine dispatches workers to do useful work.

```
39. workers/gateway.py — ModelGateway + ScriptedProvider + (optional: Anthropic provider)
40. workers/runner.py — WorkerRunner protocol + AsyncWorkerRunner + ScriptedRunner
41. workers/interpreter.py — build prompt, call gateway, validate output
42. workers/planner.py — build prompt, call gateway, validate output
43. workers/composer.py — build prompt, call gateway, validate output + template fallback
```

### Phase 13: Executor + Responder (V1, Step 4)
**Why next:** These generate the intended actions from plans and worker results.

```
44. kernel/interpret_apply.py — interpretation -> fact mutations
45. kernel/executor.py — PlanExecutor (arg binding, call creation, commit-last)
46. kernel/responder.py — FastResponder + UtteranceBudget
47. config/templates.py — populate with utterance templates
```

### Phase 14: Integration (V1, Step 5)
**Why next:** Wire everything together.

```
48. kernel/step.py — extend to integrate turns, task, executor, responder in decide phase
49. kernel/reducers.py — extend to handle text_chunk, end_of_turn, interruption, worker_result
50. observability/watchdog.py — ScenarioWatchdog (105s)
51. entry.py — Runtime setup + run_scenario
```

### Phase 15: V1 Tests + Stabilization (V1, Step 6)
```
52. tests/scenarios/*.yaml — 15 scenario files
53. tests/test_v1.py — 15 scenarios with ScriptedProvider
54. Run tests, fix failures, tune prompts
55. Replay identity test
56. V1 FREEZE: git tag v1-text-agent, git branch release/v1
```

### Phase 16: V2 Innovations (V2, Steps 1-3)
```
57. kernel/invalidation.py — transitive retraction (provenance graph walk)
58. store/facts.py — derivation_read_set on set_fact for derived facts
59. kernel/commit.py — settle barrier (G10, G11), timer liveness rule
60. kernel/emission.py — two-phase emission, claim grades
61. kernel/executor.py — rebinder (localized correction -> re-bind without replanning)
62. kernel/responder.py — templates keyed by claim grade
63. config.py — V2 flags default True
64. tests/test_v2.py — 12 additional scenarios
65. V2 FREEZE: git tag v2-robust-recovery
```

### Phase 17: V3 Multimodal (if time permits)
```
66. kernel/perception.py — simplified perception scheduler
67. store/ledgers.py — extend with EvidenceStore
68. workers/vision.py — vision analyzer
69. kernel/reducers.py — handle video_frame events
70. tests/test_v3.py — 5 multimodal scenarios
71. V3 FREEZE: git tag v3-multimodal
```

### Phase 18: V4 Hardening + Packaging
```
72. Dockerfile
73. README.md
74. Timing sweep tests
75. Docker build + test run
76. Demo recording
77. V4 FREEZE: git tag v4-hardened, git tag PRISM_GENAI_HACKATHON_Y2026
```

---

## 6. Simulator / Harness

### 6.1 SimHarness Design

The simulator is a deterministic event replay engine. It does NOT simulate real-time — it uses the `SteppedClock` to control time precisely.

```python
class SimHarness:
    def __init__(self, scenario: ScenarioConfig, config: Config):
        self.clock = SteppedClock()
        self.store = SessionStore.new(config, IdGenerator(scenario.seed))
        self.output_buffer = []
        self.mock_tools = MockToolRegistry(scenario.tools)
        self.kernel = Kernel(config, self.store, self.clock, ...)

    def run(self) -> RunResult:
        for event_group in self.scenario.event_groups:
            self.clock.advance_to(event_group.ts_us)
            envelopes = self.build_envelopes(event_group)
            # Add any pending mock tool results due at this time
            envelopes += self.mock_tools.due_results(event_group.ts_us)
            report = self.kernel.step(envelopes)
            self.check_assertions(event_group, report)
        return RunResult(actions=self.output_buffer, trace=self.decision_log)
```

### 6.2 Scenario YAML Format

```yaml
scenario_id: "I-03"
description: "Interrupt during tool execution: destination corrected mid-search"
seed: 42
tools:
  search_flights:
    mutability: read_only
    latency_ms: 500
    response:
      flights:
        - flight_id: "AI-505"
          departure_time: "06:10"
          price: 4200

# Scripted LLM responses for deterministic tests
scripted_responses:
  interpret_1:
    kind: interpret
    input_contains: "Delhi"
    response:
      act: new_goal
      primary_tool: search_flights
      slot_deltas:
        - name: destination
          scope: goal
          op: set
          value: "Delhi"
      commit_intent: false
  interpret_2:
    kind: interpret
    input_contains: "Mumbai"
    response:
      act: slot_update
      slot_deltas:
        - name: destination
          scope: goal
          op: set
          value: "Mumbai"
      commit_intent: false
  plan_1:
    kind: plan
    response:
      status: ok
      steps:
        - local_id: s1
          tool: search_flights
          bindings:
            destination:
              type: fact
              key: "slot.$G.destination"
          after: []

events:
  - ts_ms: 0
    type: manifest
    payload:
      tools: [...]

  - ts_ms: 100
    type: text_chunk
    payload:
      text: "Find flights to Delhi"

  - ts_ms: 200
    type: end_of_turn

  # After interpreter + planner complete, tool call is in flight
  # At ts=400, user interrupts:
  - ts_ms: 400
    type: text_chunk
    payload:
      text: "Actually make it Mumbai"

  - ts_ms: 500
    type: end_of_turn

  # Tool result for Delhi search arrives at 600 (after cancellation)
  - ts_ms: 600
    type: tool_result
    payload:
      call_id: "c-0001"
      status: ok
      result:
        flights: [...]

assertions:
  - type: action_emitted
    action_type: cancel
    target_call_id: "c-0001"
    before_ts_ms: 500

  - type: action_emitted
    action_type: tool_call
    tool: search_flights
    args_contain:
      destination: "Mumbai"

  - type: action_not_emitted
    action_type: final
    text_contains: "Delhi"

  - type: result_disposition
    call_id: "c-0001"
    disposition: completed_after_cancel

  - type: invariant
    name: P3
```

### 6.3 MockToolRegistry

```python
class MockToolRegistry:
    def on_call(self, call: ToolCallBody, ts_us: int) -> None
        # Schedule result at ts_us + latency_us
    def on_cancel(self, call_id: str) -> None
        # Remove scheduled result (or mark completed_after_cancel)
    def due_results(self, now_us: int) -> list:
        # Return all results due at or before now_us
```

### 6.4 Making Races Reproducible

Races become reproducible because:
1. **SteppedClock** only advances when told to — no real-time variance
2. **Batch ordering** is deterministic: `(ts_us, class, seq)`
3. **ScriptedProvider** returns canned responses — no LLM variance
4. **MockToolRegistry** uses exact timestamps — no latency variance
5. **IdGenerator** is seeded — same IDs every run

---

## 7. Tool Subsystem

### 7.1 Tool Classification

| Property | READ Tool | WRITE Tool |
|---|---|---|
| Mutability | `read_only` | `state_changing` |
| Speculation | Allowed | Never |
| Retry on error | Up to 2 retries | 1 retry only if explicitly `retryable` |
| CommitGate | G1-G4, G8 | G1-G9 (all conditions) |
| EffectLedger | Not recorded | PENDING before emission |
| Duplicate check | Fingerprint-based (same tool+args -> reuse result) | Fingerprint + lineage (same goal:step_key -> block) |
| Cancel safety | Cancel anytime; retry or re-issue | Cancel -> reconcile (INFORM truthfully) |
| Unknown outcome | Retry | Block further writes; INFORM |

### 7.2 Tool Call Lifecycle

```
PROPOSED -> [CommitGate passes] -> IN_FLIGHT -> [result arrives] -> CONSUMED
                                             -> [cancelled]      -> CANCEL_REQUESTED -> CANCELLED
                                                                                     -> COMPLETED_AFTER_CANCEL
                                             -> [stale read set]  -> STALE
                                             -> [goal changed]    -> RETAINED
         -> [CommitGate fails]  -> DISCARDED
         -> [fact invalidated]  -> INVALIDATED -> CANCEL_REQUESTED -> ...
```

### 7.3 EffectLedger Rules

```python
# Before emitting a write call:
effect = EffectRecord(
    fingerprint=call.fingerprint,
    lineage=f"{call.goal_id}:{call.step_key}",
    call_id=call.call_id,
    status=EffectStatus.PENDING
)
effect_ledger.create(effect)

# When result arrives:
# if result.status == "ok":
#     effect_ledger.set_status(fp, EffectStatus.CONFIRMED)
# elif result.error.retryable:
#     effect_ledger.set_status(fp, EffectStatus.FAILED)  # allows retry
# else:
#     effect_ledger.set_status(fp, EffectStatus.UNKNOWN)  # blocks further writes

# CommitGate G5 check:
# existing = effect_ledger.by_fingerprint(fingerprint)
# if existing and existing.status not in (FAILED,):
#     return GateDecision(allowed=False, reason="duplicate_effect")
```

---

## 8. Test Strategy

### 8.1 Test Categories (in implementation order)

| Priority | Category | Tool | Version |
|---|---|---|---|
| 1 | Unit: canonical, ids, config | pytest | V0 |
| 2 | Unit: FactStore, DependencyIndex | pytest | V0 |
| 3 | Unit: ToolCatalog manifest parsing | pytest | V0 |
| 4 | Unit: batch ordering | pytest | V0 |
| 5 | Deterministic: 7 injected-plan scenarios | SimHarness | V0 |
| 6 | Deterministic: 15 text agent scenarios | SimHarness + ScriptedProvider | V1 |
| 7 | Interruption: slot correction + cancellation | SimHarness | V1 |
| 8 | Stale result: tool result after slot change | SimHarness | V1 |
| 9 | Cancellation: rapid repeated corrections | SimHarness | V1 |
| 10 | Tool effect: duplicate write prevention | SimHarness | V1 |
| 11 | Goal switch: replace + return | SimHarness | V1 |
| 12 | Transitive: chain correction scenarios | SimHarness | V2 |
| 13 | Settle: timing sweep scenarios | SimHarness | V2 |
| 14 | Multimodal: visual lookup + conflict | SimHarness | V3 |
| 15 | Adversarial: timing offset sweeps | SimHarness | V4 |

### 8.2 Minimum High-Value Tests Per Version

**V0: 7 tests** (all unit/deterministic with injected plans)

**V1: 15 tests** (all use ScriptedProvider + SimHarness)

**V2: 12 tests** (innovation-specific scenarios)

**V3: 5 tests** (multimodal scenarios)

**V4: 3 tests** (timing sweeps on I-03, I-14, R-05)

### 8.3 Test Determinism Rules

1. All tests use `SteppedClock` — no real sleeps
2. All tests use `ScriptedProvider` — no LLM calls
3. All tests use `MockToolRegistry` — deterministic tool latency
4. All tests use seeded `IdGenerator` — same IDs
5. `TraceChecker` runs after every test
6. Replay identity verified: run twice and compare for byte-identical decision logs

---

## 9. Observability

### 9.1 What Is Captured

| Artifact | Format | Purpose |
|---|---|---|
| `decisions.jsonl` | One JSON line per step | Primary debugging + replay source |
| `actions.jsonl` | One JSON line per emitted action | Trace analysis + demo |
| `events.jsonl` | One JSON line per received event | Replay source |

### 9.2 DecisionRecord Structure (per step)

```json
{
  "step_no": 9,
  "now_us": 1452000,
  "batch_size": 1,
  "batch": [{"seq": 11, "type": "text_chunk"}],
  "fact_changes": [{"key": "slot.g2.destination", "old": "Delhi", "new": "Mumbai"}],
  "invalidations": [{"call_id": "c-0007", "reason": "slot.g2.destination changed"}],
  "task_transition": {"from": "executing", "to": "triage"},
  "intended_actions": [{"type": "cancel", "target": "c-0007"}, {"type": "speak", "text": "Mumbai instead..."}],
  "emitted": [{"action_id": "a00011", "type": "cancel"}, {"action_id": "a00012", "type": "speak"}],
  "rejected": [],
  "jobs_dispatched": ["j00032"],
  "jobs_aborted": ["j00030"]
}
```

### 9.3 What Is NOT Captured (CUT)

- Prometheus metrics
- Wall-clock telemetry spans
- Production-grade dashboards
- Structured run reports
- Per-field metrics computation

---

## 10. Performance

### 10.1 Design Targets

| Metric | Target | Measurement Method |
|---|---|---|
| Step latency (phases 1-6) | < 5ms p95 wall time | `time.perf_counter()` around `kernel.step()` (in sim only) |
| Time to first ACK after EOT | < 500ms harness time | Difference between EOT `ts_us` and first SPEAK `ts_us` |
| Time from last result to FINAL | < 2000ms harness time | Difference between last `tool_result` `ts_us` and FINAL `ts_us` |
| Cancel latency | 0 steps (same-step cancellation) | Steps between fact change and CANCEL emission |

### 10.2 Actual Measurements

Measurements will be populated in `docs/measurements.md` after V1+ tests run. No fabricated numbers.

---

## 11. Git / Version Safety

### 11.1 Branch Strategy

```
main ------
  |
  +-- v0-skeleton (tag)
  |     |
  |     +-- feature/v1-text-agent ---- merge to main
  |     |                                |
  |     |                          v1-text-agent (tag)
  |     |                          release/v1 (branch)
  |     |                                |
  |     |     feature/v2-innovations ---- merge to main
  |     |                                     |
  |     |                               v2-robust-recovery (tag)
  |     |                               release/v2 (branch)
  ...
```

### 11.2 Tag Creation Rules

A version tag is created when ALL of the following are true:
1. All listed scenario tests pass with ScriptedProvider
2. All listed scenario tests pass in replay mode (deterministic)
3. `TraceChecker` reports 0 invariant violations on all scenarios
4. No scenario produces a crash, timeout, or malformed output
5. All previous version tests still pass (no regression)

### 11.3 Exact Commands

```bash
# After V0 passes:
git add . && git commit -m "feat(v0): foundation skeleton"
git tag -a v0-skeleton -m "V0: deterministic kernel with injected plans"

# After V1 passes:
git add . && git commit -m "feat(v1): interruptible text agent MVP"
git tag -a v1-text-agent -m "V1: submission-capable text agent"
git branch release/v1

# After V2 passes:
git checkout -b feature/v2-innovations
# ... implement ...
git checkout main && git merge feature/v2-innovations
git tag -a v2-robust-recovery -m "V2: transitive invalidation + settle"
git branch release/v2

# Emergency submission:
git checkout release/v2  # (or v1 if v2 broke)
git tag PRISM_GENAI_HACKATHON_Y2026
git push origin PRISM_GENAI_HACKATHON_Y2026
```

---

## 12. One-Week Execution Plan

### Day 1 (Wed Sep 17): V0 Foundation — 10-12 hours

| Hours | Task |
|---|---|
| 1.0 | Project bootstrap: pyproject.toml, directory structure, all `__init__.py` |
| 1.5 | `canonical.py`, `ids.py`, `errors.py`, `config.py` |
| 1.5 | `model/types.py`, `model/events.py`, `model/actions.py` |
| 2.0 | `store/facts.py` (FactStore + DependencyIndex), `store/catalog.py` |
| 1.0 | `store/ledgers.py` (CallLedger, EffectLedger), `store/session.py` |
| 1.5 | `adapters/clock.py`, `adapters/codec.py`, `adapters/output_writer.py` |
| 1.5 | Buffer: debugging, unit tests for canonical/ids/facts |

### Day 2 (Thu Sep 18): V0 Complete + V1 Start — 10-12 hours

| Hours | Task |
|---|---|
| 1.5 | `kernel/ordering.py`, `kernel/reducers.py` (V0 subset) |
| 1.5 | `kernel/invalidation.py`, `kernel/commit.py`, `kernel/results.py` |
| 1.0 | `kernel/snapshot.py`, `kernel/emission.py` |
| 1.0 | `kernel/step.py` (7-phase step engine) |
| 0.5 | `observability/decision_log.py` |
| 1.5 | `sim/harness.py`, `sim/checker.py` |
| 1.0 | `tests/test_v0.py` — 7 scenarios |
| 0.5 | **V0 FREEZE: `git tag v0-skeleton`** |
| 1.5 | `kernel/turns.py`, `kernel/detector.py`, `config/lexicons.py` |
| 1.0 | Buffer |

### Day 3 (Fri Sep 19): V1 Core — 10-12 hours

| Hours | Task |
|---|---|
| 1.5 | `kernel/task.py` (TaskStateMachine + GoalLifecycle + triage) |
| 0.5 | `store/ledgers.py` extend (GoalRegistry, PlanStore, TurnLog) |
| 1.5 | `workers/gateway.py` (ModelGateway + ScriptedProvider) |
| 1.0 | `workers/runner.py` (WorkerRunner + ScriptedRunner) |
| 2.0 | `workers/interpreter.py`, `workers/planner.py`, `workers/composer.py` |
| 1.5 | `kernel/interpret_apply.py`, `kernel/executor.py` |
| 1.0 | `kernel/responder.py`, `config/templates.py` |
| 1.5 | Buffer: integration debugging |

### Day 4 (Sat Sep 20): V1 Complete — 10-12 hours

| Hours | Task |
|---|---|
| 1.0 | `observability/watchdog.py`, `entry.py` |
| 1.5 | Wire everything in `kernel/step.py` + `kernel/reducers.py` |
| 2.0 | Write 15 scenario YAML files |
| 2.0 | `tests/test_v1.py` — run and fix |
| 2.0 | Prompt tuning + scenario debugging |
| 1.0 | Replay identity verification |
| 0.5 | **V1 FREEZE: `git tag v1-text-agent`, `git branch release/v1`** |

### Day 5 (Sun Sep 21): V2 Innovations — 10-12 hours

| Hours | Task |
|---|---|
| 2.0 | Transitive invalidation (provenance graph walk, fixpoint loop) |
| 1.5 | Settle barrier (G10, G11, timer liveness rule) |
| 1.0 | Claim-typed emission (two-phase, claim grades) |
| 1.0 | Rebinder (localized corrections) |
| 1.0 | Enhanced triage (hold/release with read-set validation) |
| 2.0 | `tests/test_v2.py` — 12 scenarios, fix failures |
| 1.0 | Settle sweep test (I-14 with offsets) |
| 0.5 | **V2 FREEZE: `git tag v2-robust-recovery`, `git branch release/v2`** |

### Day 6 (Mon Sep 22): V3 Multimodal + Stabilization — 10-12 hours

| Hours | Task |
|---|---|
| 2.0 | `kernel/perception.py` (simplified), `workers/vision.py` |
| 1.0 | `store/ledgers.py` extend (EvidenceStore), wire in `kernel/step.py` |
| 1.5 | 5 multimodal test scenarios, fix failures |
| 0.5 | **V3 FREEZE: `git tag v3-multimodal`** |
| 2.0 | Full regression run across V0-V3 |
| 1.5 | Fix any regressions |
| 1.0 | Dockerfile + README |
| 0.5 | Buffer |

### Day 7 (Tue Sep 23): Hardening + Final Packaging — 10-12 hours

| Hours | Task |
|---|---|
| 1.0 | Timing sweep tests (V4) |
| 1.5 | Docker build + test |
| 1.0 | Live LLM validation (optional, if API key available) |
| 2.0 | Demo video recording (5 min) |
| 1.5 | PPT preparation |
| 1.0 | `docs/measurements.md` — fill in results |
| 1.0 | Final regression run |
| 0.5 | **FINAL FREEZE: `git tag v4-hardened`, `git tag PRISM_GENAI_HACKATHON_Y2026`** |
| 0.5 | Push to GitHub, verify README |

### Buffer Days (Wed Sep 24 — Thu Sep 25): Emergency

| Hours | Task |
|---|---|
| If needed | Fix critical bugs, re-record demo, finalize PPT |
| Emergency | `git checkout release/v2` (or v1) and tag for submission |

---

## 13. Stop Conditions

### MUST STOP adding features when:

| Condition | Action | Fallback |
|---|---|---|
| Critical V0 test failing | Debug V0. Do not proceed to V1. | Fix V0 first. |
| V1 regression detected after V2 changes | Revert V2 changes. Submit V1. | `git checkout release/v1` |
| Race condition detected in kernel | Stop all feature work. Fix race. | Tag latest stable version. |
| < 48 hours remaining and V2 not frozen | Stop V2. Stabilize V1. Submit V1. | `git checkout release/v1` |
| < 24 hours remaining and V3 not frozen | Stop V3. Submit V2 (or V1). | `git checkout release/v2` |
| Multimodal component unstable | Disable via Config flag. Submit without multimodal. | `Config(vision_enabled=False)` |
| Harness incompatibility discovered | Adapt codec. Do not restructure kernel. | Wire-format fix only. |
| LLM provider unreliable | Use ScriptedProvider for demo. | Pre-record demo scenarios. |

### Decision Tree

```
Time remaining > 48h?
  YES -> Continue current version
  NO  -> Is current version frozen?
    YES -> Attempt next version (max 24h)
    NO  -> STOP. Freeze current. Stabilize.

Tests passing?
  ALL PASS -> Continue
  CURRENT VERSION FAILING -> Fix (max 4h). If can't fix -> revert to previous.
  PREVIOUS VERSION FAILING -> CRITICAL. Drop all new work. Fix regression.
```

---

## 14. Feature Flag Configuration

```python
@dataclass(frozen=True)
class Config:
    # Clock
    clock_model: str = "B"
    settle_ms: int = 300           # 0 = disabled
    watchdog_timeout_ms: int = 105_000

    # Policies
    max_read_retries: int = 2
    max_write_retries: int = 1
    max_clarification_attempts: int = 2
    max_consecutive_holds: int = 3
    min_speech_gap_ms: int = 400

    # V0 Foundation (always active)
    enforce_commit_gate: bool = True
    enforce_read_set_at_emission: bool = True

    # V2 Innovation Feature Flags
    transitive_invalidation: bool = False    # V2: True
    settle_barrier_enabled: bool = False     # V2: True
    absence_read_sets: bool = False          # V2: True
    claim_grades_enabled: bool = False       # V2: True
    rebinder_enabled: bool = False           # V2: True

    # V3 Multimodal Feature Flags
    vision_enabled: bool = False             # V3: True
    asr_enabled: bool = False                # V3: not implemented

    # Observability
    log_decisions: bool = True
    record_trace: bool = True
```

Every V(N+1) feature is guarded by its flag. Disabling the flag restores V(N) behavior.

---

## 15. Final Coding Checklist

### Version Milestones
- [ ] V0 runnable (events in, actions out, no LLM)
- [ ] V0 frozen (`git tag v0-skeleton`)
- [ ] V1 runnable (text agent handles 15 scenarios)
- [ ] V1 frozen (`git tag v1-text-agent`, `git branch release/v1`)
- [ ] V2 runnable (transitive invalidation + settle)
- [ ] V2 frozen (`git tag v2-robust-recovery`, `git branch release/v2`)
- [ ] V3 runnable (multimodal grounding)
- [ ] V3 frozen (`git tag v3-multimodal`, `git branch release/v3`)
- [ ] V4 hardened + Docker

### Core Properties
- [ ] Deterministic tests (ScriptedProvider + SteppedClock)
- [ ] Interruption recovery (slot correction -> same-step cancel)
- [ ] Cancellation (INVALIDATED -> CANCEL_REQUESTED -> CANCELLED)
- [ ] Stale result rejection (ResultRouter branch 6)
- [ ] Protected writes (CommitGate G1-G9, EffectLedger)
- [ ] Snapshots (SnapshotProjector at emission, never cached)
- [ ] Tool lifecycle (PROPOSED -> IN_FLIGHT -> CONSUMED / CANCELLED / STALE)
- [ ] Goal management (replace, suspend, return)
- [ ] Trace logging (decisions.jsonl, actions.jsonl, events.jsonl)

### Multimodal
- [ ] Vision worker (V3)
- [ ] Conflict detection (V3)
- [ ] Evidence leases (V3, simplified)

### Packaging
- [ ] Docker builds from clean checkout
- [ ] README with reproducible setup instructions
- [ ] Demo scenario runs end-to-end
- [ ] measurements.md populated with real results
- [ ] Final Git tag `PRISM_GENAI_HACKATHON_Y2026`

---

## Appendix A: Fact Key Grammar

| Pattern | Meaning | Writer |
|---|---|---|
| `goal.active` | Active goal_id or null | TaskStateMachine |
| `goal.{gid}.intent` | Intent label | interpret_apply |
| `goal.{gid}.commit_intent` | true/false | interpret_apply |
| `goal.{gid}.plan_rev` | Integer | executor |
| `slot.{gid}.{name}` | Goal-scoped slot | interpret_apply |
| `session.{name}` | Session-scoped fact | interpret_apply |
| `hyp.{turn_id}.{name}` | Hypothesis from QuickDetector | detector via reducers |
| `turn.{turn_id}.prefix` | Prefix digest | TurnManager |
| `result.{call_id}` | Canonical tool result | ResultRouter |
| `derived.{gid}.{name}` | Mapped step output | ResultRouter |
| `catalog.version` | Integer | catalog reducer |

## Appendix B: CommitGate Conditions

| Condition | Rule | Description |
|---|---|---|
| G1 | GATE.TOOL_EXISTS | Tool exists in catalog and is usable |
| G2 | GATE.READ_SET_VALID | Call's read set is valid against current FactStore |
| G3 | GATE.FLOOR_CLOSED | Floor state is `user_turn_closed` |
| G4 | GATE.COMMIT_INTENT | `commit_intent == true` for write tools |
| G5 | GATE.NO_DUPLICATE_EFFECT | No existing effect with same fingerprint (non-FAILED) |
| G6 | GATE.NO_DUPLICATE_LINEAGE | No existing effect with same lineage (non-FAILED) |
| G7 | GATE.NO_UNKNOWN_EFFECT | No UNKNOWN effect for same lineage |
| G8 | GATE.ARGS_VALID | Tool arguments pass JSON Schema validation |
| G9 | GATE.EMISSION_READ_SET | Read set re-validated at emission time |
| G10 | GATE.SETTLE (V2) | Time since EOT >= settle_ms AND no new user event since EOT |
| G11 | GATE.SETTLE_TIMER (V2) | Settle timer scheduled; fires with liveness rule |

## Appendix C: ResultRouter Branches

| Branch | Condition | Action |
|---|---|---|
| 1 | `call_id` not in CallLedger | Ignore, log `RES.UNKNOWN_CALL` |
| 2 | Result already received for `call_id` | Ignore, log `RES.DUPLICATE` |
| 3 | Result fails output schema validation | Treat as error, log `RES.MALFORMED` |
| 4 | Call status is `CANCEL_REQUESTED` | Set `COMPLETED_AFTER_CANCEL`, do not consume |
| 5 | Call `goal_id` != active goal | Set `RETAINED` (available for goal return) |
| 6 | Call read set is invalid | Set `STALE`, do not consume |
| 7 | Valid | Set `CONSUMED`, write result to facts |
