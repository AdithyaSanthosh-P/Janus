"""The SmartThings tools the kernel drives: two reads and eight device commands.

Device commands are state-setting, so every write is `repeatable` (a later
request is its own action, not a duplicate). Because the cloud answers ACCEPTED
even for an offline device, each write first reads the state (already there ->
nothing is sent), sends, then reads the state back: confirmed -> `done`, never
seen to change -> `outcome: "unconfirmed"` (the kernel's "sent, not confirmed").
Unlock and open-garage are `requires_confirmation`: the kernel asks first.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from prism_rt.smartthings.client import SmartThingsError
from prism_rt.smartthings.confirm import WAITS, attribute_value, confirm_state, confirm_until, same

GARAGE_CAPABILITIES = ("garageDoorControl", "doorControl")
SETPOINT_RANGE = (16, 30)

# Words that say which kind of device the user means, for labels that don't
# contain them ("front door" -> the lock).
_KIND_WORDS = {
    "lock": ("lock", "door", "front", "back", "entrance"),
    "garage": ("garage",),
    "thermostat": ("ac", "air", "thermostat", "temperature", "cooling", "cool"),
    "switch": ("light", "lamp", "switch", "plug", "bulb"),
}


_KIND_ORDER = ("garage", "thermostat", "switch", "lock")

# What each watched write targets: (capabilities, kind, attribute). The same
# resolution the write's own `_set` uses, so the evidence watch
# (adapters/world_watch.py) reads the very device the write would change.
_WRITE_TARGETS = {
    "st_lock": (("lock",), "lock", "lock"),
    "st_unlock": (("lock",), "lock", "lock"),
    "st_open_garage": (GARAGE_CAPABILITIES, "garage", "door"),
    "st_close_garage": (GARAGE_CAPABILITIES, "garage", "door"),
    "st_switch": (("switch",), "switch", "switch"),
    "st_set_cooling_setpoint": (("thermostatCoolingSetpoint",), "thermostat", "coolingSetpoint"),
}


def _obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def _str(description: str, enum: list | None = None) -> dict:
    return {"type": "string", "description": description, **({"enum": enum} if enum else {})}


_DEVICE = _str("The device as the user names it, for example front door, garage, air conditioner or light.")


def _write(name: str, description: str, properties: dict, required: list[str], *, security: bool = False, label: str = "",
           done_when: str | None = None, state_unit: str | None = None) -> dict:
    spec = {"name": name, "description": description, "mutability": "state_changing", "repeatable": True, "label": label,
            "params_schema": _obj(properties, required)}
    if security:
        spec["requires_confirmation"] = True
    if done_when is not None:
        # kernel/evidence.py: the device is watched while the write waits for
        # a yes; if it reaches this value by itself, the write is withdrawn.
        spec["watch"] = "device"
        spec["done_when"] = done_when
    if state_unit:
        spec["state_unit"] = state_unit
    return spec


MANIFEST: list[dict] = [
    {"name": "st_list_devices", "description": "List the smart-home devices that can be controlled.",
     "mutability": "read_only", "params_schema": _obj({}, [])},
    {"name": "st_get_status", "description": "Read the current state of one smart-home device (locked or unlocked, open or closed, on or off, temperature setpoint).",
     "mutability": "read_only", "params_schema": _obj({"device": _DEVICE}, ["device"])},
    _write("st_lock", "Lock a door lock.", {"device": _DEVICE}, ["device"], label="Lock the {device}", done_when="locked"),
    _write("st_unlock", "Unlock a door lock. A security action: the user is asked to confirm first.", {"device": _DEVICE}, ["device"], security=True, label="Unlock the {device}", done_when="unlocked"),
    _write("st_open_garage", "Open the garage door. A security action: the user is asked to confirm first.", {"device": _DEVICE}, ["device"], security=True, label="Open the {device}", done_when="open"),
    _write("st_close_garage", "Close the garage door.", {"device": _DEVICE}, ["device"], label="Close the {device}", done_when="closed"),
    _write("st_set_cooling_setpoint", "Set the air-conditioner / thermostat cooling temperature in degrees Celsius (16 to 30).",
           {"device": _DEVICE, "celsius": {"type": "number", "description": "Target temperature in Celsius, 16 to 30."}}, ["device", "celsius"], label="Set the {device} to {celsius} degrees", done_when="{celsius}", state_unit="degrees"),
    _write("st_switch", "Turn a switch or light on or off.", {"device": _DEVICE, "state": _str("on or off.", ["on", "off"])}, ["device", "state"], label="Turn the {device} {state}", done_when="{state}"),
    _write("st_set_location_mode", "Set the home mode (Home, Away or Night).", {"mode": _str("Home, Away or Night.")}, ["mode"], label="Set the home mode to {mode}"),
    _write("st_execute_scene", "Run a saved SmartThings scene by name.", {"scene": _str("The scene's name, for example Goodnight.")}, ["scene"], label="Run the {scene} scene"),
]


def _norm(text) -> str:
    return " ".join(str(text or "").strip().lower().replace("-", " ").replace("_", " ").split())


class SmartThingsToolset:
    def __init__(self, backend, *, sleep: Callable[[float], Awaitable] = asyncio.sleep, waits=WAITS,
                 on_device_state: Callable[[str, str, str, str, object], None] | None = None) -> None:
        self.backend = backend
        # Optional (device_id, label, capability, attribute, value) callback --
        # the companion app's device tiles. Never lets a callback error escape.
        self._on_device_state = on_device_state
        self.manifest: list[dict] = MANIFEST
        self._sleep = sleep
        self._waits = waits
        self._devices: list[dict] | None = None

    def make_executor(self) -> Callable[[str, dict], Awaitable[dict]]:
        async def execute(tool_name: str, args: dict) -> dict:
            handler = getattr(self, f"_{tool_name}", None)
            if handler is None:
                raise ValueError(f"unknown tool {tool_name!r}")
            return await handler(args)

        return execute

    # ---- device resolution ------------------------------------------------
    async def _all_devices(self, refresh: bool = False) -> list[dict]:
        if self._devices is None or refresh:
            self._devices = await self.backend.list_devices()
        return self._devices

    @staticmethod
    def _caps(device: dict) -> set[str]:
        return {c["id"] for comp in device.get("components", []) for c in comp.get("capabilities", [])}

    async def _resolve(self, spoken, capabilities: tuple[str, ...] | None, kind: str | None) -> dict:
        devices = await self._all_devices()
        pool = [d for d in devices if capabilities is None or self._caps(d) & set(capabilities)]
        text = _norm(spoken)
        words = set(text.split())
        exact = [d for d in pool if _norm(d.get("label")) == text or (text and text in _norm(d.get("label")))]
        if len(exact) == 1:
            return exact[0]
        named = [d for d in pool if words & set(_norm(d.get("label")).split()) - {"janus"}]
        if len(named) == 1:
            return named[0]
        if kind and words & set(_KIND_WORDS[kind]) and len(pool) == 1:
            return pool[0]
        if len(pool) == 1:
            return pool[0]
        options = ", ".join(d["label"] for d in (named or pool)) or "none"
        raise SmartThingsError(f"I couldn't tell which device you mean by {spoken!r}. I can see: {options}.")

    def _report(self, device: dict, capability: str, attribute: str, value) -> None:
        if self._on_device_state is None or value is None:
            return
        try:
            self._on_device_state(device["deviceId"], device["label"], capability, attribute, value)
        except Exception:  # noqa: BLE001 - the app must never break a device command
            pass

    def _primary(self, device: dict) -> tuple[str, str] | None:
        return {"lock": ("lock", "lock"), "garage": (self._garage_cap(device), "door"),
                "thermostat": ("thermostatCoolingSetpoint", "coolingSetpoint"), "switch": ("switch", "switch")}.get(self._kind(device))

    async def snapshot_states(self) -> None:
        """One status read per device, reported through `on_device_state` (the app's initial tiles)."""
        for device in await self._all_devices(refresh=True):
            primary = self._primary(device)
            if primary is not None:
                self._report(device, *primary, attribute_value(await self.backend.status(device["deviceId"]), *primary))

    async def read_target(self, tool: str, spoken) -> object:
        """The current value of what `tool` would change on the device the
        user named -- resolved exactly as the write resolves it. None for a
        tool that targets no single device attribute."""
        target = _WRITE_TARGETS.get(tool)
        if target is None:
            return None
        capabilities, kind, attribute = target
        device = await self._resolve(spoken, capabilities, kind)
        capability = next(c for c in capabilities if c in self._caps(device))
        value = attribute_value(await self.backend.status(device["deviceId"]), capability, attribute)
        self._report(device, capability, attribute, value)
        return value

    async def read_temperature(self, spoken="thermostat") -> object:
        """The room temperature the thermostat reports (temperatureMeasurement),
        or None when it reports none. For offers (adapters/offers.py)."""
        device = await self._resolve(spoken, ("thermostatCoolingSetpoint",), "thermostat")
        return attribute_value(await self.backend.status(device["deviceId"]), "temperatureMeasurement", "temperature")

    # ---- reads --------------------------------------------------------------
    async def _st_list_devices(self, a: dict) -> dict:
        devices = await self._all_devices(refresh=True)
        return {"devices": [{"label": d["label"], "kind": self._kind(d)} for d in devices]}

    def _kind(self, device: dict) -> str:
        caps = self._caps(device)
        for kind, names in (("lock", ("lock",)), ("garage", GARAGE_CAPABILITIES), ("thermostat", ("thermostatCoolingSetpoint",)),
                            ("switch", ("switch",))):
            if caps & set(names):
                return kind
        return "device"

    async def _st_get_status(self, a: dict) -> dict:
        spoken = a.get("device")
        words = set(_norm(spoken).split())
        # The most specific kind word wins: "garage door" is the garage and
        # "front porch light" the light, though "door" and "front" are lock words.
        kind = next((k for k in _KIND_ORDER if words & set(_KIND_WORDS[k])), None)
        by_kind = {"lock": ("lock",), "garage": GARAGE_CAPABILITIES, "thermostat": ("thermostatCoolingSetpoint",), "switch": ("switch",)}
        device = await self._resolve(spoken, by_kind[kind] if kind else None, kind)
        status = await self.backend.status(device["deviceId"])
        attrs = {cap: {attr: v["value"] for attr, v in body.items()} for cap, body in status["components"]["main"].items()}
        primary = self._primary(device)
        state = attribute_value(status, *primary) if primary else None
        if primary:
            self._report(device, *primary, state)
        return {"found": True, "device": device["label"], "state": state, "attributes": attrs,
                "note": f"This is the device the user called {spoken!r}." if spoken and _norm(spoken) != _norm(device["label"]) else ""}

    def _garage_cap(self, device: dict) -> str:
        caps = self._caps(device)
        return next((c for c in GARAGE_CAPABILITIES if c in caps), GARAGE_CAPABILITIES[0])

    # ---- writes -------------------------------------------------------------
    async def _set(self, spoken, capabilities, kind, attribute, command, want, args=()) -> dict:
        device = await self._resolve(spoken, capabilities, kind)
        capability = next(c for c in capabilities if c in self._caps(device))
        device_id, label = device["deviceId"], device["label"]
        current = attribute_value(await self.backend.status(device_id), capability, attribute)
        if same(current, want):
            self._report(device, capability, attribute, current)
            return {"done": True, "already": True, "device": label, "state": want,
                    "note": "No command was sent: the device was already in that state."}
        await self.backend.command(device_id, capability, command, list(args))
        seen: list = []
        confirmed = await confirm_state(self.backend, device_id, capability, attribute, want, waits=self._waits,
                                        sleep=self._sleep, seen=seen)
        self._report(device, capability, attribute, want if confirmed else (seen[-1] if seen else current))
        if confirmed:
            return {"done": True, "device": label, "state": want}
        return {"outcome": "unconfirmed", "sent": True, "device": label, "wanted": want}

    async def _st_lock(self, a: dict) -> dict:
        return await self._set(a.get("device"), ("lock",), "lock", "lock", "lock", "locked")

    async def _st_unlock(self, a: dict) -> dict:
        return await self._set(a.get("device"), ("lock",), "lock", "lock", "unlock", "unlocked")

    async def _st_open_garage(self, a: dict) -> dict:
        return await self._set(a.get("device"), GARAGE_CAPABILITIES, "garage", "door", "open", "open")

    async def _st_close_garage(self, a: dict) -> dict:
        return await self._set(a.get("device"), GARAGE_CAPABILITIES, "garage", "door", "close", "closed")

    async def _st_switch(self, a: dict) -> dict:
        state = _norm(a.get("state"))
        if state not in ("on", "off"):
            raise SmartThingsError("A switch can only be turned on or off.")
        return await self._set(a.get("device"), ("switch",), "switch", "switch", state, state)

    async def _st_set_cooling_setpoint(self, a: dict) -> dict:
        try:
            celsius = float(a.get("celsius"))
        except (TypeError, ValueError):
            raise SmartThingsError("I need a temperature in degrees Celsius.") from None
        low, high = SETPOINT_RANGE
        if not low <= celsius <= high:
            raise SmartThingsError(f"I can only set the cooling temperature between {low} and {high} degrees.")
        celsius = int(celsius) if celsius == int(celsius) else celsius
        return await self._set(a.get("device"), ("thermostatCoolingSetpoint",), "thermostat", "coolingSetpoint",
                               "setCoolingSetpoint", celsius, [celsius])

    async def _st_set_location_mode(self, a: dict) -> dict:
        location = await self.backend.default_location()
        modes = await self.backend.list_modes(location)
        want = _norm(a.get("mode"))
        mode = next((m for m in modes if _norm(m.get("name")) == want or _norm(m.get("label")) == want), None)
        if mode is None:
            raise SmartThingsError("The home modes are " + ", ".join(m["name"] for m in modes) + ".")
        current = await self.backend.current_mode(location)
        if current.get("id") == mode["id"]:
            return {"done": True, "already": True, "state": mode["name"],
                    "note": "No command was sent: the home was already in that mode."}
        await self.backend.set_mode(location, mode["id"])

        async def read():
            return (await self.backend.current_mode(location)).get("id")

        if await confirm_until(read, mode["id"], waits=self._waits, sleep=self._sleep):
            return {"done": True, "state": mode["name"]}
        return {"outcome": "unconfirmed", "sent": True, "wanted": mode["name"]}

    async def _st_execute_scene(self, a: dict) -> dict:
        location = await self.backend.default_location()
        scenes = await self.backend.list_scenes(location)
        want = _norm(a.get("scene"))
        scene = next((s for s in scenes if _norm(s.get("sceneName")) == want), None) or next(
            (s for s in scenes if want and want in _norm(s.get("sceneName"))), None)
        if scene is None:
            raise SmartThingsError(
                "The saved scenes are " + ", ".join(s["sceneName"] for s in scenes) + "." if scenes
                else "There are no saved scenes in SmartThings to run.")
        await self.backend.execute_scene(scene["sceneId"])
        return {"done": True, "scene": scene["sceneName"]}
