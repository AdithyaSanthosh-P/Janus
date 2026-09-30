"""T4 offline audio-replay harness (Day 2 WP5, docs/fdb_v3_day2_plan.md):
replays each FDB-v3 recording's *real* speech timing (segments and
mid-request pauses, from whisper_segment.py's precomputed output)
through VoiceBridge into Janus's real async entry point -- live Gemini,
FDB's own mock tools -- instead of T3's "whole transcript as one
chunk." Scored the same way as T3, via FDB's own unmodified evaluators.

Two-stage pipeline (see whisper_segment.py's own docstring for why):
    1. whisper_segment.py (run inside the janus-invest-stt container,
       which has faster-whisper + the cached model weights) writes one
       <folder>.segments.json per recording.
    2. This script (run locally, against live Gemini) reads those and
       replays them at real relative timing through VoiceBridge.

Run:
    cd /home/adi/Desktop/Hackathons/Prism
    source .venv/bin/activate
    PYTHONPATH=src:. python scripts/fdb_v3/run_audio_replay.py \
        --segments-dir ~/Desktop/Hackathons/fdb_work/whisper_segments --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "fdb_v3"))

from fdb_common import DEFAULT_DATA_ROOT, DEFAULT_FDB_ROOT, import_fdb_module, load_dotenv, score_and_write_reports  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from prism_rt.adapters.fdb_manifest import introspect_file, to_catalog_manifest  # noqa: E402
from prism_rt.adapters.fdb_tool_adapter import FdbToolAdapter  # noqa: E402
from prism_rt.adapters.voice_bridge import VoiceBridge  # noqa: E402
from prism_rt.entry import setup  # noqa: E402
from prism_rt.profiles import fdb_v3_config  # noqa: E402
from prism_rt.workers.gateway import GeminiProvider  # noqa: E402


async def replay_one_recording(
    segments_path: Path,
    data_root: Path,
    manifest: list[dict],
    provider,
    config,
    mock_apis_module,
    *,
    speed: float,
    seed: int,
    final_timeout_s: float,
) -> dict:
    data = json.loads(segments_path.read_text())
    folder = data["folder"]
    segments = data["segments"]
    metadata = json.loads((data_root / folder / "metadata.json").read_text())
    room_name = f"eval-{folder}"

    manifest_by_name = {t["name"]: t["params_schema"] for t in manifest}
    log_path = f"/tmp/janus_fdb_audio_replay_{folder}.log"
    if os.path.exists(log_path):
        os.remove(log_path)
    registry = mock_apis_module.MockAPIRegistry(latency_profile="instant", enable_logging=False)
    tool_adapter = FdbToolAdapter(registry, room_name, log_path=log_path, get_params_schema=manifest_by_name.get)

    events: "asyncio.Queue[dict | None]" = asyncio.Queue()
    actions: "asyncio.Queue[dict]" = asyncio.Queue()
    runtime = setup(config=config, provider=provider)

    spoken: list[str] = []
    final_event = asyncio.Event()

    async def say(text: str) -> None:
        spoken.append(text)

    async def execute_tool(tool_name: str, args: dict) -> dict:
        return await tool_adapter.execute(tool_name, args)

    bridge = VoiceBridge(
        events=events, actions=actions, say=say, execute_tool=execute_tool,
        t_eot_ms=1000, on_final=final_event.set,
    )
    bridge.start()
    await bridge.on_manifest(manifest)

    run_task = asyncio.create_task(runtime.run_scenario(events, actions, meta={"seed": seed}))

    t0 = time.monotonic()
    for seg in segments:
        # "emit each segment's final at segment_end + measured STT
        # latency" (docs/fdb_v3_day2_plan.md WP5) -- the +0.15s below is
        # the faster-whisper batch-transcription latency measured last
        # session; real-time streaming STT would emit sooner, but this
        # is a reasonable stand-in until WP6's live STT is wired up.
        target_wall = t0 + seg["end"] / speed + 0.15
        now = time.monotonic()
        if target_wall > now:
            await asyncio.sleep(target_wall - now)
        await bridge.on_segment_final(seg["text"])

    try:
        await asyncio.wait_for(final_event.wait(), timeout=final_timeout_s)
    except asyncio.TimeoutError:
        pass

    await asyncio.sleep(0.2)  # grace period for a trailing SPEAK just before/with FINAL
    await events.put(None)
    summary = await run_task
    await bridge.stop()

    actual_calls = []
    if os.path.exists(log_path):
        with open(log_path) as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    actual_calls.append({"function": entry["call"]["function"], "args": entry["call"]["args"]})

    return {
        "example_id": metadata["id"],
        "folder": folder,
        "actual_tool_calls": actual_calls,
        "transcript": " ".join(spoken),
        "watchdog_fired": summary.watchdog_fired,
        "final_reached": final_event.is_set(),
        "metadata": metadata,
    }


async def main_async(args: argparse.Namespace) -> None:
    manifest_tools = introspect_file(os.path.join(args.fdb_root, "lk_agent_tool.py"))
    manifest = to_catalog_manifest(manifest_tools)
    mock_apis_module = import_fdb_module(args.fdb_root, "mock_apis")
    provider = GeminiProvider(model=args.model)
    config = fdb_v3_config(log_decisions=args.debug)

    segments_dir = Path(args.segments_dir)
    data_root = Path(args.data_root)
    seg_files = sorted(segments_dir.glob("*.segments.json"))
    if args.only:
        seg_files = [f for f in seg_files if args.only in f.name]
    if args.limit:
        seg_files = seg_files[: args.limit]
    print(f"Replaying {len(seg_files)} recordings from {segments_dir}")

    per_recording: list[dict] = []
    entries: list[dict] = []
    for i, seg_file in enumerate(seg_files):
        print(f"[{i + 1}/{len(seg_files)}] {seg_file.stem} ...", end=" ", flush=True)
        t0 = time.monotonic()
        try:
            result = await replay_one_recording(
                seg_file, data_root, manifest, provider, config, mock_apis_module,
                speed=args.speed, seed=i, final_timeout_s=args.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - one recording's crash must not kill the whole run
            print(f"CRASHED: {exc!r}")
            continue
        elapsed = time.monotonic() - t0
        print(f"{elapsed:.1f}s, {len(result['actual_tool_calls'])} calls, final_reached={result['final_reached']}")
        per_recording.append(result)
        entries.append({
            "scenario": result["metadata"],
            "calls": result["actual_tool_calls"],
            "transcript": result["transcript"],
            "result_data": result,
        })

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "raw_results.json").write_text(json.dumps(per_recording, indent=2, default=str))

    if entries:
        score_and_write_reports(
            entries, fdb_root=args.fdb_root, out_dir=out_dir,
            benchmark_name="T4 audio replay", use_llm=args.use_llm,
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Janus T4 offline audio-replay harness")
    p.add_argument("--segments-dir", required=True, help="output of whisper_segment.py")
    p.add_argument("--fdb-root", default=DEFAULT_FDB_ROOT)
    p.add_argument("--data-root", default=DEFAULT_DATA_ROOT)
    p.add_argument("--model", default="gemini-3.5-flash-lite")
    p.add_argument("--speed", type=float, default=1.0, help="replay speed multiplier; >1 replays faster than real time")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--only", default=None)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--use-llm", action="store_true")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--out-dir", default=str(REPO_ROOT / "run_output" / "fdb_v3_audio_replay"))
    return p.parse_args()


def main() -> None:
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
