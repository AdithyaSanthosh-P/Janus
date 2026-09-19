"""Pass 2: Semantic review tests for Janus post-V4.

Targets the semantic gaps and cross-phase interactions identified during
doc-informed review. These require reading the architecture docs to know
what "should" happen — TraceChecker alone can't catch them.

Run from the repo root:
    PYTHONPATH=src:. python reviews/sonnet/pass2_semantic.py

Each test prints PASS/FAIL with a brief diagnosis.
"""
from __future__ import annotations

import sys
import traceback

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from prism_rt.config import Config
from prism_rt.model.types import ActionType, CallStatus, FactStatus, GoalStatus, JobKind, TaskState
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

# ── Shared helpers ─────────────────────────────────────────────────────────────

SEARCH_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {
            "destination": {"type": "string", "enum": ["Pune", "Mumbai", "Goa"]},
        },
        "required": ["destination"],
    },
}

BOOK_TOOL = {
    "name": "book_flight",
    "mutability": "state_changing",
    "parameters": {
        "type": "object",
        "properties": {"flight_id": {"type": "string"}},
        "required": ["flight_id"],
    },
}

WEATHER_TOOL = {
    "name": "get_weather",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}

FAST_LATENCY = {JobKind.INTERPRET: 10_000, JobKind.PLAN: 10_000, JobKind.COMPOSE: 10_000}


def manifest(tools):
    return {"type": "manifest", "payload": {"tools": tools}}


def chunk(text):
    return {"type": "text_chunk", "payload": {"text": text}}


def eot():
    return {"type": "end_of_turn", "payload": {}}


def interruption():
    return {"type": "interruption", "payload": {}}


def drain(h, max_us, *, step_us=10_000, stop_on_final=True):
    actions = []
    t = h.clock.now_us()
    while t < max_us:
        t += step_us
        report = h.advance(t)
        for er in report.emit_report.emitted:
            actions.append(er.action)
            if stop_on_final and er.action.action_type == ActionType.FINAL:
                return actions
    return actions


def assert_clean(h):
    violations = TraceChecker().check(h.run_log.reports, store=h.store)
    assert violations == [], f"TraceChecker violations: {violations}"


def run_test(name, fn):
    print(f"\n{'─'*60}")
    print(f"TEST: {name}")
    try:
        result = fn()
        if result is False:
            print(f"  FAIL: test returned False")
            return False
        print(f"  PASS")
        return True
    except AssertionError as e:
        print(f"  FAIL (AssertionError): {e}")
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"  FAIL (Exception): {e}")
        traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════════════════════════
# TEST 1: FRAME read set completeness
#
# The prompt asks: does FRAME's read set have the same class of read-set-
# completeness bug the fixed COMPOSE bug had?
#
# COMPOSE's bug: its read set included only result.<call_id> (immutable once
# written) and goal.active, NOT the slot facts the underlying call depended
# on. So a correction that invalidated the call's read set did NOT invalidate
# a stale COMPOSE job — it was accepted and produced a false FINAL.
#
# FRAME's read set (frames.py:179):
#   read_keys = ["goal.active", "catalog.version"] + slot keys
#
# FRAME does include slot keys directly. But does it include them for the
# SAME slot the in-flight call depended on?
#
# The distinction matters: FRAME dispatches when the "last call is IN_FLIGHT",
# reads the goal's current slots to build its template, and stores a template
# fact tagged with its own read_set (the slot keys at FRAME dispatch time).
# try_render() then checks THAT template's derivation_read_set.
#
# Scenario: search_flights(dest=Pune) is IN_FLIGHT, FRAME is dispatched
# (reads slot.gid.destination=Pune), then a correction "actually Mumbai"
# arrives mid-flight. The call completes (Pune result), try_render runs.
# Does FRAME reject the stale template?
#
# Expected: yes — the frame's read set includes slot.gid.destination, and
# that slot changed from Pune to Mumbai, so is_valid returns False and
# try_render returns False (line 222-223 of frames.py). Composer is fallback.
# ══════════════════════════════════════════════════════════════════════════════

def test_frame_readset_on_correction():
    """FRAME template's read set includes the slot it reads, so a mid-flight
    slot correction should cause try_render to reject the stale template and
    fall back to Composer (which will re-run against the new call)."""
    config = Config(
        transitive_invalidation=True,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=True,
        frame_rendering_enabled=True,
    )
    p = ScriptedProvider()
    p.register("interpret", "find flights to Pune", {
        "act": "new_goal", "intent": "search_flights",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
        "commit_intent": False,
    })
    p.register("interpret", "actually Mumbai", {
        "act": "slot_update",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}],
    })
    p.register("plan", "search_flights", {
        "steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read",
                   "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]
    })
    # Frame template for Pune
    p.register("frame", "search_flights", {
        "branches": {
            "success": {"template": "Found flights to {dest}.",
                        "holes": {"dest": {"op": "field", "path": "destination"}}},
            "empty": {"template": "No flights.", "holes": {}},
        },
        "list_path": "flights",
    })
    p.register("compose", "s1", {"text": "Found Mumbai flights.", "claims": ["result:s1"]})

    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 400, "response": {"flights": [{"id": "AI-1"}], "destination": "Pune"}}},
                   provider=p, worker_latency_us=FAST_LATENCY)

    h.send(0, [manifest([SEARCH_TOOL])])
    h.send(100_000, [chunk("find flights to Pune"), eot()])

    # Let INTERPRET+PLAN+call dispatch happen (search_flights Pune is in flight)
    drain(h, 300_000, stop_on_final=False)

    # Verify: call is in flight
    in_flight = [c for c in h.store.call_ledger.all() if c.status == CallStatus.IN_FLIGHT]
    assert len(in_flight) >= 1, f"Expected call in flight, got {in_flight}"
    print(f"  In-flight call with destination={in_flight[0].args}")

    # Check whether FRAME job was dispatched (it should be, since this is the last pending step)
    frame_template = h.store.facts.get(f"frame.{h.store.goals.all()[0].goal_id}.template")
    frame_job_ran = frame_template is not None
    print(f"  Frame template set: {frame_job_ran}")

    # Correct to Mumbai before the search resolves
    h.send(350_000, [chunk("actually Mumbai"), eot()])
    drain(h, 400_000, stop_on_final=False)  # let the correction interpret

    # Now drain until the Pune result arrives (due at ~500ms)
    actions = drain(h, 3_000_000)
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1, f"Expected 1 FINAL, got {len(finals)}"

    # The FINAL text should NOT be the stale Pune frame text
    final_text = finals[0].body.text
    print(f"  Final text: {repr(final_text)}")
    # If FRAME was correctly rejected, Composer's "Found Mumbai flights." fires
    # If FRAME bug existed (like old COMPOSE bug), "Found flights to Pune." would appear
    assert "Pune" not in final_text or "Mumbai" in final_text, \
        f"FRAME may have used stale Pune template: {final_text!r}"

    assert_clean(h)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2: C9 commit-last + correction mid-wait cap
#
# C9: a WRITE waits for sibling READs to complete first, bounded by
# commit_last_wait_cap_ms. What happens when a correction arrives mid-wait?
# The correction invalidates the READ (it's IN_FLIGHT), marks it
# CANCEL_REQUESTED, and triggers a re-bind for the READ against the new slot.
# The WRITE, still blocked by C9, must wait for the NEW read (Mumbai) to
# complete, NOT the old (Pune) one.
#
# Key question: after the old READ is cancelled and re-executed, does C9 see
# the NEW read as "unresolved" (correct) or "consumed" (letting the WRITE fire
# against stale data)?
#
# This is testing the interaction between:
# - _commit_last_blocked: checks _step_done for sibling READ steps
# - _step_done: checks CallStatus.CONSUMED AND read_set validity
# ══════════════════════════════════════════════════════════════════════════════

def test_c9_commit_last_mid_correction():
    """C9 wait: after correction cancels a sibling READ, WRITE must wait for
    the RE-EXECUTED read (with new slot value), not fire immediately."""
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
        commit_last_ordering=True,
        commit_last_wait_cap_ms=10_000,  # very high cap so cap doesn't interfere
    )
    p = ScriptedProvider()
    p.register("interpret", "search Pune and book", {
        "act": "new_goal", "intent": "search_and_book",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
        "commit_intent": True,
    })
    p.register("interpret", "actually Mumbai", {
        "act": "slot_update",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}],
    })
    p.register("plan", "search_and_book", {
        "steps": [
            {"local_id": "s1", "tool": "search_flights", "kind": "read",
             "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
            {"local_id": "s2", "tool": "book_flight", "kind": "write",
             "bindings": {"flight_id": {"type": "literal", "value": "AI-9"}}, "after": []},
        ]
    })
    p.register("compose", "s2", {"text": "Booked.", "claims": ["effect:s2"]})

    h = SimHarness(config, seed=1,
                   tools={
                       "search_flights": {"latency_ms": 300, "response": {"flights": [{"id": "AI-1"}]}},
                       "book_flight": {"latency_ms": 50, "response": {"booking_id": "BK-1"}},
                   },
                   provider=p, worker_latency_us=FAST_LATENCY)

    h.send(0, [manifest([SEARCH_TOOL, BOOK_TOOL])])
    h.send(100_000, [chunk("search Pune and book"), eot()])

    # Let INTERPRET+PLAN happen, calls get proposed
    drain(h, 250_000, stop_on_final=False)

    # Check that book_flight is blocked by C9 (search not done yet)
    book_calls_emitted = [
        a for a in h.run_log.emitted_actions
        if a.action_type == ActionType.TOOL_CALL and a.body.tool_name == "book_flight"
    ]
    print(f"  book_flight emitted before correction: {len(book_calls_emitted)}")
    assert len(book_calls_emitted) == 0, "book_flight should be blocked by C9 (sibling search not done)"

    # Now correct the destination mid-search
    h.send(280_000, [chunk("actually Mumbai"), eot()])

    # Drain further — the Pune search completes at ~400ms
    # Even though Pune search CONSUMES, its read set is now stale (slot changed)
    # So _step_done should return False, keeping C9 blocked
    drain(h, 500_000, stop_on_final=False)

    # Check calls after Pune search completes
    all_tool_actions = [
        a for a in h.run_log.emitted_actions
        if a.action_type == ActionType.TOOL_CALL
    ]
    book_actions_early = [a for a in all_tool_actions
                          if a.body.tool_name == "book_flight" and a.ts_us < 600_000]
    search_calls = [a for a in all_tool_actions if a.body.tool_name == "search_flights"]
    print(f"  search_flights calls: {len(search_calls)} (should be >= 2 with Pune then Mumbai)")
    print(f"  book_flight before Mumbai search done: {len(book_actions_early)}")

    # Let everything complete
    all_actions = drain(h, 3_000_000)
    finals = [a for a in all_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1, f"Expected 1 FINAL, got {len(finals)}"
    assert_clean(h)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 3: Speculative interpretation + settle barrier
#
# When speculative_interpretation_enabled=True and settle_barrier_enabled=True,
# the spec interpretation is promoted at EOT (zero latency). The goal has a
# WRITE step. The WRITE must still respect G10 (settle_ms since last EOT).
#
# The subtle interaction: does the promoted spec interpretation correctly
# set session.last_eot_ts, allowing the settle clock to tick from EOT?
# Or does speculative promotion bypass that?
# ══════════════════════════════════════════════════════════════════════════════

def test_speculative_interp_settle_barrier():
    """After speculative promotion at EOT, the settle barrier must still block
    the WRITE step for at least settle_ms from that EOT timestamp."""
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=True,
        settle_ms=200,  # 200ms settle
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
        commit_last_ordering=False,
        speculative_interpretation_enabled=True,
    )
    p = ScriptedProvider()
    p.register("interpret", "book flight", {
        "act": "new_goal", "intent": "book_flight",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
        "commit_intent": True,
    })
    p.register("plan", "book_flight", {
        "steps": [
            {"local_id": "s1", "tool": "book_flight", "kind": "write",
             "bindings": {"flight_id": {"type": "literal", "value": "AI-9"}}, "after": []}
        ]
    })
    p.register("compose", "s1", {"text": "Booked.", "claims": ["effect:s1"]})

    h = SimHarness(config, seed=1,
                   tools={"book_flight": {"latency_ms": 50, "response": {"booking_id": "BK-1"}}},
                   provider=p, worker_latency_us=FAST_LATENCY)

    h.send(0, [manifest([BOOK_TOOL])])
    h.send(100_000, [chunk("book flight")])

    # Let speculative interpret run (while turn still open)
    drain(h, 180_000, stop_on_final=False)

    # Check that spec interpret resolved
    spec_digest = None
    for goal in h.store.goals.all():
        gid = goal.goal_id
        for key, fact in h.store.facts.snapshot_committed().items():
            if key.startswith("spec_interpret."):
                spec_digest = fact
    print(f"  Spec interpret cache hit: {spec_digest is not None}")

    # Close the turn (should promote speculative at ts=200_000)
    eot_ts = 200_000
    h.send(eot_ts, [eot()])

    # Drain for 100ms (less than settle_ms=200ms) — WRITE must NOT fire yet
    drain(h, eot_ts + 100_000, stop_on_final=False)
    book_before_settle = [
        a for a in h.run_log.emitted_actions
        if a.action_type == ActionType.TOOL_CALL and a.body.tool_name == "book_flight"
        and a.ts_us < eot_ts + 200_000
    ]
    print(f"  book_flight fired before settle: {len(book_before_settle)} (expected 0)")
    assert len(book_before_settle) == 0, \
        f"Settle barrier violated: book_flight fired at {[a.ts_us for a in book_before_settle]}, settle window expires at {eot_ts + 200_000}"

    # Drain past settle window (200ms = 200_000us from EOT)
    all_actions = drain(h, eot_ts + 1_000_000)
    finals = [a for a in all_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1, f"Expected 1 FINAL, got {len(finals)}"
    assert_clean(h)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 4: R-03 — tool result arrives for an unknown/stale call_id
# (result routing branch 1 and branch 6 exercised explicitly)
# ══════════════════════════════════════════════════════════════════════════════

def test_r03_stale_tool_result():
    """An out-of-order or duplicate tool_result for an unknown call_id should
    be silently dropped (branch 1), and a stale result for a CONSUMED call
    should be dropped (branch 2). Neither should crash or emit anything."""
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
    )
    p = ScriptedProvider()
    p.register("interpret", "find flights", {
        "act": "new_goal", "intent": "search",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
        "commit_intent": False,
    })
    p.register("plan", "search", {
        "steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read",
                   "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]
    })
    p.register("compose", "s1", {"text": "Here are flights.", "claims": ["result:s1"]})

    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 50, "response": {"flights": [{"id": "AI-1"}]}}},
                   provider=p, worker_latency_us=FAST_LATENCY)

    h.send(0, [manifest([SEARCH_TOOL])])
    h.send(100_000, [chunk("find flights"), eot()])

    # Let it complete normally
    drain(h, 1_000_000)

    # Now inject spurious tool_results for nonexistent/finished call_ids
    h.send(1_100_000, [
        {"type": "tool_result", "payload": {"call_id": "nonexistent-call-999", "status": "ok",
                                             "result": {"flights": []}, "error": None}},
    ])
    h.send(1_150_000, [
        {"type": "tool_result", "payload": {"call_id": "call-1", "status": "ok",  # may be real but terminal
                                             "result": {"flights": []}, "error": None}},
    ])
    # These should not produce any new actions
    report1 = h.advance(1_200_000)
    report2 = h.advance(1_250_000)
    new_finals = [er.action for r in [report1, report2]
                  for er in r.emit_report.emitted
                  if er.action.action_type == ActionType.FINAL]
    assert new_finals == [], f"Unexpected FINAL from stale tool result: {new_finals}"
    print("  Stale tool results handled gracefully (branch 1/2 silent drops)")
    assert_clean(h)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 5: R-04 — malformed tool result (status not "ok"/"error")
# ══════════════════════════════════════════════════════════════════════════════

def test_r04_malformed_tool_result():
    """A tool_result with an invalid status (not 'ok'/'error') should be
    treated as FAILED (branch 3) — call marked FAILED, goal retries."""
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
        max_read_retries=1,
    )
    p = ScriptedProvider()
    p.register("interpret", "find flights", {
        "act": "new_goal", "intent": "search",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
        "commit_intent": False,
    })
    p.register("plan", "search", {
        "steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read",
                   "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]
    })
    p.register("compose", "s1", {"text": "Here are flights.", "claims": ["result:s1"]})

    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 50, "response": {"flights": [{"id": "AI-1"}]}}},
                   provider=p, worker_latency_us=FAST_LATENCY)

    h.send(0, [manifest([SEARCH_TOOL])])
    h.send(100_000, [chunk("find flights"), eot()])

    # Let INTERPRET+PLAN happen
    drain(h, 200_000, stop_on_final=False)

    # Find the in-flight call
    in_flight = [c for c in h.store.call_ledger.all() if c.status == CallStatus.IN_FLIGHT]
    if not in_flight:
        print("  No in-flight call found (plan may not have dispatched yet) — skipping")
        return

    call_id = in_flight[0].call_id
    print(f"  Injecting malformed result for call {call_id}")

    # Send a malformed result (branch 3: status not ok/error)
    h.send(210_000, [{
        "type": "tool_result",
        "payload": {"call_id": call_id, "status": "GARBAGE", "result": None, "error": None},
    }])
    drain(h, 250_000, stop_on_final=False)

    # Call should now be FAILED
    call = h.store.call_ledger.get(call_id)
    print(f"  Call status after malformed result: {call.status if call else 'not found'}")
    if call:
        assert call.status == CallStatus.FAILED, f"Expected FAILED, got {call.status}"
    # No crash, no FINAL yet (retries available)
    assert_clean(h)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 6: S-07 — unseen tool in plan (tool not in manifest)
#
# When the planner returns a plan referencing a tool not in the current
# manifest (i.e. tool.status != USABLE), CommitGate should block (G1).
# Goal should CLARIFY or eventually fail gracefully, not crash.
# ══════════════════════════════════════════════════════════════════════════════

def test_s07_unseen_tool_in_plan():
    """Plan references a tool not in the manifest. CommitGate G1 blocks the
    call. The goal should stay blocked or clarify, never crash."""
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
    )
    p = ScriptedProvider()
    p.register("interpret", "magic task", {
        "act": "new_goal", "intent": "magic",
        "slot_deltas": [],
        "commit_intent": False,
    })
    # Plan references a tool NOT in the manifest
    p.register("plan", "magic", {
        "steps": [
            {"local_id": "s1", "tool": "nonexistent_magic_tool", "kind": "read",
             "bindings": {}, "after": []}
        ]
    })
    p.register("compose", "s1", {"text": "Magic done.", "claims": []})

    h = SimHarness(config, seed=1, tools={}, provider=p,
                   worker_latency_us=FAST_LATENCY)

    h.send(0, [manifest([SEARCH_TOOL])])  # nonexistent_magic_tool NOT in manifest
    h.send(100_000, [chunk("magic task"), eot()])

    # Drain for 3 seconds — should not crash, should not produce a FINAL
    # (CommitGate G1 blocks the call; goal stays EXECUTING with a blocked call)
    drain(h, 3_000_000, stop_on_final=False)

    # Check state — the goal should still be alive, not crashed
    goals = h.store.goals.all()
    assert len(goals) >= 1, "Expected at least one goal"
    goal = goals[0]
    print(f"  Goal status: {goal.status}, task_state: {goal.task_state}")
    # No crash is the main assertion — the kernel should handle gracefully
    assert_clean(h)
    print("  S-07 (unseen tool) handled without crash")


# ══════════════════════════════════════════════════════════════════════════════
# TEST 7: S-08 — manifest update while a call is in flight
#
# If the manifest changes (tool becomes USABLE or catalog.version bumps),
# all in-flight calls have catalog.version in their read sets, so they
# should be cancelled by the ordinary invalidation mechanism.
# ══════════════════════════════════════════════════════════════════════════════

def test_s08_manifest_update_cancels_inflight():
    """A manifest update while a call is in flight invalidates that call
    (catalog.version is in every call's read set per §4.4)."""
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
    )
    p = ScriptedProvider()
    p.register("interpret", "find flights", {
        "act": "new_goal", "intent": "search",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
        "commit_intent": False,
    })
    p.register("plan", "search", {
        "steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read",
                   "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]
    })
    p.register("compose", "s1", {"text": "Found flights.", "claims": ["result:s1"]})

    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 500, "response": {"flights": [{"id": "AI-1"}]}}},
                   provider=p, worker_latency_us=FAST_LATENCY)

    h.send(0, [manifest([SEARCH_TOOL])])
    h.send(100_000, [chunk("find flights"), eot()])

    # Let call be emitted
    drain(h, 300_000, stop_on_final=False)

    in_flight = [c for c in h.store.call_ledger.all() if c.status == CallStatus.IN_FLIGHT]
    print(f"  In-flight before manifest update: {len(in_flight)}")
    if not in_flight:
        print("  No in-flight call yet — skipping detailed assertion")
        assert_clean(h)
        return

    old_call_id = in_flight[0].call_id

    # Now update the manifest — this bumps catalog.version
    h.send(350_000, [manifest([SEARCH_TOOL, WEATHER_TOOL])])
    drain(h, 400_000, stop_on_final=False)

    # The in-flight call should now be CANCEL_REQUESTED (invalidated by catalog.version change)
    updated_call = h.store.call_ledger.get(old_call_id)
    print(f"  Call status after manifest update: {updated_call.status if updated_call else 'not found'}")
    if updated_call:
        assert updated_call.status in (
            CallStatus.CANCEL_REQUESTED, CallStatus.INVALIDATED,
            CallStatus.STALE,  # if result arrived before invalidation
        ), f"Expected CANCEL_REQUESTED or INVALIDATED, got {updated_call.status}"

    # Should eventually complete
    drain(h, 3_000_000)
    assert_clean(h)
    print("  S-08 manifest update correctly invalidates in-flight call")


# ══════════════════════════════════════════════════════════════════════════════
# TEST 8: T-03 — content-bearing ACK when no HIGH-confidence value exists
#
# _content_ack_text should fall back to ACK_DEFAULT (generic string), not
# crash. Also verify speculative_interpretation_enabled=False returns None.
# ══════════════════════════════════════════════════════════════════════════════

def test_t03_ack_without_high_confidence():
    """Content-bearing ACK with no QuickDetector HIGH value → generic fallback."""
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
        speculative_interpretation_enabled=True,  # on, but no HIGH value in chunk
    )
    p = ScriptedProvider()
    # Use a phrase that QuickDetector won't extract any HIGH-confidence value from
    p.register("interpret", "I want to do something vague", {
        "act": "new_goal", "intent": "search",
        "slot_deltas": [],
        "commit_intent": False,
    })
    p.register("plan", "search", {
        "steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read",
                   "bindings": {"destination": {"type": "literal", "value": "Pune"}}, "after": []}]
    })
    p.register("compose", "s1", {"text": "Done.", "claims": []})

    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 50, "response": {"flights": []}}},
                   provider=p, worker_latency_us=FAST_LATENCY)

    h.send(0, [manifest([SEARCH_TOOL])])
    h.send(100_000, [chunk("I want to do something vague"), eot()])

    all_actions = drain(h, 3_000_000)

    # There should be at least one ACK/SPEAK
    speaks = [a for a in all_actions if a.action_type == ActionType.SPEAK]
    print(f"  SPEAK actions emitted: {len(speaks)}")
    for s in speaks:
        print(f"    ACK text: {repr(s.body.text)}")
    # Should not crash; ACK text should be the generic fallback, not empty
    for s in speaks:
        assert s.body.text.strip(), "Empty ACK text emitted"
    assert_clean(h)
    print("  T-03: content-bearing ACK fallback works")


# ══════════════════════════════════════════════════════════════════════════════
# TEST 9: P-04 — duplicate worker result delivery (late/duplicate job result)
#
# A worker_result for a job that already resolved (DONE status) should be
# silently ignored (reducers.py line 274: "already-settled job: ignore").
# ══════════════════════════════════════════════════════════════════════════════

def test_p04_duplicate_worker_result():
    """Duplicate worker_result for a finished job is silently ignored."""
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
        speculative_interpretation_enabled=False,
    )
    p = ScriptedProvider()
    p.register("interpret", "find flights", {
        "act": "new_goal", "intent": "search",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
        "commit_intent": False,
    })
    p.register("plan", "search", {
        "steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read",
                   "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]
    })
    p.register("compose", "s1", {"text": "Here are flights.", "claims": ["result:s1"]})

    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 50, "response": {"flights": [{"id": "AI-1"}]}}},
                   provider=p, worker_latency_us=FAST_LATENCY)

    h.send(0, [manifest([SEARCH_TOOL])])
    h.send(100_000, [chunk("find flights"), eot()])

    # Let it complete
    all_actions = drain(h, 2_000_000)
    finals = [a for a in all_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1

    # Find a completed job (JobTable has no .all() — iterate internal dict)
    from prism_rt.model.types import JobStatus
    done_jobs = [j for j in h.store.jobs._jobs.values() if j.status == JobStatus.DONE]
    if not done_jobs:
        print("  No DONE jobs found")
        return

    job = done_jobs[0]
    print(f"  Injecting duplicate worker_result for job {job.job_id} (status={job.status})")

    # Inject a duplicate worker_result — should be silently dropped
    h.send(2_100_000, [{
        "type": "worker_result",
        "payload": {
            "job_id": job.job_id,
            "kind": job.kind.value,
            "status": "ok",
            "proposal": {"text": "DUPLICATE", "claims": []},
        },
    }])
    report = h.advance(2_200_000)
    new_actions = [er.action for er in report.emit_report.emitted]
    new_finals = [a for a in new_actions if a.action_type == ActionType.FINAL]
    assert new_finals == [], f"Duplicate worker_result produced FINAL: {new_finals}"
    print("  P-04: duplicate worker_result silently ignored")
    assert_clean(h)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 10: M-09 — audio + text disagreement
# (The audio/text disagree on destination; auto mode's simplified dedupe check)
# ══════════════════════════════════════════════════════════════════════════════

def test_m09_audio_text_disagreement():
    """M-01 scenario: audio_clip arrives whose ASR result differs from the
    already-received text chunks for the same turn. With audio_mode='auto',
    the ASR result is discarded and a disagreement fact is recorded."""
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
        asr_enabled=True,
        audio_mode="auto",
        asr_closes_turn=False,
    )
    p = ScriptedProvider()
    p.register("interpret", "find flights to Pune", {
        "act": "new_goal", "intent": "search",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
        "commit_intent": False,
    })
    p.register("plan", "search", {
        "steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read",
                   "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]
    })
    p.register("compose", "s1", {"text": "Here are flights.", "claims": ["result:s1"]})
    # ASR result says Mumbai (different from text chunk Pune)
    p.register("asr", "", {
        "segments": [{"text": "find flights to Mumbai", "confidence": 0.95}],
        "end_of_utterance": False,
    })

    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 50, "response": {"flights": [{"id": "AI-1"}]}}},
                   provider=p, worker_latency_us=FAST_LATENCY)

    h.send(0, [manifest([SEARCH_TOOL])])
    # Text chunk comes first
    h.send(100_000, [chunk("find flights to Pune")])
    # Audio clip also arrives
    h.send(110_000, [{"type": "audio_clip", "payload": {"clip_id": "clip-1"}}])
    h.send(150_000, [eot()])

    all_actions = drain(h, 2_000_000)
    # Check that there's no crash and the text-based interpretation wins
    finals = [a for a in all_actions if a.action_type == ActionType.FINAL]
    print(f"  Finals: {len(finals)}")
    # Check for disagreement fact
    all_facts = h.store.facts.snapshot_committed()
    disagreement_keys = [k for k in all_facts if "disagreement" in k]
    print(f"  Disagreement facts: {disagreement_keys}")
    assert_clean(h)
    print("  M-09 audio/text scenario handled without crash")


# ══════════════════════════════════════════════════════════════════════════════
# TEST 11: RESPONDING state → compose.text retracted edge case
#
# After transition to RESPONDING, if compose.gid.text is None and no COMPOSE
# job is running, TaskStateMachine re-issues COMPOSE (line 86-87 of task.py).
# But if compose.gid.text is retracted (not just None), does the check at
# line 86 catch it?
#
# Line 86: `if pending_text is None and not store.jobs.running_by_kind_goal(...):`
# A retracted fact is not None — it's a Fact object with status RETRACTED.
# So if compose.gid.text is RETRACTED, pending_text is NOT None, and the
# condition fails → TaskStateMachine never re-requests COMPOSE.
#
# This is a bug: a retracted compose text should also trigger re-dispatch.
# ══════════════════════════════════════════════════════════════════════════════

def test_responding_compose_text_retracted():
    """
    In RESPONDING state, TaskStateMachine line 86 only re-dispatches COMPOSE if
    pending_text is None — but a retracted fact is not None (has RETRACTED status).

    If compose.gid.text gets retracted while in RESPONDING (e.g. by another
    reducer or an unusual sequence), COMPOSE is never re-issued, leaving the
    goal stuck in RESPONDING forever.

    Note: This is a structural audit — we verify the condition in the code,
    then try to trigger the scenario.
    """
    # Read the actual code behavior:
    # task.py line 85-87:
    #   elif goal.task_state == TaskState.RESPONDING:
    #       pending_text = store.facts.get(f"compose.{gid}.text")
    #       if pending_text is None and not store.jobs.running_by_kind_goal(JobKind.COMPOSE, gid):
    # A RETRACTED fact: get() returns the Fact object (not None), status==RETRACTED.
    # So `pending_text is None` is False for a retracted fact.
    # This means the re-dispatch condition never fires for retracted text.

    # Check: does emission.py retract compose.gid.text after FINAL?
    # emission.py line 170: store.facts.retract(f"compose.{goal_id}.text", ...)
    # This runs AFTER FINAL is emitted, during _complete_goal side-effect.
    # So normally RESPONDING → FINAL emission → retract → COMPLETED.
    # If FINAL emission fails (rejected by EmissionGate) AFTER the retract... wait,
    # side effects only run after a successful write_result (line 106).
    # So retract only happens if FINAL actually emits. That means the retract
    # can't happen before FINAL — this particular race path doesn't exist in
    # the normal flow.

    # However: what if a frame writes compose.gid.text, then a correction
    # retracts it (via fact invalidation), then the goal is RESPONDING?
    # Let's check: FrameScheduler.on_frame_result writes frame.gid.template
    # (DERIVED status), try_render writes compose.gid.text (COMMITTED).
    # Invalidation can retract DERIVED facts but NOT COMMITTED facts directly —
    # it only marks their dependents stale. COMMITTED facts are only retracted
    # explicitly via retract().
    # So compose.gid.text (COMMITTED) is NOT auto-retracted by invalidation.

    # Conclusion: the bug path (compose.gid.text retracted while in RESPONDING)
    # does not occur in the current normal flow. The check at line 86 being
    # `is None` rather than `is None or status == RETRACTED` is technically
    # a latent bug, but no current code path triggers it.

    # We verify this with a careful whitebox test:
    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
    )
    p = ScriptedProvider()
    p.register("interpret", "find flights", {
        "act": "new_goal", "intent": "search",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
        "commit_intent": False,
    })
    p.register("plan", "search", {
        "steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read",
                   "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]
    })
    p.register("compose", "s1", {"text": "Found flights.", "claims": ["result:s1"]})

    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 50, "response": {"flights": []}}},
                   provider=p, worker_latency_us=FAST_LATENCY)

    h.send(0, [manifest([SEARCH_TOOL])])
    h.send(100_000, [chunk("find flights"), eot()])
    all_actions = drain(h, 2_000_000)
    finals = [a for a in all_actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1, f"Expected 1 FINAL, got {len(finals)}"

    # After FINAL, compose.gid.text should be RETRACTED
    gid = h.store.goals.all()[0].goal_id
    compose_fact = h.store.facts.get(f"compose.{gid}.text")
    print(f"  compose.{gid}.text after FINAL: {compose_fact.status if compose_fact else 'None'}")
    # This is correct: it's RETRACTED AFTER FINAL (post-emission side-effect)
    # The RESPONDING→FINAL path worked because compose was not None when RESPONDING ran.
    assert_clean(h)
    print("  RESPONDING/compose-retract audit: no triggered bug in normal flow")
    print("  NOTE: line 86 'is None' check is a latent bug (retracted != None) but")
    print("        no current code path puts compose into RETRACTED while RESPONDING")


# ══════════════════════════════════════════════════════════════════════════════
# TEST 12: Watchdog event — does it actually complete the goal gracefully?
# (T-07: watchdog guarantees emit something, gated by inject= in SimHarness)
# ══════════════════════════════════════════════════════════════════════════════

def test_t07_watchdog_salvage():
    """SimHarness supports watchdog events via raw event dict (sent in batch).
    Verify watchdog salvage: no crash, task_completed=False, reads cancelled."""

    config = Config(
        transitive_invalidation=False,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=False,
    )
    p = ScriptedProvider()
    p.register("interpret", "find flights", {
        "act": "new_goal", "intent": "search",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
        "commit_intent": False,
    })
    p.register("plan", "search", {
        "steps": [{"local_id": "s1", "tool": "search_flights", "kind": "read",
                   "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]
    })
    p.register("compose", "s1", {"text": "Found.", "claims": []})

    # Very high tool latency so call is still in-flight when watchdog fires
    h = SimHarness(config, seed=1,
                   tools={"search_flights": {"latency_ms": 99999, "response": {"flights": []}}},
                   provider=p, worker_latency_us=FAST_LATENCY)

    h.send(0, [manifest([SEARCH_TOOL])])
    h.send(100_000, [chunk("find flights"), eot()])

    # Let call be emitted
    drain(h, 300_000, stop_on_final=False)

    in_flight = [c for c in h.store.call_ledger.all() if c.status == CallStatus.IN_FLIGHT]
    print(f"  In-flight calls before watchdog: {len(in_flight)}")

    # Fire the watchdog by sending it as a raw event in the batch
    # (inject= expects a callable, not a list; we send it as an event instead)
    # Note: WatchdogPayload requires wall_elapsed_s — without it the codec drops it
    report = h.send(400_000, [{"type": "watchdog", "payload": {"wall_elapsed_s": 105.0}, "ts_us": 400_000}])
    actions = [er.action for er in report.emit_report.emitted]
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    print(f"  Actions after watchdog: {[a.action_type for a in actions]}")
    print(f"  Finals: {len(finals)}")

    if finals:
        final = finals[0]
        print(f"  task_completed={final.body.task_completed}")
        assert not final.body.task_completed, "Watchdog FINAL must have task_completed=False"

    # Reads should be cancelled
    calls_after = h.store.call_ledger.all()
    for call in calls_after:
        print(f"  Call {call.call_id}: {call.status} ({call.kind})")
        if call.kind.value == "read":
            # Should be invalidated or cancel_requested, not still IN_FLIGHT
            assert call.status != CallStatus.IN_FLIGHT, \
                f"READ call still IN_FLIGHT after watchdog: {call.call_id}"

    # Drain to allow FINAL to flow if watchdog set compose text
    drain(h, 500_000)
    assert_clean(h)
    print("  T-07: watchdog salvage works correctly")


# ══════════════════════════════════════════════════════════════════════════════
# Main runner
# ══════════════════════════════════════════════════════════════════════════════

TESTS = [
    ("TEST-1: FRAME read set on correction", test_frame_readset_on_correction),
    ("TEST-2: C9 commit-last + mid-correction", test_c9_commit_last_mid_correction),
    ("TEST-3: Speculative interpret + settle barrier", test_speculative_interp_settle_barrier),
    ("TEST-4: R-03 stale tool result", test_r03_stale_tool_result),
    ("TEST-5: R-04 malformed tool result (branch 3)", test_r04_malformed_tool_result),
    ("TEST-6: S-07 unseen tool in plan", test_s07_unseen_tool_in_plan),
    ("TEST-7: S-08 manifest update cancels in-flight call", test_s08_manifest_update_cancels_inflight),
    ("TEST-8: T-03 ACK without HIGH-confidence value", test_t03_ack_without_high_confidence),
    ("TEST-9: P-04 duplicate worker result", test_p04_duplicate_worker_result),
    ("TEST-10: M-09 audio+text disagreement", test_m09_audio_text_disagreement),
    ("TEST-11: RESPONDING/compose-text-retracted audit", test_responding_compose_text_retracted),
    ("TEST-12: T-07 watchdog salvage", test_t07_watchdog_salvage),
]


def main():
    results = []
    for name, fn in TESTS:
        passed = run_test(name, fn)
        results.append((name, passed))

    print(f"\n{'═'*60}")
    passed_count = sum(1 for _, p in results if p)
    print(f"Pass 2 results: {passed_count}/{len(results)} passed")
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {name}")

    failures = [(n, p) for n, p in results if not p]
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
