"""FactStore: the versioned, digest-validated fact store, and the
DependencyIndex that maps changed fact keys to the objects that read them.

This is the mechanism behind every invalidation and staleness decision in
the kernel (`docs/prompt 2.txt` §3). Mutation is only permitted while the
shared `MutationGuard` is active (during a kernel step's APPLY-through-COMMIT
phases) — see `store/session.py`.
"""

from __future__ import annotations

from typing import Any

from prism_rt.canonical import ABSENT, compute_digest, normalize_value
from prism_rt.model.types import (
    Fact,
    FactStatus,
    Provenance,
    ReadSet,
    ReadSetEntry,
    ValidityResult,
    make_read_set,
)


class MutationGuard:
    """Shared flag checked by every store before it mutates. Active only
    while a `StoreTxn` is open (kernel step phases APPLY..COMMIT — K3)."""

    def __init__(self) -> None:
        self.active = False

    def check(self) -> None:
        from prism_rt.errors import StoreMutationOutsideStep

        if not self.active:
            raise StoreMutationOutsideStep(
                "session state mutated outside an active kernel step"
            )


class FactStore:
    def __init__(self, guard: MutationGuard) -> None:
        self._guard = guard
        self._facts: dict[str, Fact] = {}
        self._revision = 0
        self._pending_changes: list[tuple] = []

    def get(self, key: str) -> Fact | None:
        return self._facts.get(key)

    def revision(self) -> int:
        return self._revision

    def set(
        self,
        key: str,
        value: Any,
        status: FactStatus,
        provenance: Provenance,
        *,
        rule: str,
    ) -> bool:
        """Set `key` to `value`/`status`. Returns True iff the store actually
        changed (digest or status differs from what was there). A true no-op
        never bumps the revision, so reverting a slot back to a value it
        held earlier does not spuriously invalidate work that read it then
        (D4 in `docs/prompt 2.txt` §19)."""
        self._guard.check()
        normalized = normalize_value(value)
        digest = compute_digest(normalized)
        existing = self._facts.get(key)
        if existing is not None and existing.digest == digest and existing.status == status:
            return False
        self._revision += 1
        ver = self._revision
        old_digest = existing.digest if existing is not None else ABSENT
        old_status = existing.status if existing is not None else None
        fact = Fact(key=key, value=normalized, digest=digest, ver=ver, status=status, provenance=provenance)
        self._facts[key] = fact
        self._pending_changes.append((key, old_digest, digest, old_status, status, ver, rule))
        return True

    def retract(self, key: str, *, rule: str, provenance: Provenance | None = None) -> bool:
        """Mark `key` RETRACTED. No-op if the key never existed or is
        already retracted."""
        self._guard.check()
        existing = self._facts.get(key)
        if existing is None or existing.status == FactStatus.RETRACTED:
            return False
        self._revision += 1
        ver = self._revision
        prov = provenance if provenance is not None else existing.provenance
        fact = Fact(
            key=key,
            value=existing.value,
            digest=existing.digest,
            ver=ver,
            status=FactStatus.RETRACTED,
            provenance=prov,
        )
        self._facts[key] = fact
        self._pending_changes.append(
            (key, existing.digest, existing.digest, existing.status, FactStatus.RETRACTED, ver, rule)
        )
        return True

    def is_valid(self, read_set: ReadSet) -> ValidityResult:
        """The validity rule (`docs/prompt 2.txt` §3.3): every entry's key
        must exist, not be RETRACTED, and have an unchanged digest. Version
        is deliberately not compared here — a fact whose version moved but
        whose value reverted to the recorded digest is still valid."""
        failing: list[tuple] = []
        for entry in read_set.entries:
            fact = self._facts.get(entry.key)
            if entry.digest == ABSENT:
                if fact is not None and fact.status != FactStatus.RETRACTED:
                    failing.append((entry.key, entry.digest, fact.digest, "expected_absent_now_present"))
                continue
            if fact is None:
                failing.append((entry.key, entry.digest, ABSENT, "missing"))
                continue
            if fact.status == FactStatus.RETRACTED:
                failing.append((entry.key, entry.digest, fact.digest, "retracted"))
                continue
            if fact.digest != entry.digest:
                failing.append((entry.key, entry.digest, fact.digest, "digest_mismatch"))
        return ValidityResult(is_valid=(len(failing) == 0), failing=tuple(failing))

    def build_read_set(self, keys: list[str]) -> ReadSet:
        entries = []
        for key in keys:
            fact = self._facts.get(key)
            if fact is not None and fact.status != FactStatus.RETRACTED:
                entries.append(ReadSetEntry(key=key, version=fact.ver, digest=fact.digest))
            else:
                entries.append(ReadSetEntry(key=key, version=0, digest=ABSENT))
        return make_read_set(entries)

    def snapshot_committed(self) -> dict[str, Any]:
        """Facts usable in a projection: COMMITTED or DERIVED, never
        HYPOTHESIS or RETRACTED (§12.1)."""
        return {
            key: fact.value
            for key, fact in self._facts.items()
            if fact.status in (FactStatus.COMMITTED, FactStatus.DERIVED)
        }

    def all_derived(self) -> list[Fact]:
        """V2: facts carrying a `derivation_read_set` — candidates for
        transitive retraction when what they were derived from goes stale
        (`kernel/invalidation.py`)."""
        return [f for f in self._facts.values() if f.status == FactStatus.DERIVED]

    # --- change tracking, used by StoreTxn --------------------------------

    def changed_keys_pending(self) -> set[str]:
        return {c[0] for c in self._pending_changes}

    def drain_changes(self) -> tuple:
        changes = tuple(self._pending_changes)
        self._pending_changes = []
        return changes


class DependencyIndex:
    """Reverse index: fact key -> the IDs of calls/jobs/proposals whose read
    set includes that key. §3.2, §8.1 of `docs/prompt 2.txt`."""

    def __init__(self, guard: MutationGuard) -> None:
        self._guard = guard
        self._by_key: dict[str, set[str]] = {}
        self._by_dep: dict[str, set[str]] = {}

    def register(self, dep_id: str, read_set: ReadSet) -> None:
        self._guard.check()
        self.unregister(dep_id)
        keys = {entry.key for entry in read_set.entries}
        self._by_dep[dep_id] = keys
        for key in keys:
            self._by_key.setdefault(key, set()).add(dep_id)

    def unregister(self, dep_id: str) -> None:
        self._guard.check()
        keys = self._by_dep.pop(dep_id, None)
        if not keys:
            return
        for key in keys:
            bucket = self._by_key.get(key)
            if bucket is not None:
                bucket.discard(dep_id)
                if not bucket:
                    del self._by_key[key]

    def dependents(self, changed_keys: set[str]) -> set[str]:
        result: set[str] = set()
        for key in changed_keys:
            result |= self._by_key.get(key, set())
        return result
