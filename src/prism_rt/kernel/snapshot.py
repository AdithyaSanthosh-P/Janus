"""SnapshotProjector: compute the state snapshot from committed facts at the
moment of emission. Never cached (S3) — recomputed every time it's attached
to an action, from whatever the FactStore holds right now.
"""

from __future__ import annotations

from prism_rt.canonical import compute_digest
from prism_rt.model.types import FactStatus, Snapshot
from prism_rt.store.catalog import value_matches_schema_type
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

        # P-04 (blueprint line 2528): snapshot keys ⊆ tool parameter names,
        # types match schema. A live model can extract a slot no tool takes
        # ("mood") or a value of the wrong type; those facts stay in the
        # store (the Interpreter's view still sees them), but they're not
        # state the harness can score against, so they're not reported.
        # Verified by reproduction: before this, a FINAL's snapshot carried
        # {'destination': 'Pune', 'mood': 'excited'}.
        params: dict[str, dict] = {}
        for tool in store.catalog.usable_tools():
            for name, prop in ((tool.params_schema or {}).get("properties") or {}).items():
                params.setdefault(name, prop if isinstance(prop, dict) else {})
        prefix = f"slot.{goal_id}."
        slots = {
            key[len(prefix):]: value
            for key, value in store.facts.snapshot_committed().items()
            if key.startswith(prefix)
            and key[len(prefix):] in params
            and value_matches_schema_type(params[key[len(prefix):]], value)
        }

        digest = compute_digest({"intent": intent, "slots": slots})
        return Snapshot(intent=intent, slots=slots, revision=store.facts.revision(), digest=digest)
