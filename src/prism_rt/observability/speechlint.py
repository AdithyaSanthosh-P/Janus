"""SpeechLint (Q8, win_plan §6.2, `docs-personal/private-docs/
competitor_comparison_2026-09-27.md`'s A5): a deterministic post-lint over
already-constructed SPEAK/CLARIFY/FINAL text.

Two independent checks, both real regressions this project has already
hit in practice:

1. A raw tool/parameter identifier leaking into speech ("what should
   indicator_state be?" -- M-09, `currentStatus.md`'s Phase 7 section,
   never fixed). Humanized in place (underscores -> spaces), not
   stripped, so the meaning survives.
2. An ACK claiming an action is already done, booked, or confirmed before
   anything has actually run -- the exact failure mode a teammate's own
   comparison found live ("Step 1 was successful... the effect is
   confirmed"). Rejected outright (`LintResult.text` becomes `None`) for
   `kind == "ack"` specifically -- a FINAL is allowed to make exactly
   this kind of claim, once it's actually true.

Reused by two call sites for the same reason C6/T-04 established: one
function, not two copies. `kernel/emission.py.EmissionGate` runs it live,
right before an action is written (gated on `Config.speechlint_enabled`);
`scripts/fdb_v3/run_text_replay.py`'s own report (Q11) re-runs it after
the fact over every transcript in a whole run, regardless of whether the
live gate was on, to surface how often each violation class actually
occurs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SNAKE_CASE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

# Internal tracking-system nouns, not ordinary English words -- kept
# narrow and phrase-anchored on purpose so a legitimate sentence ("your
# travel plan", "the effect wore off") is never touched.
_INTERNAL_JARGON_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\bstep\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bthe effect\b", re.IGNORECASE),
    re.compile(r"\beffect is\b", re.IGNORECASE),
)

# Phrases that assert an action already completed -- legitimate in a
# FINAL grounded in a real result, never legitimate in an ACK (spoken
# before any call has even been dispatched).
_COMPLETION_CLAIM_PHRASES: tuple[str, ...] = (
    "has been booked",
    "successfully confirmed",
    "is confirmed",
    "has been completed",
    "has been confirmed",
    "booked successfully",
    "was successful",
    "all set",
    "is done",
    "is all done",
)


@dataclass(frozen=True)
class LintResult:
    text: str | None  # None means "reject this text entirely" (ack completion-claim)
    violations: tuple[str, ...]


def lint(text: str, kind: str) -> LintResult:
    """`kind`: "ack" | "clarify" | "inform" | "final" (matches
    `SpeakBody.kind`/`ActionType`). Pure function, safe to call from
    anywhere -- no store, no side effects."""
    violations: list[str] = []
    cleaned = text

    def _humanize(m: re.Match) -> str:
        violations.append(f"jargon:{m.group(0)}")
        return m.group(0).replace("_", " ")

    cleaned = _SNAKE_CASE.sub(_humanize, cleaned)

    for pattern in _INTERNAL_JARGON_PATTERNS:
        if pattern.search(cleaned):
            violations.append(f"internal_jargon:{pattern.pattern}")

    if kind == "ack":
        lowered = cleaned.lower()
        for phrase in _COMPLETION_CLAIM_PHRASES:
            if phrase in lowered:
                violations.append(f"premature_completion_claim:{phrase}")
                return LintResult(text=None, violations=tuple(violations))

    return LintResult(text=cleaned, violations=tuple(violations))
