"""Metrics: a read-only aggregation layer over a run's `StepReport` trace
(`docs/theme05_implementation_blueprint.md` §10.3, T-04). Same calling
convention as `sim/checker.py.TraceChecker.check(reports, store=None)` —
`reports: list[kernel.step.StepReport]`, `store` optional for the handful
of metrics that need it.

The blueprint's own §10.3 assumes a three-file `events.jsonl`/
`actions.jsonl`/`decisions.jsonl` artifact layout this project never built
(`observability/decision_log.py` is a JSONL *summary*, not the primary
trace — see its own docstring: "the harness trace shows what happened;
this shows why"). This module reads the richer, actually-existing
in-memory trace (`RunLog.reports`, i.e. `list[StepReport]`) instead, which
carries everything the JSONL layout would have (full `Action` bodies,
`Envelope` payloads) and more.

Every function here is a pure read: it never mutates `reports`, `store`,
or anything reachable from them, and calling it twice on the same input
returns equal (`==`) results (`tests/test_metrics.py`'s own reproducibility
tests hold each function to exactly this standard). Nothing here reads a
wall clock -- every timestamp is `ts_us` already present on the trace.

Implemented, from §10.3's table:
- `substantive_actions` -- the shared "substantive action" classifier
  every other metric below is built on.
- `ttfs` -- Time To First Substantive response, per trigger type
  (`end_of_turn`, `interruption`, `first_chunk`), matching §10.3's
  definition exactly (this is the same metric `docs/measurements.md`'s
  Phase 6 entry computed once, by hand, in a throwaway script -- this
  module makes it a real, reusable, tested function; see
  `tests/test_metrics.py`'s cross-check against that exact number).
- `tool_latency` -- `result.ts - call.emitted_ts` per call, grouped by
  (tool, status). Derivable purely from the trace: `ToolCallBody` already
  carries `tool_name`, and a matching `tool_result` envelope carries
  `status`.
- `interruption_to_cancellation_latency` -- the *signal-anchored* half
  only (`cancel.ts - most recent preceding interruption.ts`), plus the
  blueprint's own explicit safety count ("calls invalidated but never
  cancelled before their result -- must be 0"). The *evidence-anchored*
  half is a documented limitation, not implemented -- see below.
- `end_to_end_latency` -- both of §10.3's definitions, computed per FINAL
  action against turn boundaries inferred structurally from the envelope
  stream (see the function's own docstring for why this sidesteps needing
  `goal_id` on `Action`, which the trace schema doesn't carry).

Deliberately NOT implemented, with reasons (per this project's own
established practice of naming a scope trim rather than silently skipping
it or inventing a proxy -- see `sim/checker.py`'s docstring for the same
pattern applied to CS-15/16/24/26/30):
- **Evidence-anchored cancel latency** and **cancel delay for transitive
  dependents**: both need, for a specific invalidated call, which single
  fact-changing envelope caused *that call's* invalidation. `StepReport.
  invalidation` records only an aggregate per-step `changed_keys`/
  `invalidated_call_ids` -- when a step's batch contains more than one
  fact-changing envelope, or invalidates more than one call, the trace
  doesn't preserve a specific envelope-to-call causal link. Building this
  soundly needs new instrumentation (recording *which* key invalidated
  *which* call, not just the two aggregate sets), the same class of gap
  `checker.py` already documents for CS-15/CS-26.
- **Event-anchored ratio**, **state-update timing**: not implemented this
  session for time reasons, not because they're undeliverable -- both
  look derivable from the existing trace (event_class on each envelope;
  slot-fact changes are already in `change_set`) and are reasonable next
  additions to this module, not a new one.
- **"Others" (INNOV §6.2) grab-bag** (stale consumptions, omission
  escapes, false claims, duplicates, reconciliations, write exposure
  window, promotion hit/disagreement, speculative waste): each of these is
  really its own metric needing its own definition matched against this
  project's specific mechanisms; building all of them in one pass is
  exactly the "giant observability framework" this module is deliberately
  not trying to be. Left for a future, scoped addition per metric, not
  attempted here. **Liveness fires** (P0.1, `docs/original_design_audit.md`
  C9c) is the one exception, added alongside the mechanism itself since a
  liveness fire is directly countable straight off the trace's own
  envelope stream -- see `liveness_fire_count` below.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from prism_rt.model.actions import CancelBody, FinalBody, SpeakBody, ToolCallBody
from prism_rt.model.types import ActionType

_SUBSTANTIVE_SPEAK_KINDS = frozenset({"ack", "inform"})
# The blueprint's own qualifier ("...and at least one claim of grade !=
# question") has no analog in this codebase's `ClaimGrade` (UNDERSTOOD /
# INTENDED / IN_PROGRESS / RESULT / EFFECT_DONE -- no "question" grade
# exists; a genuine question is its own action type, CLARIFY, handled
# separately below), so every ack/inform SPEAK qualifies.

_TRIGGER_TYPES = ("end_of_turn", "interruption", "first_chunk")
_CONTENT_PAYLOAD_TYPES = frozenset({"text_chunk", "audio_clip"})


def _percentile(values: list[int], p: float) -> int | None:
    """Nearest-rank percentile over a *pre-sorted-ascending* list. Pure,
    deterministic, no external dependency (this project has none besides
    pydantic/PyYAML/pytest) -- the exact method matters less than that it
    never changes between two calls on the same input, which it can't,
    since it does nothing but index."""
    if not values:
        return None
    n = len(values)
    rank = max(1, min(n, math.ceil(p / 100 * n)))
    return values[rank - 1]


@dataclass(frozen=True)
class LatencyStats:
    count: int
    p50: int | None
    p95: int | None
    max: int | None


def _stats(values: list[int]) -> LatencyStats:
    ordered = sorted(values)
    return LatencyStats(
        count=len(ordered),
        p50=_percentile(ordered, 50),
        p95=_percentile(ordered, 95),
        max=ordered[-1] if ordered else None,
    )


@dataclass(frozen=True)
class SubstantiveAction:
    step_no: int
    ts_us: int
    action_type: str
    kind: str | None


def substantive_actions(reports: list) -> list[SubstantiveAction]:
    """§10.3's own definition, adapted to this codebase's actual `kind`
    vocabulary (see the module docstring): a SPEAK with kind in
    {ack, inform}; or a CLARIFY; or a FINAL. `hold`/`progress`/`repair`
    never appear in this codebase (V1's own docstring: not implemented),
    so there is nothing to exclude for them."""
    out: list[SubstantiveAction] = []
    for report in reports:
        for er in report.emit_report.emitted:
            action = er.action
            if action.action_type == ActionType.SPEAK:
                body = action.body
                if isinstance(body, SpeakBody) and body.kind in _SUBSTANTIVE_SPEAK_KINDS:
                    out.append(SubstantiveAction(report.step_no, action.ts_us, action.action_type.value, body.kind))
            elif action.action_type in (ActionType.CLARIFY, ActionType.FINAL):
                kind = action.body.kind if isinstance(action.body, SpeakBody) else None
                out.append(SubstantiveAction(report.step_no, action.ts_us, action.action_type.value, kind))
    return out


@dataclass(frozen=True)
class Trigger:
    trigger_type: str
    step_no: int
    ts_us: int


def _find_triggers(reports: list) -> list[Trigger]:
    """`end_of_turn` and `interruption` triggers fire once per matching
    envelope. `first_chunk` fires once per turn -- the first `text_chunk`/
    `audio_clip` envelope seen since the start of the trace or since the
    last `end_of_turn`, tracked structurally from the envelope stream
    itself (no `turn_id` needed): `report.batch` is already ordered
    (ts_us, class, seq) within a step (CS-03), and reports are appended in
    non-decreasing step order, so a single forward pass is sound."""
    triggers: list[Trigger] = []
    turn_open = False
    for report in reports:
        for env in report.batch:
            if env.payload_type == "end_of_turn":
                triggers.append(Trigger("end_of_turn", report.step_no, env.ts_us))
                turn_open = False
            elif env.payload_type == "interruption":
                triggers.append(Trigger("interruption", report.step_no, env.ts_us))
            elif env.payload_type in _CONTENT_PAYLOAD_TYPES:
                if not turn_open:
                    triggers.append(Trigger("first_chunk", report.step_no, env.ts_us))
                turn_open = True
    return triggers


@dataclass(frozen=True)
class TTFSTriggerResult:
    trigger_count: int
    miss_count: int
    stats: LatencyStats


@dataclass(frozen=True)
class TTFSResult:
    per_trigger: dict[str, TTFSTriggerResult]


def ttfs(reports: list) -> TTFSResult:
    """Time To First Substantive response (§10.3). For each trigger `E`:
    `min(A.ts_us - E.ts_us)` over substantive actions `A` with
    `A.ts_us >= E.ts_us` and `A.ts_us < ts of the next trigger of the same
    type` (or +inf if `E` is the last of its type). No qualifying action
    in that window: a miss."""
    triggers = _find_triggers(reports)
    actions = substantive_actions(reports)
    action_ts = sorted(a.ts_us for a in actions)

    per_trigger: dict[str, TTFSTriggerResult] = {}
    for ttype in _TRIGGER_TYPES:
        same_type = [t for t in triggers if t.trigger_type == ttype]
        hits: list[int] = []
        misses = 0
        for i, trig in enumerate(same_type):
            window_end = same_type[i + 1].ts_us if i + 1 < len(same_type) else None
            best = None
            for ts in action_ts:
                if ts < trig.ts_us:
                    continue
                if window_end is not None and ts >= window_end:
                    break
                candidate = ts - trig.ts_us
                if best is None or candidate < best:
                    best = candidate
            if best is None:
                misses += 1
            else:
                hits.append(best)
        per_trigger[ttype] = TTFSTriggerResult(
            trigger_count=len(same_type), miss_count=misses, stats=_stats(hits)
        )
    return TTFSResult(per_trigger=per_trigger)


@dataclass(frozen=True)
class ToolLatencyKey:
    tool: str
    status: str


def tool_latency(reports: list) -> dict[ToolLatencyKey, LatencyStats]:
    """`result.ts - call.emitted_ts` per call, grouped by (tool, status).
    Purely trace-derivable: a `TOOL_CALL` action's own `ToolCallBody`
    carries `tool_name`; a matching `tool_result` envelope (by `call_id`)
    carries `status`. `call.emitted_ts` is the emitting step's `now_us`,
    i.e. the `TOOL_CALL` action's own `ts_us` (`kernel/emission.py`'s
    `_apply_side_effects` sets `emitted_ts_us=now_us` to the identical
    value at the identical moment)."""
    dispatched: dict[str, tuple[str, int]] = {}  # call_id -> (tool_name, emitted_ts_us)
    for report in reports:
        for er in report.emit_report.emitted:
            if er.action.action_type == ActionType.TOOL_CALL:
                body = er.action.body
                assert isinstance(body, ToolCallBody)
                dispatched[body.call_id] = (body.tool_name, er.action.ts_us)

    samples: dict[ToolLatencyKey, list[int]] = {}
    for report in reports:
        for env in report.batch:
            if env.payload_type != "tool_result":
                continue
            call_id = getattr(env.payload, "call_id", None)
            status = getattr(env.payload, "status", "ok")
            if call_id is None or call_id not in dispatched:
                continue
            tool_name, emitted_ts = dispatched[call_id]
            key = ToolLatencyKey(tool_name, status)
            samples.setdefault(key, []).append(env.ts_us - emitted_ts)

    return {key: _stats(values) for key, values in samples.items()}


@dataclass(frozen=True)
class CancelLatencyResult:
    signal_anchored: LatencyStats
    invalidated_never_cancelled_count: int


def interruption_to_cancellation_latency(reports: list) -> CancelLatencyResult:
    """Signal-anchored half of §10.3's "Interruption-to-cancellation
    latency": for each CANCEL action, `cancel.ts_us - ts_us of the most
    recent preceding interruption envelope` (CANCELs with no preceding
    interruption in the trace -- e.g. a plain correction with no explicit
    interruption event -- are excluded from this specific stat, since
    it's defined relative to a signal that may not exist; they are still
    counted correctly by every other metric in this module).

    Also returns the blueprint's own explicit safety count: calls that
    were invalidated (`StepReport.invalidation.invalidated_call_ids`) but
    never received a matching CANCEL before their result was consumed --
    the blueprint states this "must be 0"; this function reports the
    actual count rather than asserting it, so a caller (a test, or this
    module's own future checker integration) can decide what to do with a
    nonzero value."""
    last_interruption_ts: int | None = None
    signal_deltas: list[int] = []
    invalidated_ever: set[str] = set()
    cancelled_ever: set[str] = set()

    for report in reports:
        for env in report.batch:
            if env.payload_type == "interruption":
                last_interruption_ts = env.ts_us

        invalidated_ever.update(report.invalidation.invalidated_call_ids)

        for er in report.emit_report.emitted:
            if er.action.action_type == ActionType.CANCEL:
                body = er.action.body
                assert isinstance(body, CancelBody)
                cancelled_ever.add(body.target_call_id)
                if last_interruption_ts is not None:
                    signal_deltas.append(er.action.ts_us - last_interruption_ts)

    never_cancelled = invalidated_ever - cancelled_ever
    return CancelLatencyResult(
        signal_anchored=_stats(signal_deltas),
        invalidated_never_cancelled_count=len(never_cancelled),
    )


@dataclass(frozen=True)
class EndToEndLatency:
    step_no: int
    final_ts_us: int
    since_first_chunk_us: int | None
    since_end_of_turn_us: int | None


def liveness_fire_count(reports: list) -> int:
    """P0.1 (C9c): how many `timer_fired` envelopes with `liveness_fire`
    set were processed in this trace -- a driver-synthesized step (never
    from an external harness event) that exists purely so a step-scheduled
    timer (e.g. G10's settle wake, `kernel/commit.py`) gets re-checked even
    when nothing else is happening (D1, `docs/original_design_audit.md`).
    Zero across a whole run with no blocked timers is normal, not a bug;
    T-05-shaped scenarios (a write genuinely blocked with no further
    external events) are expected to show `>= 1`."""
    return sum(
        1
        for report in reports
        for env in report.batch
        if env.payload_type == "timer_fired" and getattr(env.payload, "liveness_fire", False)
    )


def end_to_end_latency(reports: list) -> list[EndToEndLatency]:
    """§10.3's two end-to-end definitions, per FINAL action: (a) time
    since the first content envelope of the most recently *opened* turn
    before this FINAL, (b) time since the most recent `end_of_turn`
    before this FINAL. Neither needs `goal_id` (which `Action` doesn't
    carry -- see the module docstring): both are defined purely against
    turn boundaries, which this project's single-active-goal architecture
    makes equivalent to "this goal's own creating/committing turn" for
    every scenario this codebase's own test suite exercises (one goal
    worked at a time). A run with two FINALs for two genuinely
    interleaved, concurrently-active goals -- which this architecture
    doesn't support -- would need `goal_id` on `Action` to disambiguate;
    not attempted here."""
    results: list[EndToEndLatency] = []
    turn_open = False
    last_first_chunk_ts: int | None = None
    last_eot_ts: int | None = None

    for report in reports:
        for env in report.batch:
            if env.payload_type == "end_of_turn":
                last_eot_ts = env.ts_us
                turn_open = False
            elif env.payload_type in _CONTENT_PAYLOAD_TYPES:
                if not turn_open:
                    last_first_chunk_ts = env.ts_us
                turn_open = True

        for er in report.emit_report.emitted:
            if er.action.action_type == ActionType.FINAL:
                assert isinstance(er.action.body, FinalBody)
                results.append(
                    EndToEndLatency(
                        step_no=report.step_no,
                        final_ts_us=er.action.ts_us,
                        since_first_chunk_us=(er.action.ts_us - last_first_chunk_ts) if last_first_chunk_ts is not None else None,
                        since_end_of_turn_us=(er.action.ts_us - last_eot_ts) if last_eot_ts is not None else None,
                    )
                )
    return results
