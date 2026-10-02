"""Write the provenance record for a reproduction run.

Produces, next to the results FDB's own evaluators wrote:
  manifest.json   git commit, FDB commit, model ids, seeds, the full Janus
                  profile, package versions, judge on/off, headline numbers
  PROVIDER.txt    the plain-language provider declaration the guide asks for
  SUMMARY.md      the headline numbers, human-readable

Run by scripts/fdb_v3/repro_inner.sh; safe to run by hand on any results directory.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.metadata as md
import json
import os
import platform
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

PACKAGES = [
    "livekit", "livekit-agents", "livekit-plugins-silero", "faster-whisper", "ctranslate2",
    "kokoro", "nemo_toolkit", "torch", "pydantic", "google-genai", "openai",
]


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-c", "safe.directory=*", *args], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - provenance is best effort
        return os.environ.get("JANUS_COMMIT", "unknown")


_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SOURCE_ROOTS = ("src/prism_rt", "config", "scripts/fdb_v3")


def _source_digest() -> str:
    """SHA-256 over the agent's source files (path + content, sorted): names
    the exact code even when the run directory is a copied tree with no git
    history (the 30 Sep cloud runs recorded "Janus commit: unknown")."""
    import hashlib

    h = hashlib.sha256()
    for root in _SOURCE_ROOTS:
        base = os.path.join(_REPO, root)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for name in sorted(filenames):
                if not name.endswith((".py", ".sh")):
                    continue
                path = os.path.join(dirpath, name)
                h.update(os.path.relpath(path, _REPO).encode())
                with open(path, "rb") as fh:
                    h.update(fh.read())
    return h.hexdigest()


def _versions() -> dict:
    out = {}
    for name in PACKAGES:
        try:
            out[name] = md.version(name)
        except md.PackageNotFoundError:
            out[name] = None
    return out


def _load(path: str):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--fdb-root", required=True)
    ap.add_argument("--judge", default="")
    args = ap.parse_args()

    from prism_rt.profiles import fdb_v3_config

    cfg = fdb_v3_config(watchdog_timeout_ms=int(os.environ.get("JANUS_WATCHDOG_MS", "105000")))
    llm_provider = os.environ.get("JANUS_LLM_PROVIDER", "gemini")
    model = os.environ.get("JANUS_LLM_MODEL", "gpt-4.1" if llm_provider == "openai" else "gemini-3.6-flash")
    thinking = os.environ.get("JANUS_THINKING_BUDGET", "0") if llm_provider == "gemini" else "n/a"
    stt_backend = os.environ.get("JANUS_STT", "openai")
    if stt_backend == "openai":
        stt_model = os.environ.get("JANUS_STT_MODEL") or "gpt-4o-mini-transcribe-2025-12-15"
        stt_desc = f"{stt_model} (hosted OpenAI API)"
    else:
        stt_desc = "faster-whisper large-v3-turbo (local, GPU)"
    llm_desc = f"{model} (hosted {'OpenAI' if llm_provider == 'openai' else 'Gemini'} API"

    pass_rate = _load(os.path.join(args.out, "pass_rate_report.json")) or {}
    tools = _load(os.path.join(args.out, "tool_calls_report.json")) or {}
    headline = {
        "scenarios": pass_rate.get("total_scenarios"),
        "strict_pass_rate": pass_rate.get("overall_pass_rate"),
        "passed": pass_rate.get("passed"),
        "failed": pass_rate.get("failed"),
        "by_domain": pass_rate.get("by_domain"),
        "tool_metrics": tools.get("by_metric"),
        "latency": tools.get("latency"),
    }

    manifest = {
        "janus_commit": os.environ.get("JANUS_COMMIT") or _git("rev-parse", "HEAD"),
        "janus_dirty": os.environ.get("JANUS_DIRTY", ""),
        "janus_source_sha256": _source_digest(),
        "fdb_commit": _git("-C", args.fdb_root, "rev-parse", "HEAD"),
        "judge": "on (FDB --use-llm)" if args.judge else "off (exact-match scoring only)",
        "agent": {
            "llm": model,
            "llm_thinking_budget": thinking or "model default",
            "llm_temperature": 0,
            "seed": 0,
            "llm_provider": llm_provider,
            "stt": stt_desc,
            "tts": "Kokoro-82M af_heart (local, GPU)",
            "vad": "Silero VAD (local)",
            "eot_ms": os.environ.get("JANUS_EOT_MS", "1000"),
            "whisper_compute": os.environ.get("JANUS_WHISPER_COMPUTE", "float16"),
        },
        "profile": "fdb_v3_config",
        "config": {k: v for k, v in dataclasses.asdict(cfg).items() if isinstance(v, (bool, int, float, str, type(None)))},
        "packages": _versions(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "headline": headline,
        "notes": [
            "FDB's own mock-tool latency jitter is unseeded; repeat runs differ slightly.",
            "The LLM is a hosted API: temperature 0, but hosted models are not bit-reproducible.",
        ],
    }
    with open(os.path.join(args.out, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)

    thinking_part = f", thinking budget {thinking or 'default'}" if llm_provider == "gemini" else ""
    provider = (
        "Provider declaration: custom LiveKit agent (Janus). Decisions by the Janus kernel with "
        f"{llm_desc}{thinking_part}, temperature 0); "
        f"speech: {stt_desc} (STT), Kokoro-82M (TTS, local), Silero VAD (local)."
    )
    with open(os.path.join(args.out, "PROVIDER.txt"), "w") as fh:
        fh.write(provider + "\n")

    pr = headline["strict_pass_rate"]
    lines = [
        "# Janus x FDB-v3 run",
        "",
        f"- Janus commit: `{manifest['janus_commit']}`  FDB commit: `{manifest['fdb_commit']}`",
        f"- Janus source SHA-256: `{manifest['janus_source_sha256'][:16]}`",
        f"- Judge: {manifest['judge']}",
        f"- Scenarios: {headline['scenarios']}  passed: {headline['passed']}  failed: {headline['failed']}",
        f"- Strict pass rate: {pr}",
        "",
        provider,
        "",
    ]
    with open(os.path.join(args.out, "SUMMARY.md"), "w") as fh:
        fh.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
