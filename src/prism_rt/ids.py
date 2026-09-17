"""Deterministic, per-session ID generation.

No module-level counters: every `IdGenerator` instance is fresh per
`SessionStore`, which is what keeps sessions isolated from each other (C2)
and replays byte-identical (C1) — the same event sequence against a fresh
generator always yields the same IDs in the same order.
"""

from __future__ import annotations

_PREFIXES = {
    "event": "e",
    "step": "s",
    "turn": "t",
    "goal": "g",
    "plan": "p",
    "call": "c",
    "job": "j",
    "action": "a",
    "utt": "u",
    "timer": "tm",
}


class IdGenerator:
    """Counter-based ID generator. One instance per session."""

    def __init__(self, seed: int | str = 0) -> None:
        self.seed = seed
        self._counters: dict[str, int] = {}

    def next(self, kind: str) -> str:
        if kind not in _PREFIXES:
            raise KeyError(f"unknown id kind: {kind!r}")
        n = self._counters.get(kind, 0) + 1
        self._counters[kind] = n
        return f"{_PREFIXES[kind]}-{n:04d}"

    def peek(self, kind: str) -> int:
        """Next counter value that would be issued, without consuming it."""
        return self._counters.get(kind, 0) + 1
