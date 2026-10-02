"""Render the kernel X-ray for two deterministic scenarios (no network, no model).

    PYTHONPATH=src:. python scripts/make_xray.py            # writes docs/xray/*.html

Both run the real kernel under the simulator's stepped clock with scripted
model answers (the same shapes a live run produced), then draw the decision log
with `prism_rt.observability.xray`:

  1. correct_before_booking -- "book a technician for Thursday morning ... no
     wait, make it Friday afternoon": the Thursday request is superseded while
     the CommitGate is still holding it; exactly one booking, for Friday.
  2. correct_a_running_lookup -- the user changes an answer while the first
     lookup is still in flight: that call is invalidated in the same step the
     correction arrives (struck through) and re-run with the new value.
  3. withdraw_one_of_two_writes -- "stop the wash and book a technician for
     Friday afternoon", then, while both writes wait out the settle window,
     "wait, don't stop it, just pause it": the pending stop is withdrawn
     before it is sent, the booking goes through once (simulated SmartThings
     washer; the effect-ledger summary is the subtitle).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from prism_rt.devicecare import DeviceCareToolset  # noqa: E402
from prism_rt.model.types import ActionType, JobKind  # noqa: E402
from prism_rt.observability.effects import effect_summary, format_effect_summary  # noqa: E402
from prism_rt.observability.xray import build_rows, render  # noqa: E402
from prism_rt.profiles import demo_config  # noqa: E402
from prism_rt.sim.harness import SimHarness  # noqa: E402
from prism_rt.workers.gateway import ScriptedProvider  # noqa: E402


class Session:
    def __init__(self, provider: ScriptedProvider, toolset: DeviceCareToolset, latency_ms: int = 300) -> None:
        ex = toolset.make_executor()
        tools = {t["name"]: {"latency_ms": latency_ms, "handler": (lambda a, n=t["name"]: asyncio.run(ex(n, a)))} for t in toolset.manifest}
        self.h = SimHarness(demo_config(), seed=5, provider=provider, tools=tools, worker_latency_us={k: 250_000 for k in JobKind})
        self.wire: list[dict] = []
        self.send(0, [{"type": "manifest", "payload": {"tools": toolset.manifest}}])

    def _collect(self, report) -> None:
        for er in report.emit_report.emitted:
            a = er.action
            body = {}
            if a.action_type in (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL):
                body = {"text": a.body.text}
            elif a.action_type == ActionType.TOOL_CALL:
                body = {"tool_name": a.body.tool_name, "arguments": dict(a.body.arguments), "call_id": a.body.call_id}
            elif a.action_type == ActionType.CANCEL:
                body = {"call_id": getattr(a.body, "call_id", None)}
            self.wire.append({"dir": "out", "t_us": self.h.clock.now_us(), "action_type": a.action_type.value, "body": body})

    def send(self, ts: int, events: list[dict]) -> None:
        for ev in events:
            if ev["type"] != "manifest":
                self.wire.append({"dir": "in", "t_us": ts, "type": ev["type"], "payload": ev.get("payload")})
        self._collect(self.h.send(ts, events))

    def run(self, until_us: int, step_us: int = 10_000) -> None:
        t = self.h.clock.now_us()
        while t < until_us:
            t += step_us
            self._collect(self.h.advance(t))

    def html(self, title: str, subtitle: str) -> str:
        return render(build_rows(self.h.log.records, self.wire), title=title, subtitle=subtitle)


def _booking(day, slot):
    return {"act": "new_goal", "intent": "book_technician", "commit_intent": True,
            "slot_deltas": [{"name": "date", "scope": "goal", "op": "set", "value": day}, {"name": "time_slot", "scope": "goal", "op": "set", "value": slot}],
            "ack_phrase": f"I'll book a technician for {day} {slot}.",
            "actions": [{"tool": "book_technician", "args": {"date": day, "time_slot": slot}}]}


def correct_before_booking() -> str:
    p = ScriptedProvider()
    p.register("interpret", "Friday", _booking("Friday", "afternoon"))
    p.register("interpret", "Thursday", _booking("Thursday", "morning"))
    p.register("plan", "book_technician", {"steps": [{"local_id": "s1", "tool": "book_technician", "kind": "write", "bindings": {
        "date": {"type": "fact", "key": "slot.$G.date"}, "time_slot": {"type": "fact", "key": "slot.$G.time_slot"}}, "after": []}]})
    p.register("compose", "book_technician", {"text": "Your technician is booked for Friday afternoon.", "claims": ["result:s1"]})
    tk = DeviceCareToolset()
    tk.current_issue = "router_no_internet"
    s = Session(p, tk)
    s.send(100_000, [{"type": "text_chunk", "payload": {"text": "book a technician for Thursday morning"}}])
    s.run(1_500_000)
    s.send(1_600_000, [{"type": "text_chunk", "payload": {"text": "no wait, make it Friday afternoon"}}])
    s.send(2_700_000, [{"type": "end_of_turn", "payload": {}}])
    s.run(12_000_000)
    assert len(tk.bookings) == 1 and tk.bookings[0]["date"] == "Friday", tk.bookings
    return s.html("Changing your mind before the booking", "One booking, for the final answer.")


def correct_a_running_lookup() -> str:
    p = ScriptedProvider()
    lookup = lambda colour: {"act": "new_goal", "intent": "identify_indicator", "commit_intent": True,
                             "slot_deltas": [], "ack_phrase": f"Checking the {colour} light.",
                             "actions": [{"tool": "identify_indicator", "args": {"device_type": "router", "led_color": colour, "led_state": "solid", "led_name": "internet"}}]}
    p.register("interpret", "actually red", {"act": "slot_update", "slot_deltas": [{"name": "led_color", "scope": "goal", "op": "set", "value": "red"}]})
    p.register("interpret", "orange", lookup("orange"))
    p.register("plan", "identify_indicator", {"steps": [{"local_id": "s1", "tool": "identify_indicator", "kind": "read", "bindings": {
        "device_type": {"type": "fact", "key": "slot.$G.device_type"}, "led_color": {"type": "fact", "key": "slot.$G.led_color"},
        "led_state": {"type": "fact", "key": "slot.$G.led_state"}, "led_name": {"type": "fact", "key": "slot.$G.led_name"}}, "after": []}]})
    p.register("compose", "identify_indicator", {"text": "A solid red internet light means the internet line has a hard fault.", "claims": ["result:s1"]})
    tk = DeviceCareToolset()
    s = Session(p, tk, latency_ms=2500)
    s.send(100_000, [{"type": "text_chunk", "payload": {"text": "what does a solid orange internet light mean"}}])
    s.send(200_000, [{"type": "end_of_turn", "payload": {}}])
    s.run(2_400_000)
    s.send(2_500_000, [{"type": "text_chunk", "payload": {"text": "sorry, actually red"}}])
    s.send(2_600_000, [{"type": "end_of_turn", "payload": {}}])
    s.run(14_000_000)
    return s.html("Correcting a lookup that is already running", "The orange lookup is invalidated the moment the user says red.")


def withdraw_one_of_two_writes() -> str:
    def stop_and_book(command):
        return {"act": "new_goal", "intent": "control_appliance", "commit_intent": True,
                "slot_deltas": [{"name": "device_type", "scope": "goal", "op": "set", "value": "washer"},
                                {"name": "command", "scope": "goal", "op": "set", "value": command},
                                {"name": "date", "scope": "goal", "op": "set", "value": "Friday"},
                                {"name": "time_slot", "scope": "goal", "op": "set", "value": "afternoon"}],
                "ack_phrase": f"I'll {command} the washer and book a technician for Friday afternoon.",
                "actions": [{"tool": "control_appliance", "args": {"device_type": "washer", "command": command}},
                            {"tool": "book_technician", "args": {"date": "Friday", "time_slot": "afternoon"}}]}

    p = ScriptedProvider()
    p.register("interpret", "pause it", {"act": "slot_update",
               "slot_deltas": [{"name": "command", "scope": "goal", "op": "set", "value": "pause"}],
               "actions": [{"tool": "control_appliance", "args": {"device_type": "washer", "command": "pause"}}]})
    p.register("interpret", "stop the wash", stop_and_book("stop"))
    p.register("compose", "control_appliance", {"text": "The washer is paused, and a technician is booked for Friday afternoon.", "claims": []})
    tk = DeviceCareToolset()
    tk.current_issue = "washer_no_water"
    tk.status["washer"]["machine_state"] = "run"
    s = Session(p, tk)
    s.send(100_000, [{"type": "text_chunk", "payload": {"text": "stop the wash and book a technician for Friday afternoon"}}])
    s.send(200_000, [{"type": "end_of_turn", "payload": {}}])
    s.run(1_000_000)
    s.send(1_050_000, [{"type": "text_chunk", "payload": {"text": "wait, don't stop it, just pause it"}}])
    s.send(1_150_000, [{"type": "end_of_turn", "payload": {}}])
    s.run(14_000_000)
    assert tk.commands == [{"device": "washer", "command": "pause"}] and len(tk.bookings) == 1, (tk.commands, tk.bookings)
    summary = effect_summary(s.h.store)
    assert summary["withdrawn_before_sending"] == 1 and summary["duplicates"] == 0, summary
    return s.html("Withdrawing one of two pending writes", format_effect_summary(summary) + " (simulated SmartThings washer).")


if __name__ == "__main__":
    out = ROOT / "docs" / "xray"
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in (("correct_before_booking", correct_before_booking), ("correct_a_running_lookup", correct_a_running_lookup),
                     ("withdraw_one_of_two_writes", withdraw_one_of_two_writes)):
        (out / f"{name}.html").write_text(fn(), encoding="utf-8")
        print("wrote", out / f"{name}.html")
