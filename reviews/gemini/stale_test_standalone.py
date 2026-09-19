
def test_w4_stale_write_reconciliation():
    """W4 violation: A WRITE call that becomes STALE but completes successfully 
    silently drops the effect without triggering reconciliation."""
    config = Config(
        transitive_invalidation=True,
        settle_barrier_enabled=False,
        absence_read_sets=False,
        claim_grades_enabled=False,
        rebinder_enabled=True,
    )
    p = ScriptedProvider()
    p.register("interpret", "book flight to Pune", {
        "act": "new_goal", "intent": "book_flight",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Pune"}],
        "commit_intent": True,
    })
    p.register("interpret", "actually Mumbai", {
        "act": "slot_update",
        "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": "Mumbai"}],
        "commit_intent": True,
    })
    p.register("plan", "book_flight", {
        "steps": [{"local_id": "s1", "tool": "book_flight", "kind": "write",
                   "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []}]
    })

    h = SimHarness(config, seed=1,
                   tools={"book_flight": {"latency_ms": 400, "response": {"success": True}}},
                   provider=p, worker_latency_us=FAST_LATENCY)

    h.send(0, [manifest([BOOK_TOOL])])
    h.send(100_000, [chunk("book flight to Pune"), eot()])

    drain(h, 300_000, stop_on_final=False)

    in_flight = [c for c in h.store.call_ledger.all() if c.status == CallStatus.IN_FLIGHT]
    assert len(in_flight) >= 1
    call1 = in_flight[0]

    # Correct to Mumbai while the call is IN_FLIGHT
    h.send(350_000, [chunk("actually Mumbai"), eot()])
    drain(h, 400_000, stop_on_final=False)

    # Let the tool result arrive for the stale call
    # The auto-responder will deliver it at around 700_000
    actions = drain(h, 1_000_000)

    final_call1 = h.store.call_ledger.get(call1.call_id)
    print(f"\nFinal status of cancelled call: {final_call1.status}")
    
    # We should see a RECONCILIATION notice because the tool executed successfully
    reconcile_actions = [a for a in actions if a.action_type == ActionType.SPEAK and a.body.kind == "inform"]
    if not reconcile_actions:
        print("BUG FOUND: No reconciliation action for STALE write that completed.")
    assert len(reconcile_actions) > 0, "No reconciliation action for STALE write that completed"

TESTS.append(("TEST-13: W4 STALE write reconciliation", test_w4_stale_write_reconciliation))
