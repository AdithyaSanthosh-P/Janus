"""FdbToolAdapter: executes an admitted Janus TOOL_CALL against FDB's own
mock tool registry (docs/fdb_v3_implementation_plan.md §3.3).

This is deliberately thin and stateless, matching every other adapter in
this package: it never touches the store or the kernel, and it runs
only because `CommitGate` already
admitted the call (Janus emitted `TOOL_CALL`) -- it does not itself
decide whether a call is allowed. `MockAPIRegistry.call` is FDB's own,
unmodified code (`v3/mock_apis.py`); this module never edits it, only
calls it.

Two things FDB's own inference pipeline (`lk_agent_tool.py.AssistantFnc`)
does that a naive pass-through would miss:

1. **Default-filling.** FDB's per-tool wrapper methods declare Python
   defaults (`add_to_cart(quantity=1)`, `calculate_commute(mode="driving")`)
   and always pass the *resolved* value to `registry.call` -- including
   when the model omitted that argument. If Janus's own plan step leaves
   an optional parameter unbound, the schema-declared default (already
   present on the tool's `params_schema` via `fdb_manifest.py`'s
   introspection) must be filled in before the call, or FDB's own
   argument-accuracy judge sees a missing key FDB's own template would
   never have produced.
2. **Telemetry format.** FDB's evaluators (`evaluate_pass_rate.py`,
   `evaluate_tool_calls.py`) read tool calls *only* from
   `/tmp/agent_tool_calls.log`, one JSON object per line, keyed by room:
   `{"room": ..., "call": {"function", "args", "timestamp_start",
   "timestamp_end"}}` -- reproduced here byte-for-byte
   (`AssistantFnc.log_tool_call`, `v3/lk_agent_tool.py`).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Callable


def fill_schema_defaults(params_schema: dict, args: dict) -> dict:
    """Adds any optional parameter FDB's own tool schema declares a
    `default` for, that `args` doesn't already set -- never overrides a
    value that's actually present, even if it equals the default."""
    filled = dict(args)
    properties = (params_schema or {}).get("properties") or {}
    for name, prop in properties.items():
        if name not in filled and isinstance(prop, dict) and "default" in prop:
            filled[name] = prop["default"]
    return filled


@dataclass(frozen=True)
class LoggedCall:
    room: str
    function: str
    args: dict
    timestamp_start: float
    timestamp_end: float

    def as_fdb_line(self) -> str:
        return json.dumps({
            "room": self.room,
            "call": {
                "function": self.function,
                "args": self.args,
                "timestamp_start": self.timestamp_start,
                "timestamp_end": self.timestamp_end,
            },
        })


class FdbToolAdapter:
    """`registry` is FDB's real `mock_apis.MockAPIRegistry` instance (or
    anything exposing the same `.call(function_name, **kwargs) -> dict`).
    `get_params_schema(tool_name)` looks up the tool's schema (from the
    catalog fdb_manifest.py built) for default-filling; pass `None` to
    skip default-filling entirely (arguments are sent exactly as given)."""

    def __init__(
        self,
        registry,
        room_name: str,
        *,
        log_path: str = "/tmp/agent_tool_calls.log",
        get_params_schema: Callable[[str], dict | None] | None = None,
    ) -> None:
        self._registry = registry
        self._room_name = room_name
        self._log_path = log_path
        self._get_params_schema = get_params_schema

    async def execute(self, tool_name: str, args: dict) -> dict:
        filled = args
        if self._get_params_schema is not None:
            schema = self._get_params_schema(tool_name)
            if schema is not None:
                filled = fill_schema_defaults(schema, args)
        t_start = time.time()
        # `MockAPIRegistry.call` -> `LatencyInjector.inject` does a real
        # blocking `time.sleep` (v3/latency_injector.py:182) -- run it off
        # the event loop so it never stalls Janus's own kernel/entry loop.
        result = await asyncio.to_thread(self._registry.call, tool_name, **filled)
        t_end = time.time()
        self._append_log(LoggedCall(self._room_name, tool_name, filled, t_start, t_end))
        return result

    def _append_log(self, call: LoggedCall) -> None:
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(call.as_fdb_line() + "\n")
