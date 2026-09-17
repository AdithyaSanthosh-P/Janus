"""Utterance templates by kind (`docs/prompt 2.txt` §9.3).

One template per kind is enough for V1's scenario suite; §9.3's
digest-based deterministic variation (to avoid verbatim repetition across
a long session while staying replay-identical) is a straightforward
addition once there is more than one variant per kind to rotate between —
not needed until there's a repetition problem to solve.
"""

from __future__ import annotations

ACK_DEFAULT = "Got it, working on it."
CLARIFY_TEMPLATE = "I need a bit more information — what should {target} be?"
INFORM_DUPLICATE_WRITE = "That's already been taken care of."
INFORM_UNKNOWN_WRITE_OUTCOME = "I haven't received confirmation for that yet."
FINAL_FALLBACK = "Here's what I found."
