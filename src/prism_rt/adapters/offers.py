"""Offers the demo worker can make (Config.proactive_offers_enabled), built from
what the devices actually say right now: an offer is made only when there is
something to offer ("The garage is open. Shall I close it?" only while it is
open). `read_target(tool, spoken)` reads the device a tool would change, as the
SmartThings pack resolves it (`SmartThingsToolset.read_target`).

Appliance offers (survey B1) read a device-care appliance instead:
`read_appliance(device)` returns its status as `get_device_status` does
(`DeviceCareToolset._get_device_status`), and the washer offer is made only
while the washer shows an error code and no visit is booked for it ("Your washer has stopped
with code 4C: ... Shall I book a technician for tomorrow morning?"). The visit's day
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

# The AC offer (demo): "It's 29 degrees inside. Shall I set the AC to 24?",
# made only while the thermostat is set above AC_TARGET. While Janus waits,
# a change made by someone else (another phone, the SmartThings app) is a
# change in what the question rested on: the kernel withdraws the offer if it
# reached 24 anyway, or asks again (kernel/evidence.py).
AC_TARGET = 24

# kind -> (appliance, write tool, its arguments, how the visit is said)
APPLIANCE_OFFERS = {
    "washer_error": ("washer", "book_technician", {"date": "tomorrow", "time_slot": "morning", "device": "washer"}, "tomorrow morning"),
}

_ids = itertools.count(1)


def _appliance_offer(kind: str, read_appliance: Callable[[str], dict] | None) -> dict | None:
    appliance, tool, args, when = APPLIANCE_OFFERS[kind]
    if read_appliance is None:
        return None
    status = read_appliance(appliance)
    code = status.get("error_code") if isinstance(status, dict) and status.get("found") else None
    if not code or status.get("visit_booked"):
        return None  # no fault, or a visit for it is already booked: nothing to offer
    meaning = str(status.get("error_meaning") or "").strip().rstrip(".")
    reason = f": {meaning[:1].lower()}{meaning[1:]}" if meaning else ""
    text = f"Your {appliance} has stopped with code {code}{reason}. Shall I book a technician for {when}?"
    return {"offer_id": f"{kind}-{next(_ids)}", "text": text, "tool": tool, "args": dict(args)}


async def _ac_offer(read_target, read_temperature) -> dict | None:
    tool = "st_set_cooling_setpoint"
    try:
        setpoint = float(await read_target(tool, "thermostat"))
    except (TypeError, ValueError):
        return None
    if setpoint <= AC_TARGET:
        return None
    inside = None
    if read_temperature is not None:
        try:
            inside = float(await read_temperature())
        except (TypeError, ValueError):
            inside = None
    reading = f"It's {inside:g} degrees inside" if inside is not None else f"The AC is set to {setpoint:g}"
    return {"offer_id": f"ac_hot-{next(_ids)}", "text": f"{reading}. Shall I set the AC to {AC_TARGET}?",
            "tool": tool, "args": {"device": "thermostat", "celsius": AC_TARGET}}


async def build_offer(kind: str, read_target: Callable[[str, str], Awaitable[object]],
                      read_appliance: Callable[[str], dict] | None = None,
                      read_temperature: Callable[[], Awaitable[object]] | None = None) -> dict | None:
    """The `offer` event payload for `kind`, or None when the device is already
    in the state the offer would put it in, the appliance shows no fault, or
    `kind` is unknown."""
    if kind in APPLIANCE_OFFERS:
        return _appliance_offer(kind, read_appliance)
    if kind == "ac_hot":
        return await _ac_offer(read_target, read_temperature)
    spec = OFFERS.get(kind)
    if spec is None:
        return None
    tool, device, done, text = spec
    if str(await read_target(tool, device)) == done:
        return None
    return {"offer_id": f"{kind}-{next(_ids)}", "text": text, "tool": tool, "args": {"device": device}}


async def no_offer_text(kind: str, read_target: Callable[[str, str], Awaitable[object]],
                        read_appliance: Callable[[str], dict] | None = None) -> str:
    """What Janus says when an offer was asked for (the app's drawer) but
    there is nothing to offer: silence looked like a freeze (found live, 22
    Oct: a washer offer after the visit was already booked)."""
    try:
        if kind in APPLIANCE_OFFERS:
            appliance = APPLIANCE_OFFERS[kind][0]
            status = read_appliance(appliance) if read_appliance is not None else {}
            if isinstance(status, dict) and status.get("visit_booked"):
                return f"Nothing to offer for the {appliance} right now: a technician visit is already booked."
            return f"The {appliance} shows no error right now, so there's nothing to offer."
        if kind == "ac_hot":
            return f"The AC is already at {AC_TARGET} degrees or cooler, so there's nothing to offer."
        spec = OFFERS.get(kind)
        if spec is not None:
            _tool, device, done, _text = spec
            return f"The {device} is already {done}, so there's nothing to offer."
    except Exception:  # noqa: BLE001 - the notice is best effort
        pass
    return "There's nothing to offer right now."
