"""Live multimodal demo: a real image and a real audio clip, both actually
sent as bytes to a real model — not by reference, the way `workers/vision.py`
and `workers/asr.py` work in every test in this project.

Phase 3 (`docs/post_v4_implementation_plan.md`): before this, `GeminiProvider`
(added earlier for `demo/run_v1_live_demo.py`) only ever carried text.
`Provider.complete_json` now takes an optional `media` argument, and
`workers/runner.py`'s `blob_resolver` is the one place in the whole
codebase that resolves a harness-given reference (`frame_id`/`clip_id`)
into actual bytes — the kernel and `store/` still never touch bytes, by
design; this demo's resolver is just a plain dict lookup living entirely
outside that boundary.

Two acts: a synthesized image (pure Python, no dependency — same
red-top/blue-bottom generator verified working against the live Gemini
API earlier this project), and a synthesized WAV tone (stdlib `wave`,
also already verified against the live API). Wording will vary since it's
a real model; the point is that a real answer comes back grounded in the
actual bytes, not just the reference id.

NOTE on free-tier quota: this script makes several real API calls in a
tight sequence (INTERPRET, PLAN, VISION/ASR, COMPOSE — per act). The
Gemini free tier's request quota is low enough that running this demo
back-to-back with other live testing can exhaust it (observed directly:
`HTTP 429 RESOURCE_EXHAUSTED`, "limit: 20"). The request/response
plumbing itself — real image/audio bytes actually reaching the API and a
real structured response coming back — was verified working with a
direct, single provider call (see the Phase 3 section of
`docs/post_v4_implementation_plan.md`); a 429 here reflects the quota,
not a defect in this code. If you hit one, wait a minute with no other
calls and try again; the debug lines below will show `[]`/empty results
if a call was silently dropped rather than crash the script — that's the
existing "a worker failure is an expected outcome, never a crash" design
(`workers/runner.py`), working as intended even when the "failure" is a
quota limit rather than a bug.

Run:
    cd /home/adi/Desktop/Hackathons/Prism
    source .venv/bin/activate
    echo 'GEMINI_API_KEY=your_key_here' > .env
    PYTHONPATH=src:. python demo/run_live_multimodal_demo.py
"""

from __future__ import annotations

import math
import os
import struct
import sys
import wave
import zlib
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip()


_load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import GeminiProvider, MediaPart

MANUAL_LOOKUP_TOOL = {
    "name": "manual_lookup",
    "mutability": "read_only",
    "parameters": {"type": "object", "properties": {"description": {"type": "string"}}, "required": ["description"]},
}

RULE = "-" * 78


def say(who: str, text: str) -> None:
    print(f"  {who:9} {text}")


def make_png(w: int = 60, h: int = 60) -> bytes:
    """Pure-Python, dependency-free red-top/blue-bottom PNG — the same
    synthetic image verified working against the live Gemini API earlier
    this project."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw += bytes((255, 0, 0)) if y < h // 2 else bytes((0, 0, 255))
    compressed = zlib.compress(bytes(raw), 9)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")


def make_wav(freq_hz: float = 440.0, duration_s: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Pure stdlib (`wave`) sine-tone WAV — the same synthetic clip
    verified working against the live Gemini API earlier this project."""
    n_samples = int(sample_rate * duration_s)
    samples = bytearray()
    for i in range(n_samples):
        val = int(3000 * math.sin(2 * math.pi * freq_hz * i / sample_rate))
        samples += struct.pack("<h", val)
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(bytes(samples))
    return buf.getvalue()


def main() -> None:
    png_bytes = make_png()
    wav_bytes = make_wav()

    blobs = {
        "device-photo": MediaPart(mime_type="image/png", data=png_bytes),
        "voice-note": MediaPart(mime_type="audio/wav", data=wav_bytes),
    }

    def blob_resolver(frame_id: str) -> MediaPart | None:
        return blobs.get(frame_id)

    provider = GeminiProvider()
    config = Config(vision_enabled=True, asr_enabled=True, audio_mode="audio_only", asr_closes_turn=True)
    h = SimHarness(
        config,
        seed=7,
        provider=provider,
        worker_latency_us={JobKind.INTERPRET: 15_000, JobKind.PLAN: 15_000, JobKind.COMPOSE: 15_000, JobKind.VISION: 15_000, JobKind.ASR: 15_000},
        tools={"manual_lookup": {"latency_ms": 50, "response": {"note": "no manual for a synthetic test image"}}},
        blob_resolver=blob_resolver,
    )

    print(RULE)
    print("Live multimodal demo — real image + real audio, sent as actual bytes")
    print(RULE)

    # === Act 1: vision, grounded in a real image =============================
    print()
    print("Act 1 — a real image, described by a real model")
    h.send(0, [{"type": "manifest", "payload": {"tools": [MANUAL_LOOKUP_TOOL]}}])
    say("CAMERA", "[a 60x60 PNG: solid red top half, solid blue bottom half]")
    h.send(50_000, [{"type": "video_frame", "payload": {"frame_id": "device-photo"}}])
    say("USER", '"what colors do you see in this image, top and bottom?"')
    h.send(100_000, [{"type": "text_chunk", "payload": {"text": "what colors do you see in this image, top and bottom?"}}])
    h.send(150_000, [{"type": "end_of_turn", "payload": {}}])

    t = h.clock.now_us()
    while t < 3_000_000:
        t += 20_000
        report = h.advance(t)
        for er in report.emit_report.emitted:
            action = er.action
            if action.action_type in (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL):
                say("AGENT", action.body.text)
            elif action.action_type == ActionType.TOOL_CALL:
                print(f"            [internal: calling {action.body.tool_name}({action.body.arguments})]")

    claims = [f for f in h.store.facts.snapshot_committed().items() if f[0].startswith("claim.")]
    print(f"  -> vision claims grounded in the real image: {claims}")
    print(f"  -> internal state — goal: {h.store.goals.all()}")
    print(f"  -> internal state — questions: {h.store.evidence.all_questions()}")
    if not h.store.goals.all():
        print("  -> (empty state above usually means a live call was dropped — see the quota note in this file's docstring)")

    # === Act 2: ASR, grounded in a real audio clip ============================
    print()
    print("Act 2 — a real audio clip, transcribed by a real model")
    say("USER", "[a 1-second 440Hz WAV tone — not speech, but real audio bytes]")
    h.send(t + 50_000, [{"type": "audio_clip", "payload": {"clip_id": "voice-note"}}])

    t2 = h.clock.now_us()
    t2_end = t2 + 2_000_000
    while t2 < t2_end:
        t2 += 20_000
        report = h.advance(t2)
        for er in report.emit_report.emitted:
            action = er.action
            if action.action_type in (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL):
                say("AGENT", action.body.text)

    obs = [o for o in h.store.evidence.observations() if o.modality == "audio"]
    print(f"  -> audio observation after live ASR: {obs}")
    if obs and not obs[0].asr_done:
        print("  -> (asr_done=False usually means the live ASR call was dropped — see the quota note in this file's docstring)")

    print(RULE)


if __name__ == "__main__":
    main()
