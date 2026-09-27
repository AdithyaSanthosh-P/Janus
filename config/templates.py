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
FRESH_VIEW_REQUEST = "The last view I have is a few seconds old — could you show it to me again before I go ahead?"
INFORM_DUPLICATE_WRITE = "That's already been taken care of."
INFORM_UNKNOWN_WRITE_OUTCOME = "I haven't received confirmation for that yet."
CLARIFY_RETRY_WRITE = "Should I try again?"
FINAL_FALLBACK = "Here's what I found."
WATCHDOG_FALLBACK = "I wasn't able to finish in time — here's where things stood."
# Q6a (win_plan §6.2): spoken when interpretation classified the turn as
# BACKCHANNEL/SMALLTALK/UNCLEAR with no active goal to attach it to --
# almost certainly real content a live model failed to extract a goal
# from (confirmed via housing_11/housing_13's saved decision logs), not
# actual noise. Never claims anything was understood.
UNCLEAR_NO_GOAL = "Sorry, I didn't quite catch what you'd like me to do — could you say that again?"
