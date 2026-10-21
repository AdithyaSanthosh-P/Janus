"""Offers the demo worker can make (Config.proactive_offers_enabled), built from
what the devices actually say right now: an offer is made only when there is
something to offer ("The garage is open. Shall I close it?" only while it is
open). `read_target(tool, spoken)` reads the device a tool would change, as the
SmartThings pack resolves it (`SmartThingsToolset.read_target`)."""

from __future__ import annotations

import itertools
from typing import Awaitable, Callable

# kind -> (write tool, device as spoken, value meaning "nothing to offer", question)
OFFERS = {
    "garage_open": ("st_close_garage", "garage", "closed", "The garage is open. Shall I close it?"),
    "door_unlocked": ("st_lock", "front door", "locked", "The front door is unlocked. Shall I lock it?"),
}

_ids = itertools.count(1)


async def build_offer(kind: str, read_target: Callable[[str, str], Awaitable[object]]) -> dict | None:
    """The `offer` event payload for `kind`, or None when the device is already
    in the state the offer would put it in (or `kind` is unknown)."""
    spec = OFFERS.get(kind)
    if spec is None:
        return None
    tool, device, done, text = spec
    if str(await read_target(tool, device)) == done:
        return None
    return {"offer_id": f"{kind}-{next(_ids)}", "text": text, "tool": tool, "args": {"device": device}}
