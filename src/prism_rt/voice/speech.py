"""Local speech models for the LiveKit host, wrapped as livekit-agents 1.8.3
plugins: faster-whisper large-v3-turbo (STT) and Kokoro-82M (TTS).

Both are pinned by Hugging Face repo *and* revision, so a re-run after the
deadline loads exactly what was tested (the Whisper repo has already been
renamed once upstream; the old name only survives as a redirect). Loading
runs a tiny inference so a broken CUDA/cuDNN setup fails at worker start,
not in the middle of the first scenario.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading

import numpy as np
from livekit import rtc
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions, stt, tts, utils
from livekit.agents.types import NOT_GIVEN, NotGivenOr

WHISPER_REPO = "dropbox-dash/faster-whisper-large-v3-turbo"
WHISPER_REVISION = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
KOKORO_REPO = "hexgrad/Kokoro-82M"
KOKORO_REVISION = "f3ff3571791e39611d31c381e3a41a3af07b4987"
KOKORO_VOICE = "af_heart"
KOKORO_SAMPLE_RATE = 24_000
WHISPER_SAMPLE_RATE = 16_000

_DASHES = re.compile("[–—―]")
_SPACES = re.compile(r"\s+")

logger = logging.getLogger("janus.voice.speech")

# Whisper and Kokoro share one 8 GB GPU with FDB's own scoring ASR. Running
# an STT encode and a TTS synthesis at the same moment stacks both models'
# activation memory; serializing them keeps the peak to one at a time. Both
# are called from worker threads (asyncio.to_thread), hence a thread lock.
_GPU_LOCK = threading.Lock()


def _locked_transcribe(model, audio, **kwargs) -> str:
    with _GPU_LOCK:
        return _transcribe(model, audio, **kwargs)


def clean_transcript(text: str) -> str:
    # An em dash between two words ("Paris—no, Berlin") must stay a word
    # boundary; everything else passes through untouched.
    return _SPACES.sub(" ", _DASHES.sub(" - ", text)).strip()


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 - no GPU runtime at all means CPU
        return False


def load_whisper(device: str | None = None):
    from faster_whisper import WhisperModel
    from huggingface_hub import snapshot_download

    path = snapshot_download(WHISPER_REPO, revision=WHISPER_REVISION)
    device = device or ("cuda" if _cuda_available() else "cpu")
    try:
        model = WhisperModel(path, device=device, compute_type="float16" if device == "cuda" else "int8")
        _transcribe(model, np.zeros(WHISPER_SAMPLE_RATE, dtype=np.float32))
    except Exception:
        if device != "cuda":
            raise
        model = WhisperModel(path, device="cpu", compute_type="int8")
        _transcribe(model, np.zeros(WHISPER_SAMPLE_RATE, dtype=np.float32))
    return model


def _transcribe(model, audio: np.ndarray, *, language: str = "en", beam_size: int = 5) -> str:
    segments, _info = model.transcribe(
        audio,
        language=language,
        beam_size=beam_size,
        temperature=0.0,
        condition_on_previous_text=False,
        # `FasterWhisperSTT._recognize_impl` is only ever called by
        # `stt.StreamAdapter` on a buffer the *external* Silero VAD
        # (`voice/agent.py`'s `userdata["vad"]`) already decided was a
        # real speech segment -- running faster-whisper's own internal
        # `vad_filter` again on top of that is a second, redundant VAD
        # pass, not a safety net. Found live (2026-09-27, LiveKit
        # re-validation): 4/26 recordings never produced a transcript at
        # all, every one unusually dense in filler words/hesitation
        # ("Um so uh...", "Well, um uh, you know..."); the internal VAD is
        # the most plausible culprit, since it re-segments an already-
        # short, already-VAD-selected clip and can slice a hesitant
        # utterance down to nothing. False by default now.
        vad_filter=False,
        without_timestamps=True,
    )
    # Whisper invents text for noise; a segment it itself rates as probably
    # not speech is dropped.
    return clean_transcript(" ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.6))


def _to_mono_16k(buffer) -> np.ndarray:
    frame = utils.combine_frames(buffer)
    pcm = np.frombuffer(frame.data, dtype=np.int16)
    if frame.num_channels > 1:
        pcm = pcm.reshape(-1, frame.num_channels).mean(axis=1).astype(np.int16)
    if frame.sample_rate != WHISPER_SAMPLE_RATE:
        mono = rtc.AudioFrame(
            data=pcm.tobytes(), sample_rate=frame.sample_rate, num_channels=1, samples_per_channel=len(pcm)
        )
        resampler = rtc.AudioResampler(frame.sample_rate, WHISPER_SAMPLE_RATE, num_channels=1)
        out = resampler.push(mono) + resampler.flush()
        pcm = np.frombuffer(b"".join(bytes(f.data) for f in out), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


class FasterWhisperSTT(stt.STT):
    """Non-streaming: LiveKit's `stt.StreamAdapter` hands each VAD-delimited
    speech segment to `_recognize_impl` and emits it as one final transcript,
    which is exactly the append-only chunk Janus's turn model expects."""

    def __init__(self, model, *, language: str = "en", beam_size: int = 5) -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        self._model = model
        self._language = language
        self._beam_size = beam_size

    async def _recognize_impl(
        self,
        buffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        # Never raise out of here. `stt.StreamAdapter` swallows an exception
        # from this method and emits no transcript event at all, so the
        # kernel never learns the user spoke -- found live (2026-09-27): a
        # CUDA OOM inside Whisper's encoder produced exactly that silence.
        # On failure: retry once, then return an *empty* final transcript,
        # which the kernel turns into an honest "didn't catch that" instead
        # (never_silent_unclear_enabled).
        audio = _to_mono_16k(buffer)
        logger.info(
            "janus.voice.speech: recognizing buffer -- %d samples (%.2fs at %dHz)",
            len(audio), len(audio) / WHISPER_SAMPLE_RATE, WHISPER_SAMPLE_RATE,
        )
        text = ""
        for attempt in (1, 2):
            try:
                text = await asyncio.to_thread(
                    _locked_transcribe, self._model, audio, language=self._language, beam_size=self._beam_size
                )
                break
            except Exception:
                logger.exception("janus.voice.speech: transcription attempt %d failed", attempt)
                if attempt == 1:
                    await asyncio.sleep(0.2)
        logger.info("janus.voice.speech: recognized text=%r", text)
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language=self._language, text=text)],
        )


class KokoroEngine:
    def __init__(self, pipeline, voice_path: str, speed: float = 1.0) -> None:
        self._pipeline = pipeline
        self._voice = voice_path
        self._speed = speed

    def synthesize_pcm16(self, text: str) -> bytes:
        chunks = []
        for result in self._pipeline(text, voice=self._voice, speed=self._speed):
            audio = getattr(result, "audio", None)
            if audio is None:
                continue
            samples = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
            chunks.append(np.clip(samples * 32767.0, -32768, 32767).astype(np.int16))
        return np.concatenate(chunks).tobytes() if chunks else b""


def load_kokoro(device: str | None = None) -> KokoroEngine:
    import torch
    from huggingface_hub import snapshot_download
    from kokoro import KModel, KPipeline

    path = snapshot_download(
        KOKORO_REPO,
        revision=KOKORO_REVISION,
        allow_patterns=["config.json", "kokoro-v1_0.pth", f"voices/{KOKORO_VOICE}.pt"],
    )
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    model = KModel(
        repo_id=KOKORO_REPO,
        config=os.path.join(path, "config.json"),
        model=os.path.join(path, "kokoro-v1_0.pth"),
    ).to(device).eval()
    pipeline = KPipeline(lang_code="a", repo_id=KOKORO_REPO, model=model)
    engine = KokoroEngine(pipeline, os.path.join(path, "voices", f"{KOKORO_VOICE}.pt"))
    engine.synthesize_pcm16("Ready.")
    return engine


def _locked_synthesize(engine: KokoroEngine, text: str) -> bytes:
    with _GPU_LOCK:
        return engine.synthesize_pcm16(text)


class KokoroTTS(tts.TTS):
    def __init__(self, engine: KokoroEngine) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=KOKORO_SAMPLE_RATE,
            num_channels=1,
        )
        self._engine = engine

    def synthesize(self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> "_KokoroStream":
        return _KokoroStream(tts=self, input_text=text, conn_options=conn_options)


class _KokoroStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=KOKORO_SAMPLE_RATE,
            num_channels=1,
            mime_type="audio/pcm",
        )
        pcm = await asyncio.to_thread(_locked_synthesize, self._tts._engine, self.input_text)
        if pcm:
            output_emitter.push(pcm)
        output_emitter.flush()
