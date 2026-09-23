"""Bounded adversarial schedule exploration (M4 / C17, `docs/prompt 3.txt`).

The original thesis is "prepare early, check at the point of use, commit
late" -- every one of those is a timing argument, and a timing argument
is only as good as the interleavings it was checked against. Hand-written
scenario tests pick one ordering each. This module takes a scenario (a
fixed script of user events plus tool/worker latencies) and re-runs it
under many *perturbed* schedules -- stretched/compressed gaps between user
events, faster/slower tools, faster/slower model workers -- checking a set
of invariant oracles after every run. `sim/minimize.py` shrinks any
failing schedule back toward the baseline so the surviving deviations
name the race.

Deliberately bounded (the original plan cut the full PCT/mutation engine
as too expensive, `docs/prototype_version_plan.md` §1.D): schedules come
from (a) a seeded random sampler over a small, realistic value grid and
(b) *targeted* perturbations derived from the baseline run -- for every
tool call and every later user event, a tool latency chosen to land that
call's result just before, exactly at, or just after the event. (b) is
what makes this adversarial rather than merely random: the correction-vs-
result, cancel-vs-result and write-vs-correction races the original
analysis names (L1, L5, L14) are exactly "a result landing right next to
a user event."

Everything is deterministic: `random.Random(seed)`, `SteppedClock`,
`ScriptedProvider`. Same scenario + same seed -> same schedules -> same
findings. Nothing here imports anything the kernel doesn't already
expose; oracles re-derive their expectations from store/trace state
rather than calling kernel helpers, so a kernel bug can't hide itself by
also being in the oracle (same principle as `sim/checker.py`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from typing import Callable

from config.templates import INFORM_DUPLICATE_WRITE
from prism_rt.canonical import ABSENT
from prism_rt.config import Config
from prism_rt.model.actions import FinalBody, SpeakBody, ToolCallBody
from prism_rt.model.types import (
    ActionType,
    CallStatus,
    EffectStatus,
    FactStatus,
    FloorState,
    GoalStatus,
    JobKind,
    JobStatus,
    StepKind,
    TaskState,
    ToolMutability,
)
from prism_rt.observability import metrics
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

_SPEECH = (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL)
# Realistic worker-latency grid, including values well above the 300ms
# settle window -- a live model routinely takes longer than that.
_WORKER_LATENCY_GRID_US = (5_000, 10_000, 50_000, 150_000, 400_000, 900_000)
_TOOL_FACTOR_GRID = (0.0, 0.1, 0.5, 1.0, 2.0, 4.0)
_GAP_FACTOR_GRID = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0)


@dataclass(frozen=True)
class TimedEvent:
    ts_us: int
    event: dict


@dataclass(frozen=True)
class Scenario:
    name: str
    config: Config
    tools: dict
    provider: Callable[[], ScriptedProvider]
    events: tuple[TimedEvent, ...]
    worker_latency_us: dict
    tail_us: int = 4_000_000
    # Scenario-specific oracle: (harness, run) -> list of violation strings.
    check: Callable[["SimHarness", "RunRecord"], list[str]] | None = None


@dataclass(frozen=True)
class Schedule:
    event_ts: tuple[int, ...]
    tool_latency_ms: tuple[tuple[str, int], ...]
    worker_latency_us: tuple[tuple[str, int], ...]

    def label(self) -> str:
        return f"events={list(self.event_ts)} tools={dict(self.tool_latency_ms)} workers={dict(self.worker_latency_us)}"


@dataclass
class StepObservation:
    step_no: int
    now_us: int
    goal_before: str | None
    goal_after: str | None
    floor_after: FloorState
    interpretation_pending_after: bool


@dataclass
class RunRecord:
    schedule: Schedule
    violations: list[str] = field(default_factory=list)
    observations: list[StepObservation] = field(default_factory=list)


@dataclass(frozen=True)
class Finding:
    scenario: str
    schedule: Schedule
    violations: tuple[str, ...]


def baseline_schedule(scn: Scenario) -> Schedule:
    return Schedule(
        event_ts=tuple(e.ts_us for e in scn.events),
        tool_latency_ms=tuple(sorted((name, int(cfg.get("latency_ms", 0))) for name, cfg in scn.tools.items())),
        worker_latency_us=tuple(sorted((JobKind(k).value, int(v)) for k, v in scn.worker_latency_us.items())),
    )


# --- oracles: re-derived from state, never by calling kernel helpers -------


def _active_goal(store) -> str | None:
    fact = store.facts.get("goal.active")
    if fact is None or fact.status == FactStatus.RETRACTED:
        return None
    return fact.value


def _interpretation_pending(store) -> bool:
    pending = store.facts.get("session.pending_interpretation_turn")
    if pending is not None and pending.status != FactStatus.RETRACTED:
        return True
    for key, fact in store.facts.by_prefix("spec_interpret.").items():
        if key.endswith(".eot_waiting") and fact.status != FactStatus.RETRACTED and fact.value:
            return True
    # Speech the user produced that hasn't been transcribed yet is user
    # content not yet understood, exactly like an uninterpreted turn
    # (docs/prompt 2.txt transition 23: "user content while a goal is
    # active").
    if store.config.asr_enabled and store.config.audio_mode != "transcript_primary":
        if any(not obs.asr_done for obs in store.evidence.observations_by_modality("audio")):
            return True
    return False


def _step_oracles(h: SimHarness, report, obs: StepObservation) -> list[str]:
    out: list[str] = []
    for er in report.emit_report.emitted:
        a = er.action
        is_speech = a.action_type in _SPEECH
        is_write = a.action_type == ActionType.TOOL_CALL and isinstance(a.body, ToolCallBody) and a.body.mutability == ToolMutability.STATE_CHANGING
        # CS-28 floor rule (docs/prompt 2.txt line 370).
        if is_speech and obs.floor_after == FloorState.USER_TURN_OPEN and not h.config.speak_during_open_turn:
            out.append(f"floor: {a.action_type.value} emitted while floor open (step {report.step_no})")
        # P0.2 (docs/original_design_audit.md D3): "already taken care of"
        # may only be spoken when at least one write's effect is genuinely
        # CONFIRMED -- otherwise it's the exact false-completion claim D3
        # found (a PENDING or UNKNOWN blocker reported as done).
        if (
            a.action_type == ActionType.SPEAK
            and isinstance(a.body, SpeakBody)
            and a.body.text == INFORM_DUPLICATE_WRITE
            and not any(e.status == EffectStatus.CONFIRMED for e in h.store.effect_ledger.all())
        ):
            out.append(f"claim: 'already taken care of' spoken (step {report.step_no}) with no CONFIRMED effect")
        # Triage hold (docs/prompt 2.txt §8.3, transitions 23-31; CS-15's
        # "interpretation pending" clause): while a user turn that arrived
        # during an active goal is still being interpreted, nothing except
        # CANCEL may be emitted for that goal -- the turn might be a
        # correction that makes it stale.
        goal = obs.goal_before
        if goal is not None and obs.interpretation_pending_after and (is_speech or is_write):
            kind = "write" if is_write else a.action_type.value
            out.append(f"triage: {kind} emitted while a user turn during goal {goal} awaits interpretation (step {report.step_no})")
        # Text grounding: the words a FINAL speaks must come from results
        # that were still valid when it was spoken. `goal.active` is
        # excluded -- the FINAL itself clears it in this same step.
        if a.action_type == ActionType.FINAL and goal:
            text_fact = h.store.facts.get(f"compose.{goal}.text")
            grounding = text_fact.provenance.derivation_read_set if text_fact is not None else None
            if grounding is not None:
                for entry in grounding.entries:
                    if entry.key == "goal.active":
                        continue
                    current = h.store.facts.get(entry.key)
                    current_digest = current.digest if current is not None and current.status != FactStatus.RETRACTED else ABSENT
                    if current_digest != entry.digest:
                        out.append(f"text-grounding: FINAL (step {report.step_no}) spoke text composed from {entry.key} as it was before it changed")
                        break
        # Snapshot grounding: a successful FINAL's snapshot must agree with
        # the arguments of the goal's consumed calls it's built on.
        if a.action_type == ActionType.FINAL and isinstance(a.body, FinalBody) and a.body.task_completed and goal and a.snapshot:
            plan = h.store.plans.current(goal)
            for step in plan.steps if plan else ():
                call = h.store.call_ledger.latest_by_step(goal, step.step_key)
                if call is None or call.status != CallStatus.CONSUMED:
                    continue
                for name, value in call.args.items():
                    if name in a.snapshot.slots and a.snapshot.slots[name] != value:
                        out.append(
                            f"grounding: FINAL (step {report.step_no}) snapshot {name}={a.snapshot.slots[name]!r} "
                            f"but consumed call {call.call_id} used {name}={value!r}"
                        )
    return out


def _end_oracles(h: SimHarness, run: RunRecord) -> list[str]:
    out: list[str] = []
    store = h.store
    out.extend(f"checker: {v.invariant} {v.message}" for v in TraceChecker().check(h.run_log.reports, store=store))

    cancels = metrics.interruption_to_cancellation_latency(h.run_log.reports)
    if cancels.invalidated_never_cancelled_count:
        out.append(f"cancel: {cancels.invalidated_never_cancelled_count} invalidated call(s) never cancelled")

    # Write safety: at most one CONFIRMED effect per lineage (no double booking).
    confirmed_by_lineage: dict[str, list[str]] = {}
    for call in store.call_ledger.all():
        if call.kind != StepKind.WRITE:
            continue
        effect = store.effect_ledger.by_fingerprint(call.fingerprint)
        if effect is not None and effect.status == EffectStatus.CONFIRMED and effect.call_id == call.call_id:
            confirmed_by_lineage.setdefault(f"{call.goal_id}:{call.step_key}", []).append(call.call_id)
    for lineage, calls in sorted(confirmed_by_lineage.items()):
        if len(calls) > 1:
            out.append(f"double-effect: lineage {lineage} confirmed by {calls}")

    # Liveness at the horizon (well past every latency): nothing left
    # silently waiting. An ACTIVE goal is only allowed to be waiting on the
    # user (CLARIFYING) -- anything else is a livelock the user would hear
    # as silence (S-07's class).
    floor_closed = store.floor_state == FloorState.USER_TURN_CLOSED
    if not floor_closed:
        # Every scenario's script ends with the user having finished
        # speaking; a turn still open long after the last user event was
        # opened by something other than the user (e.g. a late ASR
        # release) and will never close -- the floor rule then silences
        # the agent forever.
        out.append(f"liveness: floor still open at horizon (turn {store.turn_log.open_turn_id()})")
    if floor_closed:
        for goal in store.goals.all():
            if goal.status == GoalStatus.ACTIVE and goal.task_state not in (TaskState.CLARIFYING, TaskState.IDLE):
                out.append(f"liveness: goal {goal.goal_id} still ACTIVE in {goal.task_state.value} at horizon")
        running = [j.job_id for j in store.jobs.all() if j.status == JobStatus.RUNNING]
        if running:
            out.append(f"liveness: jobs still running at horizon {running}")
        if _interpretation_pending(store):
            out.append("liveness: interpretation still pending at horizon")
        for call in store.call_ledger.all():
            if call.status in (CallStatus.PROPOSED, CallStatus.IN_FLIGHT):
                # A PROPOSED record left behind by a goal that already
                # finished (e.g. failed honestly, v10-3) is inert -- never
                # emitted, invisible to the user. Only a still-ACTIVE goal's
                # stuck call is a livelock.
                goal = store.goals.get(call.goal_id)
                if goal is not None and goal.status == GoalStatus.ACTIVE and goal.task_state != TaskState.CLARIFYING:
                    out.append(f"liveness: call {call.call_id} ({call.tool}) still {call.status.value} at horizon")

        # W4: a write that completed despite being cancelled must be reported.
        for call in store.call_ledger.all():
            if call.kind == StepKind.WRITE and call.status == CallStatus.COMPLETED_AFTER_CANCEL:
                effect = store.effect_ledger.by_fingerprint(call.fingerprint)
                sent = store.facts.get(f"inform.{call.call_id}.sent")
                if effect is not None and effect.status in (EffectStatus.CONFIRMED, EffectStatus.UNKNOWN) and (sent is None or sent.status == FactStatus.RETRACTED):
                    out.append(f"reconcile: write {call.call_id} completed after cancel but was never reported")
    return out


# --- running one schedule ----------------------------------------------------


def run_schedule(scn: Scenario, sched: Schedule, *, step_us: int = 10_000) -> tuple[RunRecord, SimHarness]:
    tools = {name: dict(cfg) for name, cfg in scn.tools.items()}
    for name, ms in sched.tool_latency_ms:
        tools.setdefault(name, {})["latency_ms"] = ms
    worker = {JobKind(k): v for k, v in sched.worker_latency_us}
    worker.setdefault(JobKind.VISION, 10_000)
    worker.setdefault(JobKind.ASR, 10_000)
    worker.setdefault(JobKind.FRAME, 10_000)
    h = SimHarness(scn.config, seed=1, tools=tools, provider=scn.provider(), worker_latency_us=worker)
    run = RunRecord(schedule=sched)

    timeline = sorted(zip(sched.event_ts, range(len(scn.events))), key=lambda p: (p[0], p[1]))
    # The horizon must outlast the slowest perturbed chain of work, or a
    # correctly-progressing run gets flagged as stuck: allow a few serial
    # tool calls and model jobs past the last user event.
    slowest_tool_us = max((ms for _, ms in sched.tool_latency_ms), default=0) * 1000
    slowest_worker_us = max((us for _, us in sched.worker_latency_us), default=0)
    tail = scn.tail_us + 3 * slowest_tool_us + 4 * slowest_worker_us
    horizon = (timeline[-1][0] if timeline else 0) + tail
    t = 0
    i = 0
    while True:
        goal_before = _active_goal(h.store)
        if i < len(timeline) and timeline[i][0] <= t + step_us:
            ts, idx = timeline[i]
            ts = max(ts, t)
            report = h.send(ts, [scn.events[idx].event])
            t = ts
            i += 1
        else:
            if t + step_us > horizon:
                break
            t += step_us
            report = h.advance(t)
        obs = StepObservation(
            step_no=report.step_no,
            now_us=report.now_us,
            goal_before=goal_before,
            goal_after=_active_goal(h.store),
            floor_after=h.store.floor_state,
            interpretation_pending_after=_interpretation_pending(h.store),
        )
        run.observations.append(obs)
        run.violations.extend(_step_oracles(h, report, obs))

    run.violations.extend(_end_oracles(h, run))
    if scn.check is not None:
        run.violations.extend(scn.check(h, run))
    return run, h


def violation_kinds(violations) -> frozenset[str]:
    return frozenset(v.split(":", 1)[0] for v in violations)


# --- generating schedules ----------------------------------------------------


def _random_schedule(scn: Scenario, base: Schedule, rng: random.Random) -> Schedule:
    ts = list(base.event_ts)
    new_ts = [ts[0]] if ts else []
    for j in range(1, len(ts)):
        gap = ts[j] - ts[j - 1]
        new_ts.append(new_ts[-1] + int(gap * rng.choice(_GAP_FACTOR_GRID)))
    tools = tuple((name, int(ms * rng.choice(_TOOL_FACTOR_GRID))) for name, ms in base.tool_latency_ms)
    workers = tuple((kind, rng.choice(_WORKER_LATENCY_GRID_US)) for kind, _ in base.worker_latency_us)
    return Schedule(tuple(new_ts), tools, workers)


def _targeted_schedules(scn: Scenario, base: Schedule, step_us: int) -> list[Schedule]:
    """For every tool call in the baseline run and every user event after
    it, set that tool's latency so its result lands one step before,
    exactly at, and one step after the event -- the races L1/L5/L14 name.
    Also crosses each with a slow INTERPRET (the window CS-15/triage hold
    are about)."""
    run, h = run_schedule(scn, base, step_us=step_us)
    user_ts = sorted(set(base.event_ts[1:]))
    out: list[Schedule] = []
    seen: set[Schedule] = set()
    for report in h.run_log.reports:
        for er in report.emit_report.emitted:
            if er.action.action_type != ActionType.TOOL_CALL:
                continue
            tool = er.action.body.tool_name
            for ev_ts in user_ts:
                if ev_ts <= report.now_us:
                    continue
                for delta in (-step_us, 0, step_us):
                    latency_ms = max(0, (ev_ts - report.now_us + delta) // 1000)
                    tools = tuple((n, latency_ms if n == tool else ms) for n, ms in base.tool_latency_ms)
                    for interp in (None, 400_000):
                        workers = base.worker_latency_us
                        if interp is not None:
                            workers = tuple((k, interp if k == JobKind.INTERPRET.value else v) for k, v in workers)
                        s = Schedule(base.event_ts, tools, workers)
                        if s not in seen:
                            seen.add(s)
                            out.append(s)
    return out


@dataclass
class ExplorationResult:
    scenario: str
    schedules_run: int
    findings: list[Finding]


def explore(scn: Scenario, *, seed: int = 0, random_budget: int = 40, targeted: bool = True, step_us: int = 10_000) -> ExplorationResult:
    base = baseline_schedule(scn)
    rng = random.Random(seed)
    schedules = [base]
    if targeted:
        schedules.extend(_targeted_schedules(scn, base, step_us))
    schedules.extend(_random_schedule(scn, base, rng) for _ in range(random_budget))

    findings: list[Finding] = []
    seen: set[Schedule] = set()
    for sched in schedules:
        if sched in seen:
            continue
        seen.add(sched)
        run, _ = run_schedule(scn, sched, step_us=step_us)
        if run.violations:
            findings.append(Finding(scn.name, sched, tuple(run.violations)))
    return ExplorationResult(scn.name, len(seen), findings)


def with_config(scn: Scenario, **overrides) -> Scenario:
    return replace(scn, config=replace(scn.config, **overrides))
