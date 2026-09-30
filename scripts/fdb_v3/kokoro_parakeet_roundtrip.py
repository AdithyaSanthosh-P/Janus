"""Kokoro TTS -> FDB's own Parakeet ASR round trip (docs/fdb_v3_
implementation_plan.md §4.3, Day-1 check): the judge only ever hears
what Kokoro *says* through the microphone FDB's own scorer listens
with (nvidia/parakeet-tdt-0.6b-v2), not the text Janus generated
directly -- so what matters is whether IDs, dates and prices survive
that whole round trip, not just that Kokoro pronounced them.

Two modes, since Kokoro and Parakeet need separate, heavy, mutually
incompatible-in-practice environments (this session's own `janus-
invest-tts` image for Kokoro; the FDB runtime container,
`docker/fdb_v3/Dockerfile`, for Parakeet via nemo_toolkit):

    --mode synthesize   run inside the Kokoro (TTS) container/env.
                         Writes one WAV per test sentence.
    --mode transcribe   run inside the Parakeet (nemo_toolkit) env.
                         Reads those WAVs, transcribes each, and
                         reports whether every expected fact survived.

Sentences below mirror real FINAL text Janus produced against real
FDB-v3 recordings this session (T3 text-replay harness), not invented
examples -- see currentStatus.md's FDB-v3 Day 1 section.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# (sentence, [facts that must survive transcription, case-insensitive substring match])
TEST_SENTENCES: list[tuple[str, list[str]]] = [
    (
        "Your flight FL123 to Chicago on December 12th for $450 is confirmed.",
        ["FL123", "Chicago", "450"],
    ),
    (
        "Successfully added 2 units of product K-2 to your cart. Your new cart total is $199.98.",
        ["K-2", "199.98"],
    ),
    (
        "Your order for 1 unit of product B-7 has been confirmed. The total cost added to your cart is $99.99.",
        ["B-7", "99.99"],
    ),
    (
        "I checked order ABC123 for you -- it is out for delivery.",
        ["ABC123"],
    ),
]


def run_synthesize(out_dir: Path) -> None:
    from kokoro import KPipeline
    import soundfile as sf

    out_dir.mkdir(parents=True, exist_ok=True)
    pipeline = KPipeline(lang_code="a")  # American English

    manifest = []
    for i, (text, facts) in enumerate(TEST_SENTENCES):
        wav_path = out_dir / f"sentence_{i:02d}.wav"
        chunks = []
        for result in pipeline(text, voice="af_heart"):
            chunks.append(result.audio)
        import numpy as np

        audio = np.concatenate([c.numpy() if hasattr(c, "numpy") else c for c in chunks])
        sf.write(str(wav_path), audio, 24000)
        manifest.append({"text": text, "facts": facts, "wav": wav_path.name})
        print(f"synthesized {wav_path.name}: {text!r}")

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote {len(manifest)} WAVs + manifest.json to {out_dir}")


def run_transcribe(out_dir: Path) -> None:
    import nemo.collections.asr as nemo_asr

    manifest = json.loads((out_dir / "manifest.json").read_text())
    print("Loading nvidia/parakeet-tdt-0.6b-v2 (first run downloads the model)...")
    asr_model = nemo_asr.models.ASRModel.from_pretrained(model_name="nvidia/parakeet-tdt-0.6b-v2")

    results = []
    all_ok = True
    for entry in manifest:
        wav_path = out_dir / entry["wav"]
        hyp = asr_model.transcribe([str(wav_path)])[0]
        transcript = hyp.text if hasattr(hyp, "text") else str(hyp)
        survived = {fact: (fact.lower() in transcript.lower() or _normalized_match(fact, transcript)) for fact in entry["facts"]}
        ok = all(survived.values())
        all_ok = all_ok and ok
        results.append({"original": entry["text"], "transcript": transcript, "facts_survived": survived, "ok": ok})
        status = "OK" if ok else "MISSING FACTS"
        print(f"[{status}] original: {entry['text']!r}")
        print(f"          transcript: {transcript!r}")
        print(f"          facts: {survived}")

    (out_dir / "roundtrip_results.json").write_text(json.dumps(results, indent=2))
    print(f"\n{'ALL FACTS SURVIVED' if all_ok else 'SOME FACTS DID NOT SURVIVE'} -- see {out_dir / 'roundtrip_results.json'}")


def _normalized_match(fact: str, transcript: str) -> bool:
    """Loose fallback: strip punctuation/spacing so 'K-2' matches 'K 2'
    or 'k2', '199.98' matches '199 98' or 'one ninety nine ninety
    eight' is NOT handled here (that needs real number-word parsing,
    out of scope for this quick check) -- only formatting noise."""
    norm_fact = re.sub(r"[\s\-.]", "", fact).lower()
    norm_transcript = re.sub(r"[\s\-.]", "", transcript).lower()
    return norm_fact in norm_transcript


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["synthesize", "transcribe"], required=True)
    p.add_argument("--out-dir", default="/shared/kokoro_parakeet_roundtrip")
    args = p.parse_args()
    out_dir = Path(args.out_dir)
    if args.mode == "synthesize":
        run_synthesize(out_dir)
    else:
        run_transcribe(out_dir)


if __name__ == "__main__":
    main()
