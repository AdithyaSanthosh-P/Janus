"""Multimodal race regressions found by `sim/explorer.py` (vision V1-V4,
ASR A1-A4b), plus the stale-compose-text bug the vision investigation
exposed in the *text* path.

Every regression is a deterministic, minimized schedule the explorer
produced, asserted two ways:
- with the fix: the schedule runs clean (all explorer oracles + the
  scenario's own invariants);
- with only that fix removed (a test-local `unittest.mock.patch` from
  `tests/mutations.py` -- production code is never weakened): the schedule
  violates the invariant the fix exists for.
So "this test would have failed before the fix" is a permanent,
re-runnable property of the suite, not a one-off manual experiment.
"""

from __future__ import annotations

import pytest
from explore_scenarios import SEARCH_CORRECTION
from explore_scenarios_mm import (
    A1_ASR_VS_TEXT_CORRECTION,
    A2_EARLIER_VS_NEWER_SPEECH,
    A3_ASR_VS_EOT,
    A4B_MALFORMED_ASR_THEN_TEXT,
    MULTIMODAL_SCENARIOS,
    V1_VISION_REDIRECT,
    V2_NEW_FRAME_VS_OLD_RESULT,
    V3_VISION_FAILURE_VS_REDIRECT,
    V4_SLOW_VISION_WATCHDOG,
)

import mutations as M
from prism_rt.model.types import ActionType, JobKind, JobStatus
from prism_rt.sim.explorer import Schedule, baseline_schedule, explore, run_schedule, violation_kinds


def _sched(scn, *, gaps=None, tools=None, workers=None) -> Schedule:
    """The scenario's baseline with specific knobs changed; `gaps` sets the
    gap *before* event i (so user-event order is always preserved)."""
    b = baseline_schedule(scn)
    ts = list(b.event_ts)
    if gaps:
        g = [ts[0]] + [ts[i] - ts[i - 1] for i in range(1, len(ts))]
        for i, v in gaps.items():
            g[i] = v
        ts, t = [], 0
        for x in g:
            t += x
            ts.append(t)
    return Schedule(
        tuple(ts),
        tuple((n, (tools or {}).get(n, v)) for n, v in b.tool_latency_ms),
        tuple((k, (workers or {}).get(k, v)) for k, v in b.worker_latency_us),
    )


# V4's minimized triage race: the watchdog fires before the goal exists
# (slow interpreter), the goal later completes while the user's correction
# is still being interpreted.
_V4_TRIAGE = Schedule(
    (0, 0, 100_000, 150_000, 375_000, 387_500, 587_500, 592_500, 632_500),
    (("lookup_color", 0),),
    tuple(sorted({"compose": 10_000, "interpret": 400_000, "plan": 10_000, "vision": 10_000}.items())),
)

REGRESSIONS = [
    # (id, scenario, minimized schedule, mutation removing the fix, violation kind it must bring back)
    ("stale_compose_text_after_correction", SEARCH_CORRECTION, _sched(SEARCH_CORRECTION, tools={"search_flights": 220}), M.compose_grounding_off, "text-grounding"),
    ("vision_redirect_keeps_old_evidence", V1_VISION_REDIRECT, baseline_schedule(V1_VISION_REDIRECT), M.retarget_retraction_off, "stale-evidence"),
    ("responding_recomposes_from_stale_step", V1_VISION_REDIRECT, baseline_schedule(V1_VISION_REDIRECT), M.responding_revert_off, "lost-intent"),
    ("perception_reanalyzes_after_goal_done", V2_NEW_FRAME_VS_OLD_RESULT, _sched(V2_NEW_FRAME_VS_OLD_RESULT, tools={"lookup_color": 800}, workers={"vision": 900_000}), M.perception_goal_gate_off, "liveness"),
    ("clarify_survives_vision_redirect", V3_VISION_FAILURE_VS_REDIRECT, _sched(V3_VISION_FAILURE_VS_REDIRECT, gaps={4: 37_500, 5: 0, 6: 20_000}), M.retarget_clears_clarify_off, "spurious-clarify"),
    ("watchdog_lets_genuine_final_skip_hold", V4_SLOW_VISION_WATCHDOG, _V4_TRIAGE, M.watchdog_exemption_too_broad, "triage"),
    ("frame_tie_picks_older_arrival", V1_VISION_REDIRECT, _sched(V1_VISION_REDIRECT, gaps={2: 0, 3: 0, 4: 0}), M.frame_tiebreak_off, "stale-evidence"),
    ("eot_before_transcript_dropped", A1_ASR_VS_TEXT_CORRECTION, baseline_schedule(A1_ASR_VS_TEXT_CORRECTION), M.deferred_eot_off, "lost-intent"),
    ("asr_released_out_of_capture_order", A2_EARLIER_VS_NEWER_SPEECH, _sched(A2_EARLIER_VS_NEWER_SPEECH, workers={"asr": 400_000}), M.asr_ordered_release_off, "asr-order"),
    ("asr_then_text_same_utterance_duplicated", A3_ASR_VS_EOT, _sched(A3_ASR_VS_EOT, gaps={2: 40_000}, workers={"asr": 5_000}), M.asr_ordered_release_off, "asr-duplicate"),
    ("answer_given_over_untranscribed_speech", A2_EARLIER_VS_NEWER_SPEECH, baseline_schedule(A2_EARLIER_VS_NEWER_SPEECH), M.asr_hold_off, "stale-evidence"),
    ("malformed_transcript_stalls_forever", A4B_MALFORMED_ASR_THEN_TEXT, baseline_schedule(A4B_MALFORMED_ASR_THEN_TEXT), M.malformed_asr_not_a_failure, "liveness"),
]


@pytest.mark.parametrize("name,scn,sched,mutation,kind", REGRESSIONS, ids=[r[0] for r in REGRESSIONS])
def test_regression_clean_with_fix_and_broken_without(name, scn, sched, mutation, kind):
    run, _ = run_schedule(scn, sched)
    assert run.violations == [], run.violations
    with mutation():
        broken, _ = run_schedule(scn, sched)
    assert kind in violation_kinds(broken.violations), broken.violations


# --- a few outcome assertions, stated plainly ----------------------------------


def _finals(h):
    return [er.action for r in h.run_log.reports for er in r.emit_report.emitted if er.action.action_type == ActionType.FINAL]


def test_vision_redirect_is_answered_from_the_new_frame():
    _, h = run_schedule(V1_VISION_REDIRECT, baseline_schedule(V1_VISION_REDIRECT))
    assert [f.snapshot.slots["color"] for f in _finals(h)] == ["blue"]


def test_earlier_speech_is_applied_before_later_speech_even_when_transcribed_last():
    _, h = run_schedule(A2_EARLIER_VS_NEWER_SPEECH, _sched(A2_EARLIER_VS_NEWER_SPEECH, workers={"asr": 400_000}))
    texts = [" ".join(c.text for c in t.chunks) for t in h.store.turn_log.all()]
    assert texts == ["Find flights to Pune", "actually Mumbai"]
    assert [f.snapshot.slots["destination"] for f in _finals(h)] == ["Mumbai"]


def test_perception_stops_once_the_goal_is_done():
    _, h = run_schedule(V2_NEW_FRAME_VS_OLD_RESULT, _sched(V2_NEW_FRAME_VS_OLD_RESULT, tools={"lookup_color": 800}, workers={"vision": 900_000}))
    vision = [j for j in h.store.jobs.all() if j.kind == JobKind.VISION]
    assert not [j for j in vision if j.status == JobStatus.RUNNING]
    assert len(vision) <= 3  # first question + the redirect's; never a periodic renewal after completion


def test_typed_transcript_is_not_held_behind_its_own_audio():
    """AUTO mode: text covering a clip's capture window settles the clip at
    once -- the reply isn't delayed by the 800ms dedupe window."""
    _, h = run_schedule(A3_ASR_VS_EOT, baseline_schedule(A3_ASR_VS_EOT))
    assert _finals(h)[0].ts_us < 500_000


# --- the explorer covers vision and ASR ------------------------------------------


def test_bounded_multimodal_exploration_is_clean():
    for scn in MULTIMODAL_SCENARIOS:
        result = explore(scn, seed=0, random_budget=3)
        assert result.findings == [], (scn.name, result.findings[0].violations)
