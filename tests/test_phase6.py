"""Phase 6 (`docs/post_v4_implementation_plan.md`) response-latency tests:
speculative interpretation coalescing and promotion (`docs/prompt 2.txt`
§11.3), plus content-bearing ACK (§9.3).

`kernel/task.py.TaskStateMachine._speculative_interpret`: while a turn is
open, at most one INTERPRET job runs per turn, dispatched against the
current prefix — a growing prefix doesn't spawn a second job, it just
makes the completed one stale (redispatched against the latest prefix
once noticed). `kernel/reducers.py._cache_speculative_interpretation`
caches a resolved speculative job's proposal instead of applying it.
`kernel/turns.py.TurnManager._try_promote_speculative` / `_mark_eot_
waiting_if_matching_job_in_flight`: at EOT, a cached result whose prefix
digest matches the final digest is applied in the *same step* as EOT —
zero additional model latency; a still-running matching job is waited for
instead of triggering a redundant dispatch; anything else falls back to
today's non-speculative EOT-anchored dispatch. Gated behind
`Config.speculative_interpretation_enabled` (default `False`).

The read-set subtlety flagged in `/home/adi/.claude/plans/i-have-gemini-
pro-declarative-phoenix.md`: `session.pending_interpretation_turn` is
excluded from a speculative job's read set (only a non-speculative
dispatch includes it) — otherwise EOT setting that fact would
self-invalidate the very job it should promote. Every test below that
exercises promotion is implicit proof this holds; nothing here needed to
special-case it further.
"""

from __future__ import annotations

from conftest import chunk_event, drain, eot_event, manifest_event

from prism_rt.config import Config
from prism_rt.model.types import ActionType, GoalStatus, JobKind, JobStatus
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

SEARCH_ENUM_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"destination": {"type": "string", "enum": ["Pune", "Mumbai"]}}, "required": ["destination"]},
}


def flat_plan(tool: str, *, param: str = "destination") -> dict:
    return {"steps": [{"local_id": "s1", "tool": tool, "kind": "read", "bindings": {param: {"type": "fact", "key": f"slot.$G.{param}"}}, "after": []}]}


def assert_clean(harness: SimHarness) -> None:
    violations = TraceChecker().check(harness.run_log.reports, store=harness.store)
    assert violations == [], violations


def _config(**overrides) -> Config:
    overrides.setdefault("transitive_invalidation", False)
    overrides.setdefault("settle_barrier_enabled", False)
    overrides.setdefault("absence_read_sets", False)
    overrides.setdefault("claim_grades_enabled", False)
    overrides.setdefault("rebinder_enabled", False)
    return Config(**overrides)


def _spec_config(**overrides) -> Config:
    overrides.setdefault("speculative_interpretation_enabled", True)
    return _config(**overrides)


def _pune_provider() -> ScriptedProvider:
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found flights.", "claims": ["result:s1"]})
    return provider


def _interpret_jobs(h):
    return [j for j in h.store.jobs._jobs.values() if j.kind == JobKind.INTERPRET]


# --- coalescing + exact-digest promotion ------------------------------------


def test_speculative_interpret_coalesces_to_one_job_per_turn():
    """Repeatedly re-evaluating DECIDE while the turn is open and nothing
    new has been said must never dispatch a second speculative job."""
    config = _spec_config()
    provider = _pune_provider()
    h = SimHarness(config, seed=1, provider=provider, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, worker_latency_us={JobKind.INTERPRET: 100_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000})
    h.send(0, [manifest_event([SEARCH_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])

    t = 100_000
    for _ in range(30):
        t += 10_000
        h.advance(t)

    interpret_jobs = _interpret_jobs(h)
    assert len(interpret_jobs) == 1
    assert interpret_jobs[0].status == JobStatus.DONE
    assert h.store.goals.all() == []  # not applied yet -- still cached, turn not closed


def test_speculative_interpret_promoted_at_eot_with_zero_extra_jobs():
    """The headline mechanism: a speculative job resolved before EOT, at
    exactly the final prefix, gets applied in the EOT step itself -- no
    second (non-speculative) INTERPRET job ever dispatches."""
    config = _spec_config()
    provider = _pune_provider()
    h = SimHarness(config, seed=1, provider=provider, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, worker_latency_us={JobKind.INTERPRET: 100_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000})
    h.send(0, [manifest_event([SEARCH_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    t = 100_000
    for _ in range(30):
        t += 10_000
        h.advance(t)
    assert len(_interpret_jobs(h)) == 1  # speculative job already resolved and cached

    r = h.send(t + 10_000, [eot_event()])
    # The goal exists in the SAME step as EOT -- zero-latency promotion.
    assert len(h.store.goals.all()) == 1
    assert h.store.goals.all()[0].status == GoalStatus.ACTIVE
    assert len(_interpret_jobs(h)) == 1  # still just the one speculative job -- no fresh dispatch
    assert_clean(h)


def test_speculative_interpret_waits_for_matching_in_flight_job_at_eot():
    """EOT arrives while a matching (same-digest) speculative job is still
    running -- must not dispatch a second, competing job; must apply the
    original job's result once it resolves."""
    config = _spec_config()
    provider = _pune_provider()
    h = SimHarness(config, seed=1, provider=provider, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, worker_latency_us={JobKind.INTERPRET: 200_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000})
    h.send(0, [manifest_event([SEARCH_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.advance(110_000)  # dispatch the speculative job (200ms latency, won't resolve yet)
    h.send(150_000, [eot_event()])  # EOT while it's still running

    assert len(_interpret_jobs(h)) == 1  # no second job dispatched just because EOT happened

    t = 150_000
    for _ in range(40):
        t += 10_000
        h.advance(t)
    assert len(_interpret_jobs(h)) == 1  # still exactly one, even after it resolves
    goals = h.store.goals.all()
    assert len(goals) == 1
    assert goals[0].status == GoalStatus.COMPLETED  # the goal went all the way to FINAL
    assert_clean(h)


def test_speculative_interpret_falls_back_when_prefix_grows_past_cache():
    """A chunk arriving after the speculative result cached invalidates
    it (digest mismatch) -- a fresh speculative job dispatches against the
    grown prefix, and *that* one gets promoted at EOT."""
    config = _spec_config()
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Pune",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register(
        "interpret",
        "Find flights to Pune please",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}]},
    )
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found flights.", "claims": ["result:s1"]})
    h = SimHarness(config, seed=1, provider=provider, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, worker_latency_us={JobKind.INTERPRET: 50_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000})
    h.send(0, [manifest_event([SEARCH_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    t = 100_000
    for _ in range(10):
        t += 10_000
        h.advance(t)
    assert len(_interpret_jobs(h)) == 1  # first speculative job cached

    h.send(t + 10_000, [chunk_event("please")])
    t += 10_000
    for _ in range(10):
        t += 10_000
        h.advance(t)
    assert len(_interpret_jobs(h)) == 2  # the grown prefix triggered exactly one fresh speculative job

    r = h.send(t + 10_000, [eot_event()])
    assert len(h.store.goals.all()) == 1  # promoted from the SECOND (up-to-date) cache
    assert len(_interpret_jobs(h)) == 2  # still no third (non-speculative) job needed
    assert_clean(h)


def test_speculative_interpretation_disabled_by_default_behaves_like_before():
    config = _config()  # speculative_interpretation_enabled defaults False
    provider = _pune_provider()
    h = SimHarness(config, seed=1, provider=provider, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000})
    h.send(0, [manifest_event([SEARCH_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    t = 100_000
    for _ in range(10):
        t += 10_000
        h.advance(t)
    assert _interpret_jobs(h) == []  # no speculative dispatch at all while the flag is off

    h.send(t + 10_000, [eot_event()])
    assert h.store.goals.all() == []  # interpretation dispatched, not yet resolved -- exactly as before this phase
    drain(h, t + 500_000)
    assert len(h.store.goals.all()) == 1


# --- content-bearing ACK -----------------------------------------------------


def test_content_bearing_ack_names_the_high_hypothesis_on_a_correction():
    config = _spec_config()
    provider = _pune_provider()
    provider.register(
        "interpret",
        "actually Mumbai",
        {"act": "slot_update", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}]},
    )
    h = SimHarness(config, seed=1, provider=provider, tools={"search_flights": {"latency_ms": 2000, "response": {"flight_id": "AI-1"}}}, worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000})
    h.send(0, [manifest_event([SEARCH_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Pune")])
    h.send(150_000, [eot_event()])
    drain(h, 400_000, stop_on_final=False)

    h.send(h.clock.now_us() + 10_000, [chunk_event("actually Mumbai")])
    r = h.send(h.clock.now_us() + 5_000, [eot_event()])
    # The correction's own speculative INTERPRET job may still be running
    # at the exact EOT step (it's short-lived, but not zero) -- the ACK
    # follows once it resolves and the plan (re)dispatches, not necessarily
    # in the EOT step itself.
    rest = drain(h, h.clock.now_us() + 200_000, stop_on_final=False)
    all_actions = [er.action for er in r.emit_report.emitted] + rest

    speaks = [a for a in all_actions if a.action_type == ActionType.SPEAK]
    assert len(speaks) == 1
    assert speaks[0].body.text == "Got it — destination: Mumbai."


def test_content_bearing_ack_falls_back_to_generic_with_no_hypothesis():
    """When QuickDetector never produced a HIGH value for the triggering
    turn at all (here: a destination outside the tool's declared enum, so
    the enum-match extractor never fires and no `hyp.*` fact exists), the
    ACK stays the plain generic string -- there is nothing to name."""
    config = _spec_config()
    provider = ScriptedProvider()
    provider.register(
        "interpret",
        "Find flights to Delhi",
        {"act": "new_goal", "intent": "search_flights", "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Delhi"}]},
    )
    provider.register("plan", "search_flights", flat_plan("search_flights"))
    provider.register("compose", "s1", {"text": "Found flights.", "claims": ["result:s1"]})
    h = SimHarness(config, seed=1, provider=provider, tools={"search_flights": {"latency_ms": 2000, "response": {"flight_id": "AI-1"}}}, worker_latency_us={JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000})
    h.send(0, [manifest_event([SEARCH_ENUM_TOOL])])
    h.send(100_000, [chunk_event("Find flights to Delhi")])  # "Delhi" is not in SEARCH_ENUM_TOOL's enum
    r = h.send(150_000, [eot_event()])
    rest = drain(h, 300_000, stop_on_final=False)
    all_actions = [er.action for er in r.emit_report.emitted] + rest
    speaks = [a for a in all_actions if a.action_type == ActionType.SPEAK]
    assert len(speaks) == 1
    assert speaks[0].body.text == "Got it, working on it."


def test_phase6_replay_identity():
    def run_once() -> list[dict]:
        config = _spec_config()
        provider = _pune_provider()
        h = SimHarness(config, seed=1, provider=provider, tools={"search_flights": {"latency_ms": 100, "response": {"flight_id": "AI-1"}}}, worker_latency_us={JobKind.INTERPRET: 50_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000})
        h.send(0, [manifest_event([SEARCH_ENUM_TOOL])])
        h.send(100_000, [chunk_event("Find flights to Pune")])
        t = 100_000
        for _ in range(10):
            t += 10_000
            h.advance(t)
        h.send(t + 10_000, [eot_event()])
        drain(h, t + 500_000)
        return h.log.records

    violations = TraceChecker().check_replay(run_once, times=5)
    assert violations == []
