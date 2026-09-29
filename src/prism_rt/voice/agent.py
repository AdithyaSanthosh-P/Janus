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
  JANUS_LLM_MODEL         default gemini-3.6-flash
  JANUS_THINKING_BUDGET   default 0 (thinking off); "" omits the field (flash-lite rejects it)
  JANUS_EOT_MS            silence that closes a user turn (default 1000)
  JANUS_WATCHDOG_MS       whole-session budget before a salvage FINAL (default 105000, as
                          FDB scenarios; 0 = none -- use 0 for live conversations)
  JANUS_MIN_INTERRUPTION_WORDS  words needed to cut the agent off (default 2, so
                          background noise in a recording cannot truncate an answer)
  JANUS_IDLE_PROCESSES    prewarmed job processes, each with its own models (default 1)
  JANUS_JOB_EXECUTOR      "process" (default) or "thread" (jobs share one model copy;
                          saves VRAM, but was flaky over long runs -- see _SHARED)
  JANUS_WHISPER_COMPUTE   CTranslate2 compute type on GPU (default float16; the dev
                          script uses int8_float16 to fit an 8 GB GPU)
  JANUS_DECISION_LOG_DIR  optional: one decision-log JSONL per room
  GEMINI_API_KEY, LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET

Run: python -m prism_rt.voice.agent start
(`start`, not `dev`: dev mode keeps no prewarmed process, and the benchmark
client starts streaming two seconds after it joins, without waiting.)
"""

from __future__ import annotations

import asyncio
import contextlib
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

from prism_rt.adapters.fdb_manifest import introspect_file, to_catalog_manifest
from prism_rt.adapters.fdb_tool_adapter import FdbToolAdapter
from prism_rt.adapters.voice_bridge import VoiceBridge
from prism_rt.devicecare import DeviceCareToolset
from prism_rt.entry import setup
from prism_rt.observability.xray import TracedQueue
from prism_rt.profiles import demo_config, fdb_v3_config
from prism_rt.voice.camera import FrameStore, pump_video_track
from prism_rt.voice.speech import FasterWhisperSTT, KokoroTTS, load_kokoro, load_whisper
from prism_rt.workers.gateway import GeminiProvider

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


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Toolset:
    manifest: list[dict]
    make_executor: Callable[[str], Callable[[str, dict], Awaitable[dict]]]


def fdb_toolset() -> Toolset:
    root = os.environ.get("FDB_V3_ROOT")
    if not root or not os.path.isfile(os.path.join(root, "lk_agent_tool.py")):
        raise RuntimeError("FDB_V3_ROOT must point at the Full-Duplex-Bench v3/ directory")
    manifest = to_catalog_manifest(introspect_file(os.path.join(root, "lk_agent_tool.py")))
    schemas = {t["name"]: t["params_schema"] for t in manifest}
    if root not in sys.path:
        sys.path.insert(0, root)
    mock_apis = importlib.import_module("mock_apis")
    latency = os.environ.get("JANUS_FDB_LATENCY", "instant")
    log_path = os.environ.get("JANUS_TOOL_LOG", "/tmp/agent_tool_calls.log")

    def make_executor(room_name: str):
        registry = mock_apis.MockAPIRegistry(latency_profile=latency, enable_logging=False)
        return FdbToolAdapter(registry, room_name, log_path=log_path, get_params_schema=schemas.get).execute

    return Toolset(manifest=manifest, make_executor=make_executor)


MODE = os.environ.get("JANUS_MODE", "fdb").strip().lower()
if MODE not in ("fdb", "demo"):
    raise SystemExit(f"JANUS_MODE must be 'fdb' or 'demo', not {MODE!r}")


def demo_toolset() -> Toolset:
    from prism_rt.devicecare.tools import MANIFEST

    # One toolset (its bookings, its open case) per room, never shared.
    return Toolset(manifest=MANIFEST, make_executor=lambda room_name: DeviceCareToolset().make_executor())


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
            _SHARED["whisper"] = load_whisper()
            _SHARED["kokoro"] = load_kokoro()
            _SHARED["toolset"] = demo_toolset() if MODE == "demo" else fdb_toolset()
        return _SHARED


def prewarm(proc: JobProcess) -> None:
    proc.userdata.update(_shared_models())


_load_dotenv(REPO_ROOT / ".env")

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


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    room_name = ctx.room.name
    userdata = ctx.proc.userdata
    toolset: Toolset = userdata["toolset"]
    logger.info("janus joining room %s", room_name)

    session = AgentSession(
        vad=userdata["vad"],
        stt=stt.StreamAdapter(stt=FasterWhisperSTT(userdata["whisper"]), vad=userdata["vad"]),
        tts=KokoroTTS(userdata["kokoro"]),
        min_interruption_words=_int_env("JANUS_MIN_INTERRUPTION_WORDS", 1 if MODE == "demo" else 2),
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

    async def say(text: str) -> None:
        # Queue only; never wait for playout, or a TOOL_CALL right behind an
        # acknowledgement would sit until the acknowledgement finished playing.
        session.say(text, allow_interruptions=True, add_to_chat_ctx=False)

    bridge = VoiceBridge(
        events=events,
        actions=actions,
        say=say,
        execute_tool=toolset.make_executor(room_name),
        t_eot_ms=_int_env("JANUS_EOT_MS", 1000),
    )

    # gemini-3.6-flash, thinking off (28 Sep): same judge-scored full-100 as
    # flash-lite (73%) but 3-call scenarios 50% -> 69% and it follows the
    # honesty rules flash-lite ignored live ("cancel the booking" became a
    # booking). JANUS_THINKING_BUDGET="" restores the model's own default.
    thinking = os.environ.get("JANUS_THINKING_BUDGET", "0")
    provider = GeminiProvider(
        model=os.environ.get("JANUS_LLM_MODEL", "gemini-3.6-flash"),
        thinking_budget=int(thinking) if thinking not in (None, "") else None,
    )
    frames = FrameStore()
    if MODE == "demo":
        config = demo_config(watchdog_timeout_ms=_int_env("JANUS_WATCHDOG_MS", 0))
    else:
        config = fdb_v3_config(watchdog_timeout_ms=_int_env("JANUS_WATCHDOG_MS", 105_000))
    runtime = setup(config=config, provider=provider, blob_resolver=frames.get)
    meta: dict = {"seed": 0}
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
        if ev.new_state == "speaking":
            _spawn(bridge.on_speech_start())
        elif ev.old_state == "speaking":
            _spawn(bridge.on_speech_end())

    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        bridge.on_agent_speaking_changed(ev.new_state == "speaking")

    async def _shutdown(*_args) -> None:
        await bridge.stop()
        await events.put(None)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(run_task, timeout=5)

    ctx.add_shutdown_callback(_shutdown)

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

    await session.start(room=ctx.room, agent=Agent(instructions=""))

    if MODE == "demo":
        # A camera already published before the worker joined.
        for participant in ctx.room.remote_participants.values():
            for publication in participant.track_publications.values():
                if publication.track is not None:
                    _watch_video(publication.track, publication, participant)


if __name__ == "__main__":
    cli.run_app(server)
