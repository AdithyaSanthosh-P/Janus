"""SnapshotProjector: compute the state snapshot from committed facts at the
moment of emission. Never cached (S3) — recomputed every time it's attached
to an action, from whatever the FactStore holds right now.
"""

from __future__ import annotations

from prism_rt.canonical import compute_digest
from prism_rt.model.types import FactStatus, Snapshot
from prism_rt.store.session import SessionStore


class SnapshotProjector:
    def project(self, store: SessionStore) -> Snapshot:
        active_goal_fact = store.facts.get("goal.active")
        if active_goal_fact is None or active_goal_fact.status == FactStatus.RETRACTED or not active_goal_fact.value:
            digest = compute_digest({"intent": None, "slots": {}})
            return Snapshot(intent=None, slots={}, revision=store.facts.revision(), digest=digest)

        goal_id = active_goal_fact.value
        intent_fact = store.facts.get(f"goal.{goal_id}.intent")
        intent = (
            intent_fact.value
            if intent_fact is not None and intent_fact.status != FactStatus.RETRACTED
            else None
        )

        prefix = f"slot.{goal_id}."
        slots = {
            key[len(prefix):]: value
            for key, value in store.facts.snapshot_committed().items()
            if key.startswith(prefix)
        }

        digest = compute_digest({"intent": intent, "slots": slots})
        return Snapshot(intent=intent, slots=slots, revision=store.facts.revision(), digest=digest)
