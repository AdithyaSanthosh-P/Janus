"""QuickDetector: deterministic, cue-only signal extraction per chunk.

`docs/prompt 2.txt` §9.2 also specifies HIGH-confidence slot-value
extraction (enums, dates, numbers) that feeds chunk-anchored speculative
cancellation — writing `hyp.<turn>.<name>` facts that invalidate in-flight
calls *before* end-of-turn. That's explicitly a V2 concern: V1's known
limitations (`docs/sonnet_implementation_plan.md` §4 VERSION 1) list "full
replan on every correction (no rebinder)" and no speculative execution —
V1 only cancels once the Interpreter's committed facts change, at EOT.
Building value-extraction here now would be dead code with no read set to
invalidate against. What V1 *does* need from QuickDetector: cue signals for
the triage fallback (`docs/prompt 2.txt` §6.3 — resolving triage from
detector output alone when interpretation fails) and for
backchannel/hold/abort recognition informing FastResponder pacing.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.lexicons import ABORT_CUES, BACKCHANNEL_TOKENS, CORRECTION_CUES, HOLD_CUES, VISUAL_CUES


@dataclass(frozen=True)
class ChunkSignals:
    is_correction: bool = False
    is_hold: bool = False
    is_abort: bool = False
    is_backchannel: bool = False
    visual_reference: bool = False


def _contains_any(text_lower: str, cues: tuple[str, ...]) -> bool:
    return any(cue in text_lower for cue in cues)


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
