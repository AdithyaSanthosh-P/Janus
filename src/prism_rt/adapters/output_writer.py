"""OutputWriter: the synchronous, final write to the harness.

Called only from EmissionGate, inside the EMIT phase — there is no buffer
between decision and this write (K3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from prism_rt.adapters.codec import HarnessCodec
from prism_rt.errors import CodecError
from prism_rt.model.actions import Action


@dataclass(frozen=True)
class WriteResult:
    ok: bool
    error: str | None = None


class OutputWriter(Protocol):
    def write(self, action: Action) -> WriteResult: ...


class BufferOutputWriter:
    """In-memory writer used by SimHarness and tests: encodes and appends
    rather than sending over a real transport."""

    def __init__(self, codec: HarnessCodec) -> None:
        self._codec = codec
        self.actions: list[Action] = []
        self.encoded: list[dict] = []

    def write(self, action: Action) -> WriteResult:
        try:
            encoded = self._codec.encode(action)
        except CodecError as exc:
            return WriteResult(ok=False, error=str(exc))
        self.actions.append(action)
        self.encoded.append(encoded)
        return WriteResult(ok=True)
