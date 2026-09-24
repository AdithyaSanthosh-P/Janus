#!/usr/bin/env python3
"""Measure harness clock drift, so we know how much to trust a local score.

    python tools/clockcheck.py --reps 5

The scorer measures latency from the timestamp DECLARED in the scenario file,
but compares it against the virtual time at which our action was LOGGED, which
comes from the harness's own clock:

    first = <trace t_ms of our first spoken action>
    delta = first - float(events[idx]["timestamp_ms"])
    ...only actions with first >= ev_t are considered at all.

If asyncio.sleep returns early, the harness logs the event - and therefore our
immediate response - at a virtual time BEFORE the declared one, and a perfect
response is scored "never responded". We observed exactly that on Windows:
pub_04 declares its turn at 100ms, the harness logged it at 94ms, and a 10ms
response scored zero latency.

This is a property of the measuring environment, not of the agent, and it puts
noise on every local latency number. Official runs are Linux, where sleeps
overshoot rather than undershoot, so this should not occur there - but we
would rather know the size of our local error bars than be surprised by them.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import importlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from duet import config  # noqa: E402
from harness.runner import EvaluationHarness  # noqa: E402


def load_agent(spec: str):
    module_name, _, class_name = spec.partition(":")
    return getattr(importlib.import_module(module_name), class_name)


def run_once(scenario, cls, time_scale):
    async def go():
        h = EvaluationHarness(scenario, lambda a, b: cls(a, b),
                              time_scale=time_scale, verbose=False)
        await h.prepare()
        return await h.run()
    return asyncio.run(go())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", default="agent.agent:ParticipantAgent")
    ap.add_argument("--scenarios", default=os.path.join(ROOT, "scenarios"))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--time-scale", type=float, default=1.0)
    args = ap.parse_args()

    config.STRICT = False
    cls = load_agent(args.agent)
    paths = sorted(glob.glob(os.path.join(args.scenarios, "*.json")))

    undershoots = 0
    samples = 0
    worst = 0.0
    rows = []

    for path in paths:
        with open(path, "r", encoding="utf-8") as fh:
            scenario = json.load(fh)
        declared = [float(e.get("timestamp_ms", 0)) for e in scenario["events"]]
        deltas = []
        for _ in range(args.reps):
            trace = run_once(scenario, cls, args.time_scale)
            logged = [e["t_ms"] for e in trace
                      if e.get("kind") == "event"
                      and e.get("event_type") not in ("tool_manifest", "scenario_end")]
            for want, got in zip(declared, logged):
                drift = got - want
                deltas.append(drift)
                samples += 1
                if drift < 0:
                    undershoots += 1
                    worst = min(worst, drift)
        rows.append((os.path.basename(path), min(deltas), max(deltas),
                     sum(deltas) / len(deltas) if deltas else 0.0))

    print("{:<36} {:>9} {:>9} {:>9}".format("scenario", "min", "max", "mean"))
    print("-" * 66)
    for name, lo, hi, mean in rows:
        print("{:<36} {:>9.1f} {:>9.1f} {:>9.1f}".format(name[:36], lo, hi, mean))
    print("-" * 66)
    print("event deliveries sampled : " + str(samples))
    print("delivered EARLY          : " + str(undershoots)
          + "  (" + str(round(100.0 * undershoots / max(samples, 1), 1)) + "%)")
    print("worst early delivery     : " + str(round(worst, 1)) + " ms")
    print()
    if undershoots:
        print("An early delivery makes a perfect response score zero latency,")
        print("because the scorer ignores actions logged before the declared")
        print("event time. Treat local latency numbers as having that much noise.")
    else:
        print("No early deliveries: local latency numbers are trustworthy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
