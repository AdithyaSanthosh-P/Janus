"""Evidence-bound confirmation (kernel/evidence.py): a yes counts only for the
world it was asked about -- the watched device's state and what the camera
showed for the request."""

from __future__ import annotations

import asyncio

from conftest import chunk_event, drain, eot_event, frame_event, manifest_event

from config.templates import EVIDENCE_WITHDRAWN
from prism_rt.canonical import world_target_key
from prism_rt.devicecare import DeviceCareToolset
from prism_rt.model.types import ActionType, CallStatus, JobKind
from prism_rt.profiles import demo_config, fdb_v3_config
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.smartthings.mock import MockSmartThings
from prism_rt.smartthings.tools import MANIFEST, SmartThingsToolset
from prism_rt.workers.gateway import ScriptedProvider

FAST = {kind: 10_000 for kind in JobKind}


def run(coro):
    return asyncio.run(coro)


async def _no_sleep(_s):
    return None


def world_event(spoken: str, value) -> dict:
    return {"type": "world_fact", "payload": {"key": world_target_key(spoken), "value": value}}


def said(actions) -> list[str]:
    return [a.body.text for a in actions if a.action_type in (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL)
            and getattr(a.body, "text", None)]


def tool_calls(actions) -> list[str]:
    return [a.body.tool_name for a in actions if a.action_type == ActionType.TOOL_CALL]


def clean(h) -> None:
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


# ---- the watched device --------------------------------------------------------

UNLOCK = {"act": "new_goal", "intent": "st_unlock", "slot_deltas": [], "commit_intent": True,
          "actions": [{"tool": "st_unlock", "args": {"device": "front door"}}]}


def unlock_asked(config=None, *, device="front door", before=()):
    mock = MockSmartThings()
    run(mock.command("lock-1", "lock", "lock"))
    mock.commands.clear()
    execute = SmartThingsToolset(mock, sleep=_no_sleep).make_executor()
    provider = ScriptedProvider()
    provider.register("interpret", "yes please", {"act": "confirm", "slot_deltas": []})
    provider.register("interpret", "unlock the front", {**UNLOCK, "actions": [{"tool": "st_unlock", "args": {"device": device}}]})
    provider.register("compose", "", {"text": "Done.", "claims": []})
    tools = {m["name"]: {"latency_ms": 200, "handler": (lambda args, n=m["name"]: run(execute(n, args)))} for m in MANIFEST}
    h = SimHarness(config or demo_config(), seed=1, provider=provider, tools=tools, worker_latency_us=FAST)
    h.send(0, [manifest_event(MANIFEST)])
    if before:
        h.send(50_000, list(before))
    h.send(100_000, [chunk_event("unlock the front door")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted] + drain(h, 3_000_000, stop_on_final=False)
    assert any("security action" in t for t in said(actions)) and tool_calls(actions) == []
    return h, mock, actions


def push(h, ts, events, until):
    return [er.action for er in h.send(ts, events).emit_report.emitted] + drain(h, until, stop_on_final=False)


def say(h, ts, text, until):
    h.send(ts, [chunk_event(text)])
    return [er.action for er in h.send(ts + 50_000, [eot_event()]).emit_report.emitted] + drain(h, until, stop_on_final=False)


def test_someone_else_unlocks_it_while_janus_waits_and_the_write_is_withdrawn():
    h, mock, _ = unlock_asked()
    actions = push(h, 3_100_000, [world_event("Front door", "locked")], 3_500_000)  # the host's first report: the baseline
    assert said(actions) == []
    actions = push(h, 4_000_000, [world_event("front door", "unlocked")], 5_000_000)  # someone else unlocked it
    assert EVIDENCE_WITHDRAWN.format(device="the front door", value="unlocked") in said(actions)
    call = next(c for c in h.store.call_ledger.all() if c.tool == "st_unlock")
    assert call.status == CallStatus.DISCARDED
    later = say(h, 5_100_000, "yes please", 8_000_000)
    assert tool_calls(actions + later) == [] and mock.commands == []
    clean(h)


def test_the_first_report_is_a_baseline_even_if_it_is_the_requested_state():
    h, mock, _ = unlock_asked()
    run(mock.command("lock-1", "lock", "unlock"))  # someone unlocked it before the first report
    mock.commands.clear()
    actions = push(h, 3_100_000, [world_event("front door", "unlocked")], 3_600_000)
    assert said(actions) == []
    actions = say(h, 3_700_000, "yes please", 8_000_000)
    assert tool_calls(actions) == ["st_unlock"] and mock.commands == []  # the pre-read finds it already unlocked
    clean(h)


def test_another_change_voids_the_yes_and_asks_again():
    h, mock, _ = unlock_asked()
    h.send(3_100_000, [world_event("front door", "locked")])
    drain(h, 3_500_000, stop_on_final=False)
    actions = push(h, 4_000_000, [world_event("front door", "jammed")], 5_000_000)
    reask = [t for t in said(actions) if t.startswith("Heads up")]
    assert reask == ["Heads up: The front door changed from locked to jammed since I asked. It's a security action, so I need "
                     "your yes again: shall I still unlock (front door)?"]
    actions = say(h, 5_100_000, "yes please", 9_000_000)
    assert tool_calls(actions) == ["st_unlock"]
    clean(h)


def test_a_yes_in_the_same_step_as_a_change_does_not_release_the_write():
    h, mock, _ = unlock_asked()
    h.send(3_100_000, [world_event("front door", "locked")])
    drain(h, 3_500_000, stop_on_final=False)
    pre = push(h, 4_000_000, [world_event("front door", "jammed"), chunk_event("yes please")], 4_000_000)
    actions = pre + push(h, 4_050_000, [eot_event()], 6_000_000)
    assert tool_calls(actions) == []
    assert any(t.startswith("Heads up") for t in said(actions))
    actions = say(h, 6_100_000, "yes please", 9_000_000)
    assert tool_calls(actions) == ["st_unlock"]
    clean(h)


def test_a_report_about_another_device_changes_nothing():
    h, mock, _ = unlock_asked()
    h.send(3_100_000, [world_event("front door", "locked")])
    actions = push(h, 3_200_000, [world_event("garage", "open")], 4_000_000)
    assert said(actions) == []
    clean(h)


def test_the_benchmark_profile_ignores_world_facts():
    cfg = fdb_v3_config()
    assert cfg.evidence_bound_confirmation_enabled is False and demo_config().evidence_bound_confirmation_enabled is True
    from prism_rt.config import Config
    assert Config().evidence_bound_confirmation_enabled is False


def test_flag_off_keeps_todays_behaviour():
    import dataclasses
    h, mock, _ = unlock_asked(dataclasses.replace(demo_config(), evidence_bound_confirmation_enabled=False))
    h.send(3_100_000, [world_event("front door", "locked")])
    actions = push(h, 4_000_000, [world_event("front door", "unlocked")], 5_000_000)
    assert said(actions) == [] and h.store.facts.get("world." + world_target_key("front door")) is None
    actions = say(h, 5_100_000, "yes please", 8_000_000)
    assert tool_calls(actions) == ["st_unlock"]
    clean(h)


# ---- what the camera showed -----------------------------------------------------

def washer_booking(codes):
    """The camera reads the washer's code; the request also books a
    technician, which waits for a go-ahead. `codes` is what each successive
    vision call sees."""
    seen = {"n": 0}

    def vision(_prompt):
        code = codes[min(seen["n"], len(codes) - 1)]
        seen["n"] += 1
        return {"claims": [{"name": "code", "value": code, "confidence": "high"}]}

    provider = ScriptedProvider()
    provider.register("interpret", "yes go ahead", {"act": "confirm", "slot_deltas": []})
    provider.register("interpret", "washer", {
        "act": "new_goal", "intent": "lookup_error_code", "commit_intent": False,
        "slot_deltas": [{"name": "device_type", "scope": "goal", "op": "set", "value": "washer"}],
        "visual_reference": "current_state",
        "visual_candidates": [{"name": "code", "description": "the error code on the display"}],
        "actions": [{"tool": "lookup_error_code", "args": {"device_type": "washer"}},
                    {"tool": "book_technician", "args": {"date": "Thursday", "time_slot": "morning"}}]})
    provider.register("vision", "", vision)
    provider.register("compose", "", {"text": "Okay.", "claims": []})
    tk = DeviceCareToolset()
    execute = tk.make_executor()
    tools = {t["name"]: {"latency_ms": 100, "handler": (lambda args, n=t["name"]: run(execute(n, args)))} for t in tk.manifest}
    h = SimHarness(demo_config(), seed=3, provider=provider, tools=tools, worker_latency_us=FAST)
    h.send(0, [manifest_event(tk.manifest)])
    h.send(50_000, [frame_event("cam-1")])
    h.send(100_000, [chunk_event("my washer shows this code, book a technician for Thursday morning")])
    actions = [er.action for er in h.send(150_000, [eot_event()]).emit_report.emitted] + drain(h, 3_000_000, stop_on_final=False)
    assert any("book technician (Thursday, morning)" in t for t in said(actions))
    for i, t in enumerate(range(3_200_000, 9_000_000, 1_000_000)):
        h.send(t, [frame_event(f"cam-{i + 2}")])
        actions += drain(h, t + 900_000, stop_on_final=False)
    return h, tk, actions


def test_the_code_clears_on_camera_and_janus_asks_again_before_booking():
    h, tk, actions = washer_booking(["4C", "none"])
    reask = [t for t in said(actions) if t.startswith("Heads up")]
    assert reask == ["Heads up: The code changed from 4C to none since I asked. Shall I still book technician (Thursday, morning)?"]
    assert tk.bookings == []
    later = say(h, 9_100_000, "yes go ahead", 14_000_000)
    assert tool_calls(later) == ["book_technician"] and len(tk.bookings) == 1
    clean(h)


def test_a_camera_reading_renewed_with_the_same_value_changes_nothing():
    h, tk, actions = washer_booking(["4C"])
    assert not [t for t in said(actions) if t.startswith("Heads up")]
    later = say(h, 9_100_000, "yes go ahead", 14_000_000)
    assert tool_calls(later) == ["book_technician"] and len(tk.bookings) == 1
    clean(h)


def test_replay_is_identical():
    def log():
        h, _m, _a = unlock_asked()
        h.send(3_100_000, [world_event("front door", "locked")])
        h.send(4_000_000, [world_event("front door", "unlocked")])
        drain(h, 5_000_000, stop_on_final=False)
        return [r.to_dict() if hasattr(r, "to_dict") else repr(r) for r in h.run_log.reports]

    assert log() == log()


def test_a_device_value_left_by_an_earlier_watch_is_never_the_baseline():
    """Review finding: an earlier watch left world.<front door> = locked;
    the door has since been unlocked (by Janus itself). The new question's
    first report is its baseline, not a change."""
    h, mock, _ = unlock_asked(before=[world_event("front door", "locked")])
    actions = push(h, 3_100_000, [world_event("front door", "unlocked")], 3_600_000)
    assert said(actions) == []
    clean(h)


def test_the_device_value_stays_the_baseline_after_a_reask():
    h, mock, _ = unlock_asked()
    push(h, 3_100_000, [world_event("front door", "locked")], 3_500_000)
    push(h, 4_000_000, [world_event("front door", "jammed")], 5_000_000)  # re-asked
    actions = push(h, 5_200_000, [world_event("front door", "unlocked")], 6_000_000)  # then someone unlocks it
    assert EVIDENCE_WITHDRAWN.format(device="the front door", value="unlocked") in said(actions)
    clean(h)


def test_reasks_are_bounded():
    h, mock, _ = unlock_asked()
    push(h, 3_100_000, [world_event("front door", "locked")], 3_500_000)
    actions = []
    for i, value in enumerate(["jammed", "locked", "jammed", "locked"]):
        actions += push(h, 4_000_000 + i * 1_000_000, [world_event("front door", value)], 4_900_000 + i * 1_000_000)
    assert len([t for t in said(actions) if t.startswith("Heads up")]) == 2
    later = say(h, 9_100_000, "yes please", 12_000_000)
    assert tool_calls(later) == ["st_unlock"]
    clean(h)


def test_a_flickering_camera_reading_re_asks_once():
    """Review finding: a blinking LED reads differently on every renewal."""
    h, tk, actions = washer_booking(["4C", "none", "4C", "none", "4C", "none", "4C"])
    assert len([t for t in said(actions) if t.startswith("Heads up")]) == 1
    later = say(h, 9_100_000, "yes go ahead", 14_000_000)
    assert tool_calls(later) == ["book_technician"]
    clean(h)


def test_the_device_is_named_once_with_its_article():
    h, mock, _ = unlock_asked(device="the front door")
    push(h, 3_100_000, [world_event("the front door", "locked")], 3_500_000)
    actions = push(h, 4_000_000, [world_event("the front door", "jammed")], 5_000_000)
    assert [t for t in said(actions) if t.startswith("Heads up")][0].startswith("Heads up: The front door changed")
    clean(h)
