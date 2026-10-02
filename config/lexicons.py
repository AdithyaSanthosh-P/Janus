"""Cue-word lexicons for QuickDetector (`docs/prompt 2.txt` §9.2).

English by assumption — configuration, not code, per the architecture doc.
Matched against a lower-cased chunk; a cue "hits" if it appears as a
substring token boundary is not enforced here (kept deliberately simple for
V1 — QuickDetector only drives cue signals, not slot extraction; see
kernel/detector.py's module docstring for why).
"""

from __future__ import annotations

CORRECTION_CUES: tuple[str, ...] = (
    "actually",
    "no,",
    "i mean",
    "instead",
    "make it",
    "change it to",
    "not that",
    "sorry,",
)

HOLD_CUES: tuple[str, ...] = (
    "wait",
    "hold on",
    "stop",
    "don't",
    "do not",
    "hang on",
)

ABORT_CUES: tuple[str, ...] = (
    "never mind",
    "forget it",
    "cancel everything",
    "cancel that",
    "nevermind",
)

BACKCHANNEL_TOKENS: tuple[str, ...] = (
    "ok",
    "okay",
    "mm-hmm",
    "mhm",
    "uh-huh",
    "uh huh",
    "yeah",
    "right",
    "go on",
    "sure",
    "yep",
    "got it",
)

# C1 inert-tail promotion (`docs/prompt 3.txt` C1): words that carry no
# value and no change cue, so a speculative interpretation of everything
# *before* them is still the interpretation of the whole turn. Deliberately
# tight: politeness and fillers only. Confirmation-like words ("ok",
# "yeah", "fine") are excluded -- they are content when answering a
# question -- and negations are never inert.
INERT_TOKENS: tuple[str, ...] = (
    "please",
    "thanks",
    "thank",
    "you",
    "um",
    "uh",
    "uhm",
    "erm",
    "er",
    "hmm",
)
NEGATION_TOKENS: tuple[str, ...] = ("no", "not", "don't", "dont", "never", "nope")

# Day 2 (docs/fdb_v3_day2_plan.md WP1, docs/fdb_v3_implementation_plan.md
# §5.3): a turn whose last word is one of these reads as unfinished
# ("...add two apples and" / "...to the"), so CommitGate's G10 waits
# longer than usual before treating it as settled. Fixed, generic-
# English lists written before any scoring run -- never adjusted to make
# a particular recording pass (the hard rule this project holds itself
# to for any lexicon).
TRAILING_CONNECTIVES: tuple[str, ...] = (
    "and", "or", "but", "so", "then", "to", "for", "with",
    "the", "a", "an", "of", "my", "your", "his", "her", "its", "our", "their",
    "is", "are", "was", "were", "at", "in", "on", "by", "from",
    # Words that introduce an amount still to come ("keep it under ...") --
    # found in the 1 Oct audit's voice runs.
    "under", "over", "about", "around", "below", "above", "than",
    "approximately", "roughly", "nearly", "almost", "least", "most",
)

VISUAL_CUES: tuple[str, ...] = (
    "this",
    "that one",
    "here",
    "look",
    "see",
    "screen",
    "light",
    "shows",
)
