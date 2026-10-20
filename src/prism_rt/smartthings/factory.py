"""Picks the backend from the environment and combines tool packs.

`JANUS_ST_MODE=real|mock` (default mock). Real mode needs `ST_PAT` (a personal
access token, 24 h lifetime) and reports errors honestly; it never falls back
to the mock on its own -- switching is a deliberate environment change.
"""

from __future__ import annotations

import os
from typing import Awaitable, Callable, Mapping

from prism_rt.smartthings.client import SmartThingsClient
from prism_rt.smartthings.mock import MockSmartThings


def backend_mode(env: Mapping[str, str] = os.environ) -> str:
    mode = (env.get("JANUS_ST_MODE") or "mock").strip().lower()
    if mode not in ("real", "mock"):
        raise SystemExit(f"JANUS_ST_MODE must be 'real' or 'mock', not {mode!r}")
    return mode


def make_backend(env: Mapping[str, str] = os.environ):
    if backend_mode(env) == "mock":
        return MockSmartThings()
    token = (env.get("ST_PAT") or "").strip()
    if not token:
        raise SystemExit("JANUS_ST_MODE=real needs ST_PAT (a SmartThings personal access token) in the environment")
    return SmartThingsClient(token, location_id=(env.get("ST_LOCATION_ID") or None))


def combine(*toolsets) -> tuple[list[dict], Callable[[str, dict], Awaitable[dict]]]:
    """One manifest and one executor over several packs, dispatching by tool name."""
    manifest: list[dict] = []
    routes: dict[str, Callable] = {}
    for toolset in toolsets:
        executor = toolset.make_executor()
        for tool in toolset.manifest:
            manifest.append(tool)
            routes[tool["name"]] = executor

    async def execute(tool_name: str, args: dict) -> dict:
        executor = routes.get(tool_name)
        if executor is None:
            raise ValueError(f"unknown tool {tool_name!r}")
        return await executor(tool_name, args)

    return manifest, execute
