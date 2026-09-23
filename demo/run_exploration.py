"""Reproduce the adversarial-exploration numbers in `docs/measurements.md`.

    PYTHONPATH=src:.:tests python demo/run_exploration.py            # full (~6 min)
    PYTHONPATH=src:.:tests python demo/run_exploration.py --quick    # 1 seed, small budget

Prints (1) the sweep over every scenario with all safeguards on, and
(2) the safeguard-disable ("mutation") table: each safeguard removed in
isolation via a test-local patch (`tests/mutations.py`), run against the
scenarios it protects. Deterministic: same arguments, same numbers.
"""

from __future__ import annotations

import sys
from collections import Counter

from explore_scenarios import ALL_SCENARIOS
from explore_scenarios_mm import MULTIMODAL_SCENARIOS
from mutations import MUTATIONS

from prism_rt.sim.explorer import explore, violation_kinds
from prism_rt.sim.minimize import minimize


def sweep(group, seeds, budget):
    total, failing = 0, []
    for seed in seeds:
        for scn in group:
            res = explore(scn, seed=seed, random_budget=budget, targeted=(seed == seeds[0]))
            total += res.schedules_run
            failing += [(scn, f) for f in res.findings]
    return total, failing


def main() -> None:
    quick = "--quick" in sys.argv
    seeds, budget, mut_budget = ((1,), 40, 40) if quick else ((1, 2, 3), 300, 150)

    print("== All safeguards on ==")
    for label, group in (("text/tool scenarios", ALL_SCENARIOS), ("multimodal scenarios", MULTIMODAL_SCENARIOS)):
        total, failing = sweep(group, seeds, budget)
        print(f"{label:22s} {len(group):2d} scenarios  {total:6d} schedules  {len(failing)} failing")
        for scn, f in failing[:5]:
            m = minimize(scn, f.schedule)
            print(f"    {scn.name}: {list(m.deviations)} -> {list(m.violations)[:2]}")

    print("\n== One safeguard disabled at a time ==")
    print(f"{'safeguard':40s} {'schedules':>9s} {'failing':>8s}  violation kinds")
    for name, (cm, scenarios, xform) in MUTATIONS.items():
        total, bad, kinds = 0, 0, Counter()
        with cm():
            for scn in scenarios:
                for seed in seeds:
                    res = explore(xform(scn), seed=seed, random_budget=mut_budget, targeted=(seed == seeds[0]))
                    total += res.schedules_run
                    bad += len(res.findings)
                    for f in res.findings:
                        kinds.update(violation_kinds(f.violations))
        print(f"{name:40s} {total:9d} {bad:8d}  {dict(kinds.most_common(4))}")


if __name__ == "__main__":
    main()
