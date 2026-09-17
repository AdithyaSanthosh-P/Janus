"""Terminal demo of the cumulative V2+V3+V4 behavior — everything V1's demo
doesn't show, since run_v0_demo.py/run_v1_demo.py/run_v1_live_demo.py all
predate V2.

Three acts, each turning on a different frozen version's headline feature
(all via ScriptedProvider — deterministic and offline, matching how every
version's own tests work):

  1. Settle barrier + rebinder (V2): a booking corrected 150ms after the
     user said it — well inside the 300ms settle window — never goes out
     under the wrong flight number, and the correction is a rebind, not a
     Planner round-trip.
  2. Multimodal conflict (V3): the user says "Tab A"; the camera's HIGH-
     confidence claim disagrees ("Tab S9"); the agent blocks the lookup
     and asks, naming both values, instead of silently trusting either
     one.
  3. Reference-bound identifiers (V4/C10): the Planner tries to invent a
     flight number nobody said; the agent refuses to book it and asks
     instead of acting on a fabricated identifier.

Run:
    cd /home/adi/Desktop/Hackathons/Prism
    source .venv/bin/activate
    PYTHONPATH=src:. python demo/run_v4_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

BOOK_FLIGHT_TOOL = {
    "name": "book_flight",
    "mutability": "state_changing",
    "parameters": {"type": "object", "properties": {"flight_id": {"type": "string"}}, "required": ["flight_id"]},
}

MANUAL_LOOKUP_TOOL = {
    "name": "manual_lookup",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"device_model": {"type": "string"}, "visible_state": {"type": "string"}},
        "required": ["device_model", "visible_state"],
    },
}

RULE = "-" * 78


def say(who: str, text: str) -> None:
    print(f"  {who:9} {text}")


def act(title: str) -> None:
    print()
    print(RULE)
    print(title)
    print(RULE)


def flush_speech(harness: SimHarness, until_us: int, *, step_us: int = 10_000) -> None:
    t = harness.clock.now_us()
    while t < until_us:
        t += step_us
        report = harness.advance(t)
        for er in report.emit_report.emitted:
            action = er.action
            if action.action_type in (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL):
                say("AGENT", action.body.text)
            elif action.action_type == ActionType.CANCEL:
                print(f"            [internal: cancelling {action.body.target_call_id} — {action.body.reason}]")
            elif action.action_type == ActionType.TOOL_CALL:
                print(f"            [internal: calling {action.body.tool_name}({action.body.arguments})]")


def main() -> None:
    config = Config(vision_enabled=True, reference_bound_identifiers=True)  # V2 flags already default True
    provider = ScriptedProvider()

    # --- Act 1 (V2): settle barrier + rebinder -----------------------------
    provider.register(
        "interpret",
        "Book flight AI-1",
        {"act": "new_goal", "intent": "book_flight", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-1"}], "commit_intent": True},
    )
    provider.register(
        "interpret",
        "wait, actually AI-2",
        {"act": "slot_update", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-2"}], "commit_intent": True},
    )
    provider.register(
        "plan",
        # Match the intent line specifically — the plan prompt also embeds
        # the *whole* tool catalog (every registered tool, not just this
        # goal's), so a bare "book_flight" substring would also match a
        # later dispatch that merely has book_flight listed as an option.
        "goal_intent: book_flight",
        {"steps": [{"local_id": "s1", "tool": "book_flight", "kind": "write", "bindings": {"flight_id": {"type": "fact", "key": "slot.$G.flight_id"}}, "after": []}]},
    )
    provider.register("compose", "confirmation", {"text": "Booked — confirmation ABC123.", "claims": ["effect:s1"]})

    # --- Act 2 (V3): multimodal conflict ------------------------------------
    provider.register(
        "interpret",
        "what does this light mean, I think it's my Tab A",
        {
            "act": "new_goal",
            "intent": "device_help",
            "slot_deltas": [{"name": "device_model", "scope": "goal", "op": "set", "value": "Tab A"}],
            "visual_reference": "at_utterance",
        },
    )
    provider.register(
        "interpret",
        "oh you're right, it is the S9",
        {"act": "slot_update", "slot_deltas": [{"name": "device_model", "scope": "goal", "op": "set", "value": "Tab S9"}]},
    )
    provider.register(
        "plan",
        "goal_intent: device_help",
        {
            "steps": [
                {
                    "local_id": "s1",
                    "tool": "manual_lookup",
                    "kind": "read",
                    "bindings": {
                        "device_model": {"type": "fact", "key": "slot.$G.device_model"},
                        "visible_state": {"type": "fact", "key": "slot.$G.visible_state"},
                    },
                    "after": [],
                }
            ]
        },
    )
    provider.register(
        "vision",
        "device_model",
        {"claims": [{"name": "device_model", "value": "Tab S9", "confidence": "high"}, {"name": "visible_state", "value": "blinking amber", "confidence": "high"}]},
    )
    provider.register("compose", "low battery", {"text": "That blinking amber light means low battery.", "claims": ["result:s1"]})

    # --- Act 3 (V4/C10): reference-bound identifiers ------------------------
    provider.register("interpret", "Book me the best flight to Goa", {"act": "new_goal", "intent": "reserve_flight", "commit_intent": True})
    provider.register(
        "interpret",
        "Book AI-4 then",
        {"act": "slot_update", "slot_deltas": [{"name": "flight_id", "scope": "goal", "op": "set", "value": "AI-4"}], "commit_intent": True},
    )
    # Registered before the generic fallback below: matches only once the
    # corrected slot is in the Planner's view (see the module docstring's
    # ScriptedProvider first-match-wins note in kernel/proposals.py's peers).
    provider.register(
        "plan",
        "AI-4",
        {"steps": [{"local_id": "s1", "tool": "book_flight", "kind": "write", "bindings": {"flight_id": {"type": "fact", "key": "slot.$G.flight_id"}}, "after": []}]},
    )
    # The Planner's first attempt invents a flight number nobody said — C10 must refuse it.
    provider.register(
        "plan",
        "goal_intent: reserve_flight",
        {"steps": [{"local_id": "s1", "tool": "book_flight", "kind": "write", "bindings": {"flight_id": {"type": "literal", "value": "AI-777"}}, "after": []}]},
    )
    # compose reuses the "confirmation" rule registered in Act 1 — same mock
    # tool response, both bookings; the point of this act is the refusal
    # that happens before any call is ever proposed, not the wording after.

    h = SimHarness(
        config,
        seed=7,
        provider=provider,
        worker_latency_us={JobKind.INTERPRET: 15_000, JobKind.PLAN: 15_000, JobKind.COMPOSE: 15_000, JobKind.VISION: 15_000},
        tools={
            "book_flight": {"latency_ms": 50, "response": {"confirmation": "ABC123"}},
            "manual_lookup": {"latency_ms": 50, "response": {"meaning": "low battery"}},
        },
    )
    h.send(0, [{"type": "manifest", "payload": {"tools": [BOOK_FLIGHT_TOOL, MANUAL_LOOKUP_TOOL]}}])

    # === Act 1 ===============================================================
    act("Act 1 (V2) — settle barrier + rebinder")
    say("USER", '"Book flight AI-1"')
    h.send(100_000, [{"type": "text_chunk", "payload": {"text": "Book flight AI-1"}}])
    h.send(150_000, [{"type": "end_of_turn", "payload": {}}])
    flush_speech(h, 250_000)

    say("USER", '"wait, actually AI-2" (150ms later — inside the 300ms settle window)')
    h.send(h.clock.now_us() + 150_000, [{"type": "interruption", "payload": {}}])
    h.send(h.clock.now_us() + 5_000, [{"type": "text_chunk", "payload": {"text": "wait, actually AI-2"}}])
    h.send(h.clock.now_us() + 5_000, [{"type": "end_of_turn", "payload": {}}])
    flush_speech(h, h.clock.now_us() + 1_000_000)
    print(f"  -> AI-1 was never booked; only AI-2 went out. Goal status: {h.store.goals.all()[0].status.value}")

    # === Act 2 ===============================================================
    act("Act 2 (V3) — multimodal conflict")
    say("CAMERA", "[frame: device with a blinking amber light]")
    h.send(h.clock.now_us() + 50_000, [{"type": "video_frame", "payload": {"frame_id": "f1"}}])
    say("USER", '"what does this light mean, I think it\'s my Tab A"')
    h.send(h.clock.now_us() + 50_000, [{"type": "text_chunk", "payload": {"text": "what does this light mean, I think it's my Tab A"}}])
    h.send(h.clock.now_us() + 50_000, [{"type": "end_of_turn", "payload": {}}])
    flush_speech(h, h.clock.now_us() + 400_000)

    say("USER", '"oh you\'re right, it is the S9"')
    h.send(h.clock.now_us() + 50_000, [{"type": "interruption", "payload": {}}])
    h.send(h.clock.now_us() + 5_000, [{"type": "text_chunk", "payload": {"text": "oh you're right, it is the S9"}}])
    h.send(h.clock.now_us() + 5_000, [{"type": "end_of_turn", "payload": {}}])
    flush_speech(h, h.clock.now_us() + 1_000_000)

    # === Act 3 ===============================================================
    act("Act 3 (V4 / C10) — reference-bound write identifiers")
    say("USER", '"Book me the best flight to Goa"')
    h.send(h.clock.now_us() + 50_000, [{"type": "text_chunk", "payload": {"text": "Book me the best flight to Goa"}}])
    h.send(h.clock.now_us() + 50_000, [{"type": "end_of_turn", "payload": {}}])
    flush_speech(h, h.clock.now_us() + 400_000)
    print("  -> the Planner tried to invent flight AI-777; C10 refused it — no TOOL_CALL was ever emitted for it.")

    say("USER", '"Book AI-4 then"')
    h.send(h.clock.now_us() + 50_000, [{"type": "interruption", "payload": {}}])
    h.send(h.clock.now_us() + 5_000, [{"type": "text_chunk", "payload": {"text": "Book AI-4 then"}}])
    h.send(h.clock.now_us() + 5_000, [{"type": "end_of_turn", "payload": {}}])
    flush_speech(h, h.clock.now_us() + 1_000_000)

    print()
    print(RULE)
    for goal in h.store.goals.all():
        print(f"Goal {goal.goal_id}: status={goal.status.value}")


if __name__ == "__main__":
    main()
