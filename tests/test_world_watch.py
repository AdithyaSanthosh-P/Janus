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

    async def read(spoken):
        reads.append(spoken)
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

        async def read(_spoken):
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
