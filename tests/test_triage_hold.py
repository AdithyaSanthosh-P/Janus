"""Regressions found by `sim/explorer.py` (M4 / C17), each pinned to the
minimized schedule the explorer produced -- i.e. the race stated as data.

1. TRIAGE hold (`docs/prompt 2.txt` §8.3, transitions 23-31; G3's "task
   state is not TRIAGE"; CS-15's "interpretation pending"). Never built:
   `kernel/task.py`'s own docstring deferred it. Consequences found:
   - stale FINAL: the search result lands while the user is saying
     "actually Mumbai"; the CS-28 floor rule holds FINAL, then releases it
     in the very step the correction's EOT closes the floor -- before the
     correction is interpreted. The user hears Pune results after saying
     Mumbai. Minimal deviation: search latency 400ms -> 220ms.
   - superseded write: with INTERPRET slower than settle_ms (300ms; normal
     for a live model), book_flight(F6) passed every gate after the user
     had said "no the 8pm one" -- the double-booking race (L5) the settle
     barrier was meant to close, reopened by model latency.
2. Lost rapid follow-up: a second turn closing before the first turn's
   interpretation returned *overwrote* the pending marker; the first
   turn's result was then discarded. "Find flights to Pune" + "mm-hmm"
   (INTERPRET 400ms) produced no goal at all.
3. Stuck speculative correction: with speculative interpretation on, the
   correction's speculative job went stale when the previous turn's
   interpretation created the goal; its `eot_waiting` turn had no
   fallback, so the correction was never interpreted.

Tests 1 run with `triage_hold_enabled=False` as well, proving the hold is
what prevents the failure (not the scenario being benign).
"""

from __future__ import annotations

from dataclasses import replace

from explore_scenarios import ALL_SCENARIOS, BACKCHANNEL, SEARCH_CORRECTION, SPECULATIVE_CORRECTION, WRITE_CORRECTION

from prism_rt.model.types import ActionType, EffectStatus, FactStatus, GoalStatus, JobStatus
from prism_rt.sim.explorer import Schedule, baseline_schedule, explore, run_schedule, violation_kinds, with_config
from prism_rt.sim.minimize import minimize


def _with_worker(scn, kind: str, us: int) -> Schedule:
    b = baseline_schedule(scn)
    return Schedule(b.event_ts, b.tool_latency_ms, tuple((k, us if k == kind else v) for k, v in b.worker_latency_us))


def _with_tool(scn, tool: str, ms: int) -> Schedule:
    b = baseline_schedule(scn)
    return Schedule(b.event_ts, tuple((n, ms if n == tool else v) for n, v in b.tool_latency_ms), b.worker_latency_us)


def _emitted(h, action_type):
    return [er.action for r in h.run_log.reports for er in r.emit_report.emitted if er.action.action_type == action_type]


# --- 1a. stale FINAL after a correction ---------------------------------------


def test_final_is_not_spoken_for_the_old_value_after_a_correction():
    sched = _with_tool(SEARCH_CORRECTION, "search_flights", 220)
    run, h = run_schedule(SEARCH_CORRECTION, sched)
    assert run.violations == []
    finals = _emitted(h, ActionType.FINAL)
    assert len(finals) == 1
    assert finals[0].snapshot.slots["destination"] == "Mumbai"


def test_stale_final_reappears_with_the_hold_disabled():
    scn = with_config(SEARCH_CORRECTION, triage_hold_enabled=False)
    run, _ = run_schedule(scn, _with_tool(scn, "search_flights", 220))
    assert {"stale-final", "triage"} <= violation_kinds(run.violations)


# --- 1b. write admitted while a correction is being interpreted ---------------


def test_write_is_not_admitted_while_a_correction_awaits_interpretation():
    sched = _with_worker(WRITE_CORRECTION, "interpret", 900_000)
    run, h = run_schedule(WRITE_CORRECTION, sched)
    assert run.violations == []
    writes = [a.body.arguments["flight_id"] for a in _emitted(h, ActionType.TOOL_CALL)]
    assert writes == ["F8"]
    confirmed = [e for e in h.store.effect_ledger._by_fingerprint.values() if e.status == EffectStatus.CONFIRMED]
    assert len(confirmed) == 1


def test_superseded_write_reappears_with_the_hold_disabled():
    scn = with_config(WRITE_CORRECTION, triage_hold_enabled=False)
    run, h = run_schedule(scn, _with_worker(scn, "interpret", 900_000))
    assert "superseded-write" in violation_kinds(run.violations)
    assert [a.body.arguments["flight_id"] for a in _emitted(h, ActionType.TOOL_CALL)][0] == "F6"


# --- 2. rapid follow-up must not erase the first request -------------------------


def test_rapid_follow_up_queues_instead_of_overwriting_the_first_turn():
    run, h = run_schedule(BACKCHANNEL, _with_worker(BACKCHANNEL, "interpret", 400_000))
    assert run.violations == []
    assert [g.status for g in h.store.goals.all()] == [GoalStatus.COMPLETED]
    assert [a.body.text for a in _emitted(h, ActionType.FINAL)] == ["Flights found."]


# --- 3. speculative correction whose awaited job goes stale --------------------


def test_eot_waiting_turn_falls_back_when_its_speculative_job_is_dropped():
    run, h = run_schedule(SPECULATIVE_CORRECTION, _with_worker(SPECULATIVE_CORRECTION, "interpret", 400_000))
    assert run.violations == []
    finals = _emitted(h, ActionType.FINAL)
    assert [f.snapshot.slots["destination"] for f in finals] == ["Mumbai"]
    assert not any(k.endswith(".eot_waiting") and f.status != FactStatus.RETRACTED and f.value for k, f in h.store.facts.by_prefix("spec_interpret.").items())


# --- the hold's safety valves: it must never become a silent hang ---------------


def _unscripted_follow_up():
    """A mid-task utterance the (scripted) interpreter can't handle --
    every INTERPRET attempt errors, like a persistently failing live
    provider."""
    events = list(SEARCH_CORRECTION.events)
    events[4] = replace(events[4], event={"type": "text_chunk", "payload": {"text": "zzqx unscripted"}})
    return replace(SEARCH_CORRECTION, name="unscripted_follow_up", events=tuple(events), check=None)


def test_persistently_failing_interpretation_is_given_up_and_releases_the_hold():
    scn = _unscripted_follow_up()
    run, h = run_schedule(scn, baseline_schedule(scn))
    assert run.violations == []
    gave_up = [k for k, f in h.store.facts.by_prefix("interpret.").items() if k.endswith(".gave_up") and f.value]
    assert len(gave_up) == 1
    failures = [f.value for k, f in h.store.facts.by_prefix("interpret.").items() if k.endswith(".failures")]
    assert failures == [h.config.max_read_retries + 1]
    assert [a.body.text for a in _emitted(h, ActionType.FINAL)] == ["Flights found."]
    assert not [j for j in h.store.jobs.all() if j.status == JobStatus.RUNNING]


def test_watchdog_salvage_is_not_blocked_by_the_hold():
    """First turn interpreted (goal exists, search in flight), then the
    correction closes and is still being interpreted (400ms) when the
    watchdog fires at 700ms: the salvage FINAL must go out at 700ms
    regardless -- the hold must never cost the user the only reply."""
    ev = SEARCH_CORRECTION.events
    events = (
        ev[0], ev[1], ev[2],
        replace(ev[3], ts_us=600_000), replace(ev[4], ts_us=610_000), replace(ev[5], ts_us=620_000),
        replace(ev[0], ts_us=700_000, event={"type": "watchdog", "payload": {"wall_elapsed_s": 105.0}}),
    )
    scn = replace(SEARCH_CORRECTION, events=events, check=None)
    run, h = run_schedule(scn, _with_worker(scn, "interpret", 400_000))
    finals = _emitted(h, ActionType.FINAL)
    assert finals and finals[0].ts_us == 700_000
    assert finals[0].body.task_completed is False


# --- the explorer itself --------------------------------------------------------


def test_bounded_exploration_of_every_scenario_is_clean():
    for scn in ALL_SCENARIOS:
        result = explore(scn, seed=0, random_budget=5)
        assert result.findings == [], (scn.name, result.findings[0].violations)


def test_explorer_finds_and_minimizes_the_triage_race_with_the_hold_disabled():
    """The explorer's oracles must bite: with the hold off it has to find
    the race on its own, and the minimizer has to reduce it to a single
    timing deviation from baseline."""
    scn = with_config(SEARCH_CORRECTION, triage_hold_enabled=False)
    result = explore(scn, seed=0, random_budget=10)
    stale = [f for f in result.findings if "stale-final" in violation_kinds(f.violations)]
    assert stale
    m = minimize(scn, stale[0].schedule)
    assert "stale-final" in violation_kinds(m.violations)
    assert len(m.deviations) == 1


def test_exploration_is_deterministic():
    a = explore(with_config(SEARCH_CORRECTION, triage_hold_enabled=False), seed=3, random_budget=10)
    b = explore(with_config(SEARCH_CORRECTION, triage_hold_enabled=False), seed=3, random_budget=10)
    assert a.findings == b.findings
    assert a.schedules_run == b.schedules_run
