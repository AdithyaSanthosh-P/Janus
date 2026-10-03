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


_SPOKEN_ID_SPLIT = re.compile(r"[\s\-.,]+")


_ID_PARAM = re.compile(r"(?:^|_)(?:id|number|code|ref)$")
# Separators said as words when dictating a code ("D dash L dash five").
_SPOKEN_SEPARATORS = {"dash", "hyphen"}
_DIGIT_WORDS = {
    w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine".split())
} | {"oh": "0"}


def _join_spoken_identifier(value: str):
    """For an identifier parameter only: "F A S T nine nine" -> "FAST99",
    "P.O. 999" -> "PO999", "one, two, three, ABC" -> "123ABC",
    "DL. five five five" -> "DL555", "E77-2211" -> "E772211",
    "M... Dash Q-3-3-8" -> "MQ338". Found in the 30 Sep voice runs: Whisper
    writes a spoken code as letters, digit words and separators, including a
    spoken "dash" written as a word. Every piece must be a single character, a digit word,
    all upper case or contain a digit, so a phrase ("the one from last
    week") is never glued together. None when the value doesn't look like a spoken code."""
    tokens = [t for t in _SPOKEN_ID_SPLIT.split(value) if t and t.lower() not in _SPOKEN_SEPARATORS]
    if len(tokens) < 2:
        return None
    pieces = []
    for token in tokens:
        piece = _DIGIT_WORDS.get(token.lower(), token)
        code_like = len(piece) == 1 or piece.isupper() or any(c.isdigit() for c in piece)
        if not (piece.isalnum() and code_like):
            return None
        pieces.append(piece)
    return "".join(pieces)


def canonicalize_spoken_id(value, name: str | None = None):
    """Q1 (win_plan §6.2): a value spelled out one character at a time
    ("X-Y-Z-8-8") is really one identifier and gets joined back into
    "XYZ88" -- FDB-v3's own ASR frequently renders a spoken confirmation
    code or order ID this way. A multi-character token anywhere in the
    split (e.g. "FL-DEN-8AM") means this is a *structured* code with real
    separators, not a spelled-out one, and is left untouched. Only
    strings are ever touched; anything else (a number, a bool) passes
    through unchanged. Lives here (not in kernel/interpret_apply.py, which
    re-exports it) so kernel/action_plans.py can use it without an import
    cycle."""
    if not isinstance(value, str):
        return value
    if name is not None and _ID_PARAM.search(name.rsplit(".", 1)[-1].lower()):
        joined = _join_spoken_identifier(value)
        if joined is not None:
            return joined
    tokens = [t for t in _SPOKEN_ID_SPLIT.split(value) if t]
    if len(tokens) < 2 or not all(len(t) == 1 and t.isalnum() for t in tokens):
        return value
    return "".join(tokens)


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


# An amount as people say it: optional currency sign, thousands separators,
# optional currency word ("$1,500", "1500 dollars", "1500").
_AMOUNT_RE = re.compile(
    r"^\s*[$€£₹]?\s*(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*"
    r"(?:dollars?|usd|euros?|eur|pounds?|gbp|rupees?|inr|bucks)?\s*$",
    re.IGNORECASE,
)
_TRAILING_PUNCT = " .,!?;:"


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = _AMOUNT_RE.match(value)
        if match:
            return float(match.group(1).replace(",", ""))
    return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def equivalent_values(a: Any, b: Any) -> bool:
    """True when two slot values say the same thing in different surface
    forms: equal amounts ("$1,500" / "1500" / 1500), the same boolean
    (True / "true"), or the same text ignoring case, spacing and trailing
    punctuation. Used so a restatement is not mistaken for a correction
    (1 Oct audit: "$1500" restated as "1500" cancelled a call that had
    already gone out and failed the whole goal)."""
    if type(a) is type(b) and a == b:  # not `1 == True`
        return True
    na, nb = _as_number(a), _as_number(b)
    if na is not None and nb is not None:
        return na == nb
    ba, bb = _as_bool(a), _as_bool(b)
    if ba is not None and bb is not None:
        return ba == bb
    if isinstance(a, str) and isinstance(b, str):
        fold = lambda s: _WHITESPACE_RE.sub(" ", s.strip(_TRAILING_PUNCT)).casefold()  # noqa: E731
        return fold(a) == fold(b)
    return False
