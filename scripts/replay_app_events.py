"""Publish a recorded Janus event session into a LiveKit room, for building the
companion app without the agent running (contract: app/INTEGRATION.md).

    PYTHONPATH=src:. python scripts/replay_app_events.py [--file app/sample_events.jsonl]
        [--room janus-demo-room] [--speed 1.0] [--loop]

Joins as `janus-replay` (no agent is dispatched) and publishes each line of the
file as one data packet on topic `janus.events`, keeping the gaps between the
events' `t_ms` values. Reads LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET
from the environment or ./.env. Needs the `livekit` and `livekit-api`
packages (requirements-speech.txt).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

TOPIC = "janus.events"
IDENTITY = "janus-replay"


def _load_env() -> None:
    env = Path(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_events(path: Path) -> list[dict]:
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sorted(events, key=lambda ev: ev["seq"])


async def replay(events: list[dict], room_name: str, speed: float, loop: bool) -> None:
    from livekit import api, rtc

    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(IDENTITY)
        .with_name("Janus replay")
        .with_grants(api.VideoGrants(room_join=True, room=room_name, can_publish_data=True))
        .to_jwt()
    )
    room = rtc.Room()
    await room.connect(os.environ["LIVEKIT_URL"], token)
    print(f"joined {room_name} as {IDENTITY}; publishing {len(events)} events on {TOPIC!r}")
    try:
        while True:
            last_t = events[0]["t_ms"] if events else 0
            for ev in events:
                await asyncio.sleep(max(0, ev["t_ms"] - last_t) / 1000 / speed)
                last_t = ev["t_ms"]
                await room.local_participant.publish_data(json.dumps(ev), reliable=True, topic=TOPIC)
                print(f"{ev['seq']:>4} {ev['type']}")
            if not loop:
                break
            await asyncio.sleep(3)
    finally:
        await room.disconnect()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="app/sample_events.jsonl")
    ap.add_argument("--room", default="janus-demo-room")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--loop", action="store_true")
    args = ap.parse_args()
    _load_env()
    asyncio.run(replay(load_events(Path(args.file)), args.room, args.speed, args.loop))


if __name__ == "__main__":
    main()
