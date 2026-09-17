"""Terminal demo of the V0 kernel: what it actually proves.

V0 has no speech, no LLM, no turns — that's V1. What V0 proves is the
core architectural claim: when the user corrects a slot while a tool call
is in flight, the kernel cancels the stale call and issues the corrected
one in the SAME step, and a late result for the cancelled call is safely
discarded rather than contaminating the answer. This script narrates that
happening, step by step, against the real kernel (not a mock of it).

Run:
    cd /home/adi/Desktop/Hackathons/Prism
    source .venv/bin/activate
    PYTHONPATH=src python demo/run_v0_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prism_rt.config import DEFAULT_CONFIG
from prism_rt.model.types import CallRecord, FactStatus, Provenance, StepKind, fingerprint_for
from prism_rt.sim.harness import SimHarness

SEARCH_FLIGHTS_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"destination": {"type": "string"}},
        "required": ["destination"],
    },
}

RULE = "-" * 78


def banner(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


def describe_action(action) -> str:
    body = action.body
    if action.action_type.value == "tool_call":
        return f"TOOL_CALL  {body.call_id}  {body.tool_name}({body.arguments})  [rule: {action.rule_id}]"
    if action.action_type.value == "cancel":
        return f"CANCEL     {body.target_call_id}  reason={body.reason!r}  [rule: {action.rule_id}]"
    return f"{action.action_type.value.upper()}  {body}"


def report_summary(report) -> None:
    print(f"  step {report.step_no}  (t = {report.now_us:,} us)")
    if report.change_set.changes:
        for key, old_digest, new_digest, old_status, new_status, ver, rule in report.change_set.changes:
            print(f"    fact changed: {key} -> ver {ver}  (rule: {rule})")
    if report.invalidation.invalidated_call_ids:
        print(f"    invalidated:  {list(report.invalidation.invalidated_call_ids)}")
    if report.emit_report.emitted:
        for er in report.emit_report.emitted:
            print(f"    emitted:      {describe_action(er.action)}")
    else:
        print("    emitted:      (nothing)")
    if report.emit_report.rejected:
        for rr in report.emit_report.rejected:
            print(f"    rejected:     {rr.intended.action_type.value} — {rr.reason_code}")


def main() -> None:
    h = SimHarness(
        DEFAULT_CONFIG,
        seed=1,
        tools={"search_flights": {"latency_ms": 500, "response": {"flights": [{"flight_id": "AI-505", "time": "06:10"}]}}},
    )

    banner('STEP 1 — manifest arrives ("here is what tools exist")')
    report = h.send(0, [{"type": "manifest", "payload": {"tools": [SEARCH_FLIGHTS_TOOL]}}])
    report_summary(report)
    print(f"    usable tools: {[t.name for t in h.store.catalog.usable_tools()]}")

    banner('STEP 2 — user says "find me flights to Pune"')

    def inject_pune(txn, now_us, step_no):
        txn.facts.set("goal.active", "g1", FactStatus.COMMITTED, Provenance(source="user"), rule="demo.new_goal")
        txn.facts.set(
            "goal.g1.intent", "search_flights", FactStatus.COMMITTED, Provenance(source="user"), rule="demo.new_goal"
        )
        txn.facts.set("slot.g1.destination", "Pune", FactStatus.COMMITTED, Provenance(source="user"), rule="demo.slot")
        rs = txn.facts.build_read_set(["slot.g1.destination"])
        call = CallRecord(
            call_id=txn.store.ids.next("call"),
            goal_id="g1",
            step_key="s1",
            tool="search_flights",
            kind=StepKind.READ,
            args={"destination": "Pune"},
            fingerprint=fingerprint_for("search_flights", {"destination": "Pune"}),
            read_set=rs,
        )
        txn.call_ledger.create(call)

    report = h.send(100_000, inject=inject_pune)
    report_summary(report)
    pune_call_id = report.emit_report.emitted[0].action.body.call_id
    print(f"    -> search for Pune is now in flight as {pune_call_id}, mock latency 500ms")

    banner('STEP 3 — 50ms later, user interrupts: "actually, make it Mumbai"')

    def inject_correction(txn, now_us, step_no):
        txn.facts.set(
            "slot.g1.destination", "Mumbai", FactStatus.COMMITTED, Provenance(source="user"), rule="demo.correction"
        )
        rs = txn.facts.build_read_set(["slot.g1.destination"])
        call = CallRecord(
            call_id=txn.store.ids.next("call"),
            goal_id="g1",
            step_key="s1",
            tool="search_flights",
            kind=StepKind.READ,
            args={"destination": "Mumbai"},
            fingerprint=fingerprint_for("search_flights", {"destination": "Mumbai"}),
            read_set=rs,
        )
        txn.call_ledger.create(call)

    report = h.send(150_000, inject=inject_correction)
    report_summary(report)
    mumbai_call_id = report.emit_report.emitted[1].action.body.call_id
    print(f"    -> {pune_call_id} (Pune) cancelled and {mumbai_call_id} (Mumbai) issued IN THE SAME STEP")
    print("       (this is the P3 invariant: cancellation happens the instant the fact changes,")
    print("        not on the next tick, not after the LLM catches up)")

    banner("STEP 4 — the stale Pune result finally arrives anyway (network doesn't know it was cancelled)")
    report = h.send(300_000, [{"type": "tool_result", "payload": {"call_id": pune_call_id, "status": "ok", "result": {"flights": ["should never be seen"]}}}])
    report_summary(report)
    print(f"    -> {pune_call_id} status is now: {h.store.call_ledger.get(pune_call_id).status.value}")
    print(f"    -> result.{pune_call_id} written to facts? {h.store.facts.get(f'result.{pune_call_id}')}")
    print("       (the stale Pune answer never touches session state — W4/O2: no false claims)")

    banner("STEP 5 — the Mumbai search completes for real")
    report = h.advance(650_000)
    report_summary(report)
    print(f"    -> {mumbai_call_id} status: {h.store.call_ledger.get(mumbai_call_id).status.value}")
    print(f"    -> result.{mumbai_call_id} = {h.store.facts.get(f'result.{mumbai_call_id}').value}")

    banner("What V1 adds on top of this")
    print(
        "  Everything above ran with test code standing in for the LLM (no Interpreter/Planner/\n"
        "  Composer yet — that's what 'V0: no workers' means). V1 replaces the inject() calls\n"
        "  above with a real turn manager + QuickDetector + LLM interpretation/planning, and adds\n"
        "  FastResponder so the ACK/CANCEL/FINAL above come with actual spoken text\n"
        "  ('Looking for flights to Pune' -> 'Mumbai instead, searching now' -> 'Found a 6:10am flight').\n"
        "  The cancellation mechanics you just watched do not change — V1 only adds the layer that\n"
        "  decides WHEN to call inject()'s equivalent."
    )


if __name__ == "__main__":
    main()
