"""Day 2 WP5 stage A (docs/fdb_v3_day2_plan.md): segments FDB-v3
recordings with faster-whisper + VAD into utterance-level chunks split
at real mid-request pauses, for the offline audio-replay harness
(run_audio_replay.py) to feed through VoiceBridge at their real timing.

Runs inside the `janus-invest-stt` image (faster-whisper + the cached
`large-v3-turbo` weights already live there, per last session's
investigation) -- NOT in the local venv, which has no faster-whisper.
Stage B (run_audio_replay.py) runs locally against live Gemini and only
reads this script's JSON output.

`vad_filter=True` is mandatory (Day 1 finding: without it, faster-
whisper hallucinates whole sentences into FDB's ~30s silent tail after
each recording's real speech). Segments are split at gaps >= 0.55s
between consecutive words (Silero's own default `min_silence_duration`,
docs/fdb_v3_implementation_plan.md §4.4 -- the same threshold LiveKit's
VAD will use live), so this reproduces the same segmentation boundary
the eventual live agent will see, not an arbitrary one.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SEGMENT_GAP_S = 0.55


def segment_recording(model, wav_path: str) -> list[dict]:
    segments, _info = model.transcribe(
        wav_path,
        vad_filter=True,
        word_timestamps=True,
        language="en",
    )
    words: list[tuple[str, float, float]] = []
    for seg in segments:
        if seg.words:
            for w in seg.words:
                words.append((w.word.strip(), w.start, w.end))

    out: list[dict] = []
    current_words: list[str] = []
    current_start: float | None = None
    current_end: float | None = None
    for word, start, end in words:
        if current_start is None:
            current_start = start
        elif start - current_end > SEGMENT_GAP_S:
            out.append({"text": " ".join(current_words).strip(), "start": current_start, "end": current_end})
            current_words = []
            current_start = start
        current_words.append(word)
        current_end = end
    if current_words:
        out.append({"text": " ".join(current_words).strip(), "start": current_start, "end": current_end})
    return [s for s in out if s["text"]]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-root", required=True, help="FDB_V3_DATA_ROOT -- one subfolder per recording, each with input.wav")
    p.add_argument("--out-dir", required=True, help="where to write <folder>.segments.json per recording")
    p.add_argument("--only", default=None, help="substring filter on the folder name")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--model", default="mobiuslabsgmbh/faster-whisper-large-v3-turbo")
    args = p.parse_args()

    from faster_whisper import WhisperModel

    print(f"Loading {args.model} ...")
    model = WhisperModel(args.model, device="cuda", compute_type="float16")

    data_root = Path(args.data_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    folders = sorted(p_ for p_ in data_root.iterdir() if p_.is_dir() and (p_ / "input.wav").is_file())
    if args.only:
        folders = [f for f in folders if args.only in f.name]
    if args.limit:
        folders = folders[: args.limit]

    for i, folder in enumerate(folders):
        out_path = out_dir / f"{folder.name}.segments.json"
        if out_path.exists():
            print(f"[{i + 1}/{len(folders)}] {folder.name} ... skip (already segmented)")
            continue
        segs = segment_recording(model, str(folder / "input.wav"))
        out_path.write_text(json.dumps({"folder": folder.name, "segments": segs}, indent=2))
        total_text = " ".join(s["text"] for s in segs)
        print(f"[{i + 1}/{len(folders)}] {folder.name} ... {len(segs)} segments: {total_text[:80]!r}")


if __name__ == "__main__":
    main()
