"""Q10 (win_plan §6.2): split a transcript at pause markers.

A cheap proxy for the voice path's many-chunk, mid-utterance-pause
behavior -- FDB-v3's own transcripts already mark pauses with "...", the
unicode ellipsis, or an em/en dash; a comma followed by more words is
also a natural break. `scripts/fdb_v3/run_text_replay.py --segmented`
sends each piece as its own text_chunk event, spaced apart like real ASR
streaming, to exercise TRIAGE hold/speculative interpretation/chunk-
anchor cancellation the same way a real recording would -- without
needing the full LiveKit/VoiceBridge stack that harness deliberately
doesn't pull in (see its own module docstring).

Lives under `adapters/` (not the `scripts/` tree, which the core test
Docker image doesn't copy -- see `Dockerfile`) so this pure function is
importable and testable from the main suite regardless.
"""

from __future__ import annotations

import re

_PAUSE_SPLIT = re.compile(r"\.\.\.|…|—|,\s")


def segment_transcript(transcript: str) -> list[str]:
    pieces = [p.strip() for p in _PAUSE_SPLIT.split(transcript)]
    return [p for p in pieces if p]
