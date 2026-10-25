"""SmartThings REST client: stdlib urllib run in a worker thread (no new
dependency), a per-device request budget so a runaway loop is refused locally
instead of earning a 429, and HTTP errors mapped to messages that are honest
to say aloud. The token comes from the caller (env `ST_PAT`), never stored."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from typing import Callable

BASE = "https://api.smartthings.com/v1"
BUDGET_PER_MINUTE = 12  # commands (and account-level calls) per device per minute
# Status reads have their own budget: a command spends up to five of them (the
# read before it and the confirmation reads after), and the evidence watch reads
# a device every few seconds while a question is open. Sharing the command budget
# refused the 4th switch command within a minute (scripted run, 24 Oct).
READ_BUDGET_PER_MINUTE = 60
log = logging.getLogger("prism_rt.smartthings")


class SmartThingsError(Exception):
    """A failure whose message is fit to be spoken."""


class UnknownDeviceError(SmartThingsError):
    """The device the user named is not in the home (or not one device):
    nothing can be done to it, so a question about it must not be asked
    (adapters/world_watch.py reports it as a missing target)."""

    missing_target = True


def _urllib_http(method: str, url: str, headers: dict, body: bytes | None):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - fixed https base
        return resp.status, dict(resp.headers), resp.read()


class SmartThingsClient:
    def __init__(self, token: str, base: str = BASE, *, http: Callable = _urllib_http,
                 clock: Callable[[], float] = time.monotonic, location_id: str | None = None) -> None:
        self._token = token
        self._base = base.rstrip("/")
        self._http = http
        self._clock = clock
        self._location_id = location_id
        self._sent: dict[str, deque] = defaultdict(deque)

    def _spend(self, key: str) -> None:
        now = self._clock()
        window = self._sent[key]
        while window and now - window[0] >= 60.0:
            window.popleft()
        if len(window) >= (READ_BUDGET_PER_MINUTE if key.endswith(":read") else BUDGET_PER_MINUTE):
            raise SmartThingsError("I'm holding off to stay within SmartThings' rate limit for this device. Try again in a minute.")
        window.append(now)

    async def _request(self, method: str, path: str, body: dict | None = None, *, key: str = "account"):
        self._spend(key)
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        data = json.dumps(body).encode() if body is not None else None
        try:
            status, resp_headers, raw = await asyncio.to_thread(self._http, method, self._base + path, headers, data)
        except urllib.error.HTTPError as exc:
            raise SmartThingsError(_http_message(exc.code)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SmartThingsError("I couldn't reach SmartThings.") from exc
        remaining = {k.lower(): v for k, v in resp_headers.items()}.get("x-ratelimit-remaining")
        if remaining is not None:
            log.info("smartthings %s %s -> %s (rate remaining %s)", method, path, status, remaining)
        return json.loads(raw) if raw else {}

    async def default_location(self) -> str:
        if self._location_id is None:
            items = (await self._request("GET", "/locations")).get("items", [])
            if not items:
                raise SmartThingsError("There is no SmartThings location on this account.")
            self._location_id = items[0]["locationId"]
        return self._location_id

    async def list_devices(self) -> list[dict]:
        return (await self._request("GET", "/devices")).get("items", [])

    async def status(self, device_id: str) -> dict:
        return await self._request("GET", f"/devices/{device_id}/status", key=f"{device_id}:read")

    async def command(self, device_id: str, capability: str, command: str, args=None) -> dict:
        body = {"commands": [{"component": "main", "capability": capability, "command": command, "arguments": list(args or [])}]}
        return await self._request("POST", f"/devices/{device_id}/commands", body, key=device_id)

    async def list_modes(self, location_id: str) -> list[dict]:
        return (await self._request("GET", f"/locations/{location_id}/modes")).get("items", [])

    async def current_mode(self, location_id: str) -> dict:
        return await self._request("GET", f"/locations/{location_id}/modes/current")

    async def set_mode(self, location_id: str, mode_id: str) -> dict:
        return await self._request("PUT", f"/locations/{location_id}/modes/current", {"modeId": mode_id})

    async def list_scenes(self, location_id: str) -> list[dict]:
        return (await self._request("GET", f"/scenes?locationId={location_id}")).get("items", [])

    async def execute_scene(self, scene_id: str) -> dict:
        return await self._request("POST", f"/scenes/{scene_id}/execute", key=scene_id)


def _http_message(code: int) -> str:
    return {
        401: "My SmartThings token has expired or isn't valid.",
        403: "My SmartThings token is missing a scope for that.",
        422: "SmartThings rejected that command.",
        429: "SmartThings is rate limiting me right now.",
    }.get(code, f"SmartThings returned an error ({code}).")
