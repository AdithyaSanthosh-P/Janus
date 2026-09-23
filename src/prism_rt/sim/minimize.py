"""Shrink a failing schedule back toward its scenario's baseline.

A schedule is a handful of independent knobs: each user event's
timestamp, each tool's latency, each worker kind's latency. Greedy
one-knob-at-a-time reversion (a 1-minimal delta-debugging pass): reset a
knob to its baseline value; keep the reset if the run still produces a
violation of the same kind(s). What survives is exactly the set of timing
deviations the failure needs -- i.e. the race, stated as data.

Event timestamps are reverted as a monotone *gap* (the distance from the
previous event), not an absolute time, so reverting one event never
reorders the user's own events.
"""

from __future__ import annotations

from dataclasses import dataclass

from prism_rt.sim.explorer import Schedule, Scenario, baseline_schedule, run_schedule, violation_kinds


@dataclass(frozen=True)
class Minimized:
    schedule: Schedule
    violations: tuple[str, ...]
    deviations: tuple[str, ...]  # human-readable: the knobs that still differ from baseline


def _gaps(ts: tuple[int, ...]) -> list[int]:
    return [ts[0]] + [ts[i] - ts[i - 1] for i in range(1, len(ts))]


def _from_gaps(gaps: list[int]) -> tuple[int, ...]:
    out = []
    t = 0
    for g in gaps:
        t += g
        out.append(t)
    return tuple(out)


def minimize(scn: Scenario, failing: Schedule, *, step_us: int = 10_000) -> Minimized:
    base = baseline_schedule(scn)
    run, _ = run_schedule(scn, failing, step_us=step_us)
    target = violation_kinds(run.violations)
    if not target:
        return Minimized(failing, (), ())

    def still_fails(s: Schedule) -> tuple[bool, tuple[str, ...]]:
        r, _ = run_schedule(scn, s, step_us=step_us)
        return bool(target & violation_kinds(r.violations)), tuple(r.violations)

    current = failing
    current_violations = tuple(run.violations)
    changed = True
    while changed:
        changed = False
        # tools
        base_tools = dict(base.tool_latency_ms)
        for i, (name, ms) in enumerate(current.tool_latency_ms):
            if ms == base_tools.get(name):
                continue
            cand_tools = list(current.tool_latency_ms)
            cand_tools[i] = (name, base_tools[name])
            cand = Schedule(current.event_ts, tuple(cand_tools), current.worker_latency_us)
            ok, v = still_fails(cand)
            if ok:
                current, current_violations, changed = cand, v, True
        # workers
        base_workers = dict(base.worker_latency_us)
        for i, (kind, us) in enumerate(current.worker_latency_us):
            if us == base_workers.get(kind):
                continue
            cand_workers = list(current.worker_latency_us)
            cand_workers[i] = (kind, base_workers[kind])
            cand = Schedule(current.event_ts, current.tool_latency_ms, tuple(cand_workers))
            ok, v = still_fails(cand)
            if ok:
                current, current_violations, changed = cand, v, True
        # event gaps
        base_gaps = _gaps(base.event_ts)
        for i in range(len(current.event_ts)):
            gaps = _gaps(current.event_ts)
            if gaps[i] == base_gaps[i]:
                continue
            gaps[i] = base_gaps[i]
            cand = Schedule(_from_gaps(gaps), current.tool_latency_ms, current.worker_latency_us)
            ok, v = still_fails(cand)
            if ok:
                current, current_violations, changed = cand, v, True

    deviations: list[str] = []
    for (name, ms), (_, bms) in zip(current.tool_latency_ms, base.tool_latency_ms):
        if ms != bms:
            deviations.append(f"tool {name} latency {bms}ms -> {ms}ms")
    for (kind, us), (_, bus) in zip(current.worker_latency_us, base.worker_latency_us):
        if us != bus:
            deviations.append(f"worker {kind} latency {bus}us -> {us}us")
    for i, (g, bg) in enumerate(zip(_gaps(current.event_ts), _gaps(base.event_ts))):
        if g != bg:
            deviations.append(f"event[{i}] gap {bg}us -> {g}us")
    return Minimized(current, current_violations, tuple(deviations))
