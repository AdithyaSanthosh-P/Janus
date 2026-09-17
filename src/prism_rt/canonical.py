"""Canonical JSON encoding, digesting, and value normalization.

Every fact digest, call fingerprint, and read-set entry in the kernel is
computed from the canonical form produced here, so two semantically equal
values (different key order, incidental whitespace) must always encode to
byte-identical JSON.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# Sentinel marking "this fact key does not exist" inside a ReadSetEntry.
# A real value can never equal this string because canonical values that
# happen to be the literal text are never stored un-escaped at this key —
# ReadSetEntry.digest uses ABSENT only when FactStore.get() returned None.
ABSENT = "__ABSENT__"

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return _WHITESPACE_RE.sub(" ", value.strip())
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # Canonicalize -0.0 and integral floats so 3.0 == 3.
        if value.is_integer():
            return int(value)
        return value
    return value


def normalize_value(value: Any, schema: dict | None = None) -> Any:
    """Recursively normalize a value into its canonical JSON-compatible form.

    Strings are trimmed and internally whitespace-collapsed. Integral floats
    collapse to int. Dicts and lists are normalized element-wise; dict key
    order is not significant here (to_canonical_json sorts keys separately).
    `schema` is accepted for forward compatibility with JSON-Schema-typed
    casting (e.g. string-typed numbers from a wire payload) but V0 does not
    yet require type coercion beyond the structural normalization above.
    """
    if isinstance(value, dict):
        return {str(k): normalize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_value(v) for v in value]
    if value is None:
        return None
    return _normalize_scalar(value)


def to_canonical_json(value: Any) -> str:
    """Serialize a (already or not-yet normalized) value to canonical JSON text.

    Keys are sorted, separators are compact, and the value is normalized
    first so equal values always produce identical text.
    """
    normalized = normalize_value(value)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_digest(value: Any) -> str:
    """SHA-256 hex digest of the value's canonical JSON encoding."""
    canonical = to_canonical_json(value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
