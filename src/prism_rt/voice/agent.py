"""Janus LiveKit worker.

A LiveKit `AgentSession` that only handles audio -- Silero VAD, local
faster-whisper STT, local Kokoro TTS, and no LLM (with no LLM the session
transcribes but never replies on its own). Every utterance and every tool
call is decided by the Janus kernel, reached through `VoiceBridge`.

One job = one room = one fresh `Runtime`; nothing is shared across rooms
except the loaded speech models.

Two modes (JANUS_MODE): "fdb" (default) -- the FDB-v3 benchmark toolset and
profile -- and "demo" -- the device-care extension: the mock device-care tools,
`demo_config()`, and the room's camera track sampled into `video_frame` events
(voice/camera.py). Same kernel either way.

Environment:
  JANUS_MODE              "fdb" (default) or "demo"
  JANUS_FRAME_INTERVAL_S  demo mode: seconds between camera frames (default 1.0)
  FDB_V3_ROOT             FDB checkout's v3/ directory (tool manifest + mock APIs)
  JANUS_FDB_LATENCY       FDB mock latency profile (default "instant", as FDB's own agents)
  JANUS_TOOL_LOG          tool-call log FDB's runner reads (default /tmp/agent_tool_calls.log)
  JANUS_LLM_PROVIDER      gemini (default, declared) or openai (reads OPENAI_API_KEY)
  JANUS_LLM_MODEL         default gemini-3.6-flash (gpt-4.1 when the provider is openai)
  JANUS_LLM_TIMEOUT_S     seconds per model request attempt (3 attempts; default 8 in demo mode, 30 in
                          fdb mode)
  JANUS_THINKING_BUDGET   default 0 (thinking off); "" omits the field (flash-lite rejects it)
  JANUS_EOT_MS            silence that closes a user turn (default 1000)
  JANUS_WATCHDOG_MS       whole-session budget before a salvage FINAL (default 105000, as
                          FDB scenarios; 0 = none -- use 0 for live conversations)
  JANUS_MIN_INTERRUPTION_WORDS  words needed to cut the agent off (default 2, so
                          background noise in a recording cannot truncate an answer)
  JANUS_IDLE_PROCESSES    prewarmed job processes, each with its own models (default 1)
  JANUS_JOB_EXECUTOR      "process" (default) or "thread" (jobs share one model copy;
                          saves VRAM, but was flaky over long runs -- see _SHARED)
  JANUS_AGENT_NAME        LiveKit agent name (demo mode default "janus-demo": explicit dispatch
                          only, so a demo worker never takes benchmark rooms; "" = automatic)
  JANUS_STT               "openai" (hosted transcription, the default; needs OPENAI_API_KEY;
                          OPENAI_BASE_URL is honoured), "local" (faster-whisper on the GPU) or
                          "parakeet" (NVIDIA Parakeet TDT 0.6B v2 on the GPU, via NeMo)
  JANUS_STT_MODEL         override the hosted transcription model (default: speech.OPENAI_STT_MODEL)
  JANUS_STT_MIN_RMS       demo mode: segments quieter than this RMS are not transcribed (default 0.025; 0 = off)
  JANUS_WHISPER_HOTWORDS  1 = prime the recognizer with the tool vocabulary (off by default;
                          not yet A/B-tested on the benchmark)
  JANUS_WHISPER_COMPUTE   CTranslate2 compute type on GPU (default float16; the dev
                          script uses int8_float16 to fit an 8 GB GPU)
  JANUS_DECISION_LOG_DIR  optional: one decision-log JSONL per room
  JANUS_FDB_RELAXED_HOUSING  1 = the relaxed housing tool contract (Config.fdb_relaxed_housing_schema;
                          default off; the repro scripts pass it through when set)
  JANUS_FILLER            1 = speak a short pre-synthesized phrase the moment a worded
                          turn closes, and drop the kernel's generic ACK that follows
                          (default on in demo mode, off in fdb mode; the repro scripts set it)
  JANUS_FILLER_TEXT       the filler phrase (default "Okay, one moment.")
  JANUS_FILLER_MIN_WORDS  words a closed turn needs before the filler plays (default 1 in demo mode,
                          3 in fdb mode). In demo mode a bare "Yes." to an offer or a question is
                          followed by the write itself and its answer, about 7 s of silence without it
  JANUS_FILLER_MIN_GAP_S  no second filler within this many seconds of the last (default 12 in demo
                          mode, 0 in fdb mode). In demo mode a greeting, thanks or goodbye on its own
                          gets no filler either (voice_bridge.is_social_turn)
  JANUS_FILLER_PROTECTED  1 = the filler cannot be interrupted (default on in demo mode, off in fdb
                          mode; the user's audio still reaches speech-to-text while it plays). LiveKit interrupts the current speech when its own end-of-turn
                          handling runs (agent_activity._user_turn_completed_impl), which races the
                          filler started on the same transcript; when it lost, the filler was dropped
                          and the first sound came about 3 s later (found live, 21 Oct)
  JANUS_KEEP_SPEECH_AT_TURN_END  1 = LiveKit's end-of-turn handling no longer cuts the speech playing
                          (default on in demo mode, off in fdb mode; voice_settings.keep_speech_at_turn_end)
  JANUS_TTS_CHUNKED       1 = synthesize and play each sentence as soon as it is ready
                          (default on in demo mode, off in fdb mode; the repro scripts set it)
  JANUS_AEC_WARMUP_S      seconds of LiveKit's AEC warm-up, during which the agent's first
                          speech mutes the user's audio to speech-to-text (default off in
                          fdb mode, LiveKit's 3.0 in demo mode; 0 = off; see
                          adapters/voice_settings.py)
  JANUS_ST_CONFIRM_WAITS  demo mode: seconds between SmartThings read-backs after a command, comma-
                          separated (default 0.5,1,2,4: about 10 s of silence before an honest
                          "couldn't confirm"; virtual devices confirm within about a second)
  JANUS_WORLD_POLL_S      demo mode: seconds between reads of the device a held write targets, while
                          Janus waits for the user's yes (adapters/world_watch.py; default 10, 0 = off)
  JANUS_CAMERA_WATCH_S    demo mode: seconds between camera reads of the washer's display while a
                          technician visit offered for its error waits for a yes (default 1;
                          adapters/display_reader.py)
  JANUS_DRILLS            1 = demo mode only: accept failure drills from the companion app on the
                          data topic `janus.control` (adapters/drills.py; default off, never in fdb mode)
  GEMINI_API_KEY, LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET

Run: python -m prism_rt.voice.agent start
(`start`, not `dev`: dev mode keeps no prewarmed process, and the benchmark
client starts streaming two seconds after it joins, without waiting.)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import importlib
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from livekit import rtc
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, JobExecutorType, JobProcess, cli, stt
from livekit.plugins import silero

from prism_rt.adapters.display_reader import CLEAR, DisplayReader
from prism_rt.adapters.drills import CONTROL_TOPIC, DrilledBackend, DrilledProvider, DrillState, run_external_command
from prism_rt.adapters.event_feed import EventFeed
from prism_rt.adapters.fdb_manifest import introspect_file, to_catalog_manifest
from prism_rt.adapters.fdb_schema_overrides import BACKEND_FILLS, maybe_apply
from prism_rt.adapters.fdb_tool_adapter import FdbToolAdapter
from prism_rt.adapters.voice_bridge import VoiceBridge, is_social_turn
from prism_rt.adapters.voice_settings import (
    FILLERS_FOR_QUESTION,
    FILLERS_FOR_REQUEST,
    aec_warmup_duration,
    choose_filler,
    keep_speech_at_turn_end,
)
from prism_rt.adapters.offers import build_offer, no_offer_text
from prism_rt.adapters.world_watch import WorldWatcher
from prism_rt.devicecare import DeviceCareToolset
from prism_rt.devicecare.tools import _device_key
from prism_rt.entry import setup
from prism_rt.observability.effects import effect_summary, format_effect_summary
from prism_rt.observability.xray import TracedQueue
from prism_rt.profiles import demo_config, fdb_v3_config
from prism_rt.phone import MANIFEST as PHONE_MANIFEST, TOPIC as PHONE_TOPIC, PhoneToolset
from prism_rt.recap import RecapToolset
from prism_rt.voice.camera import FrameStore, pump_video_track
from config.templates import ACK_DEFAULT, EVIDENCE_REASK, EVIDENCE_WITHDRAWN
from prism_rt.voice.speech import (
    FasterWhisperSTT,
    KokoroTTS,
    OpenAITranscribeSTT,
    ParakeetSTT,
    load_kokoro,
    load_parakeet,
    load_whisper,
    pcm_frames,
)
from prism_rt.workers.gateway import GeminiProvider, OpenAIProvider

logger = logging.getLogger("janus.voice")
# Guarantees our own INFO-level diagnostic logging (voice/speech.py's
# per-recognition logs, used to root-cause the third ASR silence layer,
# 2026-09-27) is actually visible in `docker logs`/agent.log regardless
# of whether livekit-agents' own CLI already configured logging --
# `basicConfig` is a documented no-op if the root logger already has
# handlers, so this never fights whatever's already there.
logging.basicConfig(level=logging.INFO)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


# Before any setting below is read: a JANUS_* value in .env (JANUS_STT,
# JANUS_MODE, ...) used to be ignored because .env was loaded after them.
_load_dotenv(REPO_ROOT / ".env")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Toolset:
    manifest: list[dict]
    make_executor: Callable[..., Callable[[str, dict], Awaitable[dict]]]  # (room_name, on_device_state=None[, drills])


def vocabulary_hotwords(manifest: list[dict]) -> str:
    """Words the speech recognizer should expect, built from the tool list
    itself (tool and parameter names, e.g. "search flights", "bedrooms",
    "exchange rate") -- nothing from any benchmark recording. Found live:
    "search flights to Chennai" was heard as "so as we fly to Chennai"."""
    words: list[str] = []
    for tool in manifest:
        names = [tool["name"], *((tool.get("params_schema") or {}).get("properties") or {})]
        for name in names:
            for word in name.split("_"):
                if len(word) > 2 and word not in words:
                    words.append(word)
    return " ".join(words)


RELAXED_HOUSING = os.environ.get("JANUS_FDB_RELAXED_HOUSING") == "1"


def fdb_toolset() -> Toolset:
    root = os.environ.get("FDB_V3_ROOT")
    if not root or not os.path.isfile(os.path.join(root, "lk_agent_tool.py")):
        raise RuntimeError("FDB_V3_ROOT must point at the Full-Duplex-Bench v3/ directory")
    manifest = maybe_apply(to_catalog_manifest(introspect_file(os.path.join(root, "lk_agent_tool.py"))), RELAXED_HOUSING)
    schemas = {t["name"]: t["params_schema"] for t in manifest}
    if root not in sys.path:
        sys.path.insert(0, root)
    mock_apis = importlib.import_module("mock_apis")
    latency = os.environ.get("JANUS_FDB_LATENCY", "instant")
    log_path = os.environ.get("JANUS_TOOL_LOG", "/tmp/agent_tool_calls.log")

    def make_executor(room_name: str, on_device_state=None):
        registry = mock_apis.MockAPIRegistry(latency_profile=latency, enable_logging=False)
        return FdbToolAdapter(
            registry, room_name, log_path=log_path, get_params_schema=schemas.get,
            backend_fills=BACKEND_FILLS if RELAXED_HOUSING else None,
        ).execute

    return Toolset(manifest=manifest, make_executor=make_executor)


MODE = os.environ.get("JANUS_MODE", "fdb").strip().lower()
# Speech-to-text: "openai" (hosted transcription, the default,
# speech.OPENAI_STT_MODEL; needs OPENAI_API_KEY, and honours OPENAI_BASE_URL so
# the key can sit behind a relay), "local" (faster-whisper on the GPU) or
# "parakeet" (speech.PARAKEET_REPO on the GPU).
STT_BACKEND = os.environ.get("JANUS_STT", "openai").strip().lower()
if STT_BACKEND not in ("local", "openai", "parakeet"):
    raise SystemExit(f"JANUS_STT must be 'local', 'openai' or 'parakeet', not {STT_BACKEND!r}")
if MODE not in ("fdb", "demo"):
    raise SystemExit(f"JANUS_MODE must be 'fdb' or 'demo', not {MODE!r}")


def _st_backend_label() -> str:
    from prism_rt.smartthings.factory import backend_mode

    return backend_mode()


def _flag_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw in (None, "") else raw.strip() == "1"


FILLER_ENABLED = _flag_env("JANUS_FILLER", MODE == "demo")
FILLER_TEXT = os.environ.get("JANUS_FILLER_TEXT") or "Okay, one moment."
# Demo mode: the filler varies with the turn (voice_settings.choose_filler);
# JANUS_FILLER_VARIED=0 keeps the single FILLER_TEXT.
FILLER_VARIED = _flag_env("JANUS_FILLER_VARIED", MODE == "demo")
TTS_CHUNKED = _flag_env("JANUS_TTS_CHUNKED", MODE == "demo")
FILLER_PROTECTED = _flag_env("JANUS_FILLER_PROTECTED", MODE == "demo")
FILLER_MIN_WORDS = _int_env("JANUS_FILLER_MIN_WORDS", 1 if MODE == "demo" else 3)
FILLER_MIN_GAP_S = float(os.environ.get("JANUS_FILLER_MIN_GAP_S") or (12 if MODE == "demo" else 0))
AEC_WARMUP_S = aec_warmup_duration(MODE, os.environ.get("JANUS_AEC_WARMUP_S"))
# Demo mode: a closed user turn no longer cuts Janus's speech (voice_settings.keep_speech_at_turn_end).
KEEP_SPEECH_AT_TURN_END = _flag_env("JANUS_KEEP_SPEECH_AT_TURN_END", MODE == "demo")
if KEEP_SPEECH_AT_TURN_END:
    from livekit.agents.voice.agent_activity import AgentActivity

    keep_speech_at_turn_end(AgentActivity)
DRILLS_ENABLED = MODE == "demo" and os.environ.get("JANUS_DRILLS") == "1"
# Demo mode: how long a closed session waits for a rejoining phone before it
# deletes the room (see _end_for_rejoin).
REJOIN_GRACE_S = float(os.environ.get("JANUS_REJOIN_GRACE_S") or 2.0)
# Demo mode: a withdrawal or re-ask arriving while Janus is still speaking cuts
# its current sentence first (VoiceBridge.interrupt_prefixes).
SELF_INTERRUPT_PREFIXES: tuple[str, ...] = (
    tuple(t.split("{")[0].strip() for t in (EVIDENCE_WITHDRAWN, EVIDENCE_REASK)) if MODE == "demo" else ())


def _code_optional(tool: dict) -> dict:
    schema = dict(tool["params_schema"])
    properties = dict(schema["properties"])
    properties["code"] = {**properties["code"], "description": "The code on the display, for example 4C. Leave it out to read "
                          "whatever the display shows now through the camera."}
    return {**tool, "params_schema": {**schema, "properties": properties,
                                      "required": [r for r in schema.get("required", []) if r != "code"]}}


def demo_toolset() -> Toolset:
    from prism_rt.smartthings.factory import backend_mode, combine, make_backend
    from prism_rt.smartthings.tools import SmartThingsToolset

    # One toolset per room, never shared: device care (its bookings, its open
    # case) plus the SmartThings pack (JANUS_ST_MODE=real|mock, default mock).
    mode = backend_mode()
    logger.info("demo toolset: SmartThings backend = %s", mode)

    def make_executor(room_name: str, on_device_state=None, drills: DrillState | None = None):
        backend = make_backend()
        if drills is not None:
            backend = DrilledBackend(backend, drills)
        waits = os.environ.get("JANUS_ST_CONFIRM_WAITS", "").strip()
        st = SmartThingsToolset(backend, on_device_state=on_device_state,
                                **({"waits": tuple(float(w) for w in waits.split(","))} if waits else {}))
        care = DeviceCareToolset(on_device_state=on_device_state)
        recap = RecapToolset()  # its store getter is set once the kernel session exists
        phone = PhoneToolset()  # its publish is set once the room is connected
        _manifest, execute = combine(care, st, recap, phone)
        execute.recap = recap
        execute.phone = phone

        async def snapshot_states() -> None:  # the app's initial device tiles
            await care.snapshot_states()
            await st.snapshot_states()

        execute.snapshot_states = snapshot_states
        execute.st_backend = backend
        execute.read_target = st.read_target
        execute.read_temperature = st.read_temperature
        # Appliance offers (adapters/offers.py): the washer's status as the
        # device-care pack reports it; the read also opens the case a booking needs.
        def read_appliance(device: str) -> dict:
            status = care._get_device_status({"device_type": device})
            status["visit_booked"] = any(b["issue_id"] == care.current_issue for b in care.bookings)
            return status

        execute.read_appliance = read_appliance
        execute.observe_display = care.observe_display
        execute.look = None  # set once the camera's display reader exists

        async def execute_seen(tool_name: str, args: dict) -> dict:
            # A washer question with the camera on reads the display first, so
            # the answer is what the camera shows now, not the earlier error.
            seen, value = False, None
            if execute.look is not None and tool_name in ("get_device_status", "lookup_error_code") \
                    and _device_key(care.kb, args.get("device_type")) == "washer":
                seen, value = await execute.look("washer")
            asks_display = tool_name == "get_device_status" or (
                tool_name == "lookup_error_code" and not str(args.get("code") or "").strip())
            if seen and value is None and asks_display:
                # The camera is on but no display is in view (a wall, a hand):
                # the simulated washer's own 4C and 34 minutes would be a
                # guess about what the user is pointing at (phone, 24 Oct).
                return {"found": True, "device": care.kb["devices"]["washer"]["name"], "display_visible": False,
                        "message": "The camera can't make out the washer's display. Point it at the display and ask again.",
                        "say": "I can't make out the washer's display on the camera. Point it at the display and ask me again.",
                        "verbatim": True}
            if tool_name == "lookup_error_code" and not str(args.get("code") or "").strip():
                # No code given ("is there an error now?"): the one on the
                # display, as the camera reads it, else as the washer reports it.
                code = value if seen and value != "clear" else None if seen else care.status.get("washer", {}).get("error_code")
                if not code:
                    return {"found": True, "device": care.kb["devices"]["washer"]["name"], "code": None,
                            "message": "The washer's display shows no error code right now.",
                            "say": "Your washer's display shows no error code right now.", "verbatim": True}
                args = {**args, "code": code}
            result = await execute(tool_name, args)
            if tool_name == "lookup_error_code" and seen and value == "clear" and isinstance(result, dict):
                result["display_now"] = "The washer's display shows no error code right now."
            return result

        execute_seen.__dict__.update(execute.__dict__)
        execute_seen.set_look = lambda look: setattr(execute, "look", look)
        return execute_seen

    from prism_rt.devicecare.tools import MANIFEST as CARE_MANIFEST
    from prism_rt.recap import MANIFEST as RECAP_MANIFEST
    from prism_rt.smartthings.tools import MANIFEST as ST_MANIFEST

    # The router indicator lookup is the earlier device-care story, not part
    # of this demo: given any picture with a light in it, the model reached for
    # it and explained a router fault (phone, 24 Oct). JANUS_ROUTER=1 keeps it.
    care = [t for t in CARE_MANIFEST if _flag_env("JANUS_ROUTER", False) or t["name"] != "identify_indicator"]
    # The code is optional here: without one, it is read off the camera
    # (demo_toolset's executor), so "is there an error now?" is answered,
    # not met with "what should code be?" (24 Oct).
    care = [_code_optional(t) if t["name"] == "lookup_error_code" else t for t in care]
    return Toolset(manifest=[*care, *ST_MANIFEST, *RECAP_MANIFEST, *PHONE_MANIFEST], make_executor=make_executor)


# One model set per worker process. Under the default PROCESS executor each
# job process (the active one and the prewarmed idle one) loads its own
# copy -- ~5 GB of VRAM for two, fine on the organizers' 48 GB GPU. On an
# 8 GB dev GPU that also hosts FDB's scorer, Whisper hit CUDA OOM mid-
# recognition (found live 2026-09-27); scripts/fdb_v3/dev_livekit_run.sh
# shrinks Whisper to int8 for local runs instead (JANUS_WHISPER_COMPUTE).
# JANUS_JOB_EXECUTOR=thread shares one copy across jobs, but in a 26-job run
# it produced client aborts and silent output in later jobs, so it is not
# the default.
_SHARED: dict = {}
_SHARED_LOCK = threading.Lock()


def _shared_models() -> dict:
    with _SHARED_LOCK:
        if not _SHARED:
            _SHARED["vad"] = silero.VAD.load(min_speech_duration=0.05, min_silence_duration=0.55)
            if STT_BACKEND == "local":
                _SHARED["whisper"] = load_whisper()
            elif STT_BACKEND == "parakeet":
                _SHARED["parakeet"] = load_parakeet()
            _SHARED["kokoro"] = load_kokoro()
            if FILLER_ENABLED:
                # Synthesized once per process, so the filler plays with no
                # synthesis delay at the end of every turn.
                _SHARED["filler_pcm"] = _SHARED["kokoro"].synthesize_pcm16(FILLER_TEXT)
                if FILLER_VARIED:
                    pcms = {text: _SHARED["kokoro"].synthesize_pcm16(text)
                            for text in (*FILLERS_FOR_QUESTION, *FILLERS_FOR_REQUEST)}
                    _SHARED["filler_pcms"] = {text: pcm for text, pcm in pcms.items() if pcm}
            _SHARED["toolset"] = demo_toolset() if MODE == "demo" else fdb_toolset()
        return _SHARED


def prewarm(proc: JobProcess) -> None:
    proc.userdata.update(_shared_models())


server = AgentServer(
    setup_fnc=prewarm,
    job_executor_type=(
        JobExecutorType.THREAD if os.environ.get("JANUS_JOB_EXECUTOR") == "thread" else JobExecutorType.PROCESS
    ),
    num_idle_processes=_int_env("JANUS_IDLE_PROCESSES", 1),
    initialize_process_timeout=300.0,
    load_threshold=float(os.environ.get("JANUS_LOAD_THRESHOLD", "0.95")),
    job_memory_warn_mb=6000,
)


# Demo mode registers under a name, which makes LiveKit dispatch it only to
# rooms that ask for that agent. Found live, 30 Sep: an unnamed demo worker on
# the same LiveKit project as a benchmark run was handed 33 of the benchmark's
# rooms. Benchmark mode stays unnamed (automatic dispatch), which FDB's client
# relies on. Join a demo with a token from scripts/make_demo_token.py.
AGENT_NAME = os.environ.get("JANUS_AGENT_NAME", "janus-demo" if MODE == "demo" else "")


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    room_name = ctx.room.name
    userdata = ctx.proc.userdata
    toolset: Toolset = userdata["toolset"]
    logger.info("janus joining room %s", room_name)

    min_rms = float(os.environ.get("JANUS_STT_MIN_RMS", "0.025")) if MODE == "demo" else 0.0
    if STT_BACKEND == "parakeet":
        recognizer = ParakeetSTT(userdata["parakeet"], demo_fixes=MODE == "demo", min_rms=min_rms)
    elif STT_BACKEND == "openai":
        # Demo mode no longer names the home's devices in the prompt
        # (it used to list them): with them, noise came back as invented
        # commands (23 Oct). Known mishearings are fixed after the fact instead.
        recognizer = OpenAITranscribeSTT(drop_prompt_echo=MODE == "demo", demo_fixes=MODE == "demo",
                                         min_rms=min_rms)
    else:
        recognizer = FasterWhisperSTT(
            userdata["whisper"],
            hotwords=vocabulary_hotwords(toolset.manifest) if os.environ.get("JANUS_WHISPER_HOTWORDS") == "1" else None,
        )
    session = AgentSession(
        vad=userdata["vad"],
        stt=stt.StreamAdapter(stt=recognizer, vad=userdata["vad"]),
        tts=KokoroTTS(userdata["kokoro"], chunked=TTS_CHUNKED),
        min_interruption_words=_int_env("JANUS_MIN_INTERRUPTION_WORDS", 1 if MODE == "demo" else 2),
        aec_warmup_duration=AEC_WARMUP_S,
        # A protected (uninterruptible) filler must not mute the user: by
        # default LiveKit replaces their audio with silence while such speech
        # plays, and a correction spoken over the filler ("...twenty-two.
        # Actually, make it twenty-one") was never transcribed (live, 21 Oct).
        **({"discard_audio_if_uninterruptible": False} if FILLER_PROTECTED else {}),
    )

    log_dir = os.environ.get("JANUS_DECISION_LOG_DIR")
    if log_dir:
        # Wire trace beside the decision log, so `python -m prism_rt.observability.xray`
        # can draw the conversation (user text, speech, tool calls, results).
        os.makedirs(log_dir, exist_ok=True)
        trace_start = time.monotonic()
        events: asyncio.Queue = TracedQueue(os.path.join(log_dir, f"{room_name}.wire.jsonl"), "in", trace_start)
        actions: asyncio.Queue = TracedQueue(os.path.join(log_dir, f"{room_name}.wire.jsonl"), "out", trace_start)
    else:
        events, actions = asyncio.Queue(), asyncio.Queue()

    def stop_speaking() -> None:
        # Current and queued speech (LiveKit's own barge-in stops only the
        # utterance playing). Never let this break the bridge.
        try:
            session.interrupt()
        except Exception:  # noqa: BLE001 - nothing playing / not interruptible
            logger.debug("interrupt: nothing to stop", exc_info=True)

    kernel_speech: list = []  # handles of Janus's own speech not yet done (not the filler)

    def drop_queued_speech() -> None:
        for handle in list(kernel_speech):
            if not handle.done():
                with contextlib.suppress(Exception):  # already finishing
                    handle.interrupt()

    async def say(text: str) -> None:
        # Queue only; never wait for playout, or a TOOL_CALL right behind an
        # acknowledgement would sit until the acknowledgement finished playing.
        handle = session.say(text, allow_interruptions=True, add_to_chat_ctx=False)
        kernel_speech.append(handle)
        handle.add_done_callback(lambda h: kernel_speech.remove(h) if h in kernel_speech else None)
        # Whether each reply played to the end (a reply cut short with no user
        # speech was seen twice on the phone, 24 Oct; see voice/speech.py's
        # tts chunk log for what was synthesized).
        handle.add_done_callback(lambda h, t=text: logger.info("speech %s: %s", "interrupted" if h.interrupted else "finished", t[:50]))

    filler_pcm: bytes = userdata.get("filler_pcm") or b""
    filler_pcms: dict = userdata.get("filler_pcms") or {}
    last_filler: dict = {}
    # Device tiles for the app: the executor reports each device state it reads.
    state_sink: dict = {}
    # Failure drills from the companion app (demo mode, JANUS_DRILLS=1 only).
    drills = DrillState() if DRILLS_ENABLED else None
    executor = toolset.make_executor(
        room_name, on_device_state=lambda *args, **kw: state_sink["fn"](*args, **kw) if "fn" in state_sink else None,
        **({"drills": drills} if drills is not None else {}),
    )

    # Companion-app event feed (demo mode): what the agent hears, decides and
    # does, as JSON on the data topic `janus.events` (app/INTEGRATION.md).
    # Events are queued and published by one task, so a slow or closed room
    # never holds up the kernel.
    feed: EventFeed | None = None
    event_queue: asyncio.Queue = asyncio.Queue()
    # Evidence-bound confirmation (kernel/evidence.py): while a write waits for
    # a yes, the device it targets is watched and its state reported to the
    # kernel. Fed by the same events the app gets. Created before the bridge,
    # which it reports through (bound below).
    world_sink: dict = {}
    watcher: WorldWatcher | None = None
    poll_s = float(os.environ.get("JANUS_WORLD_POLL_S", "10") or 0)
    display: dict = {}  # the camera's display reader, set once the model provider exists
    if MODE == "demo" and poll_s > 0:
        async def _read_device(tool: str, spoken: str):
            if tool == "book_technician" and "reader" in display:
                return await display["reader"].read(spoken)  # the washer's code, off the camera
            return await executor.read_target(tool, spoken)

        async def _report_world(key: str, value, missing: str | None = None) -> None:
            if "fn" in world_sink:
                await world_sink["fn"](key, value, missing)

        watcher = WorldWatcher(toolset.manifest, _read_device, _report_world, interval_s=poll_s,
                               intervals={"book_technician": float(os.environ.get("JANUS_CAMERA_WATCH_S") or 1.0)})

    def _feed_out(event: dict) -> None:
        event_queue.put_nowait(event)
        if watcher is not None:
            watcher.on_event(event)

    if MODE == "demo":
        feed_start = time.monotonic()
        feed = EventFeed(
            _feed_out,
            backend=_st_backend_label(),
            packs=["smartthings", "device_care"],
            manifest=toolset.manifest,
            clock_ms=lambda: (time.monotonic() - feed_start) * 1000,
        )

    async def play_filler() -> None:
        # Pre-synthesized audio; plain synthesis only if the cache is empty.
        try:
            interruptible = not FILLER_PROTECTED
            text = bridge.filler_now or FILLER_TEXT
            pcm = filler_pcms.get(text) or (filler_pcm if text == FILLER_TEXT else b"")
            if not pcm:  # a varied filler that did not synthesize: the default one
                text, pcm = FILLER_TEXT, filler_pcm
            last_filler["text"] = text
            if pcm:
                handle = session.say(text, audio=pcm_frames(pcm), allow_interruptions=interruptible,
                                     add_to_chat_ctx=False)
            else:
                handle = session.say(text, allow_interruptions=interruptible, add_to_chat_ctx=False)
            # Whether the filler was actually heard (latency analysis).
            handle.add_done_callback(lambda h: logger.info("filler %s", "interrupted" if h.interrupted else "finished"))
            if feed is not None:
                feed.agent_said(text, "filler")
            logger.info("filler played: %s", text)
        except Exception:  # noqa: BLE001 - a filler must never break the bridge
            logger.warning("filler failed", exc_info=True)

    bridge = VoiceBridge(
        events=events,
        actions=actions,
        say=say,
        execute_tool=executor,
        t_eot_ms=_int_env("JANUS_EOT_MS", 1000),
        # Single-request benchmark recordings: a lone "you"/"Hmm." after the
        # request is Whisper on silence, not a new turn. Kept in demo mode,
        # where a live person may say "thank you" and expect a reply.
        drop_filler_segments=MODE != "demo" and os.environ.get("JANUS_KEEP_FILLER_SEGMENTS") != "1",
        drop_self_echo=MODE == "demo",
        filler_text=FILLER_TEXT if FILLER_ENABLED else "",
        # Speech-to-text writes English; mostly-foreign text is noise or the
        # agent's own voice picked up by the microphone.
        drop_foreign_segments=True,
        # VAD activity to the kernel (honoured where the profile sets
        # vad_floor_enabled): no call or speech while the user is speaking or
        # their words are still being transcribed.
        report_user_activity=True,
        stop_speaking=stop_speaking,
        drop_queued_speech=drop_queued_speech,
        play_filler=play_filler if FILLER_ENABLED else None,
        filler_min_words=FILLER_MIN_WORDS,
        filler_min_gap_s=FILLER_MIN_GAP_S,
        filler_skip=is_social_turn if MODE == "demo" else None,
        choose_filler=(lambda said: choose_filler(said, last_filler.get("text"))) if FILLER_VARIED and filler_pcms else None,
        interrupt_prefixes=SELF_INTERRUPT_PREFIXES,
        on_interruption_logged=lambda msg: logger.info("bridge: %s", msg),
        generic_ack_texts=(ACK_DEFAULT,),
    )
    world_sink["fn"] = bridge.on_world_fact
    logger.info(
        "filler %s%s, chunked TTS %s, AEC warm-up %s",
        "on" if FILLER_ENABLED else "off", " (protected)" if FILLER_ENABLED and FILLER_PROTECTED else "",
        "on" if TTS_CHUNKED else "off",
        f"{AEC_WARMUP_S:g} s" if AEC_WARMUP_S else "off",
    )
    # The recognizer reports each transcription's start and finish, so the
    # turn never closes while one is still running (VoiceBridge.on_recognition).
    recognizer.on_recognition = bridge.on_recognition

    # gemini-3.6-flash, thinking off (28 Sep): same judge-scored full-100 as
    # flash-lite (73%) but 3-call scenarios 50% -> 69% and it follows the
    # honesty rules flash-lite ignored live ("cancel the booking" became a
    # booking). JANUS_THINKING_BUDGET="" restores the model's own default.
    thinking = os.environ.get("JANUS_THINKING_BUDGET", "0")
    if os.environ.get("JANUS_LLM_PROVIDER", "gemini") == "openai":
        # Equal to gemini-3.6-flash on a judged 15-recording comparison
        # (same 7/15, same latency); Gemini remains the declared provider.
        provider = OpenAIProvider(model=os.environ.get("JANUS_LLM_MODEL", "gpt-4.1"))
    else:
        provider = GeminiProvider(
            model=os.environ.get("JANUS_LLM_MODEL", "gemini-3.6-flash"),
            thinking_budget=int(thinking) if thinking not in (None, "") else None,
            timeout_s=float(os.environ.get("JANUS_LLM_TIMEOUT_S") or (8 if MODE == "demo" else 30)),
        )
    frames = FrameStore()
    if MODE == "demo" and getattr(executor, "read_appliance", None) is not None:
        def _washer_status(device: str) -> str | None:
            status = executor.read_appliance(device)
            if not isinstance(status, dict) or not status.get("found"):
                return None
            return status.get("error_code") or CLEAR

        # Not the drilled provider: a "model down" drill is about the conversation.
        display["reader"] = DisplayReader(provider.complete_json, frames.latest, _washer_status,
                                          observe=getattr(executor, "observe_display", None))
        if getattr(executor, "set_look", None) is not None:
            executor.set_look(display["reader"].look)
    if drills is not None:
        provider = DrilledProvider(provider, drills)
    if MODE == "demo":
        # A write waits JANUS_SETTLE_MS after the user stops: "twenty-two ...
        # actually, twenty-one" said right after the filler was too late at
        # 1 s (the 22 went out in 4 of 4 scripted runs, 24 Oct).
        config = demo_config(watchdog_timeout_ms=_int_env("JANUS_WATCHDOG_MS", 0),
                             settle_ms=_int_env("JANUS_SETTLE_MS", 2500))
    else:
        config = fdb_v3_config(
            watchdog_timeout_ms=_int_env("JANUS_WATCHDOG_MS", 105_000), fdb_relaxed_housing_schema=RELAXED_HOUSING
        )
    runtime = setup(config=config, provider=provider, blob_resolver=frames.get)
    meta: dict = {"seed": 0}
    held: dict = {}
    if MODE == "demo":
        meta["on_session"] = lambda store: held.update(store=store)
        if getattr(executor, "recap", None) is not None:
            executor.recap.store_getter = lambda: held.get("store")
        meta["on_step"] = lambda report: feed.on_step(report, held["store"]) if "store" in held else None
        state_sink["fn"] = feed.device_state
    if log_dir:
        meta["log_path"] = os.path.join(log_dir, f"{room_name}.jsonl")

    run_task = asyncio.create_task(runtime.run_scenario(events, actions, meta=meta))
    bridge.start()
    await bridge.on_manifest(toolset.manifest)

    pending: set[asyncio.Task] = set()

    def _spawn(coro) -> None:
        task = asyncio.create_task(coro)
        pending.add(task)
        task.add_done_callback(pending.discard)

    @session.on("user_input_transcribed")
    def _on_transcript(ev) -> None:
        # A *final* segment with empty/whitespace-only text still needs to
        # reach the bridge -- found live (2026-09-27): faster-whisper can
        # legitimately produce nothing for a real, VAD-detected speech
        # segment (heavy filler/hesitation, quiet audio). Previously this
        # guard silently dropped that case entirely -- no text_chunk, no
        # turn ever opens, kernel/turns.py.TurnManager.request_interpretation
        # never even sees the turn to interpret it, and no S2 safety net
        # (never_silent_unclear_enabled included) is reachable because none
        # of them fire without an interpretation happening first. Only
        # interim (non-final) results are still filtered.
        if ev.is_final:
            _spawn(bridge.on_segment_final(ev.transcript))

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:
        logger.info("user_state %s -> %s", ev.old_state, ev.new_state)  # latency analysis: VAD speech start / end
        if ev.new_state == "speaking":
            _spawn(bridge.on_speech_start())
        elif ev.old_state == "speaking":
            _spawn(bridge.on_speech_end())

    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        logger.info("agent_state %s -> %s", ev.old_state, ev.new_state)  # latency analysis: when audio actually starts
        bridge.on_agent_speaking_changed(ev.new_state == "speaking")

    async def _shutdown(*_args) -> None:
        await bridge.stop()
        await events.put(None)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(run_task, timeout=5)
        if held.get("store") is not None:
            # What the session did to the world (demo mode): the video's closing line.
            with contextlib.suppress(Exception):
                logger.info(format_effect_summary(effect_summary(held["store"])))

    ctx.add_shutdown_callback(_shutdown)

    async def _until_connected() -> None:
        # The kernel's first events (the session badge, the device tiles) are
        # queued before `session.start` connects the room; publishing then
        # fails ("cannot access local participant before connecting") and the
        # events are lost. Found on the first live run, 21 Oct.
        while not ctx.room.isconnected():
            await asyncio.sleep(0.1)

    async def _publish_events() -> None:
        while True:
            event = await event_queue.get()
            await _until_connected()
            try:
                await ctx.room.local_participant.publish_data(
                    json.dumps(event, separators=(",", ":")).encode(), reliable=True, topic="janus.events"
                )
            except Exception:  # noqa: BLE001 - the app feed is optional; never break the conversation
                logger.warning("could not publish %s event", event.get("type"), exc_info=True)

    async def _announce() -> None:
        # A late-joining app gets the session badge and the device tiles again;
        # one already in the room when the agent connects gets them here too.
        await _until_connected()
        feed.session_started()
        snapshot = getattr(executor, "snapshot_states", None)
        if snapshot is not None:
            with contextlib.suppress(Exception):
                await snapshot()

    async def _external_command(args: dict) -> None:
        # "Someone else" changes a device, outside the kernel; the tiles follow.
        try:
            sink = state_sink.get("fn")
            logger.info(await run_external_command(executor.st_backend, args, on_device_state=sink))
            if watcher is not None:
                await watcher.nudge()  # the kernel hears of the change at once, not at the next poll
        except Exception:  # noqa: BLE001 - a drill must never break the conversation
            logger.warning("external_command drill failed", exc_info=True)

    async def _offer(kind: str) -> None:
        # Janus speaks first, only if the devices give it something to offer.
        try:
            reader = display.get("reader")
            payload = await build_offer(kind, executor.read_target, getattr(executor, "read_appliance", None),
                                        getattr(executor, "read_temperature", None),
                                        reader.look if reader is not None else None)
            if payload is None or "notice" in payload:
                logger.info("offer %s: nothing to offer right now", kind)
                text = payload["notice"] if payload else await no_offer_text(
                    kind, executor.read_target, getattr(executor, "read_appliance", None))
                if feed is not None:
                    feed.agent_said(text, "inform")
                await bridge.say(text)  # a notice to the presenter's button, not a decision
                return
            logger.info("offer %s: %s", kind, payload["text"])
            await bridge.on_offer(payload)
        except Exception:  # noqa: BLE001 - a drill must never break the conversation
            logger.warning("offer drill failed", exc_info=True)

    def _on_data(packet) -> None:
        if packet.topic != CONTROL_TOPIC:
            return
        sender = getattr(packet.participant, "identity", "") or ""
        if not sender or sender.startswith("agent-"):
            logger.warning("drill ignored: sender %r", sender)
            return
        try:
            external = drills.apply(json.loads(bytes(packet.data).decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as exc:
            logger.warning("drill refused from %s: %s", sender, exc)
            return
        logger.info("drill from %s; active: %s", sender, ", ".join(drills.active()) or "none")
        if external is not None and "offer" in external:
            _spawn(_offer(external["offer"]))
        elif external is not None:
            _spawn(_external_command(external))

    phone = getattr(executor, "phone", None)
    if phone is not None:
        # Phone actions (prism_rt/phone.py): the app runs them and answers on the same topic.
        async def _publish_phone(message: dict) -> None:
            await _until_connected()
            await ctx.room.local_participant.publish_data(
                json.dumps(message, separators=(",", ":")).encode(), reliable=True, topic=PHONE_TOPIC
            )

        def _on_phone_data(packet) -> None:
            sender = getattr(packet.participant, "identity", "") or ""
            if packet.topic != PHONE_TOPIC or not sender or sender.startswith("agent-"):
                return
            try:
                reply = json.loads(bytes(packet.data).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                logger.warning("phone reply refused from %s", sender)
                return
            logger.info("phone reply from %s: %s", sender, reply)
            phone.on_reply(reply)

        phone.publish = _publish_phone
        ctx.room.on("data_received", _on_phone_data)

    if drills is not None:
        ctx.room.on("data_received", _on_data)
        logger.info("failure drills enabled (topic %s)", CONTROL_TOPIC)

    if feed is not None:
        _spawn(_publish_events())
        _spawn(_announce())
        ctx.room.on("participant_connected", lambda p: _spawn(_announce()) if not p.identity.startswith("agent-") else None)

    if MODE == "demo":
        frame_interval = float(os.environ.get("JANUS_FRAME_INTERVAL_S", "1.0"))
        pumped: set[str] = set()

        def _watch_video(track, publication, participant) -> None:
            if track.kind != rtc.TrackKind.KIND_VIDEO or track.sid in pumped:
                return
            pumped.add(track.sid)
            logger.info("camera track from %s", participant.identity)
            _spawn(pump_video_track(track, frames, bridge.on_video_frame, interval_s=frame_interval))

        ctx.room.on("track_subscribed", _watch_video)

    if MODE == "demo":

        async def _end_for_rejoin() -> None:
            # A phone rejoining with the same identity replaces its old
            # connection, which closes this session; the new one shows up a
            # moment later. Deleting the room then would throw out the phone
            # that just joined ("Connected", then offline; phone, 24 Oct),
            # so wait briefly and, if someone is in the room, send it a fresh
            # agent instead.
            await asyncio.sleep(REJOIN_GRACE_S)
            # A "viewer-" (the app on a test emulator, watching a scripted
            # run) only watches: it keeps no room alive.
            people = [p.identity for p in ctx.room.remote_participants.values()
                      if not p.identity.startswith(("agent-", "viewer-"))]
            if not people:
                logger.info("session closed; deleting room %s for a fresh rejoin", ctx.room.name)
                ctx.delete_room()
                return
            logger.info("session closed with %s in room %s; sending a fresh agent", ", ".join(people), ctx.room.name)
            from livekit import api as lk_api

            try:
                await ctx.api.agent_dispatch.create_dispatch(
                    lk_api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=ctx.room.name))
            except Exception:
                logger.exception("fresh dispatch failed; deleting room %s instead", ctx.room.name)
                ctx.delete_room()
                return
            ctx.shutdown(reason="session closed; a fresh agent takes over")

        @session.on("close")
        def _on_close(_ev) -> None:
            # The phone left (the app was closed, or the network dropped): the
            # session closes, but the worker stayed in the room, so a rejoin
            # found an agent that no longer listened and no new one was sent.
            # Found on a phone, 23 Oct. Ending this job (deleting the room, or
            # handing over to a fresh agent) lets the next join be heard.
            _spawn(_end_for_rejoin())

    await session.start(room=ctx.room, agent=Agent(instructions=""))

    if MODE == "demo":
        # A camera already published before the worker joined.
        for participant in ctx.room.remote_participants.values():
            for publication in participant.track_publications.values():
                if publication.track is not None:
                    _watch_video(publication.track, publication, participant)


if __name__ == "__main__":
    cli.run_app(server)
