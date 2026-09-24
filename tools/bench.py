#!/usr/bin/env python3
"""Invariant 1 monitor: how long does the dispatcher hold the event loop?

    python tools/bench.py
    python tools/bench.py --agent agent.agent:ParticipantAgent --time-scale 8

A blocking handler is the most expensive bug class in this project, and it is
invisible in the trace - the scorer attributes the damage to whatever the
agent did next. So we measure it directly, every run, rather than inferring it
from a score that dropped.

Also reports setup() cold-start wall time, which is charged against the 300s
per-scenario setup cap (the first scenario in a process pays the model load;
later ones hit the module-level cache).
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import importlib
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from duet import config, telemetry  # noqa: E402
from harness.runner import EvaluationHarness  # noqa: E402


def load_agent(spec: str):
    module_name, _, class_name = spec.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def bench_scenario(path: str, cls, time_scale: float):
    with open(path, "r", encoding="utf-8") as fh:
        scenario = json.load(fh)

    async def go():
        h = EvaluationHarness(scenario, lambda a, b: cls(a, b),
                              time_scale=time_scale, verbose=False)
        t0 = time.perf_counter()
        await h.prepare()
        setup_ms = (time.perf_counter() - t0) * 1000.0
        trace = await h.run()
        return setup_ms, trace

    setup_ms, trace = asyncio.run(go())
    timings = telemetry.timings()
    violations = telemetry.violations()
    return {
        "scenario": os.path.basename(path),
        "setup_ms": round(setup_ms, 1),
        "handlers": len(timings),
        "max_ms": round(max((t["ms"] for t in timings), default=0.0), 3),
        "p50_ms": round(telemetry.percentile(0.50) or 0.0, 3),
        "p99_ms": round(telemetry.percentile(0.99) or 0.0, 3),
        "violations": len(violations),
        "protocol_errors": sum(1 for e in trace if e.get("kind") == "protocol_error"),
        "crashes": sum(1 for e in trace if e.get("kind") == "agent_crash"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", default="agent.agent:ParticipantAgent")
    ap.add_argument("--scenarios", default=os.path.join(ROOT, "scenarios"))
    ap.add_argument("--time-scale", type=float, default=8.0)
    args = ap.parse_args()

    # Guard violations must be reported, not raised, while benchmarking.
    config.STRICT = False

    cls = load_agent(args.agent)
    paths = sorted(glob.glob(os.path.join(args.scenarios, "*.json")))
    if not paths:
        print("no scenarios found in " + args.scenarios)
        return 1

    rows = [bench_scenario(p, cls, args.time_scale) for p in paths]

    header = ("scenario", "setup_ms", "n", "p50", "p99", "max", "viol", "perr", "crash")
    print("{:<34} {:>9} {:>5} {:>8} {:>8} {:>8} {:>5} {:>5} {:>5}".format(*header))
    print("-" * 96)
    for r in rows:
        print("{:<34} {:>9} {:>5} {:>8} {:>8} {:>8} {:>5} {:>5} {:>5}".format(
            r["scenario"][:34], r["setup_ms"], r["handlers"], r["p50_ms"],
            r["p99_ms"], r["max_ms"], r["violations"], r["protocol_errors"],
            r["crashes"]))

    worst = max(r["max_ms"] for r in rows)
    total_viol = sum(r["violations"] for r in rows)
    total_err = sum(r["protocol_errors"] for r in rows)
    total_crash = sum(r["crashes"] for r in rows)
    print("-" * 96)
    print("worst handler        : " + str(round(worst, 3)) + " ms"
          + "   (budget " + str(config.DISPATCHER_BUDGET_MS) + " ms)")
    print("budget violations    : " + str(total_viol))
    print("protocol errors      : " + str(total_err))
    print("agent crashes        : " + str(total_crash))
    print("median setup()       : "
          + str(round(statistics.median([r["setup_ms"] for r in rows]), 1)) + " ms")

    ok = (worst <= config.DISPATCHER_BUDGET_MS and total_viol == 0
          and total_err == 0 and total_crash == 0)
    print("VERDICT: " + ("INVARIANT 1 HOLDS" if ok else "INVARIANT 1 VIOLATED"))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
