#!/usr/bin/env python3
"""Generalisation harness: score the agent on scenarios nobody wrote by hand.

    python tools/chaos.py --n 60 --seed 4242
    python tools/chaos.py --n 200 --seed 7 --out results/chaos.json

The nine public scenarios are worth zero points, and we have already tuned
against them, so their scores say almost nothing about the hidden set. This
generates fresh scenarios from the kit's own templates using seeds we have
never run, executes them in-process, and reports the DISTRIBUTION rather than
a mean - because the interesting question is not "how well does it usually do"
but "how badly does it do when it goes wrong".

The rule this exists to enforce: if a change improves the public set but
worsens this distribution, it is a hardcode and it gets reverted.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import random
import statistics
import sys
from collections import Counter
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    from duet import config  # noqa: E402
except ImportError:
    config = None
from harness.runner import EvaluationHarness  # noqa: E402
from harness.scenario_gen import TEMPLATES as KIT_TEMPLATES  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scenario_templates import TEMPLATES as OUR_TEMPLATES  # noqa: E402

# The kit's three templates, plus ours (tools/scenario_templates.py): paraphrases,
# retractions, topic changes, chained bookings and unseen state-modifying tools.
TEMPLATES = dict(KIT_TEMPLATES, **OUR_TEMPLATES)
from harness.scorer import score_scenario  # noqa: E402


def load_agent(spec: str):
    module_name, _, class_name = spec.partition(":")
    return getattr(importlib.import_module(module_name), class_name)


def build_scenarios(n: int, seed: int, templates: List[str]) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        name = templates[i % len(templates)]
        out.append(TEMPLATES[name](rng, i))
    return out


def run_one(scenario: Dict[str, Any], cls, time_scale: float) -> Dict[str, Any]:
    async def go():
        h = EvaluationHarness(scenario, lambda a, b: cls(a, b),
                              time_scale=time_scale, verbose=False)
        await h.prepare()
        return await h.run()

    try:
        trace = asyncio.run(go())
    except Exception as exc:  # noqa: BLE001
        return {"scenario_id": scenario.get("scenario_id"), "total": 0.0,
                "error": type(exc).__name__ + ": " + str(exc), "breakdown": {}}
    result = score_scenario(scenario, trace)
    result["protocol_errors"] = sum(1 for e in trace if e.get("kind") == "protocol_error")
    result["crashes"] = sum(1 for e in trace if e.get("kind") == "agent_crash")
    result["abandoned"] = sum(1 for e in trace if e.get("kind") == "tool_abandoned")
    # Irreversible calls that failed without the scenario injecting a failure:
    # the score does not charge for them, but each one is an action sent with
    # wrong arguments (a flight id once went to cancel_booking this way).
    from harness.mock_env import TOOL_REGISTRY
    kinds = {n: s.get("kind") for n, s in TOOL_REGISTRY.items()}
    kinds.update({n: (s or {}).get("kind")
                  for n, s in (scenario.get("tool_manifest") or {}).items()})
    injected = {n for n, ovs in (scenario.get("tool_overrides") or {}).items()
                if any("error" in ov for ov in ovs)}
    result["failed_commits"] = sum(
        1 for e in trace if e.get("kind") == "tool_completed"
        and e.get("status") != "success"
        and kinds.get(e.get("api_name")) == "state_modifying"
        and e.get("api_name") not in injected)
    return result


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(p * (len(ordered) - 1)))))
    return ordered[idx]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", default="agent.agent:ParticipantAgent")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=4242,
                    help="use a seed you have NOT tuned against")
    ap.add_argument("--time-scale", type=float, default=1.0)
    ap.add_argument("--templates", default=",".join(sorted(KIT_TEMPLATES)),
                    help="comma list, or 'kit', 'ours', 'all'")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if config is not None:
        config.STRICT = False
    cls = load_agent(args.agent)
    alias = {"kit": sorted(KIT_TEMPLATES), "ours": sorted(OUR_TEMPLATES),
             "all": sorted(TEMPLATES)}
    names = alias.get(args.templates.strip(), args.templates.split(","))
    templates = [t.strip() for t in names if t.strip() in TEMPLATES]
    scenarios = build_scenarios(args.n, args.seed, templates)

    rows: List[Dict[str, Any]] = []
    for i, scenario in enumerate(scenarios, 1):
        rows.append(run_one(scenario, cls, args.time_scale))
        if i % 10 == 0:
            print("  ... " + str(i) + "/" + str(len(scenarios)))

    totals = [r["total"] for r in rows]
    by_template: Dict[str, List[float]] = {}
    for scenario, row in zip(scenarios, rows):
        key = scenario["scenario_id"].rsplit("_", 1)[0]
        by_template.setdefault(key, []).append(row["total"])

    print()
    print("=" * 62)
    print("  CHAOS RUN   n=" + str(len(rows)) + "  seed=" + str(args.seed)
          + "  scale=" + str(args.time_scale))
    print("=" * 62)
    for key in sorted(by_template):
        vals = by_template[key]
        print("  {:<24} n={:<4} mean={:>6.1f}  min={:>6.1f}".format(
            key, len(vals), statistics.mean(vals), min(vals)))
    print("-" * 62)
    print("  mean   : {:>6.1f}".format(statistics.mean(totals)))
    print("  median : {:>6.1f}".format(statistics.median(totals)))
    print("  p10    : {:>6.1f}".format(percentile(totals, 0.10)))
    print("  min    : {:>6.1f}".format(min(totals)))
    print("  max    : {:>6.1f}".format(max(totals)))
    print("  <75    : {:>6d} scenario(s)".format(sum(1 for t in totals if t < 75)))
    print("  protocol errors : " + str(sum(r.get("protocol_errors", 0) for r in rows)))
    print("  crashes         : " + str(sum(r.get("crashes", 0) for r in rows)))
    print("  abandoned calls : " + str(sum(r.get("abandoned", 0) for r in rows)))
    print("  failed commits  : " + str(sum(r.get("failed_commits", 0) for r in rows)))

    worst = sorted(rows, key=lambda r: r["total"])[:5]
    if worst and worst[0]["total"] < 100.0:
        print("-" * 62)
        print("  worst scenarios:")
        for row in worst:
            print("    {:>6.1f}  {}".format(row["total"], row["scenario_id"]))
            for cat, block in row.get("breakdown", {}).items():
                for cp in block.get("detail", {}).get("checkpoints", []):
                    if not cp["passed"]:
                        print("            [FAIL] " + cp["id"] + " - " + cp["note"])
                for ch in block.get("detail", {}).get("checks", []):
                    if not ch["passed"]:
                        print("            [FAIL] " + ch["check"] + " - " + ch["note"])

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"seed": args.seed, "n": len(rows), "rows": rows}, fh, indent=2)
        print("  wrote " + args.out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
