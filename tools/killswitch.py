#!/usr/bin/env python3
"""Degradation test: how much survives when every model is gone?

    python tools/killswitch.py

The single largest unhedged risk in this submission is that something about
the evaluation machine differs from ours - a dependency that will not install,
a model checkpoint that cannot be fetched, a GPU that is busy. There is no
leaderboard and no feedback during the event, so we would not find out.

The insurance is that duet/ treats every model as optional. This asserts that
the insurance actually pays out, by running the full public set with the
speech, vision and embedding backends forcibly disabled.

The bar is deliberately modest. With no perception, audio and visual scenarios
cannot be answered correctly - the honest response is to say we could not make
it out, which still earns clarification, latency and safety credit rather than
the zero a crash would score. What must NOT happen is a crash, a protocol
error, or silence.
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Disable every backend BEFORE duet.perception is imported anywhere.
os.environ["DUET_NO_ASR"] = "1"
os.environ["DUET_NO_VISION"] = "1"
os.environ["DUET_NO_EMBED"] = "1"

from duet import config  # noqa: E402
from harness.runner import EvaluationHarness  # noqa: E402
from harness.scorer import score_scenario  # noqa: E402

SPOKEN = ("filler_speech", "clarification_request", "final_response")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", default="agent.agent:ParticipantAgent")
    ap.add_argument("--scenarios", default=os.path.join(ROOT, "scenarios"))
    ap.add_argument("--time-scale", type=float, default=1.0)
    ap.add_argument("--floor", type=float, default=55.0,
                    help="minimum acceptable average with no models at all")
    args = ap.parse_args()

    config.STRICT = False
    module_name, _, class_name = args.agent.partition(":")
    cls = getattr(importlib.import_module(module_name), class_name)

    totals, problems = [], []
    print("running with DUET_NO_ASR / DUET_NO_VISION / DUET_NO_EMBED set")
    print("-" * 62)
    for path in sorted(glob.glob(os.path.join(args.scenarios, "*.json"))):
        with open(path, "r", encoding="utf-8") as fh:
            scenario = json.load(fh)

        async def go():
            h = EvaluationHarness(scenario, lambda a, b: cls(a, b),
                                  time_scale=args.time_scale, verbose=False)
            await h.prepare()
            return await h.run()

        try:
            trace = asyncio.run(go())
        except Exception as exc:  # noqa: BLE001
            problems.append(scenario["scenario_id"] + ": raised "
                            + type(exc).__name__ + ": " + str(exc))
            totals.append(0.0)
            continue

        result = score_scenario(scenario, trace)
        crashes = [e for e in trace if e.get("kind") == "agent_crash"]
        perrs = [e for e in trace if e.get("kind") == "protocol_error"]
        spoke = any(e.get("kind") == "action" and e.get("action") in SPOKEN
                    for e in trace)

        if crashes:
            problems.append(scenario["scenario_id"] + ": agent_crash " + repr(crashes[:1]))
        if perrs:
            problems.append(scenario["scenario_id"] + ": " + str(len(perrs))
                            + " protocol error(s)")
        if not spoke:
            problems.append(scenario["scenario_id"] + ": went silent")

        totals.append(result["total"])
        print("  {:>6.1f}  {}".format(result["total"], scenario["scenario_id"]))

    mean = statistics.mean(totals) if totals else 0.0
    print("-" * 62)
    print("  average with no models : {:.1f}".format(mean))
    print("  floor                  : {:.1f}".format(args.floor))
    for problem in problems:
        print("  [PROBLEM] " + problem)

    ok = mean >= args.floor and not problems
    print("VERDICT: " + ("DEGRADES GRACEFULLY" if ok else "DEGRADATION BROKEN"))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
