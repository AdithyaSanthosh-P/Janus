"""QuickDetector: deterministic, cue and HIGH-confidence value extraction
per chunk.

Cue extraction (`.detect`) has been here since V1: the triage fallback
(`docs/prompt 2.txt` §6.3) and backchannel/hold/abort recognition for
FastResponder pacing.

Value extraction (`.detect_values`, Phase 4 of
`docs/post_v4_implementation_plan.md`) implements the HIGH-confidence part
of §9.2's specification — enum matches and session-entity matches only.
It feeds `kernel/turns.py`'s chunk-anchored cancellation: a HIGH value that
contradicts a slot an in-flight call depends on cancels that call in the
same step as the chunk (§8.2), instead of waiting for end-of-turn
interpretation. This was deferred past V1 for the reason this docstring
used to state: building it before there was a read set to invalidate
against would have been dead code. `PlanExecutor._bind`'s existing refusal
to bind a HYPOTHESIS-status fact as a call input (G4) means no "promotion"
step (§11.4 — re-registering hypothesis-dependent work against a later
committed fact) is needed here: nothing ever reads a `hyp.<turn>.<name>`
fact as a call input in this implementation, only as a cancellation
trigger. §9.2's date/time and bare-number extractors are a deliberate
scope trim — see `detect_values`'s own docstring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from config.lexicons import (
    ABORT_CUES,
    BACKCHANNEL_TOKENS,
    CORRECTION_CUES,
    HOLD_CUES,
    INERT_TOKENS,
    NEGATION_TOKENS,
    VISUAL_CUES,
)

_WORD_RE = re.compile(r"[a-z']+")


def is_inert_tail(text: str) -> bool:
    """C1 (`docs/prompt 3.txt`): true iff every word in `text` is in the
    inert lexicon (politeness, fillers) and none is a negation. An empty
    tail is not "inert" -- that case is exact-digest promotion, handled
    separately. Cue/value checks are the caller's job (they need catalog
    and session context); this is only the lexical test."""
    words = _WORD_RE.findall(text.lower())
    if not words:
        return False
    return all(w in INERT_TOKENS and w not in NEGATION_TOKENS for w in words)


@dataclass(frozen=True)
class ChunkSignals:
    is_correction: bool = False
    is_hold: bool = False
    is_abort: bool = False
    is_backchannel: bool = False
    visual_reference: bool = False


@dataclass(frozen=True)
class HypothesisValue:
    """A HIGH-confidence (slot_name, value) pair extracted from one chunk.
    Only HIGH values are ever produced (§9.2: "it is allowed to miss; it is
    not allowed to guess") — there is no MEDIUM/LOW member to discard."""

    slot_name: str
    value: str


def _contains_any(text_lower: str, cues: tuple[str, ...]) -> bool:
    return any(cue in text_lower for cue in cues)


def _contains_whole(text_lower: str, needle_lower: str) -> bool:
    """Word/phrase-boundary substring match — stricter than `_contains_any`
    (used for cues, where a loose match is acceptable) because a value
    match drives real cancellation, not just pacing."""
    if not needle_lower:
        return False
    return re.search(rf"(?<!\w){re.escape(needle_lower)}(?!\w)", text_lower) is not None


class QuickDetector:
    def detect(self, chunk_text: str, turn_text: str = "") -> ChunkSignals:
        text_lower = chunk_text.strip().lower()
        stripped = text_lower.strip(" .!?")

        is_backchannel = stripped in BACKCHANNEL_TOKENS or (
            stripped != "" and all(word in BACKCHANNEL_TOKENS for word in stripped.split())
        )

        return ChunkSignals(
            is_correction=_contains_any(text_lower, CORRECTION_CUES),
            is_hold=_contains_any(text_lower, HOLD_CUES),
            is_abort=_contains_any(text_lower, ABORT_CUES),
            is_backchannel=is_backchannel,
            visual_reference=_contains_any(text_lower, VISUAL_CUES),
        )

    def detect_values(
        self,
        chunk_text: str,
        *,
        tool_params: tuple[tuple[str, dict], ...] = (),
        seen_values: dict[str, set[str]] | None = None,
    ) -> tuple[HypothesisValue, ...]:
        """HIGH-confidence slot-value extraction (`docs/prompt 2.txt` §9.2).

        Two extractors, deliberately conservative:
        - Enum values: exact (case-insensitive), word/phrase-boundary match
          against an enum value of some known tool parameter — `tool_params`
          is `[(param_name, json_schema), ...]` across the current catalog.
        - Session entities: exact word/phrase-boundary match against a
          value already seen this session (`seen_values`: `{slot_name:
          {values...}}`) for exactly one slot name — ambiguous across more
          than one slot name is discarded, per spec.

        Date/time and bare-number extraction (§9.2's other two extractors)
        are a deliberate scope trim: both need a reference-date policy and
        unit/parameter disambiguation this project's tool schemas never
        actually exercise, and nothing currently depends on them.
        """
        text_lower = chunk_text.strip().lower()
        if not text_lower:
            return ()
        found: dict[str, HypothesisValue] = {}

        for param_name, schema in tool_params:
            enum_values = schema.get("enum") if isinstance(schema, dict) else None
            if not enum_values:
                continue
            for candidate in enum_values:
                if isinstance(candidate, str) and _contains_whole(text_lower, candidate.lower()):
                    found[param_name] = HypothesisValue(slot_name=param_name, value=candidate)

        if seen_values:
            value_to_names: dict[str, set[str]] = {}
            for name, values in seen_values.items():
                for v in values:
                    value_to_names.setdefault(v.lower(), set()).add(name)
            for value_lower, names in value_to_names.items():
                if len(names) != 1 or not _contains_whole(text_lower, value_lower):
                    continue
                (name,) = names
                original = next(v for v in seen_values[name] if v.lower() == value_lower)
                found[name] = HypothesisValue(slot_name=name, value=original)

        return tuple(found.values())
