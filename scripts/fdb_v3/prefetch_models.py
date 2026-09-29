"""Download every model weight the reproduction needs, at pinned revisions.

Run at image build time (docker/fdb_v3/Dockerfile) so nothing is fetched
inside the worker's 300 s prewarm window or mid-benchmark, and again -- as a
no-op -- by reproduce.sh, which makes a bare-metal run behave the same way.

    python scripts/fdb_v3/prefetch_models.py

Whisper and Kokoro use the same repo/revision constants the worker loads with
(prism_rt.voice.speech), so a prefetched snapshot is exactly what it finds.
Parakeet is FDB's own scoring ASR; NeMo's `from_pretrained` resolves through
the same Hugging Face cache, so `snapshot_download` populates it.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time

from huggingface_hub import snapshot_download

# Loaded by file path (speech.py imports nothing from prism_rt), so the image
# build can COPY just that file and keep the weights layer cached across
# ordinary code changes.
_SPEECH = os.path.join(
    os.environ.get("JANUS_SPEECH_PY", os.path.join(os.path.dirname(__file__), "..", "..", "src", "prism_rt", "voice", "speech.py"))
)
_spec = importlib.util.spec_from_file_location("janus_speech_pins", _SPEECH)
speech = importlib.util.module_from_spec(_spec)
sys.modules["janus_speech_pins"] = speech
_spec.loader.exec_module(speech)

PARAKEET_REPO = "nvidia/parakeet-tdt-0.6b-v2"


def _download(repo: str, **kwargs) -> None:
    """snapshot_download with retries: a multi-GB pull over a flaky link failed once
    on a single dropped CDN request (the build must not die on that). Partial
    files are resumed, so a retry costs only what was missing."""
    for attempt in range(1, 7):
        try:
            snapshot_download(repo, **kwargs)
            return
        except Exception as exc:  # noqa: BLE001 - any transport failure is retryable
            if attempt == 6:
                raise
            wait = 5 * attempt
            print(f"  download of {repo} failed ({type(exc).__name__}); retry {attempt}/5 in {wait}s", flush=True)
            time.sleep(wait)


def main() -> None:
    print(f"whisper  {speech.WHISPER_REPO}@{speech.WHISPER_REVISION[:8]}", flush=True)
    _download(speech.WHISPER_REPO, revision=speech.WHISPER_REVISION)
    print(f"kokoro   {speech.KOKORO_REPO}@{speech.KOKORO_REVISION[:8]}", flush=True)
    _download(
        speech.KOKORO_REPO,
        revision=speech.KOKORO_REVISION,
        allow_patterns=["config.json", "kokoro-v1_0.pth", f"voices/{speech.KOKORO_VOICE}.pt"],
    )
    print(f"parakeet {PARAKEET_REPO}", flush=True)
    _download(PARAKEET_REPO)
    print("all model weights cached", flush=True)


if __name__ == "__main__":
    main()
