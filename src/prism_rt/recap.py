"""The `session_recap` demo tool (survey H4): "What did you do this morning?"
answered from the kernel's own ledgers, never from the model's memory of the
conversation. Read-only; `say_result`, so with demo speech polish the answer
is the ledger's sentence word for word (kernel/replies.session_recap_text).

`RecapToolset(store_getter)` follows the pack shape (`manifest`,
`make_executor`); the host passes a getter because the session's store exists
only once the kernel session starts."""

from __future__ import annotations

from typing import Awaitable, Callable

from prism_rt.kernel.replies import session_recap_text

MANIFEST: list[dict] = [
    {
        "name": "session_recap",
        "description": "Say what you did and did not do in this conversation: actions done, never sent, sent but not "
        "confirmed. Use it when the user asks what you did, what you didn't do, or what happened.",
        "mutability": "read_only",
        "say_result": True,
        "params_schema": {"type": "object", "properties": {}, "required": []},
    }
]


class RecapToolset:
    def __init__(self, store_getter: Callable[[], object] | None = None) -> None:
        self.manifest = MANIFEST
        self.store_getter = store_getter

    def make_executor(self) -> Callable[[str, dict], Awaitable[dict]]:
        async def execute(tool_name: str, args: dict) -> dict:
            if tool_name != "session_recap":
                raise ValueError(f"unknown tool {tool_name!r}")
            store = self.store_getter() if self.store_getter is not None else None
            if store is None:
                return {"say": "I can't see the record of this conversation right now."}
            return {"say": session_recap_text(store)}

        return execute
