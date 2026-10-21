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
CLARIFY_REPEAT_TEMPLATE = "Sorry, I still need the {target} before I can go on."
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
# Asked before a requires_confirmation write (CommitGate G12); {action} is the
# tool name in words, {args} the argument values.
CONFIRM_SECURITY = "That's a security action. Shall I go ahead and {action}{args}?"
UNCLEAR_NO_GOAL = "Sorry, I didn't quite catch what you'd like me to do — could you say that again?"
# Said once when a turn's interpretation failed every retry (the model could
# not be reached, or kept answering with something unusable): that turn was
# not acted on, and the user is told so instead of silence. Cause-neutral, and
# says nothing about earlier turns, which may well have done things.
INTERPRET_FAILED = "Sorry, I couldn't process that just now, so I didn't act on it. Please try again in a moment."
# Config.slow_interpret_notice_ms: the user's turn is taking long to understand.
SLOW_INTERPRET = "Still working on it."
# kernel/evidence.py: the device reached the requested state by itself while
# Janus waited for the yes; {device} as the user named it, {value} its state.
EVIDENCE_WITHDRAWN = "Never mind: {device} is already {value}. It changed while I was waiting, so I didn't send anything."
# Something the question rested on changed; asked again. {what} lists the changes.
EVIDENCE_REASK = "Heads up: {what} since I asked. {question}"
EVIDENCE_REASK_QUESTION = "Shall I still {action}{args}?"
EVIDENCE_REASK_SECURITY = "It's a security action, so I need your yes again: shall I still {action}{args}?"
# Asked again EVIDENCE_MAX_REASKS times and it is still changing: no further
# re-asks; the write's own pre-read before sending still applies.
EVIDENCE_MAX_REASKS = 2
