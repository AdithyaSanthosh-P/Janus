"""Phase 3 (`docs/post_v4_implementation_plan.md`) media-passthrough tests.

Live provider behavior (`GeminiProvider` actually sending bytes) was
verified manually against the real API, not here — this project's tests
never call a real model (§8.3). What *is* tested here, deterministically:
that `media` flows correctly from a resolved blob all the way through
`workers/runner.py`'s dispatch into whatever `Provider.complete_json`
receives — the plumbing, independent of any live model's actual behavior.
"""

from __future__ import annotations

from prism_rt.model.types import JobKind
from prism_rt.workers.gateway import MediaPart, ModelGateway
from prism_rt.workers.runner import ScriptedRunner


class _RecordingProvider:
    """Captures the `media` argument it was called with — a `ScriptedProvider`
    can't do this itself since it deliberately ignores media."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[MediaPart] | None]] = []

    def complete_json(self, kind: str, prompt: str, schema: dict, *, media=None) -> dict:
        self.calls.append((kind, media))
        if kind == "vision":
            return {"claims": [{"name": "device_model", "value": "seen", "confidence": "high"}]}
        if kind == "asr":
            return {"segments": [{"text": "hello", "offset_us": 0, "end_us": 100}], "end_of_utterance": True}
        raise LookupError(kind)


def test_blob_resolver_delivers_media_to_vision_job():
    provider = _RecordingProvider()
    gateway = ModelGateway(provider)
    part = MediaPart(mime_type="image/png", data=b"\x89PNG-fake-bytes")

    def blob_resolver(frame_id: str) -> MediaPart | None:
        return part if frame_id == "device-photo" else None

    runner = ScriptedRunner(gateway, latency_us_by_kind={JobKind.VISION: 0}, blob_resolver=blob_resolver)
    runner.submit("j-1", JobKind.VISION, {"obs_id": "o-0001", "frame_id": "device-photo", "mode": "at_utterance", "targets": []}, dispatched_us=0)
    results = runner.poll_results(0)

    assert len(results) == 1 and results[0].status == "ok"
    assert len(provider.calls) == 1
    kind, media = provider.calls[0]
    assert kind == "vision"
    assert media == [part]


def test_blob_resolver_delivers_media_to_asr_job():
    provider = _RecordingProvider()
    gateway = ModelGateway(provider)
    part = MediaPart(mime_type="audio/wav", data=b"RIFF-fake-bytes")

    def blob_resolver(frame_id: str) -> MediaPart | None:
        return part if frame_id == "voice-note" else None

    runner = ScriptedRunner(gateway, latency_us_by_kind={JobKind.ASR: 0}, blob_resolver=blob_resolver)
    runner.submit("j-1", JobKind.ASR, {"obs_id": "o-0001", "frame_id": "voice-note"}, dispatched_us=0)
    results = runner.poll_results(0)

    assert len(results) == 1 and results[0].status == "ok"
    kind, media = provider.calls[0]
    assert kind == "asr" and media == [part]


def test_no_blob_resolver_means_no_media_regardless_of_frame_id():
    """Default behavior (no live resolver configured, every ScriptedProvider
    test): media is always None — existing V3/Phase-2 tests are unaffected."""
    provider = _RecordingProvider()
    gateway = ModelGateway(provider)
    runner = ScriptedRunner(gateway, latency_us_by_kind={JobKind.VISION: 0})  # no blob_resolver
    runner.submit("j-1", JobKind.VISION, {"obs_id": "o-0001", "frame_id": "device-photo", "mode": "at_utterance", "targets": []}, dispatched_us=0)
    runner.poll_results(0)
    kind, media = provider.calls[0]
    assert media is None


def test_unresolvable_frame_id_falls_back_to_no_media():
    provider = _RecordingProvider()
    gateway = ModelGateway(provider)
    runner = ScriptedRunner(gateway, latency_us_by_kind={JobKind.VISION: 0}, blob_resolver=lambda frame_id: None)
    runner.submit("j-1", JobKind.VISION, {"obs_id": "o-0001", "frame_id": "unknown-ref", "mode": "at_utterance", "targets": []}, dispatched_us=0)
    runner.poll_results(0)
    kind, media = provider.calls[0]
    assert media is None
