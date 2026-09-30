"""A LiveKit access token for joining the device-care demo from the Agents
Playground (https://agents-playground.livekit.io -> manual connect). The token
dispatches the demo agent by name ("janus-demo"), so it only ever reaches a
worker started with JANUS_MODE=demo -- never a benchmark worker, and a
benchmark room never reaches the demo worker.

    python scripts/make_demo_token.py [--room janus-demo-room] [--hours 12] [--identity presenter]

Reads LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET from the environment
or ./.env. Needs the livekit-api package (in the speech image and the native
venv). Prints the URL and the token; keep the token out of screenshots.
"""

from __future__ import annotations

import argparse
import os
from datetime import timedelta
from pathlib import Path


def _load_env() -> None:
    env = Path(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", default="janus-demo-room")
    ap.add_argument("--hours", type=float, default=12)
    ap.add_argument("--identity", default="presenter")
    ap.add_argument("--agent", default=os.environ.get("JANUS_AGENT_NAME", "janus-demo"))
    args = ap.parse_args()
    _load_env()
    from livekit import api

    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(args.identity)
        .with_name(args.identity.title())
        .with_ttl(timedelta(hours=args.hours))
        .with_grants(api.VideoGrants(room_join=True, room=args.room))
        .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=args.agent)]))
    )
    print("URL  ", os.environ["LIVEKIT_URL"])
    print("TOKEN", token.to_jwt())


if __name__ == "__main__":
    main()
