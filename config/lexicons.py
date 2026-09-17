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
