"""Put the SmartThings demo devices in their starting state before a rehearsal
or the demo: lock locked, garage closed, thermostat at 26, switch on (so the
correction beat "22... actually 21" really changes something).

    PYTHONPATH=src:. python scripts/demo_reset_devices.py [--celsius 26]

Reads ST_PAT from the environment or ./.env. Talks to SmartThings directly,
not through Janus.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from prism_rt.smartthings.client import SmartThingsClient
from prism_rt.smartthings.confirm import attribute_value

# capability -> (command, args, attribute, value read back)
TARGETS = {
    "lock": ("lock", [], "lock", "locked"),
    "doorControl": ("close", [], "door", "closed"),
    "garageDoorControl": ("close", [], "door", "closed"),
    "switch": ("on", [], "switch", "on"),
}


def _load_env() -> None:
    path = Path(".env")
    if path.exists():
        for line in path.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() and key.strip() not in os.environ:
                os.environ[key.strip()] = value.strip().strip('"').strip("'")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--celsius", type=float, default=26)
    args = ap.parse_args()
    _load_env()
    client = SmartThingsClient(os.environ["ST_PAT"])
    targets = dict(TARGETS, thermostatCoolingSetpoint=("setCoolingSetpoint", [args.celsius], "coolingSetpoint", args.celsius))
    for device in await client.list_devices():
        caps = {c["id"] for comp in device.get("components", []) for c in comp.get("capabilities", [])}
        for capability, (command, cmd_args, attribute, want) in targets.items():
            if capability not in caps:
                continue
            await client.command(device["deviceId"], capability, command, cmd_args)
            await asyncio.sleep(1.0)
            now = attribute_value(await client.status(device["deviceId"]), capability, attribute)
            print(f"{device['label']:<20} {capability:<26} {now}{'' if str(now) == str(want) or now == want else '  (wanted ' + str(want) + ')'}")
            break


if __name__ == "__main__":
    asyncio.run(main())
