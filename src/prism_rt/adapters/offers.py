"""Offers the demo worker can make (Config.proactive_offers_enabled), built from
what the devices actually say right now: an offer is made only when there is
something to offer ("The garage is open. Shall I close it?" only while it is
open). `read_target(tool, spoken)` reads the device a tool would change, as the
SmartThings pack resolves it (`SmartThingsToolset.read_target`).

Appliance offers (survey B1) read a device-care appliance instead:
`read_appliance(device)` returns its status as `get_device_status` does
(`DeviceCareToolset._get_device_status`), and the washer offer is made only
while the washer shows an error code ("Your washer has stopped with code
4C: ... Shall I book a technician for tomorrow morning?"). The visit's day
and slot are proposed in the question, so a "yes" books exactly what was
said and a correction ("make it Friday afternoon") changes it before
anything is booked."""

from __future__ import annotations

import itertools
from typing import Awaitable, Callable

# kind -> (write tool, device as spoken, value meaning "nothing to offer", question)
OFFERS = {
    "garage_open": ("st_close_garage", "garage", "closed", "The garage is open. Shall I close it?"),
    "door_unlocked": ("st_lock", "front door", "locked", "The front door is unlocked. Shall I lock it?"),
}

# kind -> (appliance, write tool, its arguments, how the visit is said)
APPLIANCE_OFFERS = {
    "washer_error": ("washer", "book_technician", {"date": "tomorrow", "time_slot": "morning"}, "tomorrow morning"),
}

_ids = itertools.count(1)


def _appliance_offer(kind: str, read_appliance: Callable[[str], dict] | None) -> dict | None:
    appliance, tool, args, when = APPLIANCE_OFFERS[kind]
    if read_appliance is None:
        return None
    status = read_appliance(appliance)
    code = status.get("error_code") if isinstance(status, dict) and status.get("found") else None
    if not code:
        return None
    meaning = str(status.get("error_meaning") or "").strip().rstrip(".")
    reason = f": {meaning[:1].lower()}{meaning[1:]}" if meaning else ""
    text = f"Your {appliance} has stopped with code {code}{reason}. Shall I book a technician for {when}?"
    return {"offer_id": f"{kind}-{next(_ids)}", "text": text, "tool": tool, "args": dict(args)}


async def build_offer(kind: str, read_target: Callable[[str, str], Awaitable[object]],
                      read_appliance: Callable[[str], dict] | None = None) -> dict | None:
    """The `offer` event payload for `kind`, or None when the device is already
    in the state the offer would put it in, the appliance shows no fault, or
    `kind` is unknown."""
    if kind in APPLIANCE_OFFERS:
        return _appliance_offer(kind, read_appliance)
    spec = OFFERS.get(kind)
    if spec is None:
        return None
    tool, device, done, text = spec
    if str(await read_target(tool, device)) == done:
        return None
    return {"offer_id": f"{kind}-{next(_ids)}", "text": text, "tool": tool, "args": {"device": device}}
