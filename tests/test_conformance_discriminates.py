"""Testing the tests.

Our conformance scenarios exist to catch agents that fail to cancel stale
work. That claim is only true if the timing arithmetic in
tools/make_conformance.py is right: an uncancelled call must PROVABLY complete
more than CANCEL_GRACE_MS after the interruption that invalidated it.

So we run two probe agents that differ in exactly one respect - whether they
cancel on interruption - and assert the scenarios separate them. If they score
the same, the scenario is vacuous and would give us false confidence on the
hidden set.
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from harness.runner import EvaluationHarness  # noqa: E402
from harness.scorer import CANCEL_GRACE_MS, score_scenario  # noqa: E402
from tests.probes import CancellingProbe, NaiveProbe  # noqa: E402

CONF_DIR = os.path.join(ROOT, "tests", "conformance")

# The scenarios whose whole point is cancellation of invalidated work.
INTERRUPTION_SCENARIOS = [
    "conf_01_double_interrupt",
    "conf_02_retraction",
    "conf_03_intent_change",
    "conf_04_interrupt_during_booking",
    # adversarial timing. conf_20 is deliberately absent: its interruption is
    # 15 ms after the turn, below the ~15.6 ms timer granularity of Windows
    # (notes/FINDINGS.md F8), so on this platform the turn is sometimes
    # delivered after the interruption's declared time and ANY agent's first
    # call reads as a re-issue - the probes cannot be judged fairly. It is
    # still scored for the real agent, which issues its call synchronously.
    "conf_21_retract_during_booking",
    "conf_22_three_corrections",
    "conf_23_correction_inside_commit_hold",
]


def _load(scenario_id: str):
    with open(os.path.join(CONF_DIR, scenario_id + ".json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _scale_for(scenario) -> float:
    """Scale 8 compresses the probe's own processing time by 8x in virtual
    terms; with user events only 15-150 ms apart that alone moved a probe's
    ORIGINAL call past the interruption, where the scorer reads it as a
    re-issue. Scenarios that tight are replayed at real speed."""
    stamps = [e.get("timestamp_ms", 0) for e in scenario.get("events", [])]
    gaps = [b - a for a, b in zip(stamps, stamps[1:]) if b > a]
    if gaps and min(gaps) < 200:
        return 1.0
    # A race between an interruption and a tool result tens of milliseconds
    # apart (conf_19) is decided by real milliseconds; compressed 8x, the
    # result sometimes overtakes the interruption and the test measures noise.
    notes = scenario.get("_design_notes") or {}
    if "stale_returns_ms" in notes and "interrupt_ms" in notes and \
            abs(notes["stale_returns_ms"] - notes["interrupt_ms"]) < 200:
        return 1.0
    return 8.0


def _run(scenario, cls, time_scale=None):
    if time_scale is None:
        time_scale = _scale_for(scenario)

    async def go():
        h = EvaluationHarness(scenario, lambda a, b: cls(a, b),
                              time_scale=time_scale, verbose=False)
        await h.prepare()
        return await h.run()
    return asyncio.run(go())


def _recovery_fraction(scenario, trace):
    result = score_scenario(scenario, trace)
    rec = result["breakdown"].get("recovery")
    return None if rec is None else rec["fraction"]


def _stale_checks(scenario, trace):
    """Just the 'invalidated:<tool>' half of the recovery score."""
    result = score_scenario(scenario, trace)
    rec = result["breakdown"].get("recovery")
    if rec is None:
        return []
    return [c for c in rec["detail"]["checks"] if c["check"].startswith("invalidated:")]


# ---------------------------------------------------------------- structure
def test_all_conformance_scenarios_are_wellformed():
    paths = sorted(glob.glob(os.path.join(CONF_DIR, "*.json")))
    assert len(paths) >= 8, "expected at least 8 conformance scenarios"
    for path in paths:
        with open(path, "r", encoding="utf-8") as fh:
            scenario = json.load(fh)
        assert scenario.get("scenario_id"), path
        assert scenario.get("events"), path
        gt = scenario.get("ground_truth") or {}
        cps = gt.get("checkpoints") or []
        assert cps, "no checkpoints in " + path
        total = sum(float(c.get("weight", 1.0)) for c in cps)
        assert abs(total - 1.0) < 1e-6, (
            path + " checkpoint weights sum to " + str(total) + ", expected 1.0")
        # event timestamps must be non-decreasing or the harness replays oddly
        stamps = [e.get("timestamp_ms", 0) for e in scenario["events"]]
        assert stamps == sorted(stamps), path


# ------------------------------------------------------- the discrimination
@pytest.mark.parametrize("scenario_id", INTERRUPTION_SCENARIOS)
def test_scenario_penalises_an_agent_that_never_cancels(scenario_id):
    scenario = _load(scenario_id)
    trace = _run(scenario, NaiveProbe)
    checks = _stale_checks(scenario, trace)
    assert checks, "no invalidated-call checks in " + scenario_id
    failed = [c for c in checks if not c["passed"]]
    assert failed, (
        scenario_id + " did NOT penalise an agent that never cancels - the "
        "scenario is vacuous. Checks: " + repr(checks))


@pytest.mark.parametrize("scenario_id", INTERRUPTION_SCENARIOS)
def test_scenario_rewards_an_agent_that_cancels(scenario_id):
    scenario = _load(scenario_id)
    trace = _run(scenario, CancellingProbe)
    checks = _stale_checks(scenario, trace)
    failed = [c for c in checks if not c["passed"]]
    assert not failed, (
        scenario_id + " penalised an agent that DID cancel - the scenario is "
        "unfair or the margins are wrong: " + repr(failed))


@pytest.mark.parametrize("scenario_id", INTERRUPTION_SCENARIOS)
def test_cancelling_beats_not_cancelling(scenario_id):
    """End to end: the recovery subscore must actually move."""
    scenario = _load(scenario_id)
    naive = _recovery_fraction(scenario, _run(scenario, NaiveProbe))
    good = _recovery_fraction(scenario, _run(scenario, CancellingProbe))
    assert naive is not None and good is not None
    assert good > naive, (
        scenario_id + ": cancelling scored " + str(good) + " vs naive "
        + str(naive) + " - no discrimination")


# ------------------------------------------------------------- the margins
@pytest.mark.parametrize("scenario_id", INTERRUPTION_SCENARIOS)
def test_uncancelled_stale_call_really_does_land_past_the_grace_window(scenario_id):
    """Verify the arithmetic directly in the trace rather than trusting the
    comment in the generator: with a non-cancelling agent, at least one
    invalidated call must complete more than CANCEL_GRACE_MS after its
    invalidation timestamp."""
    scenario = _load(scenario_id)
    trace = _run(scenario, NaiveProbe)
    rec = scenario["ground_truth"]["recovery"]

    completions = [e for e in trace if e.get("kind") == "tool_completed"]
    late = []
    for inv in rec["invalidated_calls"]:
        deadline = float(inv["invalid_after_ms"]) + CANCEL_GRACE_MS
        for comp in completions:
            if comp.get("api_name") != inv["tool"]:
                continue
            args = comp.get("args", {})
            subset = inv.get("args_subset") or {}
            if not all(str(args.get(k, "")).lower() in [str(x).lower() for x in v]
                       for k, v in subset.items()):
                continue
            if comp["t_ms"] > deadline:
                late.append((inv["tool"], comp["t_ms"], deadline))
    assert late, (
        scenario_id + ": no invalidated call completed past its grace window, so "
        "the scenario cannot detect a missing cancel. Completions: "
        + repr([(c.get("api_name"), c.get("args"), c["t_ms"]) for c in completions]))


# ------------------------------------------------- a race cancellation cannot win
def test_a_stale_result_that_beats_the_cancel_is_never_spoken():
    """conf_19: the stale search returns ~40 ms after the interruption, so
    cancelling or not makes no difference to the recovery score - the result
    is inside the grace window either way. What the scenario tests is the
    other half of the contract: a result from superseded work must never be
    spoken. An agent that grounds whatever comes back says FL-REC."""
    from harness.scorer import _eval_checkpoint

    scenario = _load("conf_19_interrupt_just_before_result")
    check = next(c for c in scenario["ground_truth"]["checkpoints"]
                 if c["id"] == "stale_result_never_spoken")
    naive = _eval_checkpoint(_run(scenario, NaiveProbe), check)
    good = _eval_checkpoint(_run(scenario, CancellingProbe), check)
    assert not naive["passed"], "naive probe was not caught speaking the stale result"
    assert good["passed"], good
