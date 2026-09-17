# Theme 05: Interruptible Real-Time Agents — Implementation Blueprint

Samsung PRISM Generative AI Hackathon, 3rd Edition. Fourth document in the series:

1. *Pre-Architecture Problem Analysis* (cited as **AN**)
2. *System Architecture* (cited as **ARCH**)
3. *Technical Innovation Analysis* (cited as **INNOV**)
4. This blueprint

This document is the build specification for the implementing agent. It incorporates the ARCH design plus the INNOV selected set (M1 prepared reactions, M2 complete validity conditions, M3 settled commits, M4 adversarial schedule exploration) and the defect fixes INNOV L3 and L4.

It contains data models, interfaces, rules, and tests. It contains no implementation.

**Conventions**

- MUST, MUST NOT, SHOULD, MAY carry their usual normative meaning.
- Interface signatures use Python-like notation for precision. They define contracts, not code.
- All internal times are integers in **microseconds** of harness time, named `*_us`.
- "Step" means one kernel step (§3.4).
- **[KIT]** marks anything that depends on the unreleased evaluation kit. Each such item has a default that MUST work in the local simulator and MUST be revisited in Phase 11 (§13).

---

## 1. Repository structure

### 1.1 Tree

```
prism-theme05/
├── README.md
├── pyproject.toml
├── Dockerfile
├── Makefile
├── .importlinter
├── config/
│   ├── policies.default.yaml
│   ├── models.default.yaml
│   ├── lexicons/en.yaml
│   └── templates/en.yaml
├── docs/
│   ├── 01_problem_analysis.md
│   ├── 02_architecture.md
│   ├── 03_innovations.md
│   ├── 04_blueprint.md
│   ├── kit_mapping.md
│   └── measurements.md
├── src/prism_rt/
│   ├── __init__.py
│   ├── entry.py
│   ├── config.py
│   ├── ids.py
│   ├── canonical.py
│   ├── errors.py
│   ├── model/
│   │   ├── __init__.py
│   │   ├── enums.py
│   │   ├── events.py
│   │   ├── actions.py
│   │   ├── facts.py
│   │   ├── tools.py
│   │   ├── plans.py
│   │   ├── calls.py
│   │   ├── effects.py
│   │   ├── evidence.py
│   │   ├── conversation.py
│   │   ├── interpretation.py
│   │   ├── frames.py
│   │   ├── jobs.py
│   │   ├── snapshot.py
│   │   └── trace.py
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── harness_io.py
│   │   ├── codec.py
│   │   ├── ingress.py
│   │   ├── output_writer.py
│   │   └── clock.py
│   ├── store/
│   │   ├── __init__.py
│   │   ├── session.py
│   │   ├── txn.py
│   │   ├── facts.py
│   │   ├── depindex.py
│   │   ├── catalog.py
│   │   ├── goals.py
│   │   ├── calls.py
│   │   ├── effects.py
│   │   ├── evidence.py
│   │   ├── conversation.py
│   │   ├── holds.py
│   │   ├── jobs.py
│   │   └── blobs.py
│   ├── kernel/
│   │   ├── __init__.py
│   │   ├── driver.py
│   │   ├── step.py
│   │   ├── ordering.py
│   │   ├── reducers.py
│   │   ├── invalidation.py
│   │   ├── timers.py
│   │   ├── task.py
│   │   ├── turns.py
│   │   ├── detector.py
│   │   ├── interpret_apply.py
│   │   ├── executor.py
│   │   ├── binder.py
│   │   ├── commit.py
│   │   ├── results.py
│   │   ├── retry.py
│   │   ├── reconcile.py
│   │   ├── clarify.py
│   │   ├── perception.py
│   │   ├── responder.py
│   │   ├── frames.py
│   │   ├── snapshot.py
│   │   ├── scheduler.py
│   │   ├── emission.py
│   │   └── monitors.py
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── runner.py
│   │   ├── gateway.py
│   │   ├── providers/
│   │   │   ├── anthropic_provider.py
│   │   │   ├── openai_compat_provider.py
│   │   │   ├── scripted_provider.py
│   │   │   └── cassette.py
│   │   ├── schemas.py
│   │   ├── interpreter.py
│   │   ├── planner.py
│   │   ├── framer.py
│   │   ├── composer.py
│   │   ├── vision.py
│   │   ├── asr.py
│   │   └── prompts/
│   │       ├── interpret.md
│   │       ├── plan.md
│   │       ├── frame.md
│   │       ├── compose.md
│   │       └── vision.md
│   ├── observability/
│   │   ├── __init__.py
│   │   ├── decision_log.py
│   │   ├── run_writer.py
│   │   ├── metrics.py
│   │   ├── telemetry.py
│   │   └── watchdog.py
│   └── sim/
│       ├── __init__.py
│       ├── scenario.py
│       ├── simulator.py
│       ├── mock_tools.py
│       ├── fake_workers.py
│       ├── checker.py
│       ├── explorer.py
│       ├── minimize.py
│       └── report.py
├── scenarios/
│   ├── normal/
│   ├── interruptions/
│   ├── races/
│   ├── tool_safety/
│   ├── multimodal/
│   ├── timing/
│   └── fixtures/ (frames .png, clips .wav, manifests .json)
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── scenario/
│   └── static/
└── tools/
    ├── run_scenario.py
    ├── run_suite.py
    ├── explore.py
    ├── replay.py
    ├── check_clock_usage.py
    └── report.py
```

### 1.2 Dependency rules (enforced by import-linter in `.importlinter`)

| Package | May import |
|---|---|
| `model` | standard library, `pydantic` |
| `canonical`, `ids`, `errors`, `config` | `model`, standard library, `pydantic`, `yaml` |
| `store` | `model`, `canonical`, `ids`, `errors`, `config` |
| `adapters` | `model`, `canonical`, `ids`, `errors`, `config` |
| `kernel` | `model`, `store`, `adapters.clock` (interface only), `adapters.output_writer` (interface only), `canonical`, `ids`, `errors`, `config`, `observability.decision_log` |
| `workers` | `model`, `canonical`, `errors`, `config` |
| `observability` | `model`, `canonical`, `config` |
| `sim` | everything |
| `entry` | everything except `sim` |

Additional rules:

- `kernel` MUST NOT import `workers`. Jobs are dispatched through the `WorkerRunner` interface injected at construction.
- `workers` MUST NOT import `store` or `kernel`.
- No module outside `adapters/clock.py`, `observability/telemetry.py`, `observability/watchdog.py`, `workers/runner.py` (transport timeouts), and `sim/` may reference `time`, `datetime.now`, `asyncio.sleep`, or `loop.time`. Enforced by `tools/check_clock_usage.py` (test `static/test_clock_usage`).

### 1.3 Third-party dependencies

| Library | Use | Required |
|---|---|---|
| `pydantic>=2.6` | Wire models, worker output validation, scenario files | Yes |
| `jsonschema>=4.21` | Tool parameter and output schema validation (Draft 2020-12 with format checker) | Yes |
| `PyYAML>=6` | Config, templates, scenarios | Yes |
| `httpx>=0.27` | OpenAI-compatible provider | Yes |
| `anthropic` | Anthropic provider | Optional extra `[anthropic]` |
| `Pillow` | PNG decoding, frame metadata | Yes |
| `numpy`, `soundfile` | WAV decoding | Yes |
| `faster-whisper` | ASR | Optional extra `[asr]` |
| `pytest`, `pytest-asyncio`, `hypothesis`, `import-linter` | Tests | Dev |

Python target: 3.11 in Docker; the code MUST also run on 3.10 and 3.12 (spec constraint). Language features requiring 3.11+ (`ExceptionGroup`, `TaskGroup`, `Self`) MUST NOT be used.

### 1.4 File specifications

Each table row lists: purpose and responsibilities; dependencies; public interface; invariants.

#### 1.4.1 Top level

| File | Purpose and responsibilities | Depends on | Public interface | Invariants |
|---|---|---|---|---|
| `entry.py` | Harness entry points. Builds runtime at setup (loads config, models, ASR, warm-up). Runs one scenario per call with a fresh session. | all but `sim` | `setup(config_dir: str, kit_mode: str) -> Runtime`; `async Runtime.run_scenario(io: HarnessIO, meta: ScenarioMeta) -> RunSummary`; `Runtime.close()` | Setup MUST finish in under 300 s. Each `run_scenario` creates a new `SessionStore`; nothing session-derived survives the call |
| `config.py` | Load and validate `policies.default.yaml`, `models.default.yaml`, lexicons, templates; apply overrides. | `model`, `yaml` | `load_config(dir, overrides: dict) -> Config`; `Config.policy(name)`; frozen `Config` | Config is immutable during a scenario |
| `ids.py` | Deterministic per-session ID generation. | — | `IdGen(prefixes).next(kind) -> str` for kinds in §2.1 | Counters are per session; the same input stream yields the same IDs |
| `canonical.py` | Canonical JSON, value normalization, digests, JSONPath subset. | — | `canonical_json(v) -> str`; `digest(v) -> str`; `normalize_value(v, schema: dict|None) -> JSON`; `ABSENT: str`; `jsonpath_get(obj, path) -> JSON`; `jsonpath_parse(path) -> PathAst` | `digest(a) == digest(b)` iff `canonical_json(a) == canonical_json(b)` |
| `errors.py` | Typed exceptions. | — | `StoreMutationOutsideStep`, `ValidationError`, `CodecError`, `MonitorViolation`, `WorkerOutputInvalid` | Kernel converts all exceptions from components into logged degraded outcomes (§8.8) |

#### 1.4.2 `model/`

All models are defined in §2. Files map as follows.

| File | Contents | Invariants |
|---|---|---|
| `enums.py` | Every enum in §2.2 | Enum string values are the lowercase names shown in §2.2 and are used verbatim in JSON |
| `events.py` | `InputEvent`, per-type payloads, `InternalEvent`, `Envelope` | Envelopes are immutable |
| `actions.py` | `Action`, `SpeakBody`, `ToolCallBody`, `CancelBody`, `SnapshotBody` | Actions are immutable after creation |
| `facts.py` | `Fact`, `Provenance`, `ReadSetEntry`, `ReadSet`, `Lease` | `ReadSet` is a frozen, sorted tuple of entries unique by key |
| `tools.py` | `ToolSpec`, `ManifestReport` | — |
| `plans.py` | `Plan`, `Step`, `Binding` variants | `step_key` is stable across rebinds (§2.9) |
| `calls.py` | `CallRecord`, `ToolResultRecord` | Terminal statuses are final |
| `effects.py` | `EffectRecord` | — |
| `evidence.py` | `Observation`, `Analysis`, `PerceptionClaim`, `Question`, `Conflict` | — |
| `conversation.py` | `Turn`, `Chunk`, `Utterance`, `SpeechClaim`, `Clarification`, `Goal` | — |
| `interpretation.py` | `TurnInterpretation`, `SlotDelta`, `Ambiguity` | Exactly the JSON schema in §2.13 |
| `frames.py` | `ResponseFrame`, `FrameBranch`, `Hole` | Exactly §2.15 |
| `jobs.py` | `Job`, `JobView`, `WorkerResult`, `Proposal` variants | Views are plain JSON (no references into the store) |
| `snapshot.py` | `Snapshot`, `SnapshotRecord` | — |
| `trace.py` | `DecisionRecord`, `EventTraceRow`, `ActionTraceRow`, `JobTraceRow` | Exactly §2.17 |

Wire-facing models (events, actions, interpretation, plan proposal, frame, vision output, scenario, trace rows) MUST be pydantic v2 models. Outbound and worker-output models use `extra="forbid"`; inbound harness models use `extra="allow"`. Store records MUST be `@dataclass(frozen=True)` and updated by replacement (`dataclasses.replace`).

#### 1.4.3 `adapters/`

| File | Purpose and responsibilities | Depends on | Public interface | Invariants |
|---|---|---|---|---|
| `harness_io.py` | Abstract interface to the evaluation harness. Implementations: `KitHarnessIO` [KIT], `SimHarnessIO` (in `sim`). | `model` | Protocol `HarnessIO`: `async next_raw() -> dict | None`; `emit_raw(obj: dict) -> None`; `clock_hint() -> ClockHint | None`; `capabilities() -> HarnessCaps` | `emit_raw` MUST NOT await |
| `codec.py` | Map raw harness JSON to `InputEvent` and `Action` to raw JSON. Defines the Internal Canonical Wire format (ICW, §2.3–2.4); a `KitMapping` table converts kit format to ICW [KIT]. Converts timestamps to µs. Splits chunk-with-EOT into two events. | `model`, `canonical` | `decode(raw: dict) -> list[InputEvent]` (raises `CodecError`); `encode(action: Action) -> dict`; `Codec(mapping: KitMapping)` | Every decoded event has `ts_us`; encode of a valid `Action` always produces schema-valid output |
| `ingress.py` | Pull raw events, decode, assign `seq` and `event_id`, post to mailbox; convert decode failures to `MALFORMED_INPUT`. | `codec` | `Ingress(io, codec, mailbox, idgen).run()` (async task) | `seq` strictly increasing; ingress never touches the store |
| `output_writer.py` | Synchronous write of encoded actions to the harness. | `codec`, `harness_io` | `OutputWriter.write(action: Action) -> WriteResult` | Called only from the emission phase; never buffers |
| `clock.py` | `ClockPort` interface and implementations for Models A, B, C, plus `SimClock` and `ReplayClock`. | `model` | See §10.1 | Only this module (plus telemetry, watchdog, runner transport timeouts, and `sim`) may read wall time |

#### 1.4.4 `store/`

| File | Purpose and responsibilities | Depends on | Public interface | Invariants |
|---|---|---|---|---|
| `session.py` | Aggregate of all stores for one scenario. | all store modules | `SessionStore.new(config, idgen) -> SessionStore`; read-only accessors for every sub-store; `begin_txn(step_no) -> StoreTxn` | One instance per scenario |
| `txn.py` | The only mutation API. Records a `ChangeSet`. | all store modules | `StoreTxn` methods listed in §4.3; `commit() -> ChangeSet` | Raises `StoreMutationOutsideStep` if used after `commit()` or from outside the active step |
| `facts.py` | Versioned fact store. | `model.facts`, `canonical` | `get(key) -> Fact | None`; `digest_of(key) -> str` (returns `ABSENT` if missing); `revision -> int`; `is_valid(read_set) -> ValidityResult`; `keys_with_prefix(prefix) -> list[str]` | `ver` values are strictly increasing session revisions |
| `depindex.py` | Reverse index from fact key (present or absent) to dependents. | `model` | `register(dep_id: DepRef, read_set)`; `unregister(dep_id)`; `dependents(keys: Iterable[str]) -> set[DepRef]` | Every live read set is registered exactly once; unregistered on terminal status |
| `catalog.py` | Manifest parser and tool registry (§5.1–5.3). | `model.tools`, `jsonschema`, `canonical`, `config` | `parse_manifest(raw: dict, version: int) -> ManifestReport`; `Catalog.get(name)`, `Catalog.usable_tools()`, `Catalog.version`, `Catalog.tool_digest(name)`; `validate_args(name, args) -> list[str]`; `validate_result(name, result) -> list[str]` | Unknown mutability → `write`; quarantined tools are never returned by `usable_tools()` |
| `goals.py` | Goals, plans, steps. | `model` | `GoalRegistry.get/active_id/all`; `PlanStore.current(goal_id) -> Plan | None`; `PlanStore.get(plan_id)` | At most one ACTIVE goal |
| `calls.py` | Call ledger. | `model.calls` | `get(call_id)`; `in_flight(goal_id=None)`; `by_step(step_key)`; `by_fingerprint(fp)` | Terminal statuses never change |
| `effects.py` | Effect ledger. | `model.effects` | `by_fingerprint(fp)`; `by_lineage(lineage)`; `blocking(fp, lineage) -> BlockInfo` | Every WRITE call has an effect record created before emission |
| `evidence.py` | Observations, analyses, questions, conflicts, leases. | `model.evidence` | `observations(modality)`; `latest_frame()`; `frame_at_or_before(ts_us)`; `frame_seq_now()`; `Question`, `Conflict` accessors | Observations are append-only |
| `conversation.py` | Turns, utterances, clarifications, output ledger. | `model.conversation` | `open_turn()`; `turn(turn_id)`; `last_closed_turn()`; `utterances_since(ts)`; `pending_clarification(goal_id)` | At most one open turn |
| `holds.py` | Proposals held during triage. | `model.jobs` | `held()`; `by_kind(kind)` | Held items always carry a read set |
| `jobs.py` | Job table and reaction slots. | `model.jobs` | `get(job_id)`; `running(kind=None)`; `slot(slot_key)` | A job is in exactly one of `queued`, `running`, `done`, `aborted` |
| `blobs.py` | Session-scoped binary storage for PNG and WAV payloads. | — | `put(bytes, media_type) -> blob_ref`; `get(blob_ref) -> bytes` | Cleared at scenario end |

#### 1.4.5 `kernel/`

| File | Purpose and responsibilities | Depends on | Public interface | Invariants |
|---|---|---|---|---|
| `driver.py` | Runs the kernel against a mailbox. `AsyncDriver` for real harness; `SimDriver` for the simulator (step on demand). | `step`, `clock` | `AsyncDriver(kernel, mailbox, clock).run()`; `SimDriver(kernel).deliver(envelopes) -> StepReport`; `SimDriver.run_until_idle()` | A step is never started while another is running |
| `step.py` | `Kernel` object and the eight-phase step (§3.4). Holds references to every kernel component and the store. | all kernel modules, `store` | `Kernel(config, store, clock, writer, runner, log)`; `Kernel.step(batch: list[Envelope]) -> StepReport`; `Kernel.idle_state() -> IdleState` | `step` is synchronous; phases run in fixed order |
| `ordering.py` | Batch ordering key and tie rules. | `model` | `order_batch(envs) -> list[Envelope]`; `EVENT_CLASS: dict[EventType, int]` | Total order: `(ts_us, class, seq)` |
| `reducers.py` | Apply each envelope to the transaction. Dispatch table by type. | `store.txn`, all kernel services invoked in apply | `apply(env, txn, ctx) -> None` | Reducers emit no actions and dispatch no jobs |
| `invalidation.py` | Transitive retraction and dependent marking (§4.6). | `store` | `invalidate(changes: ChangeSet, txn) -> InvalidationReport` | After it runs, no non-retracted derived fact has invalid provenance |
| `timers.py` | Harness-time timer wheel with liveness rule. | `store`, `clock` | `TimerWheel.schedule(kind, at_us, ref) -> timer_id`; `cancel(timer_id)`; `due(now_us) -> list[TimerFired]`; `next_due_us() -> int | None` | Timers are no-earlier-than deadlines |
| `task.py` | Task state machine and goal operations (§4.2, §8.5). | `store`, `interpret_apply` | `TaskController.on_interpretation(...)`, `.on_interruption(...)`, `.on_user_content(...)`, `.resolve_triage(...)`, `.replace_goal(...)`, `.return_to_goal(...)`, `.abort_goal(...)`, `.decide(ctx)` | Single ACTIVE goal; transitions logged with transition numbers |
| `turns.py` | Turn assembly, prefix digest, inert-tail computation. | `store`, `detector` | `TurnManager.on_chunk`, `.on_eot`, `.tail_since(turn_id, covered_len) -> str`, `.prefix_digest(turn_id)` | `prefix_digest` covers the normalized joined text |
| `detector.py` | QuickDetector (§6.3). | `config` (lexicons), `store.catalog` | `detect(chunk_text, turn_text, ctx) -> ChunkSignals`; `is_inert_tail(tail, ctx) -> bool` | Emits only HIGH-confidence values |
| `interpret_apply.py` | Convert an accepted `TurnInterpretation` into fact mutations (§4.4). | `store`, `canonical` | `apply_interpretation(interp, turn, txn, ctx) -> ApplyReport` | Only schema-known names become slots; unknown names become constraints |
| `executor.py` | PlanExecutor and Rebinder (§5.4, §8.4). | `binder`, `store` | `PlanExecutor.decide(ctx) -> list[IntendedAction]`; `Rebinder.try_rebind(goal_id, changes, txn) -> RebindResult` | Commit-last ordering for writes |
| `binder.py` | ArgBinder: resolve bindings, reference-bound identifiers, absence entries, canonical args, fingerprint (§5.5). | `store`, `canonical` | `bind(step, goal_id, ctx) -> BindResult` | Read set includes every schema parameter |
| `commit.py` | CommitGate (§5.7). | `store`, `timers` | `CommitGate.evaluate(step, bind: BindResult, ctx) -> GateDecision` | Every write passes all conditions; effect record created before emission |
| `results.py` | ResultRouter (§5.8). | `store`, `retry`, `reconcile` | `route(result_env, txn, ctx) -> RouteOutcome` | Consumes only valid, non-cancelled, active-goal results |
| `retry.py` | Retry decisions (§5.9). | `store`, `config` | `RetryPolicy.on_error(call, err, ctx)`; `.on_deadline(call, ctx)` | No retry for write with unknown outcome |
| `reconcile.py` | Reconciliation (§5.10). | `store` | `ReconcilePolicy.decide(ctx) -> list[IntendedAction]` | Never re-issues an equivalent write |
| `clarify.py` | ClarificationManager (§6.6). | `store` | `decide(ctx) -> IntendedAction | None` | One pending clarification per goal |
| `perception.py` | PerceptionScheduler, SlotResolver, alignment, leases, conflicts (§9). | `store` | `on_frame`, `on_audio`, `decide(ctx)`, `on_perception_proposal(p, txn)`, `lease_valid(fact_key, now_us) -> bool` | Frames are analyzed only for open questions or renewals |
| `responder.py` | FastResponder, UtteranceBudget, templates (§6). | `store`, `config` | `decide(ctx) -> list[IntendedAction]` | Every utterance has typed claims; no content-free speech |
| `frames.py` | Response frame validation and evaluation (§2.15, §7.6). | `store`, `canonical` | `validate_frame(frame, tool_spec, sample_result|None) -> list[str]`; `evaluate(frame, result, ctx) -> RenderedUtterance | FrameFailure` | Every data value in rendered text comes from a hole |
| `snapshot.py` | Snapshot projection (§4.7). | `store`, `catalog` | `project(ctx) -> Snapshot`; `changed_since_last(ctx) -> bool` | Projection is computed at emission, never cached |
| `scheduler.py` | JobScheduler with reaction slots and priority classes (§7.8). | `store`, `runner` interface | `request(job_spec)`; `abort(job_id)`; `decide(ctx)`; `dispatch_pending() -> list[Job]` (called in dispatch phase) | Opportunistic jobs never delay critical jobs |
| `emission.py` | Two-phase EmissionGate, truth checks, ordering (§8.6). | `store`, `monitors`, `output_writer`, `codec` | `EmissionGate.emit(intended: list[IntendedAction], txn, ctx) -> EmitReport` | Nothing unvalidated is written |
| `monitors.py` | Online invariant monitors shared with offline checker (§11). | `model` | `ONLINE_MONITORS: list[Monitor]`; `Monitor.check(action, overlay_ctx) -> Violation | None` | Same predicate code used by `sim/checker.py` |

#### 1.4.6 `workers/`

| File | Purpose and responsibilities | Depends on | Public interface | Invariants |
|---|---|---|---|---|
| `runner.py` | Executes `Job`s: LLM jobs as asyncio tasks, CPU jobs in a process pool. Posts `WORKER_RESULT` envelopes to the mailbox on the loop thread. Enforces transport timeouts. | `model.jobs`, `gateway`, job modules | Protocol `WorkerRunner`: `submit(job: Job) -> None`; `abort(job_id) -> None`; `running() -> set[str]` | Results are posted via `loop.call_soon_threadsafe`; a job produces exactly one `WorkerResult` |
| `gateway.py` | ModelGateway: provider selection, structured output enforcement (JSON schema), deterministic settings, retries on transport failure, cassette record/replay. | providers, `schemas` | `ModelGateway.complete_json(kind, prompt, schema, images=None, timeout_s) -> dict`; `mode: live | record | replay | scripted` | Evaluation mode never persists cassettes across scenarios |
| `providers/*.py` | Provider implementations. | `httpx` or `anthropic` | `Provider.complete_json(...)` | — |
| `schemas.py` | JSON Schemas for worker outputs (§2.13–2.16). | `model` | `INTERPRET_SCHEMA`, `PLAN_SCHEMA`, `FRAME_SCHEMA`, `COMPOSE_SCHEMA`, `VISION_SCHEMA` | Match pydantic models exactly |
| `interpreter.py` | Build interpret prompt from `JobView`, call gateway, validate into `TurnInterpretation`. | `gateway`, `schemas` | `run_interpret(view) -> InterpretationProposalBody` | Output validated; invalid → `WorkerOutputInvalid` |
| `planner.py` | Plan generation. | same | `run_plan(view) -> PlanProposalBody` | — |
| `framer.py` | Response frame generation. | same | `run_frame(view) -> FrameProposalBody` | — |
| `composer.py` | Fallback free-text composition. | same | `run_compose(view) -> ComposeProposalBody` | Output includes declared claims |
| `vision.py` | Frame analysis for a question. | `gateway` | `run_vision(view) -> PerceptionProposalBody` | Claims only for question targets |
| `asr.py` | WAV transcription with segment offsets. | `faster_whisper` | `run_asr(view) -> TranscriptProposalBody` | Disfluencies preserved |
| `prompts/*.md` | Prompt templates. Content requirements in §7.2–7.7. | — | — | Prompts reference only view fields |

#### 1.4.7 `observability/`

| File | Purpose and responsibilities | Depends on | Public interface | Invariants |
|---|---|---|---|---|
| `decision_log.py` | Build one `DecisionRecord` per step. | `model.trace` | `DecisionLog.begin(step_no, now_us)`, `.note(section, item)`, `.end() -> DecisionRecord` | Every emitted action links to a rule ID |
| `run_writer.py` | Persist run artifacts: `events.jsonl`, `actions.jsonl`, `decisions.jsonl`, `jobs.jsonl`, `metrics.json`. | `model.trace` | `RunWriter(dir)`; `write_*` | Append-only; flushed at scenario end |
| `metrics.py` | Compute metrics from run artifacts (§10.3). | `model.trace` | `compute_metrics(run_dir, config) -> Metrics` | Uses harness times only |
| `telemetry.py` | Wall-clock telemetry (step durations, worker latencies). | — | `Telemetry.span(name)` | Never read by kernel decisions |
| `watchdog.py` | Scenario wall-clock budget; posts `WATCHDOG` internal event. | `telemetry` | `Watchdog(budget_s, mailbox).start()` | The only wall-clock-driven decision |

#### 1.4.8 `sim/`

| File | Purpose and responsibilities | Public interface |
|---|---|---|
| `scenario.py` | Scenario DSL model and loader (§12.2). | `load_scenario(path) -> Scenario`; `Scenario.variants() -> Iterator[ScenarioInstance]` |
| `simulator.py` | Deterministic discrete-event simulator acting as harness: releases events, runs mock tools, drives `SimDriver`, simulates worker latency (Model A) or zero (Model B), applies cancellation semantics. | `Simulator(instance, runtime_factory, clock_model, seed).run() -> RunResult` |
| `mock_tools.py` | Mock tool behaviors: latency, matching responses, faults, cancellation semantics. | `MockTool.on_call(call, now_us) -> list[ScheduledEvent]`; `.on_cancel(call_id, now_us) -> list[ScheduledEvent]` |
| `fake_workers.py` | Scripted worker outputs with configured latency; implements `WorkerRunner` inside the simulator. | `ScriptedRunner(scripts, latency_model, sim)` |
| `checker.py` | Offline invariant checker over run artifacts; reuses `kernel/monitors.py` predicates plus trace-level rules (§11). | `check_run(run_dir) -> list[Violation]` |
| `explorer.py` | Offset sweeps, randomized priority schedules (PCT), scenario mutation (§12.5). | `explore(scenario, strategy, budget, seed) -> ExploreReport` |
| `minimize.py` | Delta-debugging minimization of failing instances. | `minimize(instance, predicate) -> ScenarioInstance` |
| `report.py` | Aggregate metrics into `docs/measurements.md` tables. | `render_report(results) -> str` |

---

## 2. Data models

### 2.1 Identifier formats

All counters are per session and start at 1.

| Kind | Format | Example |
|---|---|---|
| `event_id` | Harness-provided if present, else `e{seq:06d}` | `e000042` |
| `seq` | Integer, global arrival order (external and internal) | `42` |
| `step_no` | Integer | `17` |
| `turn_id` | `t{n}` | `t3` |
| `goal_id` | `g{n}` | `g2` |
| `plan_id` | `{goal_id}.r{rev}` | `g2.r4` |
| `step_key` | `sk_{digest[:12]}` (§2.9) | `sk_3fa91c0b2e7d` |
| `call_id` | `{call_prefix}{n:04d}`, `call_prefix` policy default `c-` [KIT] | `c-0007` |
| `job_id` | `j{n:05d}` | `j00031` |
| `action_id` | `a{n:05d}` | `a00012` |
| `utt_id` | `u{n:04d}` | `u0009` |
| `obs_id` | `o{n:04d}` | `o0015` |
| `question_id` | `vq{n}` | `vq2` |
| `conflict_id` | `cf{n}` | `cf1` |
| `clarification_id` | `q{n}` | `q1` |
| `frame_id` (response frame) | `rf{n}` | `rf3` |
| `timer_id` | `tm{n}` | `tm20` |
| `effect lineage` | `{goal_id}:{step_key}` | `g2:sk_3fa91c0b2e7d` |

### 2.2 Enumerations

| Enum | Values |
|---|---|
| `EventType` | `manifest`, `interruption`, `text_chunk`, `synthetic_chunk`, `audio_clip`, `video_frame`, `end_of_turn`, `tool_result`, `cancel_ack`, `worker_result`, `timer_fired`, `watchdog`, `emit_rejected`, `malformed_input`, `scenario_start`, `scenario_end` |
| `ActionType` | `speak`, `tool_call`, `cancel`, `clarify`, `final` |
| `UtteranceKind` | `ack`, `progress`, `hold`, `clarify`, `final`, `repair`, `inform` |
| `ClaimGrade` | `understood`, `intended`, `in_progress`, `result`, `effect_done`, `effect_failed`, `effect_unknown`, `question` |
| `FactStatus` | `hypothesis`, `committed`, `confirmed`, `derived`, `retracted` |
| `ProvenanceSource` | `user`, `tool`, `perception`, `carry`, `system` |
| `Confidence` | `high`, `medium`, `low` |
| `Mutability` | `read`, `write` |
| `MutabilitySource` | `declared`, `defaulted`, `upgraded` |
| `ToolStatus` | `usable`, `quarantined` |
| `StepKind` | `read`, `write` |
| `StepStatus` | `pending`, `ready`, `called`, `done`, `failed`, `skipped`, `stale` |
| `CallStatus` | `proposed`, `held`, `blocked`, `discarded`, `in_flight`, `cancel_requested`, `cancelled`, `completed_after_cancel`, `consumed`, `stale`, `retained`, `failed`, `deadline_passed`, `superseded_by_retry` |
| `EffectState` | `pending`, `confirmed`, `failed`, `unknown`, `cancelled_inferred`, `cancelled_confirmed`, `compensated` |
| `GoalStatus` | `active`, `suspended`, `completed`, `failed`, `abandoned` |
| `TaskState` | `idle`, `listening`, `understanding`, `clarifying`, `planning`, `executing`, `responding`, `completed`, `failed`, `triage`, `reconciling` |
| `FloorState` | `user_turn_open`, `user_turn_closed` |
| `InterpretAct` | `new_goal`, `slot_update`, `addition`, `confirm`, `deny`, `answer_clarification`, `progress_question`, `backchannel`, `abort`, `return_to_goal`, `smalltalk`, `unclear` |
| `QuestionMode` | `at_utterance`, `current_state` |
| `JobKind` | `interpret`, `plan`, `frame`, `compose`, `vision`, `asr` |
| `JobPriority` | `critical`, `anticipatory`, `opportunistic` |
| `JobStatus` | `queued`, `running`, `done`, `aborted` |
| `ProposalDisposition` | `accepted`, `held`, `rejected_stale`, `rejected_invalid`, `rejected_aborted` |
| `Modality` | `text`, `audio`, `frame` |

### 2.3 Input events (ICW)

**Envelope.** Every event entering the mailbox has this shape. Harness events are decoded into it by the codec [KIT].

```json
{
  "event_id": "e000012",
  "seq": 12,
  "ts_us": 1450000,
  "type": "text_chunk",
  "class": 2,
  "payload": { }
}
```

`class` is derived from `type` by `ordering.EVENT_CLASS` (§3.3), not supplied by the harness.

**Payloads**

`scenario_start`
```json
{ "scenario_id": "S-hidden-17", "reference_date": "2026-09-18", "locale": "en-IN" }
```
`reference_date` optional [KIT]. If absent, `reference_date_source` policy applies (§6.3).

`manifest`
```json
{
  "manifest_id": "m1",
  "tools": [
    {
      "name": "search_flights",
      "description": "Search available flights between two cities on a date.",
      "mutability": "read_only",
      "parameters": {
        "type": "object",
        "properties": {
          "origin": { "type": "string" },
          "destination": { "type": "string" },
          "date": { "type": "string", "format": "date" },
          "nonstop": { "type": "boolean" }
        },
        "required": ["origin", "destination", "date"]
      },
      "returns": {
        "type": "object",
        "properties": {
          "flights": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "flight_id": { "type": "string" },
                "departure_time": { "type": "string" },
                "price": { "type": "number" }
              }
            }
          }
        }
      }
    }
  ]
}
```
Accepted aliases (codec): `parameters` | `input_schema` | `args_schema`; `returns` | `output_schema`; mutability per §5.2.

`text_chunk`
```json
{ "text": "actually make it Mumbai", "turn_hint": null }
```
If the harness sends a chunk with an end-of-turn flag, the codec emits `text_chunk` then `end_of_turn` with the same `ts_us` and consecutive `seq`.

`end_of_turn`
```json
{ }
```

`interruption`
```json
{ "reason": null }
```

`audio_clip`
```json
{ "clip_id": "clip-3", "media_type": "audio/wav", "duration_us": 2100000, "blob_ref": "b0004" }
```
The codec decodes base64 or file path payloads into `blobs` and replaces them with `blob_ref`. `ts_us` is the clip's capture start [KIT assumption].

`video_frame`
```json
{ "frame_id": "frame-18", "media_type": "image/png", "width": 1280, "height": 720, "blob_ref": "b0009" }
```
`ts_us` is capture time [KIT assumption].

`tool_result`
```json
{
  "call_id": "c-0007",
  "status": "ok",
  "result": { "flights": [ { "flight_id": "AI-505", "departure_time": "06:10", "price": 4200 } ] },
  "error": null
}
```
Error form:
```json
{
  "call_id": "c-0007",
  "status": "error",
  "result": null,
  "error": { "code": "UPSTREAM_TIMEOUT", "message": "…", "retryable": true }
}
```

`cancel_ack` [KIT, optional]
```json
{ "call_id": "c-0007", "outcome": "cancelled" }
```
`outcome` ∈ `cancelled`, `too_late`, `unknown_call`.

`scenario_end`
```json
{ }
```

**Internal event payloads**

`synthetic_chunk`
```json
{ "text": "book it for Wednesday", "source_obs_id": "o0004", "segment_index": 0, "capture_ts_us": 3120000 }
```

`worker_result`
```json
{ "job_id": "j00031", "kind": "interpret", "status": "ok", "proposal": { }, "diagnostics": { "provider": "anthropic", "wall_ms": 412 } }
```

`timer_fired`
```json
{ "timer_id": "tm20", "timer_kind": "call_deadline", "ref": "c-0007", "due_us": 7450000, "liveness_fire": false }
```
`timer_kind` ∈ `call_deadline`, `cancel_inference`, `settle`, `hold`, `min_gap`, `clarify_reprompt`, `compose_deadline`, `asr_dedupe`, `lease_expiry`, `commit_wait_cap`.

`watchdog`
```json
{ "wall_elapsed_s": 105.0 }
```

`emit_rejected`
```json
{ "action_id": "a00012", "reason_code": "SCHEMA_INVALID", "detail": "…" }
```

`malformed_input`
```json
{ "raw_excerpt": "{\"ty…", "error": "CodecError: missing ts" }
```

### 2.4 Output actions (ICW)

The codec maps ICW to the kit's action format [KIT]. ICW is also the format written to `actions.jsonl`.

**Common envelope**
```json
{
  "action_id": "a00012",
  "type": "speak",
  "ts_us": 1452000,
  "trigger_event_id": "e000012",
  "step_no": 9
}
```

`speak`
```json
{
  "action_id": "a00012", "type": "speak", "ts_us": 1452000, "trigger_event_id": "e000012", "step_no": 9,
  "utterance": { "utt_id": "u0004", "kind": "ack", "text": "Mumbai instead. Searching now." },
  "snapshot": { "intent": "search_flights", "slots": { "origin": "Bengaluru", "destination": "Mumbai", "date": "2026-09-19" } }
}
```
`snapshot` is present or `null` according to `snapshot_carrier` (§4.7).

`tool_call`
```json
{
  "action_id": "a00013", "type": "tool_call", "ts_us": 1452000, "trigger_event_id": "e000012", "step_no": 9,
  "call_id": "c-0008", "tool": "search_flights",
  "arguments": { "origin": "Bengaluru", "destination": "Mumbai", "date": "2026-09-19" }
}
```

`cancel`
```json
{ "action_id": "a00011", "type": "cancel", "ts_us": 1450000, "trigger_event_id": "e000011", "step_no": 8, "call_id": "c-0007" }
```

`clarify`
```json
{
  "action_id": "a00020", "type": "clarify", "ts_us": 2100000, "trigger_event_id": "e000019", "step_no": 14,
  "utterance": { "utt_id": "u0007", "kind": "clarify", "text": "Which date should I search for?" },
  "snapshot": { "intent": "search_flights", "slots": { "origin": "Bengaluru", "destination": "Mumbai" } }
}
```

`final`
```json
{
  "action_id": "a00031", "type": "final", "ts_us": 4020000, "trigger_event_id": "e000027", "step_no": 22,
  "utterance": { "utt_id": "u0011", "kind": "final", "text": "I found 3 flights to Mumbai on 19 September. The cheapest leaves at 06:10 for ₹4,200." },
  "snapshot": { "intent": "search_flights", "slots": { "origin": "Bengaluru", "destination": "Mumbai", "date": "2026-09-19" } }
}
```

Internal `Utterance` records additionally carry `claims`, `read_set`, `template_id`, `frame_id`; these are not sent to the harness.

### 2.5 Interruptions

Interruption is represented at three levels.

| Level | Model | Fields |
|---|---|---|
| Harness event | `interruption` envelope (§2.3) | `ts_us`, `event_id` |
| Turn attribute | `Turn.is_interruption = true` | Set when the turn is opened by, or contains, an interruption event, or when it opens while a goal is ACTIVE and task state ∈ {understanding, planning, executing, responding, reconciling} |
| Triage record | `TriageRecord` (in `store/goals.py`) | `triage_id`, `turn_id`, `goal_id`, `entered_step`, `entered_ts_us`, `prior_task_state`, `anchor_event_id`, `resolution` (`resume`, `slot_update`, `addition`, `replace`, `return`, `abort`, `clarify`, `fallback_resume`, `fallback_clarify`), `resolved_step`, `cancelled_call_ids` |

### 2.6 Tool manifest and registry

**`ToolSpec`** (frozen dataclass)

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Unique within catalog |
| `description` | `str` | Empty string if missing |
| `params_schema` | `dict` | Normalized JSON Schema object |
| `param_names` | `tuple[str, ...]` | Sorted |
| `required` | `tuple[str, ...]` | Sorted |
| `param_types` | `dict[str, str]` | `string`, `integer`, `number`, `boolean`, `array`, `object` |
| `param_formats` | `dict[str, str]` | `date`, `date-time`, `time`, etc. |
| `param_enums` | `dict[str, tuple]` | — |
| `identifier_params` | `tuple[str, ...]` | §5.5 |
| `frame_param` | `str | None` | Parameter accepting a frame reference (§9.5) |
| `output_schema` | `dict | None` | — |
| `mutability` | `Mutability` | — |
| `mutability_source` | `MutabilitySource` | — |
| `status` | `ToolStatus` | — |
| `quarantine_reasons` | `tuple[str, ...]` | — |
| `spec_digest` | `str` | Digest of canonical spec |
| `catalog_version` | `int` | Version at which defined |

**`ManifestReport`**

```json
{
  "catalog_version": 2,
  "usable": ["search_flights", "book_flight"],
  "quarantined": [ { "name": "bad_tool", "reasons": ["params_schema_unparseable"] } ],
  "warnings": [ { "name": "book_flight", "warning": "unsupported_keyword:pattern" } ],
  "removed": ["old_tool"],
  "changed": ["search_flights"]
}
```

### 2.7 Tool calls and tool results

**`CallRecord`**

| Field | Type | Notes |
|---|---|---|
| `call_id` | `str` | — |
| `goal_id` | `str` | — |
| `plan_id` | `str` | Plan revision at creation |
| `step_key` | `str` | — |
| `tool` | `str` | — |
| `kind` | `StepKind` | — |
| `args` | `dict` | Canonical |
| `fingerprint` | `str` | `digest({"tool": tool, "args": args})` |
| `lineage` | `str` | `{goal_id}:{step_key}` |
| `read_set` | `ReadSet` | §2.8; includes absence entries |
| `status` | `CallStatus` | — |
| `speculative` | `bool` | — |
| `attempt` | `int` | 1-based |
| `retry_of` | `str | None` | — |
| `created_step` | `int` | — |
| `emitted_ts_us` | `int | None` | — |
| `deadline_ts_us` | `int | None` | — |
| `cancel_ts_us` | `int | None` | — |
| `cancel_reason` | `str | None` | Rule ID |
| `result_ts_us` | `int | None` | — |
| `result_ref` | `str | None` | Fact key `result.{call_id}` if consumed or retained; raw stored in `ToolResultRecord` otherwise |
| `status_history` | `tuple[tuple[int, CallStatus, str], ...]` | `(step_no, status, rule_id)` |

**`ToolResultRecord`**

| Field | Type |
|---|---|
| `call_id` | `str` |
| `event_id` | `str` |
| `ts_us` | `int` |
| `status` | `ok` \| `error` |
| `result` | `JSON | None` |
| `error` | `{code, message, retryable: bool | None} | None` |
| `disposition` | `consumed`, `stale`, `retained`, `completed_after_cancel`, `duplicate_ignored`, `unmatched_ignored`, `malformed`, `superseded_ignored` |
| `validation_errors` | `tuple[str, ...]` |

### 2.8 Facts, read sets, session state

**`Fact`**

| Field | Type | Notes |
|---|---|---|
| `key` | `str` | Grammar below |
| `value` | `JSON` | Canonical |
| `digest` | `str` | `digest(value)` |
| `ver` | `int` | Session revision at last change |
| `status` | `FactStatus` | — |
| `provenance` | `Provenance` | — |
| `confidence` | `Confidence | None` | Perception-derived facts only |
| `lease` | `Lease | None` | Perception-derived CURRENT_STATE facts only |

**`Provenance`**

| Field | Type |
|---|---|
| `source` | `ProvenanceSource` |
| `event_ids` | `tuple[str, ...]` |
| `turn_id` | `str | None` |
| `call_id` | `str | None` |
| `obs_id` | `str | None` |
| `job_id` | `str | None` |
| `capture_ts_us` | `int | None` |
| `derivation_read_set` | `ReadSet | None` — required for `derived` and `perception` sources |

**`ReadSetEntry`** `{ "key": str, "ver": int, "digest": str }`. Absence: `ver = 0`, `digest = "ABSENT"`.

**`ReadSet`** is a tuple of entries sorted by key, unique by key.

**`ValidityResult`** `{ "valid": bool, "failing": [ { "key": str, "expected": str, "actual": str, "reason": "digest_mismatch" | "retracted" | "missing" } ] }`.

**Fact key grammar**

| Pattern | Meaning | Writers |
|---|---|---|
| `goal.active` | Value: active `goal_id` or `null` | TaskController |
| `goal.{gid}.intent` | Intent label | interpret_apply |
| `goal.{gid}.commit_intent` | `true`/`false` | interpret_apply |
| `goal.{gid}.plan_rev` | Integer | executor on plan acceptance |
| `goal.{gid}.constraint.{name}` | Constraint not mapped to any tool parameter | interpret_apply |
| `slot.{gid}.{name}` | Goal-scoped slot; `name` = tool parameter name | interpret_apply, perception, carry-over |
| `session.{name}` | Session-scoped fact; `name` ∈ `session_slot_names` policy or interpreter scope `session` | interpret_apply |
| `hyp.{turn_id}.{name}` | Hypothesis | detector (via reducers), speculative interpretation acceptance |
| `turn.{turn_id}.prefix` | Prefix digest | turns |
| `result.{call_id}` | Canonical result payload | results |
| `derived.{gid}.{name}` | Mapped step output | results |
| `claim.{qid}.{name}` | Accepted perception claim | perception |
| `catalog.version` | Integer | catalog reducer |
| `catalog.tool.{name}` | Tool `spec_digest` | catalog reducer |

**`SessionState`** is the union of stores in `SessionStore`. Its JSON dump (for debugging and the `state_dump` test utility) is:

```json
{
  "revision": 214,
  "step_no": 38,
  "floor": "user_turn_closed",
  "task_state": "executing",
  "active_goal": "g2",
  "facts": { "slot.g2.destination": { "value": "Mumbai", "ver": 201, "status": "committed" } },
  "goals": [ { "goal_id": "g1", "status": "suspended" }, { "goal_id": "g2", "status": "active" } ],
  "plans": { "g2": "g2.r3" },
  "calls": { "c-0008": { "status": "in_flight", "tool": "search_flights" } },
  "effects": { },
  "turns": { "open": null, "last_closed": "t4" },
  "pending_clarification": null,
  "open_questions": [ ],
  "conflicts": [ ],
  "holds": [ ],
  "jobs": { "running": ["j00033"] },
  "timers": [ { "timer_id": "tm22", "kind": "call_deadline", "due_us": 8452000 } ],
  "last_snapshot": { "revision": 208, "step_no": 36 }
}
```

### 2.9 Slots, goals, plans, generations

**Slot** is a fact with key `slot.{gid}.{name}` or `session.{name}`. The slot view used by the snapshot projector:

```json
{ "name": "destination", "scope": "goal", "value": "Mumbai", "status": "committed", "source": "user", "ver": 201, "turn_id": "t4" }
```

**`Goal`**

| Field | Type |
|---|---|
| `goal_id` | `str` |
| `status` | `GoalStatus` |
| `created_turn` | `str` |
| `created_step` | `int` |
| `replaced_by` | `str | None` |
| `suspended_task_state` | `TaskState | None` |
| `primary_tool` | `str | None` |

**`Plan`**

| Field | Type | Notes |
|---|---|---|
| `plan_id` | `str` | `{gid}.r{rev}` |
| `goal_id` | `str` | — |
| `rev` | `int` | Equals `goal.{gid}.plan_rev` after acceptance |
| `steps` | `tuple[Step, ...]` | — |
| `source` | `planner` \| `rebind` \| `speculative_planner` | — |
| `read_set` | `ReadSet` | Planner view read set (for acceptance) |
| `accepted_step` | `int` | — |

**`Step`**

| Field | Type | Notes |
|---|---|---|
| `step_key` | `str` | See below |
| `tool` | `str` | — |
| `kind` | `StepKind` | Copied from catalog at acceptance |
| `bindings` | `dict[str, Binding]` | Parameter name → binding |
| `after` | `tuple[str, ...]` | Step keys |
| `output_map` | `dict[str, str]` | JSONPath → derived name |
| `structure_depends_on` | `tuple[str, ...]` | Slot names whose values influenced tool choice |
| `visual_candidates` | `tuple[str, ...]` | Parameter names plausibly resolvable from frames |
| `status` | `StepStatus` | — |
| `carried_from` | `str | None` | Previous plan_id when carried over |

**`Binding`** (tagged union, JSON form)

```json
{ "type": "fact", "key": "slot.g2.destination" }
{ "type": "literal", "value": "economy", "source": "user" }
{ "type": "step_output", "step_key": "sk_3fa91c0b2e7d", "path": "$.flights[0].flight_id" }
{ "type": "derived", "name": "selected_flight_id" }
{ "type": "frame", "question_id": "vq2" }
```

`literal.source` ∈ `user` (verbatim in a committed turn) or `model`. `model` literals are forbidden for identifier parameters of write tools (§5.5).

**`step_key` derivation.** `step_key = "sk_" + digest({"goal": goal_id, "tool": tool, "template": T})[:12]`, where `T` maps each bound parameter to its binding *shape*: for `fact` the key, for `derived` the name, for `step_output` the upstream `step_key` and path, for `frame` the string `"frame"`, and for `literal` the string `"literal:" + source`. Literal values and fact values are excluded. Rebinding a step after a slot change therefore keeps its `step_key`.

**Generations.** There is no global generation counter. Generations are facts (ARCH §3.4):

- Coarse: `goal.active` is in every goal-bound read set.
- Fine: slot, derived, and claim facts.
- Plan revision: `goal.{gid}.plan_rev` is in every planner proposal read set.

### 2.10 Evidence

**`Observation`**

| Field | Type |
|---|---|
| `obs_id` | `str` |
| `modality` | `audio` \| `frame` |
| `event_id` | `str` |
| `capture_ts_us` | `int` |
| `arrival_step` | `int` |
| `modality_seq` | `int` (1-based per modality) |
| `blob_ref` | `str` |
| `meta` | `{width, height}` or `{duration_us}` |
| `readable` | `bool` |
| `analyses` | `tuple[Analysis, ...]` |

**`Analysis`** `{ job_id, question_id, completed_step, claims: tuple[PerceptionClaim] }`

**`PerceptionClaim`** `{ name, value, confidence: Confidence, obs_id, question_id }`

**`Question`**

| Field | Type |
|---|---|
| `question_id` | `str` |
| `goal_id` | `str | None` |
| `targets` | `tuple[{name, description, schema_type}]` |
| `mode` | `QuestionMode` |
| `anchor_ts_us` | `int` |
| `created_by` | `cue` \| `demand` \| `plan` \| `renewal` |
| `status` | `open` \| `answered` \| `void` |
| `latest_pending_obs` | `str | None` |

**`Lease`**

| Field | Type |
|---|---|
| `mode` | `QuestionMode` |
| `anchor_obs_id` | `str` |
| `granted_frame_seq` | `int` |
| `granted_ts_us` | `int` (capture time of analyzed frame) |
| `max_frames` | `int` (policy `lease_frames`, default 5) |
| `max_us` | `int` (policy `lease_ms` × 1000, default 4 000 000) |
| `renewals` | `int` |

Lease valid at time `now_us` with current frame sequence `F` iff `mode == at_utterance` or (`F - granted_frame_seq < max_frames` and `now_us - granted_ts_us < max_us`).

**`Conflict`**

```json
{
  "conflict_id": "cf1",
  "goal_id": "g3",
  "name": "device_model",
  "candidates": [
    { "value": "Galaxy Tab A", "source": "user", "confidence": null, "ref": "t7" },
    { "value": "Galaxy Tab S9", "source": "perception", "confidence": "high", "ref": "o0012" }
  ],
  "blocks_steps": ["sk_91ab02cd33ef"],
  "status": "open"
}
```

### 2.11 Conversation records

**`Turn`**

| Field | Type |
|---|---|
| `turn_id` | `str` |
| `opened_ts_us` | `int` |
| `closed_ts_us` | `int | None` |
| `chunks` | `tuple[{event_id, ts_us, text, source: text|asr}]` |
| `text` | `str` (chunks joined by one space, whitespace-normalized) |
| `prefix_digest` | `str` |
| `is_interruption` | `bool` |
| `during_goal` | `str | None` |
| `signals` | `tuple[ChunkSignals]` |
| `spec_interpretations` | `tuple[{job_id, covered_len, digest, interpretation}]` |
| `interpretation` | `TurnInterpretation | None` (committed) |
| `promoted` | `exact` \| `inert_tail` \| `none` |

**`Utterance`**

| Field | Type |
|---|---|
| `utt_id` | `str` |
| `kind` | `UtteranceKind` |
| `text` | `str` |
| `claims` | `tuple[SpeechClaim]` |
| `read_set` | `ReadSet` |
| `template_id` | `str | None` |
| `frame_id` | `str | None` |
| `trigger_event_id` | `str` |
| `emitted_ts_us` | `int | None` |
| `snapshot_attached` | `bool` |

**`SpeechClaim`** `{ "grade": ClaimGrade, "ref_kind": "fact" | "call" | "effect" | "question" | "step", "ref": str }`.

**`Clarification`** `{ clarification_id, goal_id, targets: tuple[str], utt_id, asked_ts_us, blocks_steps: tuple[str], status: pending|answered|answered_by_evidence|void, reprompts: int }`.

### 2.12 Jobs and proposals

**`Job`**

| Field | Type |
|---|---|
| `job_id` | `str` |
| `kind` | `JobKind` |
| `priority` | `JobPriority` |
| `speculative` | `bool` |
| `slot_key` | `str | None` (§7.8) |
| `view` | `dict` (JobView JSON) |
| `read_set` | `ReadSet` |
| `input_digest` | `str` |
| `created_step` | `int` |
| `status` | `JobStatus` |

**`WorkerResult` → proposals.** `proposal` is one of:

| Proposal | Body |
|---|---|
| `InterpretationProposal` | `{ "turn_id", "covered_len", "input_digest", "interpretation": TurnInterpretation }` |
| `PlanProposal` | `{ "goal_ref": "g2" | "$G", "based_on_rev": int, "plan": PlanOutput }` |
| `FrameProposal` | `{ "for_call_id", "frame": ResponseFrame }` |
| `ComposeProposal` | `{ "text", "claims": [SpeechClaim] }` |
| `PerceptionProposal` | `{ "question_id", "obs_id", "claims": [PerceptionClaim] }` |
| `TranscriptProposal` | `{ "obs_id", "segments": [ { "text", "offset_us", "end_us" } ], "end_of_utterance": bool }` |

### 2.13 `TurnInterpretation` (Interpreter output JSON schema)

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["act", "slot_deltas", "commit_intent", "visual_reference", "ambiguities", "ack_phrase"],
  "properties": {
    "act": { "enum": ["new_goal","slot_update","addition","confirm","deny","answer_clarification","progress_question","backchannel","abort","return_to_goal","smalltalk","unclear"] },
    "primary_tool": { "type": ["string","null"] },
    "slot_deltas": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["name","scope","op","value"],
        "properties": {
          "name": { "type": "string" },
          "scope": { "enum": ["goal","session","constraint"] },
          "op": { "enum": ["set","clear"] },
          "value": {},
          "evidence": { "enum": ["verbatim","inferred","visual"] }
        }
      }
    },
    "commit_intent": { "type": "boolean" },
    "visual_reference": { "enum": ["none","at_utterance","current_state"] },
    "ambiguities": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["target","candidates"],
        "properties": { "target": { "type": "string" }, "candidates": { "type": "array" } }
      }
    },
    "resume_goal_id": { "type": ["string","null"] },
    "ack_phrase": { "type": ["string","null"], "maxLength": 80 },
    "visual_candidates": { "type": "array", "items": { "type": "string" } }
  }
}
```

`primary_tool` MUST be `null` or a name in the job view's tool list. `commit_intent` is true only when the user explicitly requests a state-changing action or confirms it.

### 2.14 Plan output (Planner output JSON schema)

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["status","steps"],
  "properties": {
    "status": { "enum": ["ok","needs_clarification","no_usable_tool"] },
    "missing": { "type": "array", "items": { "type": "string" } },
    "steps": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["local_id","tool","bindings","after"],
        "properties": {
          "local_id": { "type": "string" },
          "tool": { "type": "string" },
          "bindings": {
            "type": "object",
            "additionalProperties": {
              "oneOf": [
                { "type": "object", "required": ["type","key"], "properties": { "type": { "const": "fact" }, "key": { "type": "string" } }, "additionalProperties": false },
                { "type": "object", "required": ["type","value","source"], "properties": { "type": { "const": "literal" }, "value": {}, "source": { "enum": ["user","model"] } }, "additionalProperties": false },
                { "type": "object", "required": ["type","local_id","path"], "properties": { "type": { "const": "step_output" }, "local_id": { "type": "string" }, "path": { "type": "string" } }, "additionalProperties": false },
                { "type": "object", "required": ["type","name"], "properties": { "type": { "const": "derived" }, "name": { "type": "string" } }, "additionalProperties": false },
                { "type": "object", "required": ["type"], "properties": { "type": { "const": "frame" } }, "additionalProperties": false }
              ]
            }
          },
          "after": { "type": "array", "items": { "type": "string" } },
          "output_map": { "type": "object", "additionalProperties": { "type": "string" } },
          "structure_depends_on": { "type": "array", "items": { "type": "string" } },
          "visual_candidates": { "type": "array", "items": { "type": "string" } }
        }
      }
    }
  }
}
```

The executor converts `local_id` references to `step_key`s at acceptance. Fact keys in planner output use `slot.$G.{name}` where the goal ID is not yet known (speculative planning); the executor substitutes the goal ID at acceptance.

### 2.15 Response frame (Framer output JSON schema)

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["branches","nonempty_test"],
  "properties": {
    "nonempty_test": { "$ref": "#/$defs/hole" },
    "branches": {
      "type": "object",
      "additionalProperties": false,
      "required": ["success_nonempty","success_empty","error"],
      "properties": {
        "success_nonempty": { "$ref": "#/$defs/branch" },
        "success_empty": { "$ref": "#/$defs/branch" },
        "error": { "$ref": "#/$defs/branch" }
      }
    }
  },
  "$defs": {
    "branch": {
      "type": "object",
      "additionalProperties": false,
      "required": ["template","holes","grade"],
      "properties": {
        "template": { "type": "string", "maxLength": 400 },
        "holes": { "type": "object", "additionalProperties": { "$ref": "#/$defs/hole" } },
        "grade": { "enum": ["result","effect_done","effect_failed"] }
      }
    },
    "hole": {
      "type": "object",
      "additionalProperties": false,
      "required": ["op"],
      "properties": {
        "op": { "enum": ["field","count","exists","min_by","max_by","first_k","fact","claim","error_message"] },
        "path": { "type": "string" },
        "by": { "type": "string" },
        "field": { "type": "string" },
        "k": { "type": "integer", "minimum": 1, "maximum": 5 },
        "fields": { "type": "array", "items": { "type": "string" } },
        "key": { "type": "string" },
        "format": { "enum": ["raw","number","currency","date","time","list"] }
      }
    }
  }
}
```

Template placeholders are `{hole_name}`. Frame validation and evaluation rules are in §7.6.

### 2.16 Vision output JSON schema

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["claims"],
  "properties": {
    "claims": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["name","value","confidence"],
        "properties": {
          "name": { "type": "string" },
          "value": {},
          "confidence": { "enum": ["high","medium","low"] }
        }
      }
    }
  }
}
```

Compose output: `{ "text": str, "claims": [ { "grade", "ref_kind", "ref" } ] }` with `additionalProperties: false`.

### 2.17 State snapshots and trace records

**`Snapshot`** (wire) `{ "intent": str | null, "slots": { name: JSON } }` [KIT: field names mapped by codec].

**`SnapshotRecord`** (internal) `{ "revision": int, "step_no": int, "goal_id": str | null, "snapshot": Snapshot, "digest": str, "action_id": str }`.

**`EventTraceRow`** (`events.jsonl`)
```json
{ "seq": 12, "event_id": "e000012", "ts_us": 1450000, "type": "text_chunk", "class": 2, "batch_step": 9, "payload_digest": "…", "payload": { } }
```
Binary payloads are omitted; `blob_ref` retained.

**`ActionTraceRow`** (`actions.jsonl`): the ICW action (§2.4) plus `{ "claims": [...], "read_set_digest": str, "rule_id": str }`.

**`JobTraceRow`** (`jobs.jsonl`)
```json
{ "job_id": "j00031", "kind": "interpret", "priority": "critical", "speculative": true, "slot_key": "eot:t4", "created_step": 8, "input_digest": "…", "view_digest": "…", "status": "done", "disposition": "accepted", "result_step": 10, "provider_wall_ms": 412, "output": { } }
```

**`DecisionRecord`** (`decisions.jsonl`, one per step)
```json
{
  "step_no": 9,
  "now_us": 1452000,
  "wall_step_ms": 1.7,
  "batch": [ { "seq": 11, "event_id": "e000011", "ts_us": 1450000, "type": "text_chunk", "class": 2 } ],
  "fact_changes": [ { "key": "hyp.t4.destination", "old": "ABSENT", "new": "9c1e…", "ver": 199, "status": "hypothesis", "rule": "DET.HIGH_VALUE" } ],
  "retractions": [ { "key": "derived.g2.selected_flight_id", "because": ["slot.g2.destination"] } ],
  "invalidations": [ { "dep": "call:c-0007", "failing": ["slot.g2.destination"], "mark": "invalidated_pending_cancel" } ],
  "transitions": [ { "region": "task", "from": "executing", "to": "triage", "transition": 23 } ],
  "decisions": [ { "component": "commit", "rule": "G11_SETTLE", "subject": "sk_91ab02cd33ef", "outcome": "blocked", "detail": { "due_us": 1750000 } } ],
  "proposals": [ { "job_id": "j00030", "disposition": "rejected_stale", "failing": ["slot.g2.destination"] } ],
  "holds": { "added": [], "released": [], "dropped": [] },
  "intended": [ { "action_id": "a00011", "type": "cancel", "rule": "INV.CANCEL_SAME_STEP" } ],
  "emitted": [ { "action_id": "a00011", "result": "emitted" } ],
  "rejected": [ ],
  "monitor_blocks": [ ],
  "dispatched_jobs": [ "j00032" ],
  "aborted_jobs": [ "j00030" ],
  "timers": { "scheduled": [ ], "cancelled": [ ], "fired": [ ] },
  "degraded": [ ]
}
```

Rule IDs are strings of the form `COMPONENT.RULE` and MUST be listed in `docs/04_blueprint.md` Appendix B (to be maintained by the implementer as rules are added).

---

## 3. Event engine

### 3.1 Mailbox semantics

- One `Mailbox` per session: an unbounded FIFO of `Envelope`s.
- Producers: `Ingress` (external events), `WorkerRunner` (worker results, posted on the event-loop thread), `Watchdog`, and the kernel itself (emit rejections, synthetic chunks derived from transcript proposals).
- `seq` is assigned when an envelope is put into the mailbox, from a single per-session counter owned by the mailbox. Because all puts happen on the event-loop thread, `seq` is strictly increasing and deterministic under replay.
- Internal envelopes receive `ts_us = clock.now_us()` at put time.
- Timers are not put into the mailbox. They are collected at the start of each step by `TimerWheel.due(now_us)` and converted into `timer_fired` envelopes with `seq` assigned at collection.

### 3.2 Drivers

**AsyncDriver (real harness).** Loop:

1. `await clock.wait_for_work(mailbox_nonempty_event, timers.next_due_us())`.
2. Drain all envelopes currently in the mailbox (`get_nowait` until empty).
3. Collect due timers.
4. If the batch is empty, go to 1.
5. `kernel.step(batch)`.
6. After the step, `clock.set_busy(scheduler.has_blocking_work())` (Model B only).
7. Go to 1.

**SimDriver (simulator).** The simulator calls `deliver(envelopes)` for all envelopes it releases at a virtual instant, then `run_until_idle()`, which repeats steps while the mailbox or due timers are non-empty.

### 3.3 Ordering

Every batch is sorted by `(ts_us, class, seq)`.

| Class | Event types |
|---|---|
| 0 | `scenario_start`, `manifest` |
| 1 | `interruption` |
| 2 | `text_chunk`, `synthetic_chunk`, `audio_clip`, `video_frame` |
| 3 | `end_of_turn` |
| 4 | `tool_result`, `cancel_ack` |
| 5 | `worker_result` |
| 6 | `timer_fired`, `watchdog` |
| 7 | `emit_rejected`, `malformed_input`, `scenario_end` |

Rationale (ARCH §5.1): at equal timestamps, events that can invalidate work are applied before events subject to invalidation.

### 3.4 Kernel step phases

`Kernel.step(batch)` runs these phases in order. No phase awaits.

| # | Phase | Operations | Outputs |
|---|---|---|---|
| 1 | ORDER | `order_batch(batch)`; `now = clock.now_us()`; `log.begin(step_no, now)` | Ordered batch |
| 2 | APPLY | `txn = store.begin_txn(step_no)`; for each envelope: `reducers.apply(env, txn, ctx)` | Mutations recorded in `txn` |
| 3 | INVALIDATE | `invalidation.invalidate(txn.changes, txn)` (§4.6); repeat until no new changes (fixpoint, max 8 rounds, else log `INV.FIXPOINT_LIMIT` and treat remaining dependents as invalid) | Marks on calls, jobs, holds, utterances, steps, clarifications, conflicts |
| 4 | DECIDE | Components in this fixed order, each appending `IntendedAction`s and job requests: (1) `cancellation_planner` (§8.2), (2) `task.decide`, (3) `reconcile.decide`, (4) `clarify.decide`, (5) `perception.decide`, (6) `executor.decide` (includes retry and rebind), (7) `commit.decide`, (8) `frames.decide` and completion, (9) `responder.decide`, (10) `scheduler.decide` | Intended actions; job requests; timers |
| 5 | EMIT | `emission.emit(intended, txn, ctx)` (two-phase, §8.6); snapshot attachment inside | Actions written; ledgers updated |
| 6 | COMMIT | `changes = txn.commit()` | Immutable change set for logging |
| 7 | DISPATCH | `scheduler.dispatch_pending()`; `runner.submit(job)` for each; `runner.abort(job_id)` for aborted | Worker tasks started |
| 8 | LOG | `log.end()`; `run_writer.write_decision(record)` | Decision record |

Dispatch happens after emission so that worker views reflect post-emission ledger state (a frame job for a call sees that call as in flight).

Budget: phases 1–6 SHOULD complete within 5 ms wall time at p95 on the evaluation hardware. Telemetry records per-phase durations.

### 3.5 Timestamp handling

- The codec converts harness timestamps to integer microseconds: seconds × 1 000 000 or milliseconds × 1 000, rounded half-even [KIT].
- Harness events with missing timestamps get `ts_us = last_applied_ts_us` and a `TS_MISSING` log entry.
- **Late external events.** If an external envelope arrives with `ts_us < last_applied_ts_us`, it is applied in the next batch at its original `ts_us` for ordering within that batch and for all timestamp comparisons, and logged `LATE_EVENT`. It cannot precede events already applied.
- Actions are stamped with `now_us` of the step (Model A and B), or the harness clock reading (Model C) [KIT].

### 3.6 Virtual-clock integration

The kernel reads time only through `ClockPort.now_us()`, and only at the start of phase 1. All decisions within a step use that single `now`. Timers compare against it. See §10.

### 3.7 Event correlation

| Relation | Mechanism |
|---|---|
| Action → cause | `Action.trigger_event_id`: the envelope in the batch that made the rule fire (the latest-ordered relevant envelope); `rule_id` in trace |
| Result → call | `call_id` |
| Cancellation → call | `call_id`; `cancel_reason` rule ID |
| Fact → cause | `Provenance.event_ids`, `turn_id`, `call_id`, `obs_id`, `job_id` |
| Job → cause | `Job.created_step`, `slot_key`, `input_digest` |
| Proposal → job | `job_id` |
| Snapshot → action | `SnapshotRecord.action_id` |
| Turn → events | `Turn.chunks[].event_id` |
| Audio → turn | Capture window overlap (§9.3) |
| Step → everything | `step_no` in every record |

### 3.8 Simultaneous events

Handled entirely by §3.3 ordering followed by single decide phase. Additional rules:

- Two `text_chunk`s at identical `ts_us`: `seq` order; the turn text concatenates in that order.
- `end_of_turn` and `interruption` at identical `ts_us`: interruption first; it opens a turn (or marks the open turn as interruption) and the end of turn closes it.
- `tool_result` and a correction chunk at identical `ts_us`: chunk first; if it invalidates the call, the result is routed as `completed_after_cancel` or `stale` per §5.8.
- A `timer_fired` whose due time equals an external event's `ts_us`: the external event is applied first (class 6 > 0–4).

### 3.9 Deterministic replay

Recorded per run: `events.jsonl` (with `batch_step`), `jobs.jsonl` (with outputs), and `now_us` per step (in `decisions.jsonl`).

`tools/replay.py` reconstructs batches from `batch_step`, uses `ReplayClock` (returns recorded `now_us` per step), and uses the `cassette` provider keyed by `(kind, input_digest, view_digest)`. Replay MUST reproduce `decisions.jsonl` and `actions.jsonl` byte-for-byte, excluding `wall_step_ms` and `provider_wall_ms` fields.

---

## 4. State engine

### 4.1 Ownership

| State | Owner (only writer) | Readers |
|---|---|---|
| All stores | `StoreTxn`, used only by reducers (phase 2), invalidation (phase 3), decide components (phase 4), emission (phase 5) | Everyone in kernel; workers only via `JobView` copies |
| Worker views | JobScheduler builds them in phase 7 by deep-copying JSON | Workers |
| Run artifacts | RunWriter | Offline tools |

`StoreTxn` holds a flag `active` set by `begin_txn` and cleared by `commit`. Every mutation method checks it and raises `StoreMutationOutsideStep` if false. Test: `unit/store/test_txn_guard`.

### 4.2 Task and floor state transitions

The transition table in ARCH §6.3 is normative, with these amendments from INNOV:

| Amendment | Change |
|---|---|
| T16 (write admission) | Admission is by the extended CommitGate (§5.7), which includes the settle interval |
| T19 (reconciling) | Also entered when a write returns `completed_after_cancel` |
| T23 (enter triage) | Entry also records a `TriageRecord` |
| New T34 | `executing → executing` on lease renewal completion (perception proposal accepted or conflict opened) |
| New T35 | Any state → same state on `LIVENESS_FIRE` (timer processed normally) |

`TaskController` MUST log every transition with its number. Floor: `user_turn_open` on first content or interruption; `user_turn_closed` on `end_of_turn`.

### 4.3 Mutation API (`StoreTxn`)

| Method | Effect |
|---|---|
| `set_fact(key, value, status, provenance, confidence=None, lease=None, rule)` | Normalizes value, computes digest; if digest and status unchanged, no-op (no version bump); otherwise `revision += 1`, `ver = revision` |
| `retract_fact(key, because: list[str], rule)` | Status → `retracted`, `revision += 1` |
| `unretract_fact(key, rule)` | Only via `set_fact` with equal digest (§4.6) |
| `delete_hypotheses(turn_id)` | Removes `hyp.{turn_id}.*` at turn commit |
| `create_goal(...)`, `set_goal_status(...)`, `set_active_goal(goal_id | None)` | `set_active_goal` writes fact `goal.active` |
| `accept_plan(plan)` | Stores plan, writes `goal.{gid}.plan_rev` |
| `set_step_status(plan_id, step_key, status, rule)` | — |
| `create_call(record)`, `set_call_status(call_id, status, rule, **fields)` | Setting a terminal status twice raises |
| `create_effect(record)`, `set_effect_state(fp, state, rule)` | — |
| `open_turn(...)`, `append_chunk(...)`, `close_turn(...)`, `set_turn_interpretation(...)` | — |
| `record_utterance(...)`, `mark_utterance_emitted(...)` | — |
| `add_observation(...)`, `add_analysis(...)`, `open_question(...)`, `set_question_status(...)`, `open_conflict(...)`, `resolve_conflict(...)` | — |
| `hold(proposal)`, `release_hold(id)`, `drop_hold(id)` | — |
| `create_job(job)`, `set_job_status(job_id, status)`, `set_slot(slot_key, job_id)` | — |
| `schedule_timer(...)`, `cancel_timer(...)` | — |
| `set_task_state(state, transition_no)`, `set_floor(state)` | — |
| `record_snapshot(record)` | — |
| `register_deps(dep_ref, read_set)`, `unregister_deps(dep_ref)` | Maintains DependencyIndex |

`ChangeSet` = list of `(key, old_digest, new_digest, old_status, new_status, ver)` for facts, plus a list of non-fact record changes for logging.

### 4.4 Applying an accepted interpretation (`interpret_apply.py`)

Given accepted `TurnInterpretation I` for turn `T`:

1. **Resolve goal context.** If `I.act == new_goal`: create goal (§4.9 for replacement if one is active). If `return_to_goal`: §4.10. If `abort`: §8.5 abort path. Otherwise the active goal (if none and the act requires one, treat as `new_goal`).
2. **Intent.** If `I.primary_tool` is non-null: `set_fact(goal.{gid}.intent, label)` where label follows `intent_label_source` (default `primary_tool`: the tool name) [KIT].
3. **Commit intent.** If `I.commit_intent == true`: set `goal.{gid}.commit_intent = true`. If `I.act ∈ {deny, abort}`: set false. Otherwise unchanged (commit intent persists across slot updates).
4. **Slot deltas.** For each delta:
   - `scope == session` and `name ∈ session_slot_names` policy (default `["user_name", "phone", "email", "device_model", "booking_reference"]`): key `session.{name}`.
   - `scope == goal` and `name` is a parameter of any usable tool: key `slot.{gid}.{name}`.
   - Otherwise: key `goal.{gid}.constraint.{name}`.
   - Normalization schema: the parameter schema of `primary_tool` if it has the name, else of the first usable tool in catalog order that has it, else none.
   - `op == set`: `set_fact(key, value, committed, provenance{source: user, turn_id, event_ids})`.
   - `op == clear`: `retract_fact(key)`.
   - A value that fails schema normalization (e.g., unparseable date) is not applied; it creates an ambiguity for the clarification manager (`target = name`).
5. **Hypotheses.** Delete `hyp.{turn_id}.*` after promotion remapping (§7.5).
6. **Ambiguities.** Recorded on the turn; ClarificationManager reads them.
7. **Clarification answer.** If a clarification is pending and any delta targets its targets: status `answered`.
8. **Visual reference.** If `I.visual_reference != none` or `I.visual_candidates` non-empty: PerceptionScheduler creates or retargets a question (§9.4).
9. Return `ApplyReport` with changed keys, used by invalidation.

### 4.5 Snapshot creation

See §4.7. Snapshots are never stored as authoritative state; `SnapshotRecord`s are history.

### 4.6 Plan invalidation and stale-work prevention

**Transitive retraction (phase 3, first pass).**

1. Let `K` = keys changed in this step (digest or status change).
2. For each `k ∈ K`, find derived and perception facts whose `provenance.derivation_read_set` contains `k` (via DependencyIndex entries of kind `fact:`).
3. For each such fact `d`: if its derivation read set is now invalid, `retract_fact(d)`; add `d` to `K`.
4. Repeat until no new retractions.

**Dependent marking (phase 3, second pass).** For each dependent of any key in `K`:

| Dependent kind (`DepRef` prefix) | Validity re-check | If invalid |
|---|---|---|
| `call:` with status `in_flight` or `deadline_passed` | `facts.is_valid(call.read_set)` | Mark `invalidated_pending_cancel` (transient; cancellation planner consumes it in phase 4) |
| `call:` with status `proposed`, `held`, `blocked` | same | `discarded`; unregister |
| `job:` queued or running | `job.read_set` | `aborted`; unregister; runner abort in phase 7 |
| `hold:` | proposal read set | drop |
| `utt:` recorded but not emitted | utterance read set | drop |
| `utt:` emitted (claims) | read set | Record `repair_candidate` (§6.5) |
| `step:` | bindings' read set | `stale` |
| `frame:` (prepared response frame) | frame read set | drop |
| `clarification:` pending | targets' facts changed by user delta | `void` if the target now has a committed value |
| `conflict:` open | candidate source facts | `void` if a candidate's source retracted or its steps removed |
| `question:` open | goal and target facts | `void` if goal inactive or targets retargeted |

**Stale work cannot mutate current state.** All paths by which asynchronous work reaches the store pass one of these checks:

| Path | Check | Location |
|---|---|---|
| Worker proposal | `job.status != aborted` AND `facts.is_valid(job.read_set)` AND (for interpretation) `input_digest` covers the current turn prefix or passes inert-tail rule | `reducers.apply(worker_result)` |
| Tool result | ResultRouter procedure §5.8 | `results.route` |
| Held proposal release | Read set valid at release | `task.resolve_triage` |
| Prepared frame use | Frame read set and call consumption valid in the same step | `frames.decide` |
| Any emitted action | Two-phase gate re-validates read set | `emission.emit` |
| Derived facts | Transitive retraction before any decide component runs | `invalidation` |

Additionally, `StoreTxn` prevents any mutation outside a step (§4.1), so no worker or callback can write directly.

**Unretraction (early cutoff).** When a re-run upstream call is consumed and writes a derived fact with the same digest as a retracted one, `set_fact` changes status `retracted → derived` and bumps `ver`. Completed downstream results whose read sets list that key with the same digest become valid again (value validity), and PlanExecutor marks their steps `done` without re-running (rule `EXEC.CUTOFF_REUSE`). Calls that were cancelled are never reused (`cancelled`, `completed_after_cancel` are terminal and non-consumable).

### 4.7 Snapshot projection

`project(ctx) -> Snapshot`:

1. If no active goal: `{intent: null, slots: {}}`.
2. `intent` = value of `goal.{gid}.intent` or `null`.
3. For each fact with key `slot.{gid}.*` and status ∈ {committed, confirmed, derived}: include if (source ≠ perception) or (confidence == high and lease valid now). Excluded if an open conflict on that name exists and the value is not user-sourced.
4. For each `session.{name}` fact: include as `name` if `name` is a parameter of any tool in the current plan or of `primary_tool`.
5. Cast each value to the parameter's schema type (`primary_tool` schema first, else first tool with the name).
6. Sort keys.

**Attachment** (`snapshot_carrier` [KIT], default `any_spoken`): in phase 5, for the first emitted action of type `speak`, `clarify`, or `final` in the step, attach the projection if `digest(projection) != last_snapshot.digest` or the action is `final`. Under `final_only`: attach only to `final`. Under `standalone`: codec emits a separate snapshot action (only if the kit defines one).

Monotonicity: `SnapshotRecord.revision` MUST be ≥ the previous record's; violation is logged `SNAP.MONOTONIC` and the projection is recomputed.

### 4.8 Localized slot correction (procedure)

Triggered by an accepted interpretation with `act == slot_update` (or `answer_clarification` changing an existing slot).

1. **Apply** (§4.4): `slot.{gid}.{name}` gets a new digest and `ver`; nothing else changes.
2. **Invalidate** (§4.6): derived facts with that slot in provenance are retracted; dependent in-flight calls marked; steps stale; jobs aborted; frames dropped.
3. **Cancel** (§8.2): cancellations for marked calls in this step.
4. **Rebind** (§8.4, executor): if every stale step's `structure_depends_on` excludes the changed names and all stale steps bind the changed slot via `fact` bindings or depend on retracted derived facts, create plan revision `rev+1` by copying the plan with the same step keys; stale steps set `pending`; `accept_plan`. Otherwise request a PLAN job (critical).
5. **Execute**: ready steps bound to current facts produce new calls. Steps whose read sets are still valid keep their `done` status (no re-run).
6. **Respond**: ACK with claims `understood(slot)` and, if a new call is emitted this step, `in_progress(call)`.
7. **Snapshot**: attached to the ACK because the projection changed.

### 4.9 Complete goal replacement (procedure)

Triggered by accepted `act == new_goal` while a goal is active.

1. `create_goal(g_new)`.
2. Old goal: `set_goal_status(g_old, suspended)`, record `suspended_task_state`.
3. `set_active_goal(g_new)`: `goal.active` digest changes; every goal-bound read set of `g_old` is now invalid.
4. Carry-over (policy `slot_carryover`, default `name_match_user_stated`): for each parameter name `p` of `I.primary_tool`, if `slot.{g_old}.{p}` exists with `provenance.source == user` and no delta in `I` sets `p`, `set_fact(slot.{g_new}.{p}, value, committed, provenance{source: carry, event_ids of original})`. Session facts need no copying.
5. Apply `I`'s deltas and intent to `g_new`.
6. Invalidation: all `g_old` in-flight calls marked (cancelled in this step); `g_old` jobs aborted; `g_old` pending clarification, questions, conflicts void; `g_old` held proposals dropped.
7. Completed results of `g_old`: status unchanged (`consumed` or `retained`) and kept for a possible return.
8. Effects of `g_old` in `pending` or `confirmed`: a reconciliation note `inform_on_replace` is recorded; ReconcilePolicy emits at most one INFORM mentioning them in this or the next response.
9. PLAN job for `g_new` (critical), unless a speculative plan for this turn is promotable (§7.5).
10. ACK for the new goal with snapshot.

### 4.10 Return to a suspended goal

1. `set_goal_status(g_cur, suspended)` if active.
2. `set_active_goal(g_target)`; `set_goal_status(g_target, active)`.
3. Apply deltas.
4. Invalidation runs; results of `g_target` whose read sets are valid again become usable (`retained` → `consumed` via ResultRouter re-check in executor, rule `EXEC.RETAINED_REUSE`).
5. Task state: `planning` (rebind first).

---

## 5. Tool engine

### 5.1 Manifest parser (`store/catalog.py`)

For each tool object in a `manifest` event:

| Check | Failure handling |
|---|---|
| `name` is a non-empty string matching `^[A-Za-z_][A-Za-z0-9_.-]{0,63}$` | Quarantine `invalid_name` |
| Duplicate name within this manifest | Keep first occurrence; quarantine later ones `duplicate_name` |
| Parameter schema present (any alias) | If absent: treat as `{"type":"object","properties":{}}` with warning `no_parameters` |
| Parameter schema is an object schema | Else quarantine `params_schema_not_object` |
| Schema compiles under Draft 2020-12 | Else quarantine `params_schema_unparseable` |
| Unresolvable `$ref` | Quarantine `unresolved_ref` |
| Unsupported keywords (`pattern`, `oneOf`, etc.) | Keep; warning `unsupported_keyword:{kw}`; validation still uses full schema |
| `required` lists names not in `properties` | Quarantine `required_not_in_properties` |
| Output schema present and invalid | Drop output schema; warning `output_schema_invalid` |

Catalog versioning: each `manifest` event produces `catalog.version = previous + 1`. Tools absent from the new manifest are `removed`. Policy `manifest_update_semantics` [KIT]: `replace` (default) or `merge`.

Facts written: `catalog.version`, and `catalog.tool.{name}` = `spec_digest` for each usable tool; removed tools' facts retracted.

### 5.2 Mutability classification

Evaluated in order:

1. Field `mutability` (or `kind`, `effect`, `type_of_operation`) with value (case-insensitive) in `{read_only, read, readonly, query, safe, get, lookup}` → `read`; in `{state_modifying, state_changing, write, mutating, side_effect, side_effects, unsafe, action, command}` → `write`. Source `declared`.
2. Boolean `read_only: true` → `read`; `read_only: false` → `write`. Boolean `side_effects: true` → `write`; `false` → `read`. Source `declared`.
3. Otherwise → `write`, source `defaulted`.
4. **Upgrade heuristic** (policy `mutability_upgrade_heuristic`, default on): if the result is `read` and the tool name begins with one of `create_, book_, cancel_, delete_, remove_, update_, modify_, submit_, pay_, reserve_, send_, set_, add_, assign_, close_, escalate_, schedule_, purchase_, order_, register_, transfer_` → `write`, source `upgraded`. Never downgrade.

[KIT] The exact field name and values are mapped in `docs/kit_mapping.md`.

### 5.3 Tool registry

`Catalog` exposes usable tools sorted by name. The job views for interpreter and planner include: name, description, parameter schema, required list, mutability, output schema (if any). Quarantined tools are excluded from views and from binding. The executor treats a step referencing a non-usable tool as invalid (§5.4).

### 5.4 Call lifecycle

Statuses and transitions (`CallStatus`):

| From | To | Trigger | Rule ID |
|---|---|---|---|
| — | `proposed` | Executor creates call for a ready step | `EXEC.CREATE` |
| `proposed` | `discarded` | Read set invalid before emission | `INV.DISCARD` |
| `proposed` | `held` | Triage hold active | `TRIAGE.HOLD` |
| `held` | `proposed` | Hold released and read set valid | `TRIAGE.RELEASE` |
| `held` | `discarded` | Hold released and read set invalid | `TRIAGE.DROP` |
| `proposed` | `blocked` | CommitGate refuses (write) | `COMMIT.{condition}` |
| `blocked` | `proposed` | Blocking condition clears | `COMMIT.UNBLOCK` |
| `blocked` | `discarded` | Step stale or goal inactive | `INV.DISCARD` |
| `proposed` | `in_flight` | Emitted | `EMIT.OK` |
| `proposed` | `failed` | Emission rejected | `EMIT.REJECT` |
| `in_flight` | `cancel_requested` | Cancellation emitted | `CANCEL.EMIT` |
| `in_flight` | `consumed` / `stale` / `retained` / `failed` | Result (§5.8) | `RESULT.*` |
| `in_flight` | `deadline_passed` | Deadline timer | `DEADLINE.PASS` |
| `deadline_passed` | `consumed` / `stale` / `failed` | Late result, no retry issued | `RESULT.LATE_*` |
| `deadline_passed` | `superseded_by_retry` | Retry issued | `RETRY.ISSUE` |
| `cancel_requested` | `cancelled` | `cancel_ack(cancelled)`, or cancel-inference timer with no result under `cancel_confirmation = no_result_by_deadline` | `CANCEL.CONFIRMED` / `CANCEL.INFERRED` |
| `cancel_requested` | `completed_after_cancel` | Result arrives | `RESULT.AFTER_CANCEL` |
| `superseded_by_retry` | (terminal) | Late result ignored | `RESULT.SUPERSEDED_IGNORED` |

Terminal: `discarded`, `failed`, `cancelled`, `completed_after_cancel`, `consumed`, `stale`, `retained`, `superseded_by_retry`.

`retained` is terminal for routing but can be *reused* when its goal becomes active again with a valid read set (rule `EXEC.RETAINED_REUSE`): the step is marked `done` and derived facts are written from the stored result. No status change.

**Step readiness (executor).** A step is `ready` iff:

1. Its tool is usable in the current catalog version.
2. Every step in `after` is `done` (or `skipped`).
3. `binder.bind(step)` succeeds (all required parameters bound to valid, non-retracted facts; no open conflict on bound names; for `frame` bindings the question has an accepted claim with valid lease).
4. For `write`: every `read` step in the plan that is not downstream of this step (not reachable via `after` edges from it) is `done`, `skipped`, or `failed` (commit-last, INNOV C9).
5. No existing call for this `step_key` is `in_flight`, `cancel_requested`, `proposed`, `held`, or `blocked`.

Readiness is recomputed each decide phase for steps of the current plan of the active goal.

### 5.5 ArgBinder (`kernel/binder.py`)

`bind(step, goal_id, ctx) -> BindResult{ok, args, read_set, fingerprint, errors, blocking}`

1. For each parameter `p` in `tool.param_names`:
   - If `p` has a binding:
     - `fact`: read fact; if missing or retracted and `p` is required → error `unbound_required:{p}`; if present, value normalized to schema; add read-set entry.
     - `derived`: key `derived.{gid}.{name}`; same.
     - `step_output`: key `result.{call_id}` of the upstream step's consumed call, evaluate path; add read-set entry for that result key.
     - `literal`: value normalized; `source == model` and `p ∈ identifier_params` and tool is `write` → error `model_identifier:{p}`.
     - `frame`: question's accepted claim frame reference; add entry for `claim.{qid}.{target}`; lease must be valid, else `blocking: lease_expired`.
   - If `p` has no binding: add absence entry for the key the interpreter would use (`slot.{gid}.{p}`) with `digest = ABSENT` if the fact does not exist, or its current digest if it exists but the planner did not bind it (in which case also error `unbound_available:{p}` → executor requests rebind so the slot is used). Absence entries are subject to `absence_sensitivity` (default `user_sources_only`): perception-sourced facts appearing for an absent key do not invalidate.
2. Add `goal.active`, `catalog.version`, `catalog.tool.{tool}` entries.
3. Add constraint keys `goal.{gid}.constraint.*` only to read sets of response frames and of steps whose `output_map` feeds a selection (derived facts), not to tool calls.
4. Validate `args` with `catalog.validate_args`. Errors → `schema:{message}`.
5. **Identifier parameters** (write tools): `p` is identifier-like if any holds: schema has `enum`; `format ∈ {uuid}`; name matches `(_id|_code|_number|_ref|_reference)$` or equals `id`; or a value for `p` appeared as a field value in any consumed result this session. For each identifier parameter, the bound value MUST come from `step_output`, `derived`, a user-sourced `fact`/`literal` whose value matches verbatim text in a committed turn, or equal a value present in a consumed result of the active goal. Otherwise error `unreferenced_identifier:{p}`.
6. `fingerprint = digest({"tool": tool, "args": canonical_args})`.

### 5.6 `call_id` rules

- Allocated by `IdGen` when the executor creates a `proposed` call.
- Never reused. Retries get new IDs with `retry_of` set.
- A `proposed` call that is discarded or blocked forever consumes an ID; IDs therefore may have gaps in the harness trace. [KIT] If the kit requires gapless IDs, allocation moves to phase 5 (policy `call_id_allocation = at_emission`).

### 5.7 CommitGate (`kernel/commit.py`)

Evaluated in phase 4 for each `ready` write step. All conditions MUST hold; the first failing condition is recorded as the rule ID.

| ID | Condition | Blocking behavior |
|---|---|---|
| G1 | Tool usable in current catalog | Discard step; replan |
| G2 | Step's goal is `goal.active`; `bind` ok; read set valid | Discard or wait |
| G3 | Floor `user_turn_closed`; task state ≠ `triage` | Wait; re-evaluated on change |
| G4 | No bound value from a `hypothesis` fact; no open conflict on bound names; perception-sourced values have `confidence == high` and valid lease | Wait; clarification or renewal requested |
| G5 | `goal.{gid}.commit_intent == true` | Wait; ClarificationManager may ask for confirmation only if policy `confirm_writes_without_intent = ask` (default `wait`) |
| G6 | No effect with this fingerprint in `pending`, `confirmed`, `unknown` | Block permanently for this fingerprint; ReconcilePolicy INFORMs if the user requested it again |
| G7 | No effect with this lineage in `pending` or `unknown`; if lineage has `confirmed` with different fingerprint → route to ReconcilePolicy | Wait or reconcile |
| G8 | `args` schema-valid; no `unreferenced_identifier` | Replan with resolving read |
| G9 | No intended action in this step with same `step_key` or fingerprint | Skip duplicate |
| G10 | No `interpret` job queued or running for any turn with `closed_ts_us ≥` the committing turn's `closed_ts_us` | Wait |
| G11 | Settle: `now_us ≥ committing_turn.closed_ts_us + settle_us` and no user-content or interruption event with `ts_us > committing_turn.closed_ts_us` | Wait; schedule `settle` timer at the due time |
| G12 | Commit-last condition (§5.4 item 4) | Wait |

The *committing turn* is the latest turn whose interpretation set `commit_intent = true` or changed any bound slot of the step.

Policies: `settle_ms` default 300; `commit_wait_cap_ms` default 5000 (if a write waits longer than this on G12 alone, log `COMMIT.WAIT_CAP` and proceed ignoring G12, never ignoring other conditions).

When all pass: `create_effect({fingerprint, lineage, call_ids: [call_id], state: pending})`, set call `proposed` (it becomes `in_flight` at emission), schedule `call_deadline` timer.

### 5.8 Result handling (`kernel/results.py`)

`route(env)` for `tool_result`:

1. **Unmatched.** No call with `call_id` → `ToolResultRecord.disposition = unmatched_ignored`; log. Stop.
2. **Duplicate.** Call already has a result record with disposition in {consumed, stale, retained, completed_after_cancel, failed} → `duplicate_ignored`. Stop.
3. **Superseded.** Call status `superseded_by_retry` → `superseded_ignored`. Stop.
4. **Validate.** If `status == ok` and output schema exists and validation fails → treat as error `{code: MALFORMED_RESULT, retryable: true}` (`malformed`). If `status` is neither `ok` nor `error` → same.
5. **Cancelled.** Call status `cancel_requested` → call `completed_after_cancel`. If `kind == write` and `status == ok`: effect `confirmed`; record reconcile item `superseded_write_confirmed`. If `status == error` and write: effect `failed`. Store result record; do not write result facts. Stop.
6. **Error result.** `status == error`: call `failed`; if write: effect `failed`. RetryPolicy decides (§5.9). Stop.
7. **Goal not active.** Call's `goal_id ≠ goal.active` → call `retained`; write `result.{call_id}` fact with provenance (so reuse is possible); if write: effect `confirmed` and reconcile item `inactive_goal_write_confirmed`. Stop.
8. **Read set invalid.** → call `stale`; store record; if write: effect `confirmed`, reconcile item `stale_write_confirmed`. Stop.
9. **Consume.** Call `consumed`. `set_fact(result.{call_id}, result, derived, provenance{source: tool, call_id, derivation_read_set: call.read_set})`. For each `output_map` entry: `set_fact(derived.{gid}.{name}, jsonpath_get(result, path), derived, same provenance)`. Step `done`. If write: effect `confirmed`. Cancel the call's deadline timer.

`cancel_ack` handling: `cancelled` → call `cancelled`, effect (if write) `cancelled_confirmed`. `too_late` → no change (await result). `unknown_call` → log.

### 5.9 Retry behavior (`kernel/retry.py`)

| Situation | Read tool | Write tool |
|---|---|---|
| Error with `retryable == false` | No retry; step `failed` | No retry; step `failed` |
| Error with `retryable == true` | Retry if `attempt ≤ read_retries` (default 2) | Retry once if `write_retry == retryable_errors_once` (default) and effect state `failed` |
| Error with `retryable` absent | Retry if `attempt ≤ read_retries` | No retry |
| `MALFORMED_RESULT` | As retryable | Effect `unknown` (malformed payload does not prove failure); no retry; reconcile |
| Deadline passed | Retry if attempts remain (call → `superseded_by_retry`) | Effect `unknown`; no retry; reconcile |

A retry creates a new call for the same step with a new `call_id`, `attempt + 1`, `retry_of`, and re-bound arguments (rebinding uses current facts). A write retry passes the full CommitGate again; G6 passes only because the previous effect is `failed`.

Deadlines: `deadline_ms` per mutability (read 6000, write 12000), overridable per tool in `policies.default.yaml` under `tool_deadlines` [KIT].

When retries are exhausted: step `failed`; TaskController transition 18 (replan with failure context, max `replans_per_goal` = 2) or 20 (fail with INFORM).

### 5.10 Reconciliation (`kernel/reconcile.py`)

Reconcile items are created by ResultRouter, RetryPolicy, and goal replacement. `decide(ctx)` handles each open item once:

| Item | Default action | Policy |
|---|---|---|
| `superseded_write_confirmed` (write completed after cancel, user wanted different args) | If the plan-time catalog has a tool whose name starts with `modify_`/`update_`/`change_` or `cancel_`/`delete_` and whose parameters include an identifier present in the confirmed result: request PLAN job with reconcile context (commit intent is implied by the user's correction). Else INFORM ("The {entity} booking had already gone through.") + CLARIFY ("Should I also {action} the {new entity}?") | `reconcile_superseded`: `compensate_if_tool_else_ask` (default) or `always_ask` |
| `stale_write_confirmed` | Same as above | Same |
| `inactive_goal_write_confirmed` | INFORM once in the next response for any goal | — |
| `write_unknown` | INFORM ("I haven't received confirmation for {action}.") + CLARIFY ("Should I try again?"); effect stays `unknown`; G6 blocks re-issue until the user confirms, then effect set `failed` by rule `RECONCILE.USER_RETRY` and gate re-evaluated | — |
| `abort_with_effects` | INFORM listing confirmed and pending effects | — |

### 5.11 Idempotency and duplicate prevention summary

| Mechanism | Prevents |
|---|---|
| Effect record before emission (G-pass) | A second write with the same fingerprint in the same step or later |
| G6 fingerprint blocking over `pending`, `confirmed`, `unknown` | Repeat requests, retries after unknown outcome |
| G7 lineage blocking | Two different writes for the same logical action (double booking) |
| G9 intra-step dedupe | Two decide components producing the same call |
| Reference-bound identifiers (§5.5) | Paraphrase-driven fingerprint mismatch; fabricated identifiers |
| Write retry only on known failure | Duplicates after timeout |
| Terminal status immutability | Double processing of results |
| Duplicate result detection | Double continuation |

### 5.12 Unseen and malformed tools

- No module may contain tool names from fixtures. Enforced by `tests/static/test_no_tool_names` (greps `src/` for names in `scenarios/fixtures/manifests/*.json`).
- Unseen tools are planned from their views; binding and validation rely only on schemas.
- Malformed tools follow §5.1; a plan step referencing a quarantined tool is rejected at plan acceptance (`PLAN.QUARANTINED_TOOL`); if no usable alternative exists, transition 13 with INFORM naming the capability, not the tool.

---

## 6. Fast path

### 6.1 Components

`detector.py` (QuickDetector), `turns.py` (TurnManager), `responder.py` (FastResponder and UtteranceBudget), `clarify.py`, and the cancellation planner in `step.py` phase 4.1. All are synchronous kernel code.

### 6.2 Inputs and outputs

| Input | Source |
|---|---|
| Ordered batch envelopes | Step |
| Facts, ledgers, turns, goals, task and floor state | Store |
| Templates, lexicons | Config |
| `now_us` | Clock via step |

| Output | Form |
|---|---|
| Hypothesis facts | `hyp.{turn}.{name}` |
| Intended `cancel` actions | Via cancellation planner |
| Intended `speak` (ack, progress, hold, repair, inform) | `IntendedAction` with `Utterance` |
| Intended `clarify` | `IntendedAction` |
| Job requests (speculative interpretation, perception cue) | Scheduler |

**Timing expectations.** Detector ≤ 0.5 ms per chunk; responder decide ≤ 1 ms; total fast-path contribution inside the 5 ms step budget. Any ACK that can be emitted is emitted in the same step as its trigger; there are no fast-path timers except `hold` and `min_gap`.

### 6.3 QuickDetector specification

Inputs: chunk text, turn text, catalog (usable tools relevant to active goal: tools of current plan plus `primary_tool`; if no active goal, all usable tools), current slot facts, session value index (for each parameter name, the set of canonical values seen in committed facts or consumed results this session), reference date (from `scenario_start.reference_date`, else `reference_date_source` policy: `none` by default [KIT]).

Tokenization: lowercase; split on whitespace and punctuation except inside time (`6:10`), date (`2026-09-19`, `19/09`), and alphanumeric codes (`AI-505`).

Extractors (only HIGH results are emitted):

| Extractor | HIGH when |
|---|---|
| Enum | Token n-gram (n ≤ 4) equals, case-insensitively, one enum value of exactly one relevant parameter |
| Absolute date | Parses as ISO, `DD/MM/YYYY`, `D Month [YYYY]`, `Month D [YYYY]`; exactly one relevant `date`-format parameter |
| Relative date | Only if a reference date is known; words `today`, `tomorrow`, `day after tomorrow`, weekday names (next occurrence strictly after reference date); exactly one relevant date parameter |
| Time | `H:MM`, `H am/pm`, `HH:MM`; exactly one relevant `time` or `date-time` parameter |
| Number | Numeral adjacent (±2 tokens) to a noun that appears in exactly one relevant integer/number parameter's name or description |
| Session entity | N-gram equals a value in the session value index for exactly one parameter name |
| Cues | Lexicon match (config `lexicons/en.yaml`): `correction`, `hold`, `abort`, `negation`, `backchannel`, `visual`, `politeness`, `filler` |

Output:

```json
{
  "values": [ { "name": "destination", "value": "Mumbai", "confidence": "high", "extractor": "session_entity" } ],
  "cues": ["correction"],
  "backchannel_only": false,
  "visual_reference": false,
  "negated_values": [ ]
}
```

`negated_values`: a value within 3 tokens after a `negation` cue ("not Mumbai") is placed here and MUST NOT create a hypothesis.

`backchannel_only`: every token of the turn so far is in `backchannel ∪ filler ∪ politeness`.

`is_inert_tail(tail, ctx)`: true iff every token is in `politeness ∪ filler` lexicons, no extractor produces any value (any confidence), no cue other than `politeness`/`filler`, and no clarification is pending for the active goal.

Reducer effect: for each HIGH value, `set_fact(hyp.{turn}.{name}, value, hypothesis, provenance{source: user, turn_id, event_ids})`.

### 6.4 Acknowledgment logic

`responder.decide` emits at most one ACK per closed user turn (UtteranceBudget `acked_turns`).

**ACK is emitted in a step when all hold:**

1. Floor is `user_turn_closed`.
2. The latest closed turn `T` has not been ACKed.
3. One of:
   - (a) `T`'s committed interpretation was accepted in this step (promotion or worker result) and its act ∈ {new_goal, slot_update, addition, confirm, answer_clarification, return_to_goal}; or
   - (b) `T` is closed in this step, no interpretation is available, and QuickDetector produced at least one HIGH value for `T` and the goal's intent is known (active goal intent, or a speculative interpretation of a prefix of `T` with act `new_goal`) — "detector ACK".
4. No FINAL or CLARIFY is intended in this step for the same goal.

**ACK content.**

| Case | Claims | Template family |
|---|---|---|
| New goal, call emitted this step | `understood` for each committed slot mentioned; `in_progress` for the call | `ack.new.in_progress` |
| New goal, planning pending | `understood` slots; `intended` for the primary tool | `ack.new.intended` |
| Slot update, new call this step | `understood` changed slot; `in_progress` call | `ack.update.in_progress` |
| Slot update affecting a waiting write | `understood` changed slot; `intended` write | `ack.update.intended` |
| Confirm of a write | `intended` write (because of settle interval) | `ack.confirm.intended` |
| Detector ACK (3b) | `understood` for HIGH hypothesis values only | `ack.detector` |

`ack_phrase` from the interpretation MAY replace the template text if it passes truth checks (§8.6.4) and contains every value named in the `understood` claims; otherwise templates are used.

Examples (`templates/en.yaml`, deterministic variant choice by `digest(session_id, utt_counter) mod n`):

```yaml
ack.new.in_progress:
  - "Looking for {primary_phrase} {slot_summary} now."
  - "Checking {primary_phrase} {slot_summary}."
ack.update.in_progress:
  - "{changed_value} instead. Updating the search."
  - "Got it, {changed_value}. Checking again."
ack.update.intended:
  - "{changed_value} instead. I'll use that."
ack.confirm.intended:
  - "I'll {action_phrase} {entity_phrase}."
hold.in_progress:
  - "Still waiting on the {tool_phrase}."
progress.result:
  - "{result_summary}. {next_phrase}"
repair.value_changed:
  - "Correction: it's {new_value}, not {old_value}."
inform.write_unknown:
  - "I haven't received confirmation for {action_phrase} yet."
```

`primary_phrase`, `tool_phrase`, `action_phrase` derive from the tool name: split on `_`, verb-first (`search_flights` → "flights" / "search for flights"). A policy map `tool_phrases` MAY override [KIT].

### 6.5 Progress narration, holds, repairs, interruption response

**PROGRESS** is emitted when, in this step, a call is consumed and all hold: another required step of the plan becomes ready or remains in flight (the task is not complete); `min_gap` since the last non-final utterance has elapsed; and the result changes user-visible knowledge (policy: result non-empty, or empty result that causes a replan). Claims: `result` for the consumed call; `in_progress` for the next emitted call (if emitted this step). If the plan completes in this step, PROGRESS is suppressed in favor of FINAL.

**HOLD** is emitted when the `hold` timer fires (armed at each ledger transition for the active goal while `executing` and floor closed, due at `last_transition_us + hold_after_ms`, default 2500 ms) and: at least one call of the active goal is `in_flight`; fewer than `max_holds_per_step` (1) HOLDs for that call and fewer than `max_holds_per_goal` (2). Text names the in-flight step. Claim: `in_progress` for that call. There is no content-free filler anywhere in the system.

**REPAIR** is emitted when an emitted utterance of the active goal has a `repair_candidate` mark (§4.6) and the change was not user-initiated in the same or previous step (user-initiated changes are acknowledged by ACK instead). Example source: perception supersession. Claims: `understood` or `result` for the new value.

**Interruption response.**

- In the step containing an `interruption` or new user content during an active goal: no speech. Cancellations only (§8.2).
- After triage resolution:
  - `backchannel`: release valid held speech; no new ACK.
  - `progress_question`: PROGRESS built from the ledger (in-flight calls → `in_progress`; consumed results → `result`).
  - `slot_update`, `addition`, `new_goal`, `return_to_goal`: ACK per §6.4.
  - `abort`: INFORM ("Okay, I've stopped.") plus effects if any.
  - `unclear`: CLARIFY.

### 6.6 Clarification logic (`kernel/clarify.py`)

Evaluated in decide order 4. At most one pending clarification per goal.

**Ask when** the active goal has no pending clarification, floor is closed, and any of the following holds (first match determines the question):

| Priority | Trigger | Question target |
|---|---|---|
| 1 | Open conflict on a name bound by a ready or blocked step, both candidates ≥ medium | That name, both values |
| 2 | Plan status `needs_clarification` or binder error `unbound_required:{p}` for a step with no other resolution path (no visual candidate with frames available, no derived source) | `p` |
| 3 | Interpretation ambiguity whose target is a parameter of a planned tool or the intent | Target |
| 4 | Perception target needed by a step with only `low` confidence claim | Target |
| 5 | Reconcile item requiring user decision | Item |
| 6 | Frame gap before a write needing CURRENT_STATE evidence (§9.4) | Request fresh view |

**Do not ask when** a committed fact for the target exists from a turn closed after the ambiguity arose; a hypothesis for the target exists in an open turn (wait for its close); or the target affects only constraint facts not used by any step.

**Question construction.** Template `clarify.{trigger}` with target description from schema; for conflicts, both values; for enums with ≤ 4 values, list them.

**Blocking.** `blocks_steps` = steps whose bindings reference the target. Other steps proceed.

**Answer binding.** An interpretation with act `answer_clarification` or any delta on the target sets status `answered`. Evidence resolving the target sets `answered_by_evidence`.

**Re-prompt.** Timer `clarify_reprompt` at `asked_ts + clarify_reprompt_ms` (6000). On fire, if still pending, no user content since asked, floor closed, and `reprompts < 1` (`clarification_reprompt = once`): emit a shorter re-prompt. `unanswered_clarification = wait` never proceeds with writes.

### 6.7 Truthfulness rules

Every fast-path utterance MUST satisfy §8.6.4 checks. Additionally:

- No utterance while floor is `user_turn_open` (policy `speak_during_open_turn = false`).
- No utterance references a non-active goal except INFORM about reconciliation or abort.
- `understood` claims referencing hypothesis facts are allowed only in detector ACKs, and only for HIGH values of the just-closed turn.
- `in_progress` requires the call to be `in_flight` after the batch (overlay, §8.6).
- `intended` requires a step of the active goal in the current plan with that tool whose status ∈ {pending, ready, called} or a `blocked` call.
- `result` requires a `consumed` call (or reused `retained`) of the active goal.
- `effect_done` requires a `confirmed` effect; `effect_failed` a `failed` effect; `effect_unknown` an `unknown` effect.

---

## 7. Slow path

### 7.1 Worker runner and concurrency

`WorkerRunner` executes jobs submitted in phase 7.

| Job kind | Execution | Max concurrent | Priority rules |
|---|---|---|---|
| `interpret` | asyncio task | 2 total; 1 per turn | Critical if turn closed or triage; else anticipatory |
| `plan` | asyncio task | 1 per goal; 2 total | Critical if goal active and not speculative; anticipatory if speculative |
| `frame` | asyncio task | 1 per goal | Anticipatory |
| `compose` | asyncio task | 1 per goal | Critical |
| `vision` | process pool | `vision_concurrency` (1) | Critical if blocking a step or renewal at a waiting barrier; anticipatory for cue-driven; opportunistic otherwise |
| `asr` | process pool | `asr_concurrency` (1) | Critical |

Global LLM concurrency cap `llm_concurrency` = 4. Scheduler dequeues by priority (critical > anticipatory > opportunistic), then by `created_step`, then `job_id`. An opportunistic job is not started while any critical job is queued. Running opportunistic LLM jobs are aborted when a critical job is queued and the cap is reached.

Operations that can run concurrently: any set of jobs within caps; tool calls (external); the kernel step (never concurrent with another step). Nothing a worker does can interleave with a step's mutations, because workers never touch the store.

### 7.2 Model gateway settings

`config/models.default.yaml`:

```yaml
provider: anthropic          # anthropic | openai_compat | scripted
mode: live                   # live | record | replay | scripted
models:
  interpret: { name: "<set by team>", max_tokens: 400, temperature: 0 }
  plan:      { name: "<set by team>", max_tokens: 900, temperature: 0 }
  frame:     { name: "<set by team>", max_tokens: 600, temperature: 0 }
  compose:   { name: "<set by team>", max_tokens: 300, temperature: 0 }
  vision:    { name: "<set by team>", max_tokens: 300, temperature: 0 }
transport_timeout_s: { interpret: 8, plan: 12, frame: 10, compose: 8, vision: 12 }
transport_retries: 1
structured_output: json_schema     # provider-native schema enforcement where available; otherwise JSON mode + validation
asr: { engine: faster_whisper, model: small.en, compute_type: int8, beam_size: 1, word_timestamps: true }
```

Model names are team decisions (local). A setup-time health check calls each configured model once with a trivial prompt; failure falls back to `openai_compat` if configured, else runtime starts in degraded mode (templates and scripted fallbacks only) and logs `SETUP.MODEL_UNAVAILABLE`.

### 7.3 Interpretation

**View** (`JobView` JSON):

```json
{
  "turn": { "turn_id": "t4", "text": "actually make it Mumbai", "is_interruption": true, "covered_len": 23 },
  "active_goal": { "goal_id": "g2", "intent": "search_flights", "slots": { "origin": "Bengaluru", "destination": "Pune", "date": "2026-09-19" }, "commit_intent": false },
  "suspended_goals": [ { "goal_id": "g1", "intent": "create_ticket", "slots": { } } ],
  "pending_clarification": null,
  "recent_utterances": [ "Looking for flights to Pune on 19 September now." ],
  "tools": [ { "name": "search_flights", "description": "…", "parameters": { }, "mutability": "read" } ],
  "reference_date": "2026-09-18",
  "session_slot_names": ["user_name","phone","email","device_model","booking_reference"]
}
```

**Prompt requirements** (`prompts/interpret.md`): define each act precisely with one example; require slot names to be parameter names from `tools`; require `commit_intent` only for explicit requests or confirmations; require preservation of self-repairs ("Tuesday, I mean Wednesday" → Wednesday); require `backchannel` for turns without task content; require `visual_reference` classification; forbid inventing values; output only the schema.

**Acceptance** (reducer for `worker_result` kind `interpret`):

1. Job not aborted; read set valid (`goal.active`, active goal slot facts in view, `turn.{id}.prefix` at dispatch).
2. Output validates against §2.13; `primary_tool` in catalog; slot names validated as in §4.4 (unknown names downgrade to constraints, not rejection).
3. If turn is still open: store as speculative interpretation on the turn (`spec_interpretations`); do not apply.
4. If turn closed: promotion test (§7.5); if it passes, apply (§4.4) and mark `promoted`.
5. If turn closed and promotion fails: if a non-speculative job for the full text is not running, request one (critical).

### 7.4 Planning

**View:** goal intent and committed slots (and hypothesis slots for speculative planning, keyed as `slot.$G.{name}`), constraints, current plan (steps with statuses and consumed result summaries truncated to 2 KB each), change set (keys and new values), failure context (last failed step and error), usable tools with schemas, reconcile context if any.

**Prompt requirements** (`prompts/plan.md`): minimum steps; reads before writes; bind parameters to facts by key whenever a fact exists; use `step_output` to pass identifiers; mark `structure_depends_on` when a tool was chosen because of a slot value; mark `visual_candidates`; return `needs_clarification` with `missing` rather than guessing; never bind model literals to identifier parameters; include write steps only if `commit_intent` is true in the view.

**Acceptance:**

1. Job not aborted; read set valid (includes `goal.active`, `goal.{gid}.plan_rev`, facts in view).
2. Output validates (§2.14); all tools usable; `after` references resolve; graph acyclic; every required parameter has a binding or the plan status is `needs_clarification`; write steps present only if `commit_intent`.
3. Convert `local_id` → `step_key`; substitute `$G`.
4. Carry-over: for each new step whose `step_key` exists in the current plan with status `done` and whose bound read set is valid, copy status `done` and `carried_from`.
5. `accept_plan`; `plan_rev += 1`.
6. Invalid output: one retry with validation errors appended to view; second failure → transition 13 with INFORM.

### 7.5 Speculative execution

| Kind | Dispatch rule | Promotion rule | Invalidation |
|---|---|---|---|
| Speculative interpretation | On each `text_chunk`/`synthetic_chunk` of an open turn: if no `interpret` job running for the turn, request one (anticipatory) with `covered_len = len(turn.text)`; else set `turn.prefix_grown = true`. On job completion with `prefix_grown` and turn still open: request again | At end of turn, let `S` = latest accepted speculative interpretation. **Exact**: `S.covered_len == len(T.text)`. **Inert tail**: `detector.is_inert_tail(T.text[S.covered_len:])` AND (if `S.commit_intent == true`) the previous speculative interpretation has identical `act`, `primary_tool`, `slot_deltas`, and `commit_intent`. Otherwise not promoted | Job read set includes `turn.{id}.prefix` at dispatch; prefix changes do NOT invalidate (the `covered_len` mechanism handles growth); goal changes do |
| Speculative planning | When a speculative interpretation with act `new_goal` is stored and no goal is active: request PLAN (anticipatory) with hypothesis slots as `slot.$G.*` | At commit of the turn: for each read-set entry `hyp.{turn}.{name}` with digest `d`, the committed `slot.{gid}.{name}` must have digest `d` (or the entry is absence and the slot is absent). If all match, accept the plan as if non-speculative | Aborted when turn closes with a different act or primary tool |
| Speculative perception | Cue-driven question (§9.4) before end of turn | Question remains open after interpretation → claims used | Question voided |
| Response frame | When the last required read or write step's call is emitted: request FRAME (anticipatory) with slot key `result:{call_id}` | Used when the call is consumed and frame read set valid | Frame read set: `goal.active`, intent, slots and constraints in view, `catalog.tool.{tool}` |
| Speculative reads | Policy `spec_reads = after_eot_independent` (default): executor MAY emit read steps whose bindings do not reference pending clarification targets while a clarification is pending. `partial_high_conf`: disabled by default [KIT Q6] | Normal consumption | Normal |
| State-changing speculation | Never | — | — |

Hypothesis mapping at promotion: speculative interpretation deltas are applied as committed facts; `hyp.*` facts for the turn are deleted after the promotion check.

### 7.6 Response frames and composition

**Framer view:** intent, committed slots, constraints, tool spec (name, description, output schema or `sample_result` from an earlier consumed call of the same tool in this session), write/read kind, user's last request text.

**Validation (`frames.validate_frame`):**

1. Schema §2.15.
2. Every `{placeholder}` in each template names a hole; every hole is used.
3. `path` expressions parse (JSONPath subset: `$`, `.name`, `[n]`); for `field`, `min_by`, `max_by`, `first_k`, paths resolve against the output schema (or sample result) to the expected types (arrays for `count`, `min_by`, `max_by`, `first_k`).
4. `fact` holes reference keys in the frame view; `claim` holes reference open question claims.
5. Template literal text contains no digits and no token equal (case-insensitively) to any value of a fact in the goal's history whose current status is not the current value (prevents stale values baked into literal text).
6. `grade` is `result` for read tools; `effect_done` / `effect_failed` for write tools on success / error branches.
7. No output schema and no sample result → frame rejected (`FRAME.NO_SHAPE`).

**Evaluation (`frames.evaluate`, in decide order 8, same step as consumption):**

1. Branch: `error` if result status error; else `success_nonempty` if `nonempty_test` evaluates truthy; else `success_empty`.
2. Evaluate holes. `claim` holes require a valid lease; `fact` holes require non-retracted facts.
3. Format: `number` (thousands separators per locale), `currency` (locale currency symbol from policy `currency_symbol`, default `₹`), `date` (`D Month`), `time` (`HH:MM`), `list` (join with ", " and " and "), `raw`.
4. Any failure → `FrameFailure`; fall back to COMPOSE job (critical) with `compose_deadline` timer; if the timer fires first → template final `final.generic.{branch}` built from the result's top-level scalar fields listed in the output schema.
5. Claims: `result` (read) or `effect_done`/`effect_failed` (write) referencing the call; `understood` referencing each `fact` hole.

**Completion:** a plan is complete when all non-skipped steps are `done` (or the only remaining steps are `failed` with no replans left → failure path). FINAL is intended in the step where the last required call is consumed, using the frame if valid.

### 7.7 Multimodal processing jobs

- `vision` view: frame blob (PNG bytes, re-encoded to JPEG quality 85 if larger than `vision_max_bytes`, default 1.5 MB), question targets (name, description, schema type), mode, allowed values (enums). Prompt requires claims only for targets and a confidence label with definitions (`high`: clearly legible or unambiguous; `medium`: likely but partially obscured; `low`: guess).
- `asr` view: WAV blob. Output segments with offsets; `end_of_utterance` true if trailing silence ≥ 600 ms.

### 7.8 Reaction slots and job scheduler

Slot keys and their jobs:

| Slot key | Anticipated event | Job | Priority |
|---|---|---|---|
| `eot:{turn_id}` | End of the open turn | Speculative INTERPRET | Anticipatory (critical once turn closes) |
| `plan:{turn_id}` | End of turn with new goal | Speculative PLAN | Anticipatory |
| `result:{call_id}` | Result of the last required call | FRAME | Anticipatory |
| `question:{question_id}` | End of turn / step needing claim | VISION | Anticipatory or critical (§7.1) |
| `renewal:{fact_key}` | Barrier or emission needing lease | VISION | Critical |

`scheduler.decide(ctx)` fills empty slots whose anticipated event is still possible (open turn exists; call in flight; question open) and aborts jobs whose slot's anticipated event became impossible (turn closed with different outcome, call terminal, question void). One job per slot.

`has_blocking_work()` (for Model B busy reporting): mailbox non-empty, or any critical job queued or running.

### 7.9 Replanning

| Trigger | Path |
|---|---|
| Slot update, rebind conditions met (§4.8 step 4) | Rebind (no job) |
| Slot update, structural dependence | PLAN (critical) with change set |
| Addition (new goal-scoped slot or constraint) | Rebind if the new slot is an unbound parameter of an existing step (absence invalidation made the step stale); else PLAN |
| Step failed, replans remain | PLAN with failure context |
| Manifest update invalidates a step | PLAN |
| Reconcile compensation | PLAN with reconcile context |
| Return to goal | Rebind, then PLAN if any step invalid |

Rebind implementation constraint: rebinding never changes tools, `after` edges, or `output_map`. It only changes step statuses and produces new calls through normal binding.

---

## 8. Coordination layer

### 8.1 Coordination responsibilities by phase

| Concern | Phase and component | Mechanism |
|---|---|---|
| Fast path | Decide 9 (`responder`), 4 (`clarify`), detector in apply | Reads post-invalidation state; intended actions validated in emit |
| Slow path | Dispatch (7), worker results in apply (2) | Jobs with read sets; acceptance checks |
| State | Apply (2) through `StoreTxn` | Single writer |
| Tools | Decide 6–7 (`executor`, `commit`), apply (`results`) | Ledgers, gate, router |
| Interruptions | Apply (`task.on_interruption`, `turns`), decide 2 (`task.decide`) | Triage, holds, anchors |
| Cancellation | Invalidate (3) marks; decide 1 (`cancellation_planner`); emit (5) | Same-step cancellation |
| Snapshots | Emit (5) via `snapshot.project` | Projection at emission |
| Stale results | Apply (`results.route`), invalidate (3) | Read-set validity, transitive retraction |

### 8.2 Cancellation planner (decide order 1)

For every call marked `invalidated_pending_cancel` in phase 3 of this step:

1. If the call's status (before marking) was `in_flight` or `deadline_passed`: intend `cancel` action with `rule = INV.CANCEL_SAME_STEP` and `trigger_event_id` = the envelope that caused the failing fact change.
2. Set status `cancel_requested` at emission; schedule `cancel_inference` timer at `max(call.deadline_ts_us, now_us + cancel_inference_ms)` (default 1500 ms).
3. If the call is a write: the effect remains `pending`; add reconcile watch.

**Anchor rules** add marks before this planner runs:

| Policy `cancel_anchor` | Additional marking in apply/invalidate |
|---|---|
| `chunk` (default) | When a chunk produces a HIGH hypothesis `hyp.{turn}.{name} = v` and there exists an in-flight call whose read set includes `slot.{gid}.{name}` with digest ≠ `digest(v)`, AND (the turn `is_interruption` OR the turn opened while the goal is active OR the chunk has a `correction` cue): mark that call `invalidated_pending_cancel` with rule `ANCHOR.CHUNK`. The committed slot is NOT changed. |
| `chunk` + `write_hold_on_wait_cue` (default on) | A chunk with a `hold` or `negation` cue in an interruption turn while a write call is in flight marks that write call with rule `ANCHOR.HOLD_CUE` |
| `signal` | On `interruption`, mark all in-flight read calls of the active goal whose read sets contain any user-sourced slot, rule `ANCHOR.SIGNAL` |
| (always) EOT | Committed interpretation changes facts; normal invalidation |

A call cancelled by a chunk anchor that the committed interpretation later shows was still valid is logged `ANCHOR.FALSE_CANCEL`; the step is re-bound and a new call emitted if still needed.

### 8.3 Triage and holds

**Entry** (transition 23), in apply for the first `interruption` or user-content envelope while a goal is active and task state ∈ {understanding, planning, executing, responding, reconciling}:

1. `TriageRecord` created; `prior_task_state` recorded.
2. Task state `triage`; floor `user_turn_open`.
3. Unsent utterances of the active goal dropped (none exist between steps; this covers same-batch intents).
4. Hold mode on: in phase 4, any intended action other than `cancel` for the active goal becomes a held proposal (`holds`), except `tool_call` for a read step that is a retry of a call already in flight before triage (none, since retries require terminal status; kept for completeness).

**During triage:** tool results are routed normally (facts may update); worker results accepted normally (plans accepted, frames stored) but resulting actions held; chunk anchors apply.

**Resolution** (decide order 2), when the triage turn's interpretation is accepted:

| Act | Resolution |
|---|---|
| `backchannel`, `smalltalk` | Task state ← prior; release holds with valid read sets in original order; drop invalid |
| `progress_question` | As backchannel, plus PROGRESS from ledger |
| `slot_update`, `answer_clarification` | §4.8; release valid held read calls; drop held speech (responder regenerates) |
| `addition` | §4.8 with addition semantics (§7.9) |
| `new_goal` | §4.9; drop all holds |
| `return_to_goal` | §4.10; drop all holds |
| `abort` | §8.5; drop all holds |
| `unclear` | CLARIFY; keep holds whose read sets reference ambiguity targets; release others |
| `confirm`, `deny` | Apply commit intent change; release valid holds |

**Fallback** if the interpretation job fails twice for the triage turn: detector signals decide. No values and no correction/hold/abort cues → `fallback_resume`. Otherwise → `fallback_clarify` with template "Sorry, what would you like to change?"

### 8.4 Lifecycle of an interrupted task (walkthrough A: correction during a chain)

Scenario: goal `g2` searches flights to Pune, then fetches a seat map for the selected flight. User corrects the destination while the seat map is in flight.

Initial state at step 20: `slot.g2.destination = Pune`; `c-0003 search_flights` consumed; `derived.g2.selected_flight_id = AI-505` (provenance: read set of `c-0003`, which includes `slot.g2.destination@Pune`); `c-0004 get_seat_map(flight_id=AI-505)` in flight (read set includes `derived.g2.selected_flight_id@AI-505`); frame `rf2` prepared for `c-0004`; task `executing`.

| Step | Batch | Phase detail | Actions emitted |
|---|---|---|---|
| 21 | `interruption` (ts 5.000 s) | Apply: turn `t5` opened, `is_interruption`; triage entered (prior `executing`); floor open. Invalidate: nothing changed. Decide: no intents. Dispatch: none (no content yet) | — |
| 22 | `text_chunk "actually make it Mumbai"` (5.080 s) | Apply: chunk appended; detector → `destination=Mumbai` HIGH (session entity) + `correction` cue; `hyp.t5.destination = Mumbai`. Chunk anchor: `c-0004` read set does not contain `slot.g2.destination` directly. **Transitive rule for anchors:** the anchor check also follows provenance: `derived.g2.selected_flight_id` provenance contains `slot.g2.destination@Pune` ≠ Mumbai → `c-0004` marked `ANCHOR.CHUNK_TRANSITIVE`. Invalidate: frame `rf2` depends on `slot.g2.destination` (in view) → dropped. Decide 1: cancel `c-0004`. Decide 10: scheduler requests speculative INTERPRET (`eot:t5`). Emit: cancel. Dispatch: `j00041` | `cancel c-0004` (5.080 s) |
| 23 | `end_of_turn` (5.300 s) | Apply: turn closed; floor closed. `j00041` not yet complete. Decide: G3 no writes anyway; responder: detector ACK conditions — goal intent known, HIGH value exists → but task is in triage; ACK deferred to resolution (policy: no detector ACK during triage) | — |
| 24 | `worker_result j00041` (5.410 s): `slot_update destination=Mumbai`, `covered_len == len(text)` | Apply: exact promotion; `slot.g2.destination = Mumbai` (ver bump); `hyp.t5.*` deleted. Invalidate pass 1: `derived.g2.selected_flight_id` provenance invalid → retracted; `result.c-0003` provenance invalid → retracted. Pass 2: step `search` (binding `fact slot.g2.destination`) stale; step `seat_map` (binding `derived`) stale. `c-0004` already `cancel_requested`. Decide 2: triage resolves `slot_update`; transition 27. Rebind: `structure_depends_on` empty → plan `g2.r3` with same step keys, both steps `pending`. Decide 6: `search` ready → call `c-0005 search_flights(destination=Mumbai)`; `seat_map` not ready (after). Decide 9: ACK `ack.update.in_progress` claims `understood(slot.g2.destination)`, `in_progress(c-0005)`. Emit phase 1 overlay: `c-0005` in flight → claims valid. Snapshot changed → attached. Phase 2: write speak, tool_call. Dispatch: none | `speak "Mumbai instead. Updating the search."` + snapshot; `tool_call c-0005` |
| 25 | `tool_result c-0004` (5.600 s, the mock delivered anyway) | Route: status `cancel_requested` → `completed_after_cancel`; read tool → no effect; not consumed | — |
| 26 | `tool_result c-0005` (6.200 s) | Route: consumed; `result.c-0005` set; `derived.g2.selected_flight_id = 6E-221` (new digest). Invalidate: nothing waits on it except step `seat_map` which becomes ready. Decide 6: `c-0006 get_seat_map(6E-221)`. Decide 9: PROGRESS `result(c-0005)`, `in_progress(c-0006)`. Decide 10: FRAME for `c-0006` (`result:c-0006`). Emit | `speak "Found 4 flights to Mumbai. Checking seats on the cheapest."`; `tool_call c-0006` |
| 27 | `worker_result` frame `rf3` (6.700 s) | Accepted; stored against `c-0006` | — |
| 28 | `tool_result c-0006` (7.000 s) | Consumed. Decide 8: plan complete; `rf3` valid → rendered FINAL. Emit with snapshot (final always carries) | `final "…"` + snapshot `{destination: Mumbai, …}` |

Measured by metrics (§10.3): interruption-to-cancellation latency = 80 ms from signal, 0 ms from the invalidating chunk; snapshot update latency from end of turn = 110 ms (interpretation), from the chunk = 330 ms.

If the correction had reverted to Pune before step 26: `search` step rebound with the Pune value; `c-0003` result is consumed and valid again → `EXEC.CUTOFF_REUSE`, `derived.g2.selected_flight_id` unretracted with equal digest; the seat map would be re-requested only because `c-0004` was cancelled (cancelled calls are not reused).

### 8.5 Lifecycle of an interrupted task (walkthrough B: goal replacement while a write waits to settle)

Initial state: goal `g1`, plan `search → book`. `c-0002 search` consumed; user turn `t3` "book the 6 pm one" closed at 10.000 s with `commit_intent=true`, `derived.g1.selected_flight_id = AI-610`. `book` step ready; CommitGate G11 blocked until 10.300 s; ACK "I'll book the 6 pm flight." emitted at 10.000 s (claim `intended`).

| Step | Batch | Detail | Actions |
|---|---|---|---|
| 40 | `interruption` (10.150 s) | Triage; floor open; G3 now fails too | — |
| 41 | `text_chunk "forget the flight, I need to raise a ticket about my phone"` (10.200 s) | Detector: `abort`-lexicon partial ("forget"), no values. No in-flight calls to anchor. Speculative INTERPRET requested | — |
| 42 | `timer_fired settle` (10.300 s) | Gate re-evaluated: G3 fails (floor open) → still blocked | — |
| 43 | `end_of_turn` (10.600 s) | Floor closed. Gate: G10 fails (interpret job running for a turn closed after `t3`) → blocked | — |
| 44 | `worker_result` interpretation: `new_goal`, `primary_tool = create_ticket`, deltas `issue_summary`, `device_model` (session) | Exact promotion. §4.9: `g2` created; `g1` suspended; `goal.active = g2`. Invalidate: `book` step's call (blocked, `proposed`) read set contains `goal.active@g1` → `discarded`; no effect record exists (effect is created only on gate pass). Carry-over: none. Decide: PLAN for `g2` unless speculative plan promotable (it is, dispatched at step 41 as `plan:t6`) → accepted. Executor: `create_ticket` is write; `commit_intent` false (user asked to raise a ticket: interpreter sets `commit_intent=true` for an explicit request) → assume true → G11 blocked until 10.900 s. Responder: ACK `ack.new.intended` "I'll raise a ticket about your phone." Snapshot `{intent: create_ticket, slots: {…}}` | `speak` + snapshot |
| 45 | `timer_fired settle` (10.900 s) | G1–G12 pass. Effect `pending`. Emit `tool_call c-0003 create_ticket`. No speech (ACK already covered `intended`; PROGRESS rule: write emitted counts as user-visible change only if no ACK in last `min_gap` → suppressed) | `tool_call c-0003` |

The booking for `g1` was never emitted. No reconciliation is needed.

### 8.6 Emission gate (two-phase)

**Phase 1 — overlay validation.**

1. Build an overlay view of ledgers: intended `cancel` → call `cancel_requested`; intended `tool_call` → call `in_flight` and (write) effect `pending`.
2. Validate each intended action in emission order (§8.6.1). Actions failing validation are removed; utterances whose claims reference removed actions are re-rendered at a lower grade if a template exists (e.g., `in_progress` → `intended`), else removed. Repeat until no change (maximum 3 passes).
3. Run online monitors (§11) on the surviving batch against the overlay. A violation removes the action, records `monitor_blocks`, and (in `strict_monitors` mode used by tests) raises `MonitorViolation` after logging.
4. Attach snapshot (§4.7) to the first eligible action.

**Phase 2 — write.** For each surviving action in order: stamp `ts_us = now_us`, encode, `writer.write`. On success: update ledgers (call `in_flight` with `emitted_ts_us`, deadline timer; call `cancel_requested`; utterance `emitted_ts_us`; snapshot record). On writer exception: mark this and remaining actions unsent; calls `failed` (`EMIT.WRITE_ERROR`), effects `failed`; log; post `emit_rejected` envelopes.

**8.6.1 Emission order.** `cancel` → `speak` (ack, repair, progress, hold, inform) → `tool_call` → `clarify` → `final`. Within a type, by `created` order in decide.

**8.6.2 Per-action validation.**

| Action | Checks |
|---|---|
| All | Pydantic model valid; `encode` produces schema-valid kit JSON [KIT] |
| `tool_call` | Tool usable; `args` validate; `call_id` unique; read set valid; if write: effect `pending` for this call in overlay and CommitGate decision recorded this step |
| `cancel` | Target call exists; status (pre-overlay) ∈ {in_flight, deadline_passed}; not already cancel-requested |
| `speak`, `clarify`, `final` | Read set valid; text non-empty, ≤ `max_utterance_chars` (600); floor closed; claims valid (§6.7) against overlay; truth checks §8.6.4 |
| Snapshot | §4.7 validation |

**8.6.3 Ordering conflicts.** If both a `final` and a `clarify` are intended for the same goal, `final` is dropped (`EMIT.FINAL_WITH_CLARIFY`). If a `final` and a `progress` are intended, `progress` is dropped.

**8.6.4 Truth checks for text.** Applied to templates (by construction) and required for composer output and `ack_phrase`:

1. Every number, time, date, currency amount, and alphanumeric code in the text must equal (after normalization) a value referenced by the utterance's claims or frame holes.
2. Completion lexicon (`booked, created, confirmed, cancelled, canceled, done, submitted, reserved, raised, sent, scheduled, completed, here are, I found, I've found`) requires a claim of grade `result`, `effect_done`, or `effect_failed` (for negative forms).
3. Tokens equal to a stale value (a previous digest-different value of any slot of the active goal, from the utterance ledger's history) are forbidden unless the utterance is `repair` or `inform`.
4. No mention of suspended goals' intents except in `inform`.

Failure → the action is re-rendered from templates; if no template applies, removed.

### 8.7 Abort path

On accepted `abort`:

1. Cancel all `in_flight` calls of the active goal (normal invalidation via `goal.active` set to `null`).
2. Goal `abandoned`; task state `idle`.
3. Reconcile item `abort_with_effects` if any effect of the goal is `pending` or `confirmed`.
4. INFORM "Okay, I've stopped." plus effect summary; snapshot `{intent: null, slots: {}}` [KIT: whether a null-intent snapshot is valid].

### 8.8 Component failure containment

Each decide component call and each reducer is wrapped by the step engine:

- On exception: record `degraded: {component, error, step_no}`; roll back nothing (mutations already made in the transaction remain, because reducers are individually atomic by design: each reducer MUST perform validation before its first mutation); skip that component's remaining work for the step.
- The step always reaches EMIT and LOG.
- Three consecutive degraded steps for the same component → `SAFE_MODE` for that component for the rest of the scenario: executor stops emitting new calls, responder uses only templates, perception stops dispatching; a truthful INFORM is emitted once.

### 8.9 Watchdog

On `watchdog` event (wall elapsed ≥ `watchdog_s`, default 105): cancel all in-flight reads; do not emit new writes; if no FINAL for the active goal yet, emit FINAL from templates with claims for consumed results and confirmed/unknown effects; snapshot attached. Excluded from determinism tests.

---

## 9. Multimodal pipeline

### 9.1 Evidence priority

When two sources provide a value for the same slot name:

| Rank | Source | Condition |
|---|---|---|
| 1 | User statement in the most recent committed turn | `evidence: verbatim` or `inferred` |
| 2 | User statement in an earlier committed turn | Not superseded |
| 3 | Perception claim `high` with valid lease | — |
| 4 | Tool-derived value (derived fact) | For slots the plan derives |
| 5 | Perception claim `medium` | Reads only; hedged speech |
| — | Perception claim `low` | Not used |

Conflict rules (SlotResolver):

| Pair | Rule |
|---|---|
| Rank 1/2 vs rank 3 or 5, different values | Open conflict; block steps binding the name; clarify (priority 1) |
| Rank 1/2 vs `low` | User value stands; no conflict |
| Rank 3 vs newer rank 3 for the same question, different | Newer supersedes (`claim.{qid}.{name}` set); cascade |
| Rank 3 vs newer rank 5, different | Open conflict (perception vs perception) |
| Rank 4 vs rank 1 (user contradicts a tool result) | For goal-scoped parameter slots: user value is used; the derived fact is not changed; response frames surface the tool value only via holes. No conflict unless the step is a write binding the derived value, in which case CLARIFY |

### 9.2 Text

- Entry: `text_chunk` → `TurnManager.on_chunk`.
- Turn text: chunks joined with one space after trimming; whitespace collapsed; Unicode NFC.
- `chunk_semantics = append` (default) [KIT]. If `revisable`: a chunk with `turn_hint` equal to the open turn replaces the turn text; `covered_len` of speculative interpretations is compared by digest of prefix, not length; the detector re-runs on the full text.
- Timestamps: `Chunk.ts_us`; turn `opened_ts_us` = first chunk or interruption; `closed_ts_us` = EOT.

### 9.3 Audio

**Modes** (policy `audio_mode`, default `auto`) [KIT]:

| Mode | Behavior |
|---|---|
| `transcript_primary` | Clip stored as observation; not analyzed |
| `audio_only` | ASR job (critical) per clip; segments → `synthetic_chunk` envelopes with `ts_us = clip.ts_us + segment.offset_us`; if `asr_closes_turn` and `end_of_utterance`, a synthetic `end_of_turn` with `ts_us = clip.ts_us + last_segment.end_us` |
| `auto` | ASR job per clip; on completion, segments are held for `asr_dedupe_ms` (800) of harness time (timer). If any `text_chunk` arrives with `ts_us` within `[clip.ts_us − 200 ms, clip.ts_us + duration_us + 800 ms]`, the ASR output is discarded (`ASR.DEDUPED`) and a disagreement record is created if the normalized texts differ (word error rate > 0.2). Otherwise released as synthetic chunks |

**Correlation.** A clip belongs to the turn open at `clip.ts_us`, or to the next turn opened within 800 ms.

**Audio/transcript disagreement.** Policy `asr_disagreement = log` (default): transcript wins; metric `asr_text_disagreements` incremented. `clarify_if_write_slot`: if the disagreement changes a value that the interpretation of the transcript binds to a write step's argument (detector run on ASR text yields a HIGH value for the same name with a different value), open a conflict (`user` vs `asr`, treated as rank 1 vs rank 5) and clarify before commit.

**Self-repair.** ASR text preserves fillers and repairs. Detector cues include `i mean`, `sorry`, `no wait`; interpreter resolves.

### 9.4 Video frames

**Entry.** `video_frame` → observation with `modality_seq`. Not analyzed on arrival.

**Questions.** Created by:

| Source | When | Mode | Targets |
|---|---|---|---|
| Cue | Detector `visual` cue in an open turn and at least one frame exists with `capture_ts_us ≥ turn.opened_ts_us − frame_lookback_ms` (3000) | From interpreter later; initially `at_utterance` | Generic: `device_model`, `visible_state` (descriptions from policy `generic_visual_targets`) plus any parameter of relevant tools with `visual_candidates` |
| Demand | Accepted interpretation with `visual_reference ≠ none` or `visual_candidates` non-empty | As interpreted | Named candidates (parameter name, description, schema type) |
| Plan | Step with `frame` binding, or required parameter in `visual_candidates` unbound | `current_state` unless interpretation said `at_utterance` | That parameter |
| Renewal | Lease expired at a point of use | Same as original | Same |

A new question for the same goal and overlapping targets retargets the existing open question (updates targets and mode, `question_id` unchanged) rather than creating a second.

**Frame selection.**

- `at_utterance`: anchor = `ts_us` of the chunk carrying the visual cue, or the turn's `closed_ts_us`. Frame = latest with `capture_ts_us ≤ anchor`; if none, earliest with `capture_ts_us ≤ anchor + align_window_ms` (1000); if none, the question waits for a frame within that window, then clarifies ("Could you show me the device?").
- `current_state`: latest frame at dispatch.

**Coalescing.** One running VISION job per question. A newer frame for a running `current_state` question sets `latest_pending_obs`. On completion: `at_utterance` ignores it; `current_state` re-runs once on `latest_pending_obs` only if the question is a renewal or blocks a step.

**Claim acceptance** (`perception.on_perception_proposal`):

1. Job not aborted; read set valid (`goal.active`, question target facts).
2. Claims for non-target names are dropped.
3. For each claim: `high` → `claim.{qid}.{name}` set (status `derived`, source `perception`, lease granted); and, if the name is a goal parameter and no user value exists, `slot.{gid}.{name}` set with same provenance and lease. `medium` → `claim.{qid}.{name}` set (no slot write). `low` → recorded in analysis only.
4. SlotResolver conflict rules §9.1.
5. Question status `answered` when all targets have ≥ medium claims; else remains open.

**Leases.** Granted at acceptance for `current_state` claims (`granted_frame_seq` = analyzed frame's `modality_seq`). Checked at: CommitGate G4; binder for `frame` bindings and perception-sourced slot facts; frame `claim` holes; emission truth check for utterances whose read sets include perception facts; snapshot projection. Expired at a point of use → renewal question (critical job) and the use waits (writes) or proceeds with hedged wording (reads and progress) or omits the slot (snapshot).

**Renewal outcome.** Same value → lease renewed (`renewals += 1`; `medium` → `high` after one agreeing renewal). Different value at `high` → claim and slot facts updated (cascade). Different at `medium` → conflict.

**Frame gap.** If a write step needs a `current_state` perception value and no frame has `capture_ts_us ≥ now_us − gap_ms` (3000), CommitGate G4 fails with `LEASE_NO_FRAME` and ClarificationManager asks for a fresh view (priority 6).

**Stale frames.** A frame whose `capture_ts_us` is earlier than the latest analyzed frame for the same question is never analyzed for that question.

**Tool-side vision.** If a tool has `frame_param` (schema parameter with `format: frame-ref`, `contentMediaType: image/*`, or name ending `frame_id`/`image_ref`) [KIT], the binder binds the aligned frame's `frame_id` (harness ID) and no VISION job is created for that parameter.

**Cancellation.** VISION and ASR jobs are aborted on read-set invalidation (goal change, question void or retargeted). Process-pool work cannot be interrupted; its result is rejected by the aborted status. Renewal jobs are aborted if the point of use disappears (step discarded, utterance dropped).

**Grounding.** Every use of perception in a tool argument, response hole, or snapshot passes through a `claim.*` or `slot.*` fact with perception provenance and lease; hedged wording (`it looks like`) is required by templates for `medium` claims and for expired leases on reads.

---

## 10. Virtual clock

### 10.1 `ClockPort` interface (`adapters/clock.py`)

| Member | Contract |
|---|---|
| `model: Literal["A","B","C","sim","replay"]` | — |
| `now_us() -> int` | Harness time; non-decreasing across calls |
| `async wait_for_work(work_event: asyncio.Event, next_timer_us: int | None) -> None` | Returns when `work_event` is set or harness time reaches `next_timer_us`. Wake-up timing only; never decides behavior |
| `set_busy(busy: bool) -> None` | Model B: tell the harness whether the agent has blocking work (no-op elsewhere) |
| `request_advance_to(ts_us: int) -> bool` | Model B: ask harness to advance time to `ts_us` if it has no earlier events; returns false if unsupported |
| `observe_event_ts(ts_us: int) -> None` | Called by ingress for every external event; Model A uses it to anchor the offset |

**Implementations**

| Class | `now_us` | `wait_for_work` |
|---|---|---|
| `CoupledClock` (A) | If harness exposes a clock: that value. Else `last_event_ts_us + (monotonic_ns() − mono_at_last_event_ns) // 1000 × scale` | `asyncio.wait` on the event with timeout derived from `next_timer_us − now_us()` |
| `SteppedClock` (B) | Harness-provided virtual time | Awaits the event; if idle with timers pending, calls `request_advance_to(next_timer_us)`; if false, returns immediately and the driver fires the earliest timer with `liveness_fire = true` |
| `HarnessClock` (C) | Harness clock read | As A or B per `clock_c_base` [KIT] |
| `SimClock` | Simulator's virtual time | Not used (SimDriver) |
| `ReplayClock` | Recorded `now_us` for the current step | Not used |

**Timer liveness rule (INNOV C9c).** In `TimerWheel.due(now_us)`: timers with `due_us ≤ now_us` fire. Additionally, the driver may call `timers.fire_earliest_for_liveness()` when the clock reports it cannot advance; the fired envelope carries `liveness_fire: true` and rule `TIMER.LIVENESS`. The simulator's Model B mode supports both `request_advance` semantics and "no advance" semantics (scenario option `harness_advances_idle`), and both are tested (T-05).

### 10.2 Separation of times

| Time | Source | Used for |
|---|---|---|
| Logical event time | Event `ts_us`; `ClockPort.now_us()` per step | Ordering, timers, deadlines, alignment, leases, staleness, action timestamps |
| Execution time | `observability/telemetry.py` (`perf_counter_ns`) | Step and job durations; never decisions |
| Measured latency | Computed offline from run artifacts using logical times | Reports |

### 10.3 Metric definitions (`observability/metrics.py`)

Definitions use logical times from `events.jsonl`, `actions.jsonl`, `decisions.jsonl`.

**Substantive action.** An action of type `speak` with kind ∈ {ack, progress, repair, inform} and at least one claim of grade ≠ `question`; or type `clarify`; or type `final`. `hold` is not substantive (policy `substantive_kinds` [KIT]).

| Metric | Definition | Reported as |
|---|---|---|
| **Time to first substantive response (TTFS)** | For each trigger `E` (computed separately for three trigger types: `end_of_turn`, `interruption`, first `text_chunk` of a turn): `min(A.ts_us − E.ts_us)` over substantive actions `A` with `A.ts_us ≥ E.ts_us` and `A.ts_us < ts of the next trigger of the same type`. If none: miss | p50, p95, miss count per trigger type |
| **Interruption-to-cancellation latency** | For each call `c` cancelled with a rule in {`INV.CANCEL_SAME_STEP`, `ANCHOR.*`} during a triage: (a) signal-anchored: `cancel.ts − interruption.ts`; (b) evidence-anchored: `cancel.ts − ts of the envelope whose fact change invalidated c` (from decision log `invalidations` and `batch`). Also: count of calls invalidated but never cancelled before their result (must be 0) | p50, p95, max; counts |
| **State-update timing** | For each committed slot change from user input: (a) fact latency: `ts(step where slot fact changed) − ts(end_of_turn of the turn)`; (b) snapshot latency: `ts(first action carrying a snapshot with the new value) − ts(end_of_turn)`; also relative to the chunk that first contained the value | p50, p95 |
| **Tool latency** | `result.ts − call.emitted_ts` per call, grouped by tool and status | p50, p95 per tool |
| **End-to-end latency** | Per goal: (a) `final.ts − first chunk ts of creating turn`; (b) `final.ts − end_of_turn ts of the last user turn before final` | p50, p95 |
| **Cancel delay for transitive dependents** | Evidence-anchored cancel latency restricted to calls whose read sets did not contain the changed slot directly | p50, p95 |
| **Event-anchored ratio** | Share of actions in {cancel, first substantive after trigger, first tool_call after EOT, final} emitted in a step whose batch contains a class 0–4 envelope | Ratio |
| Others (INNOV §6.2) | Stale consumptions, omission escapes, stale-evidence groundings, false claims, duplicates, reconciliations, write exposure window, promotion hit and disagreement, speculative waste, liveness fires | Counts or ratios |

---

## 11. Concurrency safety

Each invariant lists the responsible component, enforcement, and the test(s) that verify it. Online monitors (in `kernel/monitors.py`) are marked **[ON]**; offline checker rules (in `sim/checker.py`) are marked **[OFF]**.

| ID | Invariant | Responsible | Enforcement | Tests |
|---|---|---|---|---|
| CS-01 | Only kernel steps mutate session state | `store/txn.py` | Active-transaction guard; import-linter forbids `workers` → `store` | `unit/store/test_txn_guard`, `static/test_imports` |
| CS-02 | A kernel step never awaits | `kernel/step.py` | No `async def` in `kernel/` except `driver.py`; static check | `static/test_kernel_sync` |
| CS-03 | Events are applied in `(ts_us, class, seq)` order | `kernel/ordering.py` | Sort before apply | `unit/kernel/test_ordering`, T-01 |
| CS-04 | No action is emitted from a proposal whose read set is invalid | `reducers.py`, `emission.py` | Acceptance check + gate re-check **[ON]** | R-07, I-02 |
| CS-05 | Emission happens after invalidation in the same step | `kernel/step.py` | Fixed phase order; decision record phase markers | `unit/kernel/test_phase_order`, **[OFF]** rule `phase_order` |
| CS-06 | Every emitted claim is supported by post-batch ledger state | `emission.py` | Overlay validation **[ON]** | P-05, S-10, `unit/kernel/test_overlay_claims` |
| CS-07 | No non-retracted derived fact has invalid provenance after phase 3 | `invalidation.py` | Transitive retraction to fixpoint; debug assertion | I-04, `unit/kernel/test_transitive_retraction` |
| CS-08 | An in-flight call invalidated in step *n* receives CANCEL in step *n* | cancellation planner | Marking + decide order 1 **[OFF]** rule `cancel_same_step` | I-03, I-04, T-02 |
| CS-09 | A call in `cancel_requested` or `cancelled` is never consumed | `results.py` | Router step 5 **[OFF]** | R-04, R-05 |
| CS-10 | Terminal call statuses never change | `store/calls.py` | `set_call_status` raises | `unit/store/test_terminal_calls`, R-02 |
| CS-11 | Results are matched only by `call_id` | `results.py` | Router step 1 | R-03, R-08 |
| CS-12 | An effect record exists before any write is written | `commit.py`, `emission.py` | Gate creates effect; gate check in emission **[ON]** | N-04, `unit/kernel/test_write_ahead` |
| CS-13 | No two write calls with the same fingerprint are emitted unless the earlier effect is `failed`, `cancelled_inferred`, or `cancelled_confirmed` | `commit.py` G6 | Gate **[ON]** fingerprint monitor | S-03, S-04, S-01 |
| CS-14 | At most one write per lineage is `in_flight` or `unknown` | `commit.py` G7 | Gate **[OFF]** | S-09, I-15 |
| CS-15 | No write is emitted before `settle_ms` after its committing turn, or while floor open, triage, or interpretation pending | `commit.py` G3, G10, G11 | Gate + timers **[OFF]** | S-10, I-14 |
| CS-16 | Held proposals are released only if their read sets are valid at release | `task.py` | Triage resolution **[OFF]** | I-11, I-07 |
| CS-17 | Every attached snapshot equals the projection of committed state at its step | `snapshot.py`, `emission.py` | Projection at emit **[ON]** | I-10, P-04 |
| CS-18 | Workers never access mutable state | `scheduler.py`, `runner.py` | Views are deep-copied JSON; import-linter | `unit/workers/test_view_isolation`, `static/test_imports` |
| CS-19 | Aborted jobs' outputs are never applied | `reducers.py` | Job status check | M-07, R-07 |
| CS-20 | Timers use harness time; timers cannot deadlock under stepped clocks | `timers.py`, `clock.py` | Liveness rule | T-05, `unit/kernel/test_timers` |
| CS-21 | No core module reads wall time | all | `tools/check_clock_usage.py` | `static/test_clock_usage` |
| CS-22 | IDs and decisions are deterministic for identical inputs and worker outputs | `ids.py`, all kernel | Counters; no randomness in kernel; sorted iteration over dicts/sets | T-01 (replay identity) |
| CS-23 | Worker results are posted onto the event loop thread only | `runner.py` | `call_soon_threadsafe` | `unit/workers/test_runner_posting` |
| CS-24 | Session state does not persist across scenarios | `entry.py`, `store/session.py` | New store per scenario; no module-level mutable state (lint: module globals must be constants) | `integration/test_session_isolation`, `static/test_no_mutable_globals` |
| CS-25 | Writes and unhedged statements never rely on expired perception leases | `perception.py`, `commit.py`, `emission.py` | Lease checks at points of use **[ON]** | M-03, M-05 |
| CS-26 | Conflicting evidence on a write argument blocks the write | `commit.py` G4, `perception.py` | Gate **[OFF]** | M-06, M-01 (clarify policy) |
| CS-27 | Snapshot revisions are non-decreasing | `snapshot.py` | Monotonic check | P-04 |
| CS-28 | No speech while the floor is open | `emission.py` | Floor check **[ON]** | I-03, I-07 |
| CS-29 | Identifier arguments of writes are referenced, not model-generated | `binder.py` | Binding rule; gate G8 | S-05, S-04 |
| CS-30 | A promoted interpretation differs from the full text only by an inert tail | `turns.py`, `detector.py` | Promotion rule; offline disagreement check **[OFF]** | M-02, `unit/kernel/test_inert_tail` |
| CS-31 | A step reachable from a write is never ahead of the write, and no write precedes unrelated pending reads beyond the wait cap | `executor.py` | Commit-last readiness | N-02b, `unit/kernel/test_commit_last` |
| CS-32 | A frame-rendered response contains data only from holes | `frames.py` | Literal-text validation; evaluation | N-02, `unit/kernel/test_frame_literal` |
| CS-33 | Every CANCEL targets an issued, non-terminal call | `emission.py` | Validation **[ON]** | P-03 |
| CS-34 | Absent-parameter changes from user sources invalidate dependent calls | `binder.py`, `invalidation.py` | Absence entries | I-12 |

---

## 12. Test plan

### 12.1 Test layers

| Layer | Location | Runs |
|---|---|---|
| Unit | `tests/unit/{module}/` | Every commit; no simulator |
| Static | `tests/static/` | Every commit |
| Integration | `tests/integration/` | Kernel + store + sim driver with in-code scenario builders |
| Scenario | `tests/scenario/` parametrized over `scenarios/**/*.yaml` | Every commit (Model B), nightly (Model A and exploration) |
| Exploration | `tools/explore.py` | Phase gates and nightly |

### 12.2 Scenario DSL

```yaml
scenario_id: I-03_correction_during_read
description: Destination corrected while search is in flight.
clock_models: [A, B]
harness:
  harness_advances_idle: true      # Model B: harness advances to timers when agent idle
  cancel_semantics: deliver_anyway # suppress_if_pending | deliver_anyway | ack
reference_date: "2026-09-18"
manifest: fixtures/manifests/flights.json
policies: { }                      # overrides
mock_tools:
  search_flights:
    latency_ms: 800
    responses:
      - match: { destination: "Pune" }
        result: { flights: [ { flight_id: "AI-505", departure_time: "06:10", price: 4200 } ] }
      - match: { destination: "Mumbai" }
        result: { flights: [ { flight_id: "6E-221", departure_time: "07:05", price: 3900 } ] }
    faults: []                     # [{on_attempt: 1, error: {code, retryable}}, {on_attempt: 1, drop: true}, {duplicate_delivery: true}, {delay_extra_ms: 5000}]
events:
  - { at_ms: 0,    type: scenario_start }
  - { at_ms: 0,    type: manifest }
  - { at_ms: 100,  type: text_chunk, text: "find flights from Bengaluru to Pune" }
  - { at_ms: 250,  type: text_chunk, text: "on 19 September" }
  - { at_ms: 300,  type: end_of_turn }
  - { at_ms: 1400, type: interruption, label: INT }
  - { at_ms: 1450, type: text_chunk, text: "actually make it Mumbai", label: CORR }
  - { at_ms: 1600, type: end_of_turn }
workers:
  latency_ms: { interpret: 300, plan: 500, frame: 400, compose: 400, vision: 700, asr: 500 }
  interpret:
    - when_text: "find flights from (?P<origin>\\w+) to (?P<destination>\\w+)(?: on (?P<date>.+))?"
      output: { act: new_goal, primary_tool: search_flights, commit_intent: false, visual_reference: none, ambiguities: [], ack_phrase: null,
                slot_deltas: [ {name: origin, scope: goal, op: set, value: "{origin}"}, {name: destination, scope: goal, op: set, value: "{destination}"}, {name: date, scope: goal, op: set, value: "{date}"} ] }
    - when_text: "actually make it (?P<destination>\\w+)"
      output: { act: slot_update, primary_tool: search_flights, commit_intent: false, visual_reference: none, ambiguities: [], ack_phrase: null,
                slot_deltas: [ {name: destination, scope: goal, op: set, value: "{destination}"} ] }
  plan:
    - when_intent: search_flights
      output: { status: ok, steps: [ { local_id: s1, tool: search_flights, after: [],
                bindings: { origin: {type: fact, key: "slot.$G.origin"}, destination: {type: fact, key: "slot.$G.destination"}, date: {type: fact, key: "slot.$G.date"} } } ] }
  frame:
    - when_tool: search_flights
      output: { nonempty_test: {op: exists, path: "$.flights[0]"}, branches: { … } }
expect:
  - invariants_clean: true
  - cancel:
      call: { tool: search_flights, args: { destination: "Pune" } }
      within_ms_of: { label: CORR, max: 0 }
  - tool_calls_exact:
      - { tool: search_flights, args: { origin: "Bengaluru", destination: "Pune", date: "2026-09-19" } }
      - { tool: search_flights, args: { origin: "Bengaluru", destination: "Mumbai", date: "2026-09-19" } }
  - no_utterance_mentions: [ "Pune" ]
    after_label: CORR
  - final_snapshot: { intent: search_flights, slots: { origin: "Bengaluru", destination: "Mumbai", date: "2026-09-19" } }
  - final_grounded_in_call: { args: { destination: "Mumbai" } }
variants:
  sweep: { label: CORR, field: at_ms, from: 400, to: 1400, step: 50, also_shift: [INT] }
```

**Assertion vocabulary** (implemented in `sim/scenario.py`):

| Assertion | Meaning |
|---|---|
| `invariants_clean` | `checker.check_run` returns no violations |
| `tool_calls_exact` / `tool_calls_include` / `tool_calls_exclude` | Emitted tool calls equal, include, or exclude the listed (tool, args subset) in order |
| `max_calls` | `{tool, max}` |
| `cancel` | A cancel for a matching call exists; `within_ms_of: {label, max}` bound; `before_result: true` |
| `no_cancel` | No cancel for matching calls |
| `final_snapshot` | Snapshot on the last `final` equals |
| `snapshot_after` | `{label, within_ms, equals}` |
| `utterance_kinds` | Sequence of utterance kinds matches pattern |
| `no_utterance_mentions` | `[strings]`, optional `after_label` |
| `claim_grades` | `{text_contains, grade}` |
| `final_grounded_in_call` | FINAL claims reference a consumed call matching args |
| `clarify_count` | `{min, max}` |
| `effects` | `{fingerprint_count_by_tool: {tool: n}, states: {…}}` |
| `metric` | `{name, trigger?, op: "<=", value}` |
| `liveness_fires` | `{min, max}` |
| `no_deadlock` | Run ended before `max_virtual_ms` with `scenario_end` processed |
| `decision_rule_fired` | `{rule, min}` |

### 12.3 Test matrix

Columns: **ID**; **Name**; **Script** (key events; worker and mock behavior); **Variants** (sweeps and modes); **Assertions**; **Invariants** (CS IDs).

All scenario tests run under Model B (`harness_advances_idle: true`) on every commit, and under Model A plus Model B with `harness_advances_idle: false` nightly, unless stated.

#### Normal

| ID | Name | Script | Variants | Assertions | Invariants |
|---|---|---|---|---|---|
| N-01 | Simple read task | "find flights from Bengaluru to Pune on 19 September"; EOT; one search; result | Chunk spacing 50/150/400 ms | ACK before `tool_call`; `final_grounded_in_call`; `final_snapshot`; TTFS(EOT) ≤ step cost (Model B = 0) when speculative interpretation promoted | CS-03, 06, 17, 32 |
| N-02 | Chained tool calls | Search → `get_seat_map(flight_id from result)` → FINAL | Frame available / frame invalid (framer script returns bad path) → composer fallback | `tool_calls_exact` with step_output ID; PROGRESS after first result; FINAL rendered from frame (rule `FRAME.RENDER`) or `COMPOSE.FALLBACK` | CS-11, 32 |
| N-02b | Parallel reads + commit-last | Plan: `search_flights`, `get_weather` (independent), `book_flight` (after search, commit intent true) | Weather latency 200/2000 ms | Both reads emitted in the same step; `book_flight` not emitted before weather consumed (unless wait cap) | CS-31 |
| N-03 | Clarification | "find flights to Pune" (date missing); planner `needs_clarification [date]`; CLARIFY; user "the 19th"; complete | Answer at +500 / +7000 ms (re-prompt) | `clarify_count` 1 (or re-prompt once); final snapshot has date; no search before date | CS-06 |
| N-04 | Successful write | Search, "book the 6 am one" (commit intent), settle, booking result, FINAL | `settle_ms` 0/300 | ACK grade `intended`; `tool_call book_flight` at ≥ EOT + settle; effect `confirmed`; FINAL grade `effect_done`; one booking | CS-12, 13, 15 |
| N-05 | Visual lookup happy path | Frames of a device; "what does this light mean"; vision high claims; `manual_lookup(model, indicator)`; FINAL | — | Question mode `at_utterance`; frame aligned to cue chunk; lookup args from claims; FINAL hedging absent (high) | CS-25 |
| N-06 | Audio-only request | WAV clip; no text chunks; ASR → synthetic chunks; EOT from harness | `audio_mode` audio_only/auto | Same as N-01 with synthetic chunks; `ASR.DEDUPED` not fired | — |

#### Interruptions

| ID | Name | Script | Variants | Assertions | Invariants |
|---|---|---|---|---|---|
| I-01 | Interrupt before planning | EOT of "flights to Pune…" then interruption + "Mumbai" before interpret job returns | Correction offset 0–300 ms after EOT | Exactly one search call (Mumbai) or Pune call cancelled in the correction step; no plan accepted with Pune after correction | CS-04, 08 |
| I-02 | Interrupt during planning | Planner latency 800 ms; correction at +400 ms | Planner completes before/after correction interpretation | Stale planner proposal `rejected_stale` or rebind; no Pune call emitted after correction step | CS-04 |
| I-03 | Interrupt during tool execution | As DSL example | Sweep CORR 400–1400 ms; cancel_semantics all three | Cancel in the CORR step; late Pune result `completed_after_cancel`; FINAL Mumbai | CS-08, 09, 28 |
| I-04 | Interrupt during chain (transitive) | Search consumed; seat map in flight; "Mumbai instead" | Seat map result before/after new search result | Seat map cancelled in CORR step (rule `ANCHOR.CHUNK_TRANSITIVE` or `INV.CANCEL_SAME_STEP`); no seat map result for old flight consumed or mentioned | CS-07, 08 |
| I-05 | Interrupt immediately before completion | Last result scheduled at t; correction chunk at t−1 ms, t, t+1 ms | Three offsets | t−1 and t: result `completed_after_cancel` or `stale`, no FINAL with old values; t+1: FINAL for old values allowed, then follow-up handled as slot update with new call and new FINAL | CS-03, 09 |
| I-06 | Correction after FINAL | FINAL emitted; user "actually Mumbai" | — | New goal not created (slot_update on completed goal re-activates planning); new search; second FINAL; snapshot updated | CS-17 |
| I-07 | Repeated interruptions | Three corrections Pune→Mumbai→Goa→Chennai within 600 ms during search | Spacing 100/200/300 ms | Final snapshot Chennai; no call emitted with Mumbai or Goa after its superseding chunk; no speech between interruption and final EOT; ≤ 1 ACK per closed turn | CS-08, 16, 28 |
| I-08 | Goal replacement during read | Search in flight; "forget flights, create a ticket about my phone" | — | Search cancelled; `g1` suspended; snapshot intent `create_ticket`; no flight content in subsequent utterances | CS-08, 17 |
| I-09 | Goal replacement then return | I-08 then "okay back to the flights" after ticket created | Search result arrived before replacement vs cancelled | If consumed before replacement: no new search on return (`EXEC.RETAINED_REUSE`); if cancelled: new search | CS-09 |
| I-10 | Localized slot correction | Origin, destination, date set; search consumed; user "make it the 20th"; hotel lookup (depends only on destination) consumed earlier | — | Only date slot ver changes; hotel lookup not re-run; flight search re-run with date 20 | CS-17 |
| I-11 | Backchannel interruption | Search in flight; interruption "mm-hmm" | `cancel_anchor` chunk vs signal | chunk: no cancel; held speech released; signal: cancel + re-issue (`ANCHOR.FALSE_CANCEL` logged) | CS-16 |
| I-12 | Optional constraint addition | Search in flight; "only direct flights" (`nonstop` optional param) | — | Search cancelled; new call with `nonstop: true` | CS-34 |
| I-13 | Abort | Search in flight; "never mind" | With a confirmed booking earlier in goal | Cancel; INFORM; snapshot null intent; effect mentioned when present | CS-08 |
| I-14 | Interruption during write settle window | "book the 6 pm one" EOT; "wait, the 8 pm one" at +150 ms | Offset −100…+600 ms; `settle_ms` 0/300/600 | With 300 ms: 6 pm booking never emitted for offsets ≤ 300; one booking (8 pm) | CS-15 |
| I-15 | Interruption during write in flight | Booking in flight; "wait, don't book that" | cancel_semantics suppress/deliver_anyway | Cancel emitted (`ANCHOR.HOLD_CUE`); suppress → effect `cancelled_inferred`, INFORM; deliver → `completed_after_cancel`, effect `confirmed`, reconcile INFORM | CS-14 |

#### Async races

| ID | Name | Script | Variants | Assertions | Invariants |
|---|---|---|---|---|---|
| R-01 | Stale result | Result for a call whose slot changed by EOT-anchored interpretation (detector cannot parse "the later one") | — | Result `stale`; not mentioned; new call | CS-09 |
| R-02 | Duplicate result | Mock `duplicate_delivery: true` | Duplicate at +0 / +500 ms | Second `duplicate_ignored`; one PROGRESS/FINAL | CS-10 |
| R-03 | Out-of-order results | Two parallel reads; second-emitted returns first; dependent step joins | — | Join after both; correct args | CS-11 |
| R-04 | Delayed cancellation | Mock `cancel_semantics: deliver_anyway`, extra delay 5 s | cancel_ack `too_late` present/absent | `completed_after_cancel`; no consumption; no deadlock | CS-09 |
| R-05 | Completion/cancellation race | Result and correction chunk at identical `ts_us` | Also result 1 ms before | Equal ts: correction applied first, result not consumed; 1 ms before: result consumed then invalidated, no FINAL with old values if frame dropped in same step | CS-03, 09 |
| R-06 | Simultaneous events | Same-ts pairs: EOT+interruption; chunk+result; manifest+chunk; timer+result | — | Order per §3.8; decision record batch order matches | CS-03 |
| R-07 | Stale worker result | Interpretation for prefix dispatched; goal replaced before return | — | `rejected_stale`; no facts from it | CS-04, 19 |
| R-08 | Unknown call_id | Harness sends result for `c-9999` | — | `unmatched_ignored`; no crash | CS-11 |
| R-09 | Late original after retry | Read deadline; retry `c-0003`; original `c-0002` result arrives later | — | Original `superseded_ignored`; retry consumed | CS-10 |

#### Tool safety

| ID | Name | Script | Variants | Assertions | Invariants |
|---|---|---|---|---|---|
| S-01 | State-changing retry | Booking error | `retryable` true / false / absent | true: one retry (new call_id), one confirmed effect; false/absent: no retry, INFORM `effect_failed` | CS-13 |
| S-02 | Write timeout | Booking result dropped | — | Effect `unknown`; no re-issue; INFORM + CLARIFY; user "yes try again" → one retry | CS-13, 14 |
| S-03 | Duplicate request | After confirmed booking, user "book it" again | — | No second call; INFORM already booked | CS-13 |
| S-04 | Paraphrase duplicate | Booking via "the 6 am one" confirmed; later "book AI-505" | — | Same fingerprint (identifier from result); blocked | CS-13, 29 |
| S-05 | Fabricated identifier | Planner script binds `flight_id` literal `source: model` "XX-999" | — | Plan step error `model_identifier`; replan (script 2 uses step_output); no call with XX-999 | CS-29 |
| S-06 | Malformed manifest | Tools: missing parameters; invalid schema; missing mutability; duplicate name | — | Quarantine report as specified; missing mutability → write (`defaulted`), gate applies; other tools usable | — |
| S-07 | Unseen tool | Manifest with `reserve_lab_slot(lab_id, date, duration_minutes)` write, `list_lab_slots(date)` read; scripted planner uses them | — | Complete task; no tool names in `src/` (static) | — |
| S-08 | Manifest update | Second manifest removes `get_seat_map` while its step pending; one call in flight | — | Step invalid; replan; in-flight result still routed | — |
| S-09 | Completed-after-cancel write with compensation | Deliver-anyway booking; catalog has `modify_booking` | With/without `modify_booking` | With: `modify_booking` call; without: INFORM + CLARIFY; never a second `book_flight` with same lineage in flight | CS-14 |
| S-10 | Settle catches correction | Same as I-14 at +150 ms | — | Covered in I-14; this ID asserts claims: ACK uses `intended`, never `in_progress` for the waiting write | CS-06, 15 |

#### Multimodal

| ID | Name | Script | Variants | Assertions | Invariants |
|---|---|---|---|---|---|
| M-01 | Audio/transcript disagreement | WAV says "Wednesday"; text chunk says "Tuesday"; booking | `asr_disagreement` log / clarify_if_write_slot | log: Tuesday used, disagreement metric 1; clarify: CLARIFY before booking | CS-26 |
| M-02 | Partial transcript | Chunks "book a flight to Pune", "on Friday", "please"; EOT | Tail "please" / tail "no, Goa" | Inert tail: promoted (`promoted: inert_tail`), no extra interpret job; correction tail: re-interpretation, destination Goa | CS-30 |
| M-03 | Stale frame (lease expiry) | Lookup with `current_state` claim; 6 new frames show different indicator before FINAL | Renewal agrees / disagrees | FINAL waits for renewal or hedges; disagree → claim updated, lookup re-run, FINAL describes new state | CS-25 |
| M-04 | Frame after reasoning | Plan needs `indicator` from frames; first frame arrives after planning | Frame 500/2000 ms after plan | Lookup emitted only after claim accepted | CS-25 |
| M-05 | Frame gap before write | Ticket creation needs `current_state` device state; no frames for 5 s | — | CLARIFY request for fresh view; no write | CS-25, 26 |
| M-06 | Conflicting visual/text evidence | User "Tab A"; frame high "Tab S9"; lookup read | — | Conflict opened; CLARIFY names both; lookup not emitted until answered | CS-26 |
| M-07 | Multimodal cancellation | Vision job running; goal replaced | — | Job aborted; proposal `rejected_aborted`; no claim facts; no utterance about device | CS-19 |
| M-08 | Deictic alignment | Frames f1 (device A) at 1.0 s, f2 (device B) at 3.0 s; "this one" chunk at 1.5 s | — | Analyzed frame f1 | — |
| M-09 | Ambiguous perception | Vision returns `low` for required indicator | — | CLARIFY; no lookup | — |
| M-10 | Audio self-repair | ASR "Tuesday, uh, I mean Wednesday" | — | Slot date = Wednesday | — |

#### Timing and protocol

| ID | Name | Script | Variants | Assertions | Invariants |
|---|---|---|---|---|---|
| T-01 | Virtual-clock ordering and replay | Scenario with same-ts events of all classes | 100 replays | Batch order correct; `decisions.jsonl` and `actions.jsonl` identical across runs (excluding wall fields) | CS-03, 22 |
| T-02 | Adversarial sweeps | I-03, I-04, I-05, I-14, R-05, M-03 sweeps | All offsets; Model A and B | `invariants_clean` for every instance | All |
| T-03 | Randomized schedules | Every scenario family | 500 PCT schedules each (depth 2), seeds fixed | Zero violations; failing cases minimized and saved under `scenarios/regressions/` | All |
| T-04 | Metric correctness | Synthetic run artifacts with known timings | — | Each §10.3 metric equals expected value exactly | — |
| T-05 | Timer liveness | Model B, `harness_advances_idle: false`, cancel suppression, write blocked on cancel inference | — | `no_deadlock`; `liveness_fires ≥ 1`; write proceeds | CS-20 |
| T-06 | Model A latency with promotion | Model A, step cost 1 ms, interpret 300 ms, chunks every 150 ms for 5 chunks | — | TTFS(EOT) ≤ 1 ms when exact or inert-tail promotion; ≤ 301 ms otherwise | — |
| T-07 | Watchdog | Real driver with fake slow planner (wall) | — | FINAL emitted before 120 s; `watchdog` rule logged | — |
| T-08 | No wall clock in core | Static | — | Pass | CS-21 |
| P-01 | Malformed worker output | Planner returns invalid JSON twice | — | No malformed action emitted; INFORM; `WorkerOutputInvalid` logged | — |
| P-02 | Malformed input | Random byte payloads, missing ts, unknown type (Hypothesis fuzz, 1000 cases) | — | No crash; `malformed_input` logged | — |
| P-03 | Cancel target validity | Force cancel intent for terminal call (unit harness) | — | Removed at gate | CS-33 |
| P-04 | Snapshot validity | Every scenario | — | Snapshot keys ⊆ parameter names; types match schema; revisions monotonic | CS-17, 27 |
| P-05 | Claim overlay | Unit: ACK + tool_call in one step where tool_call fails validation | — | ACK re-rendered as `intended` or removed | CS-06 |

### 12.4 Unit test inventory (minimum)

| Module | Tests |
|---|---|
| `canonical` | Canonical JSON stability; normalization per type/format; enum spelling; JSONPath subset; digest equality |
| `store/facts` | Version bump rules; no-op on equal digest; retraction; validity with absent keys |
| `store/depindex` | Register/unregister; absent keys; dependents |
| `store/catalog` | Every quarantine reason; mutability table; upgrade heuristic; versioning; removal |
| `store/txn` | Guard |
| `kernel/ordering` | Class order; ties |
| `kernel/invalidation` | Transitive retraction depth 3; fixpoint; unretraction |
| `kernel/detector` | Each extractor HIGH/non-HIGH boundaries; negation; inert tail; backchannel_only |
| `kernel/binder` | Each binding type; absence entries; identifier classification; fingerprint |
| `kernel/commit` | Each G condition in isolation; settle timer scheduling |
| `kernel/results` | Every router branch |
| `kernel/retry` | Table §5.9 |
| `kernel/frames` | Validation rules 1–7; each operator; formats; branch selection |
| `kernel/responder` | ACK conditions; claim grades; budgets |
| `kernel/perception` | Alignment; coalescing; leases; conflicts |
| `kernel/emission` | Overlay; order; re-render; monitors |
| `kernel/snapshot` | Projection rules; carriers |
| `kernel/timers` | Due; liveness |
| `workers/*` | Schema validation; view isolation; scripted provider |
| `observability/metrics` | T-04 |

### 12.5 Exploration specification (`sim/explorer.py`)

| Strategy | Choice points | Budget |
|---|---|---|
| `sweep` | Scenario `variants.sweep` definitions | All offsets |
| `pct` | Worker latency per job sampled from `[0.5×, 2×]` of scripted latency; mock tool latency `[0.5×, 1.5×]`; at `d = 2` random change points, priorities of pending simulator items are reordered among items within 50 ms of each other | 500 schedules per scenario |
| `mutate` | Insert a correction chunk copied from the scenario at a random offset; duplicate a tool result; drop a result; move EOT ±300 ms; insert backchannel interruption | 200 per scenario |

Oracle: `checker.check_run` plus the scenario's `invariants_clean` (other scenario assertions are not applied to mutated instances). Failing instances are minimized with `ddmin` over the list of events and choice-point values, and written to `scenarios/regressions/{scenario_id}_{hash}.yaml` with `expect: [invariants_clean: true]`.

---

## 13. Implementation order

Each phase lists files, prerequisites, acceptance criteria, and tests that must pass before the next phase starts.

### Phase 0 — Repository and tooling

- **Files:** `pyproject.toml`, `Dockerfile`, `Makefile`, `.importlinter`, `config/*.yaml` (skeletons), `tools/check_clock_usage.py`, `tests/static/*`, `README.md` (setup section), `src/prism_rt/__init__.py`, `config.py`, `errors.py`.
- **Prerequisites:** none.
- **Acceptance:** `make test` runs; Docker image builds on Python 3.11; config loads with defaults.
- **Tests:** `static/test_imports` (empty packages), `static/test_clock_usage`, `unit/test_config`.

### Phase 1 — Models, canonicalization, IDs

- **Files:** `model/*`, `canonical.py`, `ids.py`.
- **Prerequisites:** Phase 0.
- **Acceptance:** Every JSON example in §2 round-trips through its pydantic model; worker output schemas in `workers/schemas.py` equal the pydantic-generated schemas (or are asserted equivalent by test).
- **Tests:** `unit/model/test_roundtrip`, `unit/canonical/*`, `unit/test_ids`.

### Phase 2 — Store

- **Files:** `store/*`.
- **Prerequisites:** Phase 1.
- **Acceptance:** All mutation methods in §4.3; catalog parser complete (§5.1–5.3).
- **Tests:** `unit/store/*` including every catalog quarantine case; S-06 manifest fixture parsed (unit level).

### Phase 3 — Event engine, clock, timers, logging, simulator core

- **Files:** `adapters/clock.py`, `adapters/codec.py` (ICW only), `adapters/output_writer.py`, `kernel/driver.py`, `kernel/step.py` (phases with empty decide components), `kernel/ordering.py`, `kernel/reducers.py` (manifest, turn open/close, tool_result placeholder), `kernel/timers.py`, `observability/decision_log.py`, `observability/run_writer.py`, `sim/scenario.py`, `sim/simulator.py`, `sim/mock_tools.py`, `sim/fake_workers.py`, `sim/checker.py` (phase-order and replay rules), `tools/run_scenario.py`, `tools/replay.py`.
- **Prerequisites:** Phase 2.
- **Acceptance:** Simulator runs a scenario that only contains events and produces all run artifacts; Model A and B clocks; replay identity.
- **Tests:** `unit/kernel/test_ordering`, `unit/kernel/test_timers`, `unit/kernel/test_phase_order`, T-01 (events only), T-05 (timer skeleton with a synthetic timer).

### Phase 4 — Tool engine and emission (no model workers; plans injected by test)

- **Files:** `kernel/binder.py`, `kernel/executor.py` (without rebind), `kernel/commit.py`, `kernel/results.py`, `kernel/retry.py`, `kernel/reconcile.py`, `kernel/invalidation.py` (non-transitive), `kernel/emission.py`, `kernel/monitors.py`, `kernel/snapshot.py`, `store/effects.py` wiring, cancellation planner.
- **Prerequisites:** Phase 3.
- **Acceptance:** With plans and interpretations injected through a test hook (`integration/inject.py`), read and write calls are emitted, routed, retried, cancelled; gate conditions G1–G9, G12 enforced (G10–G11 stubbed true until Phase 6).
- **Tests:** `unit/kernel/test_binder`, `test_commit` (G1–G9, G12), `test_results`, `test_retry`, `test_emission`, `test_snapshot`; integration versions of R-02, R-03, R-08, R-09, S-01, S-02, S-03, S-06, S-08; P-03, P-04, P-05.

### Phase 5 — Conversation and fast path (scripted interpreter and planner)

- **Files:** `kernel/turns.py`, `kernel/detector.py`, `kernel/interpret_apply.py`, `kernel/task.py`, `kernel/responder.py`, `kernel/clarify.py`, `kernel/scheduler.py` (basic dispatch, no slots), `workers/runner.py`, `workers/providers/scripted_provider.py`, `config/lexicons/en.yaml`, `config/templates/en.yaml`.
- **Prerequisites:** Phase 4.
- **Acceptance:** Full scenario runs with scripted workers; task state machine transitions logged; ACK/PROGRESS/CLARIFY/FINAL (composer template fallback) produced.
- **Tests:** N-01, N-03, N-04 (settle stubbed), I-01, I-03 (chunk anchor), I-06, I-08, I-13, R-01, R-06, `unit/kernel/test_detector`, `test_responder`, `test_task`.

### Phase 6 — Complete validity and settled commits (INNOV M2 facts part, M3)

- **Files:** `kernel/invalidation.py` (transitive retraction, unretraction), `kernel/binder.py` (absence entries, identifier rule), `kernel/executor.py` (rebind, commit-last, cutoff reuse, retained reuse), `kernel/commit.py` (G10, G11), `kernel/task.py` (triage holds, fallback, return to goal), `kernel/timers.py` (liveness), `kernel/emission.py` (claim grades fully).
- **Prerequisites:** Phase 5.
- **Acceptance:** Transitive cancellation in the same step; settle blocks; holds released only when valid; liveness under Model B without idle advance.
- **Tests:** I-02, I-04, I-05, I-07, I-09, I-10, I-11, I-12, I-14, I-15, R-04, R-05, R-07, S-04, S-05, S-09, S-10, T-05, `unit/kernel/test_transitive_retraction`, `test_commit_last`, `test_inert_tail` (detector part).

### Phase 7 — Model-backed workers

- **Files:** `workers/gateway.py`, `providers/anthropic_provider.py`, `providers/openai_compat_provider.py`, `providers/cassette.py`, `workers/interpreter.py`, `planner.py`, `composer.py`, `prompts/interpret.md`, `plan.md`, `compose.md`, `config/models.default.yaml`, `entry.py` (setup with health check).
- **Prerequisites:** Phase 6.
- **Acceptance:** Scenarios N-01, N-02, N-03, N-04, I-03, I-08 pass with live models in `record` mode and then in `replay` mode; schema-invalid outputs handled.
- **Tests:** P-01; the listed scenarios with `workers.mode: live` (manual gate) and `replay` (CI).

### Phase 8 — Prepared reactions (INNOV M1)

- **Files:** `kernel/scheduler.py` (reaction slots, priorities), `kernel/turns.py` (inert-tail promotion), `kernel/frames.py`, `workers/framer.py`, `prompts/frame.md`, speculative planning in `executor.py`.
- **Prerequisites:** Phase 7.
- **Acceptance:** Frames render FINALs; promotion (exact and inert tail) reduces post-EOT interpretation jobs; speculative plans promoted.
- **Tests:** N-02 (frame path), M-02, T-06, `unit/kernel/test_frames`, `test_frame_literal`, promotion-disagreement offline check on recorded runs (must be 0).

### Phase 9 — Multimodal

- **Files:** `kernel/perception.py`, `store/evidence.py` completion, `workers/vision.py`, `workers/asr.py`, `prompts/vision.md`, `store/blobs.py`, codec media decoding.
- **Prerequisites:** Phase 8.
- **Acceptance:** Questions, alignment, leases, renewals, conflicts, ASR modes per §9.
- **Tests:** N-05, N-06, M-01 to M-10, `unit/kernel/test_perception`.

### Phase 10 — Exploration and metrics

- **Files:** `sim/explorer.py`, `sim/minimize.py`, `sim/report.py`, `observability/metrics.py`, `tools/explore.py`, `tools/report.py`, `docs/measurements.md` template.
- **Prerequisites:** Phase 9.
- **Acceptance:** Sweeps and PCT run over all families; regressions saved; metrics computed.
- **Tests:** T-02, T-03, T-04; all previously passing tests.

### Phase 11 — Kit integration [KIT]

- **Files:** `adapters/codec.py` (`KitMapping`), `adapters/harness_io.py` (`KitHarnessIO`), `adapters/clock.py` (select model), `config/policies.default.yaml` (kit answers), `docs/kit_mapping.md`, `entry.py` final wiring, `Dockerfile` final, README run instructions.
- **Prerequisites:** Phase 10 and kit release.
- **Acceptance:** Public suite runs end to end in the kit harness; every public trace passes `checker.check_run` after mapping kit traces into ICW; policies updated per Appendix A answers; scenario fixtures updated to match kit schemas; all tests rerun.
- **Tests:** full suite; public suite; T-02/T-03 re-run with kit-derived mock latencies and cancellation semantics.

---

## 14. Definition of done

The implementation is complete when every item below holds on the tagged commit.

**Functional**

1. All tests in §12.3 and §12.4 pass under Model B (`harness_advances_idle` true and false) and Model A in the simulator, with `strict_monitors = true`.
2. T-02 sweeps and T-03 exploration (500 PCT + 200 mutation schedules per scenario) report zero invariant violations, with worker outputs in replay mode.
3. The public suite (after kit release) runs to completion for all nine scenarios in the kit harness without crashes, timeouts, or `SAFE_MODE`, and `checker.check_run` reports zero violations on each mapped trace.
4. Replay identity (T-01) holds for every scenario in the suite.

**Safety and protocol**

5. Across all simulator runs: zero duplicate write fingerprints (outside permitted failed/cancelled cases), zero emitted actions failing schema validation, zero false claims, zero stale consumptions, zero omission escapes, zero stale-evidence groundings for writes.
6. Static checks pass: import rules, no wall clock in core, no mutable module globals, no fixture tool names in `src/`.

**Timing (simulator, fixed parameters)**

7. Model A with step cost 1 ms, interpret latency 300 ms, chunk spacing 150 ms, ≥ 3 chunks: TTFS(EOT) p50 ≤ 1 ms across N-01, I-03, M-02 variants with promotion; evidence-anchored cancellation latency p95 ≤ 1 ms for chunk-parseable corrections and ≤ 301 ms for others.
8. Model B: evidence-anchored cancellation latency = 0 for all cancellations in I-03, I-04, I-07, I-12.
9. First substantive speech latency is identical with `settle_ms = 0` and `settle_ms = 300` in N-04 and I-14 (settle does not affect speech latency).

**Operational**

10. `setup` completes in under 300 s on the target container with ASR model loaded; each public scenario completes within 120 s wall time.
11. Docker image builds from a clean checkout; `README.md` gives a reproducible command to run the public suite and the simulator suite.
12. `docs/measurements.md` contains the INNOV §6.3 table populated from simulator runs (baseline configuration obtained by policy flags disabling C1, C2, C3, C5 transitive rule, C6 absence entries, C7 leases, C9 settle, C10 identifier rule).
13. Repository tagged `PRISM_GENAI_HACKATHON_Y2026` on the final commit with all referenced documents present.

---

## 15. Implementation Contract for Sonnet

### 15.1 Architecture that must not be changed casually

- Single-writer kernel with an eight-phase synchronous step (§3.4); all mutation through `StoreTxn`.
- Batch ordering by `(ts_us, class, seq)` with the class table in §3.3.
- Versioned fact store with read sets, value-digest validity, transitive retraction, and absence entries.
- Workers receive copied views and return proposals; they never import `store` or `kernel`.
- Tool results arrive as events; no per-call tasks.
- CommitGate G1–G12 and the write-ahead effect ledger.
- Two-phase emission with claim grades and online monitors.
- Harness time only, through `ClockPort`; timer liveness rule.
- Snapshot as projection at emission.
- Goals as facts (`goal.active`); suspension instead of deletion.
- Question-driven perception with leases; no continuous frame analysis.
- Response frames with holes; composer as fallback.

### 15.2 Invariants that must never be violated

CS-01 through CS-34 (§11). In particular:

1. No state mutation outside a kernel step.
2. No action emitted with an invalid read set.
3. An invalidated in-flight call is cancelled in the same step.
4. A cancelled call's result is never consumed.
5. No write without a prior `pending` effect record, commit intent, closed floor, settle interval, and passing G1–G12.
6. No second write with a blocking fingerprint or lineage.
7. No claim beyond what the ledger supports after the batch.
8. No wall-clock reads in core modules.
9. No speech while the user's turn is open.
10. No session state across scenarios.

### 15.3 Required interfaces

- `entry.setup`, `Runtime.run_scenario`.
- `HarnessIO`, `Codec.decode/encode`, `ClockPort`, `OutputWriter.write`.
- `SessionStore`, `StoreTxn` method set (§4.3), `FactStore.is_valid`, `DependencyIndex`.
- `Kernel.step`, `AsyncDriver`, `SimDriver`.
- `WorkerRunner.submit/abort`, `ModelGateway.complete_json`.
- Worker output schemas §2.13–2.16 exactly.
- ICW event and action JSON §2.3–2.4 exactly (kit mapping sits in the codec only).
- `DecisionRecord` §2.17 exactly.
- Scenario DSL §12.2 and assertion vocabulary.

### 15.4 Required tests

All of §12.3 (N, I, R, S, M, T, P families), §12.4 unit inventory, static checks, and exploration gates T-02/T-03. Phase gates in §13 are binding: do not start a phase until its predecessor's listed tests pass.

### 15.5 Known assumptions

| # | Assumption | Default | Revisit |
|---|---|---|---|
| A1 | Clock model | A in real runs; both tested | Kit Q1 |
| A2 | Event timestamps are capture times for audio and frames | Yes | Kit |
| A3 | Text chunks are append-only | `append` | Kit Q11 |
| A4 | Agent assigns `call_id`; gaps allowed | `c-` prefix, allocated at proposal | Kit |
| A5 | Mutability field and values | §5.2 table | Kit Q10 |
| A6 | Snapshot `{intent, slots}` with parameter-named slots; carrier `any_spoken` | — | Kit Q5 |
| A7 | Intent label = primary tool name | `primary_tool` | Kit Q5 |
| A8 | Duplicate = same tool and canonical args | `tool_and_args` | Kit Q3 |
| A9 | Cancelled calls may or may not deliver results; `cancel_ack` optional | Handle all | Kit Q2 |
| A10 | Cancellation grace measured from invalidating evidence | `cancel_anchor = chunk` | Kit Q2 |
| A11 | Latency trigger | All three measured | Kit Q4 |
| A12 | Audio scenarios may lack transcripts | `audio_mode = auto` | Kit Q8 |
| A13 | Frame-accepting tool parameters detectable from schema | §9.4 rules | Kit Q9 |
| A14 | Null-intent snapshot is valid after abort | Emit | Kit |
| A15 | Hosted model APIs reachable | `anthropic` with fallback | Kit Q14 |
| A16 | English lexicons suffice | `en` | Scenarios |
| A17 | Reference date provided in scenario metadata | `none` if absent | Kit Q15 |

### 15.6 Local decisions Sonnet may make

- Internal helper functions, private module structure within a listed file, and splitting a listed file into submodules if its public interface is preserved.
- Data structure choices inside stores (dicts, sorted lists, heaps) meeting determinism (sorted iteration where order affects output).
- Prompt wording that satisfies the requirements in §7.3–7.7.
- Template phrasing and additional template variants, provided claim grades and truth checks hold.
- Lexicon additions (never removal of listed entries without a test showing the need).
- Logging detail beyond the required `DecisionRecord` fields.
- Performance optimizations that preserve step semantics and replay identity.
- Test fixture content (scenario wording, mock data) consistent with §12.3 intent.
- Default numeric parameters within ±50% when a test demonstrates the need (record in `docs/measurements.md`).
- Provider and model names in `models.default.yaml`.

### 15.7 Stop and ask for architectural clarification when

1. Any invariant in §11 appears to require relaxation to pass a test or a kit scenario.
2. The kit's protocol cannot be expressed through the codec alone (e.g., it requires synchronous tool calls, speech streaming with interruption of partial utterances, or agent-side timestamps that contradict `ClockPort` semantics).
3. The kit's clock semantics match none of Models A, B, C.
4. The kit defines duplicates, snapshots, or cancellation outcomes in a way that conflicts with CommitGate or the effect ledger.
5. A change would require workers to read or write the store, the kernel to await, or a second writer of state.
6. A decide-phase order change seems necessary.
7. Exploration finds a violation that cannot be fixed without changing a rule in §4–§9.
8. Latency criteria in §14 cannot be met without moving model calls into the kernel step.
9. A new tool category (e.g., tools with streaming results, tools requiring user authentication mid-call) appears in the kit.
10. Any requirement in the official Theme 05 guide appears unaddressed by this blueprint.
