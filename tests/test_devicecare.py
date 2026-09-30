"""The device-care extension (camera-grounded troubleshooting, one safe booking).

Deterministic: ScriptedProvider answers stand in for the live model with the
same shapes a live Gemini run produced (see demo/run_device_care_demo.py);
the tools are the real `DeviceCareToolset`, driven through the harness's
`handler` hook so a booking really lands in `toolset.bookings`.
"""

from __future__ import annotations

import asyncio

from conftest import chunk_event, drain, eot_event, frame_event, manifest_event

from prism_rt.devicecare import DeviceCareToolset
from prism_rt.model.types import ActionType, JobKind
from prism_rt.profiles import demo_config, fdb_v3_config
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider
from prism_rt.workers.interpreter import build_prompt

LATENCY = {kind: 10_000 for kind in JobKind}


def run(executor, name, **args):
    return asyncio.run(executor(name, args))


# --- the tools ----------------------------------------------------------------


def test_indicator_lookup_needs_the_light_name_when_the_pattern_is_ambiguous():
    ex = DeviceCareToolset().make_executor()
    ambiguous = run(ex, "identify_indicator", device_type="router", led_color="orange", led_state="blinking")
    assert ambiguous["found"] is False and "Which light" in ambiguous["message"]
    named = run(ex, "identify_indicator", device_type="router", led_color="orange", led_state="blinking", led_name="internet")
    assert named["found"] is True and named["led"] == "internet"


def test_diagnosis_opens_a_case_that_later_calls_use():
    tk = DeviceCareToolset()
    ex = tk.make_executor()
    assert run(ex, "get_fix_steps")["found"] is False  # nothing diagnosed yet
    run(ex, "lookup_error_code", device_type="washing machine", code="e3")
    steps = run(ex, "get_fix_steps")
    assert steps["found"] is True and steps["technician_needed"] is True
    booked = run(ex, "book_technician", date="Friday", time_slot="afternoon")
    assert booked["booked"] is True and tk.bookings[0]["issue_id"] == "washer_drain_blocked"


def test_booking_is_refused_without_a_diagnosis_or_with_a_bad_slot():
    tk = DeviceCareToolset()
    ex = tk.make_executor()
    assert run(ex, "book_technician", date="Friday", time_slot="morning")["booked"] is False
    run(ex, "lookup_error_code", device_type="washer", code="E1")
    assert run(ex, "book_technician", date="Friday", time_slot="midnight")["booked"] is False
    assert tk.bookings == []


def test_warranty_lookup():
    ex = DeviceCareToolset().make_executor()
    assert run(ex, "check_warranty", serial_number="sn-r500-1042")["status"] == "active"
    assert run(ex, "check_warranty", serial_number="nope")["found"] is False


# --- the kernel driving them --------------------------------------------------


def harness(provider, toolset) -> SimHarness:
    ex = toolset.make_executor()
    tools = {t["name"]: {"latency_ms": 100, "handler": (lambda args, n=t["name"]: run(ex, n, **args))} for t in toolset.manifest}
    h = SimHarness(demo_config(), seed=3, provider=provider, tools=tools, worker_latency_us=LATENCY)
    h.send(0, [manifest_event(toolset.manifest)])
    return h


def calls(actions):
    return [(a.body.tool_name, dict(a.body.arguments)) for a in actions if a.action_type == ActionType.TOOL_CALL]


def test_camera_frame_supplies_the_light_the_user_did_not_name():
    provider = ScriptedProvider()
    provider.register("interpret", "blinking", {
        "act": "new_goal", "intent": "identify_indicator", "commit_intent": True,
        "slot_deltas": [{"name": "device_type", "scope": "goal", "op": "set", "value": "router"},
                        {"name": "led_state", "scope": "goal", "op": "set", "value": "blinking"}],
        "visual_reference": "at_utterance",
        "visual_candidates": [{"name": "led_color", "description": "colour of the light"},
                              {"name": "led_name", "description": "which light it is"}],
        "actions": [{"tool": "identify_indicator", "args": {"device_type": "router", "led_state": "blinking"}}],
    })
    provider.register("plan", "identify_indicator", {"steps": [{
        "local_id": "s1", "tool": "identify_indicator", "kind": "read",
        "bindings": {"device_type": {"type": "fact", "key": "slot.$G.device_type"},
                     "led_state": {"type": "fact", "key": "slot.$G.led_state"},
                     "led_color": {"type": "fact", "key": "slot.$G.led_color"},
                     "led_name": {"type": "fact", "key": "slot.$G.led_name"}}, "after": []}]})
    provider.register("vision", "led_color", {"claims": [
        {"name": "led_color", "value": "orange", "confidence": "high"},
        {"name": "led_name", "value": "internet", "confidence": "high"}]})
    provider.register("compose", "identify_indicator", {"text": "The orange internet light means the router cannot reach the internet.", "claims": []})

    tk = DeviceCareToolset()
    h = harness(provider, tk)
    h.send(50_000, [frame_event("cam-1")])
    h.send(100_000, [chunk_event("my router has a blinking light that is not green, what does it mean")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 6_000_000)

    assert calls(actions) == [("identify_indicator", {"device_type": "router", "led_state": "blinking", "led_color": "orange", "led_name": "internet"})]
    assert tk.current_issue == "router_no_internet"
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1 and finals[0].body.task_completed is True
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_camera_answers_a_question_the_compiled_plan_asked():
    """30 Sep live demo: the first turn named the colour but not whether the
    light blinks, so the compiled plan asked about `a0.led_state`; the user
    then pointed the camera at the router. Vision answered into the plain
    `slot.<g>.led_state` and the goal kept asking "I still need the led
    state". The camera's answer now fills the action's missing value."""
    provider = ScriptedProvider()
    provider.register("interpret", "through my camera", {
        "act": "answer_clarification", "slot_deltas": [],
        "visual_reference": "at_utterance",
        "visual_candidates": [{"name": "led_state", "description": "is the light blinking or solid"},
                              {"name": "led_name", "description": "which light it is"}],
    })
    provider.register("interpret", "orange light", {
        "act": "new_goal", "intent": "identify_indicator", "slot_deltas": [],
        "actions": [{"tool": "identify_indicator", "args": {"device_type": "router", "led_color": "orange"}}],
    })
    provider.register("extract", "orange light", {"value": None})
    provider.register("vision", "led_state", {"claims": [
        {"name": "led_state", "value": "solid", "confidence": "high"},
        {"name": "led_name", "value": "internet", "confidence": "high"}]})
    provider.register("compose", "a0", {"text": "The solid orange internet light means no internet.", "claims": []})

    h = harness(provider, DeviceCareToolset())
    h.send(50_000, [frame_event("cam-1")])
    h.send(100_000, [chunk_event("my router is showing an orange light")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 2_000_000, stop_on_final=False)
    asked = [a.body.text for a in actions if a.action_type == ActionType.CLARIFY]
    assert asked == ["What's the led state: solid, blinking or off?"]  # the option list, not a bare name
    h.send(2_100_000, [frame_event("cam-2")])
    h.send(2_200_000, [chunk_event("can you see it through my camera")])
    h.send(2_250_000, [eot_event()])
    actions = drain(h, 8_000_000)

    identify = [args for name, args in calls(actions) if name == "identify_indicator"]
    assert identify and identify[0]["led_state"] == "solid" and identify[0]["led_color"] == "orange"
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def _booking(day: str, slot: str, commit: bool = True) -> dict:
    return {"act": "new_goal", "intent": "book_technician", "commit_intent": commit,
            "slot_deltas": [{"name": "date", "scope": "goal", "op": "set", "value": day},
                            {"name": "time_slot", "scope": "goal", "op": "set", "value": slot}],
            "actions": [{"tool": "book_technician", "args": {"date": day, "time_slot": slot}}]}


def _diagnosed_toolset() -> DeviceCareToolset:
    tk = DeviceCareToolset()
    tk.current_issue = "router_no_internet"
    return tk


def _self_correcting_booking(*, commit: bool = True):
    provider = ScriptedProvider()
    provider.register("interpret", "Friday", _booking("Friday", "afternoon", commit))  # the whole corrected turn
    provider.register("interpret", "Thursday", _booking("Thursday", "morning", commit))  # a prefix, mid-sentence
    provider.register("compose", "book_technician", {"text": "Booked for Friday afternoon.", "claims": ["result:s1"]})
    provider.register("plan", "book_technician", {"steps": [{
        "local_id": "s1", "tool": "book_technician", "kind": "write",
        "bindings": {"date": {"type": "fact", "key": "slot.$G.date"}, "time_slot": {"type": "fact", "key": "slot.$G.time_slot"}}, "after": []}]})
    tk = _diagnosed_toolset()
    h = harness(provider, tk)
    h.send(100_000, [chunk_event("book a technician for Thursday morning")])
    h.send(1_600_000, [chunk_event("no wait, make it Friday afternoon")])
    h.send(1_700_000, [eot_event()])
    return h, tk, drain(h, 12_000_000)


def test_a_mid_sentence_change_of_mind_books_exactly_once_for_the_final_answer():
    h, tk, actions = _self_correcting_booking()
    assert [c[0] for c in calls(actions)] == ["book_technician"]
    assert calls(actions)[0][1] == {"date": "Friday", "time_slot": "afternoon"}
    assert len(tk.bookings) == 1 and (tk.bookings[0]["date"], tk.bookings[0]["time_slot"]) == ("Friday", "afternoon")
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_a_change_after_the_booking_went_through_never_books_twice():
    """30 Sep live demo: "book Thursday morning" went through (BK-0001), then
    "make it evening" re-ran the finished task and booked BK-0002. A
    correction to a confirmed write is answered honestly, never re-run."""
    provider = ScriptedProvider()
    provider.register("interpret", "make it evening", {
        "act": "slot_update", "slot_deltas": [{"name": "time_slot", "scope": "goal", "op": "set", "value": "evening"}]})
    provider.register("interpret", "Thursday", _booking("Thursday", "morning"))
    provider.register("compose", "book_technician", {"text": "Booked for Thursday morning.", "claims": []})
    tk = _diagnosed_toolset()
    h = harness(provider, tk)
    h.send(100_000, [chunk_event("book a technician for Thursday morning")])
    h.send(150_000, [eot_event()])
    assert [a for a in drain(h, 6_000_000) if a.action_type == ActionType.FINAL]
    h.send(6_100_000, [chunk_event("make it evening")])
    actions = [er.action for er in h.send(6_150_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 12_000_000, stop_on_final=False)

    assert len(tk.bookings) == 1 and tk.bookings[0]["time_slot"] == "morning"
    assert calls(actions) == []
    said = [a.body.text for a in actions if a.action_type in (ActionType.SPEAK, ActionType.FINAL)]
    assert said and said[0].startswith("That's already done — book technician (Thursday, morning) went through.")
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_a_new_booking_request_after_one_went_through_is_confirmed_first():
    """30 Sep live demo: after BK-0001 (Monday evening) went through, "No,
    cancel it. Make it Friday" was read as a new request and booked BK-0002.
    A repeat of a confirmed write now asks first, naming what stands; an
    explicit yes still books it."""
    provider = ScriptedProvider()
    provider.register("interpret", "yes please", {"act": "confirm", "slot_deltas": []})
    provider.register("interpret", "cancel it", _booking("Friday", "evening"))
    provider.register("interpret", "Monday", _booking("Monday", "evening"))
    provider.register("compose", "book_technician", {"text": "Booked.", "claims": []})
    tk = _diagnosed_toolset()
    h = harness(provider, tk)
    h.send(100_000, [chunk_event("book a technician for Monday evening")])
    h.send(150_000, [eot_event()])
    assert [a for a in drain(h, 6_000_000) if a.action_type == ActionType.FINAL]

    h.send(6_100_000, [chunk_event("no, cancel it, make it Friday")])
    actions = [er.action for er in h.send(6_150_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 12_000_000, stop_on_final=False)
    assert len(tk.bookings) == 1
    asked = [a.body.text for a in actions if a.action_type == ActionType.SPEAK and "already have" in a.body.text]
    assert asked == ["You already have book technician (Monday, evening) confirmed, and I can't cancel or change it "
                     "from here. Do you want a second one: book technician (Friday, evening)?"]

    h.send(12_100_000, [chunk_event("yes please")])
    h.send(12_150_000, [eot_event()])
    drain(h, 18_000_000)
    assert [(b["date"], b["time_slot"]) for b in tk.bookings] == [("Monday", "evening"), ("Friday", "evening")]
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_without_the_users_go_ahead_nothing_is_booked():
    """A declared write is never made on an answer that isn't a go-ahead (G4)."""
    h, tk, actions = _self_correcting_booking(commit=False)
    assert calls(actions) == [] and tk.bookings == []


# --- prompts and profiles -----------------------------------------------------

BASE_VIEW = {"transcript": "hello", "active_intent": None, "active_slots": {}, "suspended_goals": [],
             "pending_clarification": None, "tools": []}


def test_camera_and_commit_guidance_only_appear_when_asked_for():
    plain = build_prompt({**BASE_VIEW, "vision_enabled": True})
    assert "live camera frame" not in plain and "commit_intent: set true" not in plain
    rich = build_prompt({**BASE_VIEW, "vision_enabled": True, "camera_available": True, "commit_intent_guidance": True})
    assert "live camera frame" in rich and "commit_intent: set true" in rich


def test_the_benchmark_profile_is_unaffected_by_the_extension():
    cfg = fdb_v3_config()
    assert cfg.vision_enabled is False and cfg.g4_exempt_undeclared_mutability is True
    demo = demo_config()
    assert demo.vision_enabled is True and demo.g4_exempt_undeclared_mutability is False
    assert demo.watchdog_timeout_ms == 0 and demo.fill_unstated_required_enabled is False


# --- camera plumbing (no LiveKit needed) ---------------------------------------


def test_frame_store_resolves_recent_frames_and_forgets_old_ones():
    from prism_rt.voice.camera import FrameStore

    store = FrameStore(keep=2)
    ids = [store.put(bytes([i])) for i in range(3)]
    assert ids == ["cam-1", "cam-2", "cam-3"]
    assert store.get("cam-1") is None  # evicted
    assert store("cam-3").data == b"\x02" and store.get("cam-3").mime_type == "image/jpeg"


def test_frame_sampler_admits_about_one_frame_per_interval():
    from prism_rt.voice.camera import FrameSampler

    now = [0.0]
    sampler = FrameSampler(interval_s=1.0, clock=lambda: now[0])
    admitted = []
    for _ in range(90):  # 3 s of a 30 fps track
        admitted.append(sampler.due())
        now[0] += 1 / 30
    assert sum(admitted) == 3


def test_voice_bridge_turns_a_camera_frame_into_a_video_frame_event():
    from prism_rt.adapters.voice_bridge import VoiceBridge

    async def go():
        events, actions = asyncio.Queue(), asyncio.Queue()

        async def say(_):
            return None

        async def execute(*_):
            return {}

        bridge = VoiceBridge(events=events, actions=actions, say=say, execute_tool=execute)
        await bridge.on_video_frame("cam-7")
        return await events.get()

    ev = asyncio.run(go())
    assert ev["type"] == "video_frame" and ev["payload"] == {"frame_id": "cam-7"}


def test_a_question_about_an_earlier_result_is_answered_from_it():
    """30 Sep live demo: "What's my router's name?" right after a diagnosis
    that named it was refused as something the agent can't do."""
    provider = ScriptedProvider()
    provider.register("interpret", "router's name", {"act": "unclear", "slot_deltas": [], "status_question": True})
    provider.register("interpret", "orange", {
        "act": "new_goal", "intent": "identify_indicator", "slot_deltas": [],
        "actions": [{"tool": "identify_indicator",
                     "args": {"device_type": "router", "led_color": "orange", "led_state": "solid", "led_name": "internet"}}]})
    provider.register("compose", "a0", {"text": "No internet.", "claims": []})
    h = harness(provider, DeviceCareToolset())
    h.send(100_000, [chunk_event("the internet light is solid orange")])
    h.send(150_000, [eot_event()])
    assert [a for a in drain(h, 6_000_000) if a.action_type == ActionType.FINAL]
    h.send(6_100_000, [chunk_event("what's my router's name")])
    actions = [er.action for er in h.send(6_150_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 8_000_000, stop_on_final=False)
    said = " ".join(a.body.text for a in actions if a.action_type == ActionType.SPEAK)
    assert "Aria R500" in said


def test_a_follow_up_during_the_camera_diagnosis_waits_for_it():
    """30 Sep live demo: "How do I fix it?" said while the camera was still
    being read replaced the diagnosis, and the fix-steps lookup ran with
    nothing diagnosed. Camera requests now compile like any other, so the
    follow-up is appended after the diagnosis instead."""
    provider = ScriptedProvider()
    provider.register("interpret", "how do I fix it", {
        "act": "new_goal", "intent": "get_fix_steps", "slot_deltas": [],
        "actions": [{"tool": "get_fix_steps", "args": {}}]})
    provider.register("interpret", "look at my router", {
        "act": "new_goal", "intent": "identify_indicator", "slot_deltas": [],
        "visual_reference": "at_utterance",
        "visual_candidates": [{"name": "led_color", "description": "colour"}, {"name": "led_state", "description": "state"},
                              {"name": "led_name", "description": "which light"}],
        "actions": [{"tool": "identify_indicator", "args": {"device_type": "router"}}]})
    provider.register("vision", "led_color", {"claims": [
        {"name": "led_color", "value": "orange", "confidence": "high"},
        {"name": "led_state", "value": "solid", "confidence": "high"},
        {"name": "led_name", "value": "internet", "confidence": "high"}]})
    provider.register("compose", "get_fix_steps", {"text": "Restart the modem, then check the cable.", "claims": []})

    tk = DeviceCareToolset()
    ex = tk.make_executor()
    tools = {t["name"]: {"latency_ms": 100, "handler": (lambda args, n=t["name"]: run(ex, n, **args))} for t in tk.manifest}
    slow_vision = {**LATENCY, JobKind.VISION: 2_000_000}
    h = SimHarness(demo_config(), seed=3, provider=provider, tools=tools, worker_latency_us=slow_vision)
    h.send(0, [manifest_event(tk.manifest)])
    h.send(50_000, [frame_event("cam-1")])
    h.send(100_000, [chunk_event("can you look at my router and tell me what's wrong")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 1_000_000, stop_on_final=False)  # the camera is still being read
    h.send(1_100_000, [chunk_event("how do I fix it")])
    h.send(1_150_000, [eot_event()])
    actions += drain(h, 10_000_000)

    assert [name for name, _ in calls(actions)] == ["identify_indicator", "get_fix_steps"]
    assert tk.current_issue == "router_no_internet"
    finals = [a for a in actions if a.action_type == ActionType.FINAL]
    assert len(finals) == 1 and finals[0].body.task_completed is True
    assert TraceChecker().check(h.run_log.reports, store=h.store) == []


def test_camera_disagreeing_with_the_user_asks_instead_of_overriding():
    """The user's stated value outranks the camera (§9.1), including a value
    held under a compiled action's own key: a disagreement is asked about."""
    provider = ScriptedProvider()
    provider.register("interpret", "light is green", {
        "act": "new_goal", "intent": "identify_indicator", "slot_deltas": [],
        "visual_reference": "at_utterance",
        "visual_candidates": [{"name": "led_color", "description": "colour"}, {"name": "led_name", "description": "which light"}],
        "actions": [{"tool": "identify_indicator", "args": {"device_type": "router", "led_color": "green", "led_state": "solid"}}]})
    provider.register("vision", "led_color", {"claims": [
        {"name": "led_color", "value": "orange", "confidence": "high"},
        {"name": "led_name", "value": "internet", "confidence": "high"}]})
    h = harness(provider, DeviceCareToolset())
    h.send(50_000, [frame_event("cam-1")])
    h.send(100_000, [chunk_event("the internet light is green, can you look")])
    h.send(150_000, [eot_event()])
    actions = drain(h, 3_000_000, stop_on_final=False)
    asked = [a.body.text for a in actions if a.action_type == ActionType.CLARIFY]
    assert asked == ["The camera shows orange. Did you mean green, or orange?"]
    assert calls(actions) == []


def test_a_repeat_booking_is_not_appended_to_a_running_goal():
    """Review finding: with the first booking done but its goal still running,
    "No, cancel it. Make it Friday" was appended to that goal, whose go-ahead
    carried over -- a second booking with no question asked."""
    provider = ScriptedProvider()
    provider.register("interpret", "cancel it", _booking("Friday", "afternoon"))
    provider.register("interpret", "Thursday", {**_booking("Thursday", "morning"), "actions": [
        {"tool": "book_technician", "args": {"date": "Thursday", "time_slot": "morning"}},
        {"tool": "get_fix_steps", "args": {}}]})
    tk = _diagnosed_toolset()
    ex = tk.make_executor()
    tools = {t["name"]: {"latency_ms": 5_000 if t["name"] == "get_fix_steps" else 100,
                         "handler": (lambda args, n=t["name"]: run(ex, n, **args))} for t in tk.manifest}
    h = SimHarness(demo_config(), seed=3, provider=provider, tools=tools, worker_latency_us=LATENCY)
    h.send(0, [manifest_event(tk.manifest)])
    h.send(100_000, [chunk_event("book a technician for Thursday morning and tell me how to fix it")])
    h.send(150_000, [eot_event()])
    drain(h, 3_000_000, stop_on_final=False)
    assert len(tk.bookings) == 1  # booked; the fix-steps lookup is still running
    h.send(3_100_000, [chunk_event("no, cancel it, make it Friday")])
    actions = [er.action for er in h.send(3_150_000, [eot_event()]).emit_report.emitted]
    actions += drain(h, 10_000_000, stop_on_final=False)
    assert len(tk.bookings) == 1
    assert any("You already have book technician (Thursday, morning)" in a.body.text
               for a in actions if a.action_type == ActionType.SPEAK)


def test_a_call_built_on_the_camera_value_records_the_actions_own_key():
    """Review finding: a user value stated later for the action must
    invalidate a call built on the camera's value, so the action's own
    (absent) key is in the call's read set."""
    provider = ScriptedProvider()
    provider.register("interpret", "look at my router", {
        "act": "new_goal", "intent": "identify_indicator", "slot_deltas": [], "visual_reference": "at_utterance",
        "visual_candidates": [{"name": "led_color", "description": "colour"}, {"name": "led_name", "description": "which light"}],
        "actions": [{"tool": "identify_indicator", "args": {"device_type": "router", "led_state": "solid"}}]})
    provider.register("vision", "led_color", {"claims": [
        {"name": "led_color", "value": "orange", "confidence": "high"},
        {"name": "led_name", "value": "internet", "confidence": "high"}]})
    provider.register("compose", "identify_indicator", {"text": "No internet.", "claims": []})
    h = harness(provider, DeviceCareToolset())
    h.send(50_000, [frame_event("cam-1")])
    h.send(100_000, [chunk_event("can you look at my router")])
    h.send(150_000, [eot_event()])
    drain(h, 4_000_000)
    call = next(c for c in h.store.call_ledger.all() if c.tool == "identify_indicator")
    keys = {e.key for e in call.read_set.entries}
    assert {"slot.g-0001.led_color", "slot.g-0001.a0.led_color"} <= keys


def test_clarify_options_only_for_single_word_lists():
    from prism_rt.kernel.responder import _OPTIONS

    assert _OPTIONS.match("solid, blinking or off.") and _OPTIONS.match("morning, afternoon or evening.")
    for description in ("City name or zip code", "Date or time", "Name or ID of the account"):
        assert not _OPTIONS.match(description)
