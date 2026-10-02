#!/usr/bin/env bash
# One-shot native (no-Docker) environment for the reproduction, for a machine that
# has an NVIDIA GPU and root but no container runtime (e.g. a managed Jupyter pod).
# Mirrors docker/fdb_v3/Dockerfile: Python 3.11 venv, PyTorch 2.14.0 built for
# CUDA 12.6 first (so nothing pulls the CUDA 13 wheel), then requirements-speech.txt.
#
#   bash scripts/fdb_v3/native_setup.sh        # ~10-20 min; safe to re-run
#   scripts/fdb_v3/native_run.sh --ids "travel_01 finance_19"
#
# Reads no keys and prints none.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
VENV="${JANUS_VENV:-$REPO/.venv-native}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu126}"
say() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }
SUDO=""; [[ $(id -u) -eq 0 ]] || SUDO="sudo"

command -v nvidia-smi >/dev/null || die "nvidia-smi not found: no NVIDIA driver in this environment"
say "GPU: $(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader)"

PY=""
for c in python3.11 python3.12 python3; do
  command -v "$c" >/dev/null || continue
  v="$("$c" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  # Ubuntu 22.04's own python3.11 package is a pre-release (3.11.0rc1) that breaks modern PyTorch.
  rc="$("$c" -c 'import sys; print(sys.version_info.releaselevel)')"
  case "$v" in 3.11|3.12) [[ "$rc" == "final" ]] && { PY="$c"; break; } ;; esac
done
if [[ -z "$PY" ]]; then
  say "no Python 3.11 or 3.12 found (the pinned requirements need >= 3.11); trying apt (deadsnakes) for 3.11"
  $SUDO apt-get update -y >/dev/null
  # gnupg is needed to add the PPA key; without it apt silently falls back to the pre-release 3.11.0rc1.
  $SUDO apt-get install -y --no-install-recommends software-properties-common ca-certificates gnupg dirmngr >/dev/null
  $SUDO add-apt-repository -y ppa:deadsnakes/ppa >/dev/null && $SUDO apt-get update -y >/dev/null
  $SUDO apt-get install -y --no-install-recommends python3.11 python3.11-venv python3.11-distutils >/dev/null
  PY=python3.11
fi
[[ "$("$PY" -c 'import sys; print(sys.version_info.releaselevel)')" == "final" ]] || die "$PY is a pre-release build; install a release Python 3.11 or 3.12 and re-run"
say "python: $($PY --version) ($(command -v $PY))"

command -v ffmpeg >/dev/null || { say "installing ffmpeg"; $SUDO apt-get update -y >/dev/null; $SUDO apt-get install -y --no-install-recommends ffmpeg git curl >/dev/null; }
command -v git >/dev/null || { $SUDO apt-get update -y >/dev/null; $SUDO apt-get install -y --no-install-recommends git >/dev/null; }

if [[ ! -x "$VENV/bin/python" ]]; then
  say "creating venv at $VENV"
  "$PY" -m venv "$VENV" || { $SUDO apt-get install -y --no-install-recommends "python$($PY -c 'import sys;print("%d.%d"%sys.version_info[:2])')-venv" >/dev/null; "$PY" -m venv "$VENV"; }
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip wheel >/dev/null

say "installing PyTorch 2.14.0 (CUDA 12.6 build) -- large download"
python -m pip install --timeout 120 --retries 10 "torch==2.14.0" --index-url "$TORCH_INDEX"
say "installing the pinned speech stack (requirements-speech.txt)"
python -m pip install --timeout 120 --retries 10 -r "$REPO/requirements-speech.txt"
python -m spacy download en_core_web_sm >/dev/null

say "verifying"
python - <<'PY'
import numpy as np, torch
assert torch.cuda.is_available(), "torch cannot see the GPU"
print("torch", torch.__version__, "cuda build", torch.version.cuda, "| gpu:", torch.cuda.get_device_name(0))
import ctranslate2 as ct
assert ct.get_cuda_device_count() >= 1, "ctranslate2 sees no GPU"
ct.StorageView.from_array(np.zeros((1, 1), dtype=np.float32)).to_device(ct.Device.cuda)
print("ctranslate2", ct.__version__, "cuda ok")
import faster_whisper, kokoro, livekit.agents  # noqa: F401
import nemo.collections.asr  # noqa: F401
print("faster-whisper, kokoro, livekit-agents, nemo asr: imports ok")
PY
say "ready. Next: scripts/fdb_v3/native_run.sh --ids \"travel_01 finance_19\""
