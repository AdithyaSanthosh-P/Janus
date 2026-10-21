"""Failure drills for the live demo (demo mode only, `JANUS_DRILLS=1`).

The companion app sends a drill on the data topic `janus.control`; the voice
worker passes it to `DrillState.apply`. Each drill is a real fault injected
on purpose at the edge of the system -- the SmartThings backend or the model
provider -- never inside the kernel, so what the jury sees is the kernel's
ordinary handling of a failure:

  st_unreachable    every SmartThings call fails as a connection error
  st_no_confirm     device commands and mode changes are accepted but never take
                    effect (-> unconfirmed; both are read back). Scenes are not
                    drilled: a scene is never read back, so a fake ACCEPTED
                    would turn into a false "done"
  st_latency        every SmartThings call is delayed by `args.ms`
  model_slow        every model call is delayed by `args.ms`
  model_down        every model call fails as a connection error
  external_command  one command sent straight to the backend by "someone else",
                    outside the kernel and its ledger
  offer             one-shot: Janus speaks first with an offer (args.kind,
                    adapters/offers.py), if the devices give it something to offer
  reset             every drill off

The benchmark (fdb mode) never builds any of this.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import urllib.error
from dataclasses import dataclass
from typing import Awaitable, Callable

from prism_rt.smartthings.client import SmartThingsError

log = logging.getLogger("janus.drills")

CONTROL_TOPIC = "janus.control"
_GARAGE = ("garageDoorControl", "doorControl")
_MAX_DELAY_MS = 15_000
_TOGGLES = ("st_unreachable", "st_no_confirm", "st_latency", "model_slow", "model_down")


@dataclass
class DrillState:
    st_unreachable: bool = False
    st_no_confirm: bool = False
    st_latency_ms: int = 0
    model_slow_ms: int = 0
    model_down: bool = False

    def reset(self) -> None:
        self.__init__()

    def active(self) -> list[str]:
        on = [n for n in ("st_unreachable", "st_no_confirm", "model_down") if getattr(self, n)]
        on += [f"st_latency {self.st_latency_ms} ms"] if self.st_latency_ms else []
        on += [f"model_slow {self.model_slow_ms} ms"] if self.model_slow_ms else []
        return on

    def apply(self, message: dict) -> dict | None:
        """Apply one validated `janus.control` message. Returns the
        `external_command` arguments when that one-shot drill was asked for
        (the caller runs it), else None. Raises ValueError on a bad message."""
        if not isinstance(message, dict) or message.get("v") != 1 or message.get("type") != "drill":
            raise ValueError("not a v1 drill message")
        name, on, args = message.get("name"), message.get("on"), message.get("args") or {}
        if not isinstance(on, bool):
            raise ValueError("on must be true or false")
        if not isinstance(args, dict):
            raise ValueError("args must be an object")
        if name == "reset":
            self.reset()
        elif name == "st_unreachable":
            self.st_unreachable = on
        elif name == "st_no_confirm":
            self.st_no_confirm = on
        elif name == "st_latency":
            self.st_latency_ms = _delay(args, 3000) if on else 0
        elif name == "model_slow":
            self.model_slow_ms = _delay(args, 4000) if on else 0
        elif name == "model_down":
            self.model_down = on
        elif name == "external_command":
            if not on:
                return None
            if not all(isinstance(args.get(k), str) and args[k] for k in ("device", "capability", "command")):
                raise ValueError("external_command needs device, capability and command")
            return {k: args[k] for k in ("device", "capability", "command")}
        elif name == "offer":
            if not on:
                return None
            if not isinstance(args.get("kind"), str) or not args["kind"]:
                raise ValueError("offer needs a kind")
            return {"offer": args["kind"]}
        else:
            raise ValueError(f"unknown drill {name!r}")
        return None


def _delay(args: dict, default: int) -> int:
    raw = args.get("ms", default)
    if isinstance(raw, bool):
        raise ValueError("ms must be a number")
    try:
        ms = int(raw)
    except (TypeError, ValueError):
        raise ValueError("ms must be a number") from None
    return max(0, min(ms, _MAX_DELAY_MS))


class DrilledBackend:
    """A SmartThings backend (real client or mock) with the SmartThings drills
    applied. Same async interface as the backend it wraps."""

    _COMMANDS = ("command", "set_mode")

    def __init__(self, inner, state: DrillState, *, sleep: Callable[[float], Awaitable] = asyncio.sleep) -> None:
        self.inner = inner
        self._state = state
        self._sleep = sleep

    def __getattr__(self, name: str):
        target = getattr(self.inner, name)
        if not inspect.iscoroutinefunction(target):
            return target

        async def drilled(*args, **kwargs):
            if self._state.st_latency_ms:
                await self._sleep(self._state.st_latency_ms / 1000)
            if self._state.st_unreachable:
                raise SmartThingsError("I couldn't reach SmartThings.")
            if self._state.st_no_confirm and name in self._COMMANDS:
                # Accepted by "the cloud", never applied: the read-back sees no change.
                log.info("drill st_no_confirm: %s accepted, not applied", name)
                return {"results": [{"status": "ACCEPTED"}]}
            return await target(*args, **kwargs)

        return drilled


class DrilledProvider:
    """A model provider with the model drills applied. `complete_json` runs in
    a worker thread (`asyncio.to_thread`), so a blocking delay is fine here."""

    def __init__(self, inner, state: DrillState, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self.inner = inner
        self._state = state
        self._sleep = sleep

    def complete_json(self, kind: str, prompt: str, schema: dict, *, media=None) -> dict:
        if self._state.model_slow_ms:
            self._sleep(self._state.model_slow_ms / 1000)
        if self._state.model_down:
            # The same error class a dropped connection raises.
            raise urllib.error.URLError("drill: model_down")
        return self.inner.complete_json(kind, prompt, schema, media=media)


async def run_external_command(backend, args: dict, on_device_state: Callable | None = None) -> str:
    """The `external_command` drill: one command by "someone else", sent to the
    backend underneath the drills (it must work even while SmartThings drills
    are on) and outside the kernel. The device is matched by label or id. The
    new state is read back the same way and reported through `on_device_state`
    (the app's tile), never through the drilled backend."""
    raw = backend.inner if isinstance(backend, DrilledBackend) else backend
    wanted = args["device"].strip().lower()
    devices = await raw.list_devices()
    match = [d for d in devices if d.get("deviceId") == args["device"] or wanted in str(d.get("label", "")).lower()]
    if len(match) != 1:
        raise ValueError(f"external_command: no single device matches {args['device']!r}")
    device = match[0]
    # A garage is garageDoorControl on some devices and doorControl on others;
    # the app cannot know which, so either name means the one the device has.
    caps = {c["id"] for comp in device.get("components", []) for c in comp.get("capabilities", [])}
    capability = args["capability"]
    if capability in _GARAGE and capability not in caps:
        capability = next((c for c in _GARAGE if c in caps), capability)
    args = {**args, "capability": capability}
    await raw.command(device["deviceId"], args["capability"], args["command"], [])
    if on_device_state is not None:
        status = await raw.status(device["deviceId"])
        attrs = status.get("components", {}).get("main", {}).get(args["capability"], {})
        for attribute, body in attrs.items():
            on_device_state(device["deviceId"], device["label"], args["capability"], attribute, body.get("value"))
            break
    return f"external_command: {args['command']} sent to {device['label']} as someone else"
