"""Read-back confirmation: SmartThings answers ACCEPTED even for an offline
device, so a command only counts as done once the state is seen to change."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

WAITS = (0.5, 1.0, 2.0, 4.0)


def attribute_value(status: dict, capability: str, attribute: str) -> Any:
    try:
        return status["components"]["main"][capability][attribute]["value"]
    except (KeyError, TypeError):
        return None


def same(a: Any, b: Any) -> bool:
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a).lower() == str(b).lower()


async def confirm_until(
    read: Callable[[], Awaitable[Any]], want: Any, *, waits=WAITS, sleep=asyncio.sleep, seen: list | None = None
) -> bool:
    """Reads (at most len(waits) times, backing off between reads) until the value equals `want`.
    Every value read is appended to `seen`, so a caller can report what the device actually showed."""
    for wait in waits:
        await sleep(wait)
        value = await read()
        if seen is not None:
            seen.append(value)
        if same(value, want):
            return True
    return False


async def confirm_state(
    client, device_id: str, capability: str, attribute: str, want: Any, *, waits=WAITS, sleep=asyncio.sleep,
    seen: list | None = None,
) -> bool:
    async def read():
        return attribute_value(await client.status(device_id), capability, attribute)

    return await confirm_until(read, want, waits=waits, sleep=sleep, seen=seen)
