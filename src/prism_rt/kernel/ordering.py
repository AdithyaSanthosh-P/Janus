"""Batch ordering: sort one step's events by (ts_us, class, seq).

At equal timestamps, invalidating events (manifest, interruption, user
content) are ordered ahead of events subject to invalidation (tool
results, worker results) — see `docs/prompt 2.txt` §5.1. `seq` breaks any
remaining tie in deterministic queue-arrival order.
"""

from __future__ import annotations

from prism_rt.model.events import EVENT_CLASS_BY_PAYLOAD_TYPE as EVENT_CLASS
from prism_rt.model.events import Envelope

__all__ = ["order_batch", "EVENT_CLASS"]


def order_batch(envelopes: list[Envelope]) -> list[Envelope]:
    return sorted(envelopes, key=lambda env: env.ordering_key)
