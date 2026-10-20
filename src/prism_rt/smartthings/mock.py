"""In-memory stand-in for the SmartThings cloud, with the real response shapes.

`stuck` names capabilities whose commands are ACCEPTED but never change state
(an offline device) -- the "sent, not confirmed" path."""

from __future__ import annotations

LOCATION = "loc-1"


class MockSmartThings:
    def __init__(self, *, stuck=(), garage_capability: str = "garageDoorControl", extra_lock: bool = False) -> None:
        self.stuck = set(stuck)
        self.commands: list[tuple] = []  # (device_id, capability, command, args)
        self.status_reads = 0
        self._devices = {
            "lock-1": ("Janus Lock", {"lock": {"lock": "unlocked"}}),
            "garage-1": ("Janus Garage", {garage_capability: {"door": "closed"}}),
            "thermo-1": ("Janus Thermostat", {"thermostatCoolingSetpoint": {"coolingSetpoint": 24},
                                              "temperatureMeasurement": {"temperature": 29}}),
            "switch-1": ("Janus Switch", {"switch": {"switch": "off"}}),
        }
        if extra_lock:
            self._devices["lock-2"] = ("Back Door Lock", {"lock": {"lock": "unlocked"}})
        self._modes = {"m-home": "Home", "m-away": "Away", "m-night": "Night"}
        self._mode = "m-home"
        self.scenes_run: list[str] = []

    async def default_location(self) -> str:
        return LOCATION

    async def list_devices(self) -> list[dict]:
        return [
            {"deviceId": did, "label": label,
             "components": [{"id": "main", "capabilities": [{"id": cap} for cap in caps]}]}
            for did, (label, caps) in self._devices.items()
        ]

    async def status(self, device_id: str) -> dict:
        self.status_reads += 1
        _label, caps = self._devices[device_id]
        return {"components": {"main": {cap: {attr: {"value": val} for attr, val in attrs.items()}
                                        for cap, attrs in caps.items()}}}

    async def command(self, device_id: str, capability: str, command: str, args=None) -> dict:
        args = list(args or [])
        self.commands.append((device_id, capability, command, args))
        if capability not in self.stuck:
            attrs = self._devices[device_id][1][capability]
            if capability == "lock":
                attrs["lock"] = "locked" if command == "lock" else "unlocked"
            elif capability in ("garageDoorControl", "doorControl"):
                attrs["door"] = "open" if command == "open" else "closed"
            elif capability == "switch":
                attrs["switch"] = command
            elif capability == "thermostatCoolingSetpoint":
                attrs["coolingSetpoint"] = args[0]
        return {"results": [{"id": device_id, "status": "ACCEPTED"}]}

    async def list_modes(self, location_id: str) -> list[dict]:
        return [{"id": mid, "name": name, "label": name} for mid, name in self._modes.items()]

    async def current_mode(self, location_id: str) -> dict:
        return {"id": self._mode, "name": self._modes[self._mode]}

    async def set_mode(self, location_id: str, mode_id: str) -> dict:
        if "mode" not in self.stuck:
            self._mode = mode_id
        return {"id": mode_id, "name": self._modes[mode_id]}

    async def list_scenes(self, location_id: str) -> list[dict]:
        return [{"sceneId": "scene-1", "sceneName": "Goodnight"}]

    async def execute_scene(self, scene_id: str) -> dict:
        self.scenes_run.append(scene_id)
        return {"status": "success"}
