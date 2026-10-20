"""adapters/world_watch.py: the host watches the device a held write targets
and reports it to the kernel; and the app hears why the write was withdrawn."""

from __future__ import annotations

import asyncio

from test_evidence_confirmation import push, unlock_asked, world_event

from prism_rt.adapters.event_feed import EventFeed
from prism_rt.adapters.world_watch import WorldWatcher
from prism_rt.canonical import world_target_key
from prism_rt.smartthings.tools import MANIFEST


def watcher_with(states):
    reads, reports, ticks = [], [], []

    async def read(tool, spoken):
        reads.append((tool, spoken))
        return states[min(len(reads) - 1, len(states) - 1)]

    async def report(key, value):
        reports.append((key, value))

    async def sleep(s):
        ticks.append(s)
        await asyncio.sleep(0)

    return WorldWatcher(MANIFEST, read, report, interval_s=10, max_s=30, sleep=sleep), reads, reports


def planned(call_id="c-0001", tool="st_unlock", device="Front door"):
    return {"type": "call_planned", "call_id": call_id, "tool": tool, "args": {"device": device}}


def test_watches_from_the_question_until_the_call_is_sent():
    async def scenario():
        w, reads, reports = watcher_with(["locked", "locked", "unlocked"])
        w.on_event(planned())
        w.on_event({"type": "confirmation_asked", "call_id": "c-0001", "question": "Shall I?"})
        for _ in range(10):
            await asyncio.sleep(0)
        w.on_event({"type": "call_sent", "call_id": "c-0001"})
        await asyncio.sleep(0)
        return reads, reports, w.watching()

    reads, reports, watching = asyncio.run(scenario())
    key = world_target_key("Front door")
    assert reports == [(key, "locked"), (key, "unlocked")]  # the baseline, then only changes
    assert len(reads) == 4 and watching == []  # 30 s at a 10 s interval, then it stops


def test_unwatched_tools_and_reads_are_ignored():
    async def scenario():
        w, reads, reports = watcher_with(["locked"])
        w.on_event(planned(tool="st_get_status"))
        w.on_event({"type": "confirmation_asked", "call_id": "c-0001", "question": "?"})
        w.on_event({"type": "confirmation_asked", "call_id": "c-0009", "question": "?"})  # never planned
        await asyncio.sleep(0)
        return reads, reports

    assert asyncio.run(scenario()) == ([], [])


def test_a_failed_read_is_no_news():
    async def scenario():
        reports = []

        async def read(_tool, _spoken):
            raise RuntimeError("SmartThings down")

        async def report(key, value):
            reports.append((key, value))

        async def sleep(_s):
            await asyncio.sleep(0)

        w = WorldWatcher(MANIFEST, read, report, interval_s=10, max_s=20, sleep=sleep)
        w.on_event(planned())
        w.on_event({"type": "confirmation_asked", "call_id": "c-0001", "question": "?"})
        for _ in range(6):
            await asyncio.sleep(0)
        return reports

    assert asyncio.run(scenario()) == []


def test_the_app_hears_why_the_write_was_withdrawn():
    h, _mock, _ = unlock_asked()
    events = []
    feed = EventFeed(events.append, backend="mock", packs=["smartthings"], manifest=MANIFEST, clock_ms=lambda: 0)
    seen = len(h.run_log.reports)
    for report in h.run_log.reports:
        feed.on_step(report, h.store)
    push(h, 3_100_000, [world_event("front door", "locked")], 3_200_000)
    push(h, 4_000_000, [world_event("front door", "unlocked")], 5_000_000)
    for report in h.run_log.reports[seen:]:
        feed.on_step(report, h.store)
    cancelled = [e for e in events if e["type"] == "call_cancelled"]
    assert [e["reason"] for e in cancelled] == ["Front door is already unlocked; changed outside Janus"]
    assert not any(e["type"] == "call_result" for e in events)


def test_a_slow_read_never_reports_an_older_value_after_a_newer_one():
    """Review finding: a poll that started before the "someone else" change
    must not land after nudge()'s fresher read."""
    async def scenario():
        reports = []
        gate = asyncio.Event()
        values = iter(["25", "24"])

        async def read(_tool, _spoken):
            value = next(values)
            if value == "25":
                await gate.wait()  # the poll's read is slow
            return value

        async def report(key, value):
            reports.append(value)

        async def sleep(_s):
            await asyncio.Event().wait()  # no second poll in this test

        w = WorldWatcher(MANIFEST, read, report, interval_s=10, sleep=sleep)
        w.on_event({"type": "call_planned", "call_id": "c-1", "tool": "st_set_cooling_setpoint", "args": {"device": "ac", "celsius": 22}})
        w.on_event({"type": "confirmation_asked", "call_id": "c-1", "question": "?"})
        await asyncio.sleep(0)
        nudged = asyncio.ensure_future(w.nudge())
        await asyncio.sleep(0)
        gate.set()
        await nudged
        return reports

    assert asyncio.run(scenario()) == ["25", "24"]


def test_the_watch_reads_the_device_the_write_targets():
    """Review finding: "garage door" holds the lock word "door"; the watch
    (and a plain status question) must read the garage."""
    from prism_rt.smartthings.mock import MockSmartThings
    from prism_rt.smartthings.tools import SmartThingsToolset

    async def _no_sleep(_s):
        return None

    mock = MockSmartThings()
    ts = SmartThingsToolset(mock, sleep=_no_sleep)
    assert asyncio.run(ts.read_target("st_open_garage", "garage door")) == "closed"
    assert asyncio.run(ts.read_target("st_unlock", "front door")) == "unlocked"
    assert asyncio.run(ts.read_target("st_switch", "front porch light")) == "off"
    status = asyncio.run(ts.make_executor()("st_get_status", {"device": "garage door"}))
    assert status["device"] == "Janus Garage"
