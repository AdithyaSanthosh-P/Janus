"""adapters/display_reader.py: the washer's error code, read off the camera
while a technician visit offered for it waits for a yes; and the watch that
polls it (adapters/world_watch.py, per-tool interval)."""

from __future__ import annotations

import asyncio

from prism_rt.adapters.display_reader import CLEAR, DisplayReader, reading
from prism_rt.adapters.world_watch import WorldWatcher
from prism_rt.canonical import world_target_key
from prism_rt.devicecare.tools import MANIFEST as CARE_MANIFEST
from prism_rt.voice.camera import FrameStore


def test_reading_maps_the_model_answer_to_a_watch_value():
    assert reading({"display_visible": True, "error_code": "4c"}) == "4C"
    assert reading({"display_visible": True, "error_code": " 5 C "}) == "5C"
    assert reading({"display_visible": True, "error_code": None}) == CLEAR
    assert reading({"display_visible": True, "error_code": "none"}) == CLEAR
    # No display in view is no news, never a cleared error.
    assert reading({"display_visible": False, "error_code": None}) is None
    assert reading({"display_visible": True, "error_code": "water not supplied"}) is None
    assert reading("garbage") is None


def frames_at(now):
    return FrameStore(keep=2, clock=lambda: now[0])


def test_latest_frame_only_while_the_camera_is_on():
    now = [100.0]
    store = frames_at(now)
    assert store.latest(4.0) is None
    store.put(b"a")
    store.put(b"b")
    assert store.latest(4.0).data == b"b"
    now[0] = 105.0
    assert store.latest(4.0) is None  # the camera went quiet


def test_reader_uses_the_camera_when_there_is_a_fresh_frame():
    now = [0.0]
    store = frames_at(now)
    calls = []

    def complete_json(kind, prompt, schema, *, media=None):
        calls.append((kind, [m.data for m in media]))
        return {"display_visible": True, "error_code": None}

    reader = DisplayReader(complete_json, store.latest, lambda device: "4C")
    assert asyncio.run(reader.read("washer")) == "4C"  # no camera: the washer's own status
    assert calls == []
    store.put(b"frame")
    assert asyncio.run(reader.read("washer")) == CLEAR
    assert calls == [("display", [b"frame"])]


def test_a_failed_camera_read_is_no_news():
    store = frames_at([0.0])
    store.put(b"frame")

    def boom(*_a, **_k):
        raise TimeoutError("slow")

    assert asyncio.run(DisplayReader(boom, store.latest, lambda d: "4C").read("washer")) is None


def test_the_booking_is_watched_at_its_own_interval():
    async def scenario():
        reads, reports, ticks = [], [], []
        states = ["4C", "4C", CLEAR]

        async def read(tool, spoken):
            reads.append((tool, spoken))
            return states[min(len(reads) - 1, len(states) - 1)]

        async def report(key, value):
            reports.append((key, value))

        async def sleep(s):
            ticks.append(s)
            await asyncio.sleep(0)

        w = WorldWatcher(CARE_MANIFEST, read, report, interval_s=6, intervals={"book_technician": 2}, max_s=4, sleep=sleep)
        w.on_event({"type": "call_planned", "call_id": "c-0001", "tool": "book_technician",
                    "args": {"date": "tomorrow", "time_slot": "morning", "device": "washer"}})
        w.on_event({"type": "confirmation_asked", "call_id": "c-0001", "question": "Shall I?"})
        for _ in range(10):
            await asyncio.sleep(0)
        return reads, reports, ticks

    reads, reports, ticks = asyncio.run(scenario())
    key = world_target_key("washer")
    assert reads[0] == ("book_technician", "washer")
    assert reports == [(key, "4C"), (key, CLEAR)]
    assert set(ticks) == {2}
