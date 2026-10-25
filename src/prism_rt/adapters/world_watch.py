"""Watches the device a held write targets, for evidence-bound confirmation
(kernel/evidence.py). Demo mode only.

Driven by the companion-app event feed (adapters/event_feed.py): when the
agent asks for a yes on a write whose tool declares `watch`
(`confirmation_asked` after its `call_planned`), the device is read at once
-- the question's baseline -- and then every `interval_s`, for as long as the
call is held: until it is sent, cancelled or finished (`max_s` is only a
safety stop for a session left open).  Reads of one device are serialized,
so a slow read that started before a newer one can never report an older
value after it. Each new value is
reported to the kernel as a `world_fact` under the key both sides build with
`prism_rt.canonical.world_target_key`. `nudge()` re-reads every watched
device at once (after the "someone else" drill). A read that finds no such
device (an error marked `missing_target`, e.g. "unlock the back door" in a
home with one lock) is reported once as missing, with the error's words, and
the watch stops: the kernel drops the question.

The interval is kept long on purpose: SmartThings allows about 12 requests a
minute per device, and the write itself needs up to six (a pre-read, the
command and up to four read-backs).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from prism_rt.canonical import world_target_key

log = logging.getLogger("janus.world_watch")

_FINAL = ("call_sent", "call_result", "call_cancelled")


class WorldWatcher:
    def __init__(
        self,
        manifest: list[dict],
        read_state: Callable[[str, str], Awaitable[object]],
        report: Callable[..., Awaitable[None]],
        *,
        interval_s: float = 10.0,
        intervals: dict[str, float] | None = None,
        max_s: float = 3600.0,
        max_s_by_tool: dict[str, float] | None = None,
        sleep: Callable[[float], Awaitable] = asyncio.sleep,
    ) -> None:
        self._watch_arg = {t["name"]: t["watch"] for t in manifest if isinstance(t.get("watch"), str)}
        self._read_state = read_state
        self._report = report
        self._interval_s = interval_s
        self._intervals = dict(intervals or {})  # per tool: a camera reading can be polled faster than SmartThings
        self._max_s = max_s
        # Per tool: a camera read is a model call, so its fast polling is kept short.
        self._max_s_by_tool = dict(max_s_by_tool or {})
        self._sleep = sleep
        self._planned: dict[str, tuple[str, dict]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._last: dict[str, object] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def on_event(self, event: dict) -> None:
        """Feed every `janus.events` event here; never raises."""
        try:
            kind, call_id = event.get("type"), event.get("call_id")
            if kind == "call_planned" and call_id:
                self._planned[call_id] = (event.get("tool"), event.get("args") or {})
            elif kind == "confirmation_asked" and call_id:
                tool, args = self._planned.get(call_id, (None, {}))
                arg = self._watch_arg.get(tool)
                spoken = args.get(arg) if arg else None
                if isinstance(spoken, str) and spoken.strip() and call_id not in self._tasks:
                    self._last.pop(world_target_key(spoken), None)  # each question gets its own baseline
                    self._tasks[call_id] = asyncio.ensure_future(self._watch(call_id, tool, spoken))
            elif kind in _FINAL and call_id in self._tasks:
                self._tasks.pop(call_id).cancel()
        except Exception:  # noqa: BLE001 - the watch is optional; never break the feed
            log.warning("world watch: could not handle %s", event.get("type"), exc_info=True)

    def watching(self) -> list[tuple[str, str]]:
        return [(tool, args.get(self._watch_arg.get(tool, ""), "")) for cid, (tool, args) in self._planned.items() if cid in self._tasks]

    async def nudge(self) -> None:
        for tool, spoken in self.watching():
            await self._read_and_report(tool, spoken)

    async def _watch(self, call_id: str, tool: str, spoken: str) -> None:
        waited = 0.0
        try:
            while waited <= self._max_s_by_tool.get(tool, self._max_s):
                if not await self._read_and_report(tool, spoken):
                    break
                interval = self._intervals.get(tool, self._interval_s)
                await self._sleep(interval)
                waited += interval
        except asyncio.CancelledError:
            pass
        finally:
            self._tasks.pop(call_id, None)

    async def _read_and_report(self, tool: str, spoken: str) -> bool:
        """False when the device does not exist (reported; stop watching)."""
        key = world_target_key(spoken)
        async with self._locks.setdefault(key, asyncio.Lock()):
            try:
                value = await self._read_state(tool, spoken)
            except Exception as exc:  # noqa: BLE001 - a failed read is just no news
                log.info("world watch: could not read %r: %s", spoken, exc)
                if getattr(exc, "missing_target", False):
                    await self._report(key, None, missing=str(exc))
                    return False
                return True
            if value is None or self._last.get(key) == value:
                return True
            self._last[key] = value
            await self._report(key, value)
            return True
