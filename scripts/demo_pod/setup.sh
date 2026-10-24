#!/bin/bash
# One-time setup of the demo worker on a GPU pod without Docker (a container
# host with SSH, e.g. a Jupyter/PyTorch template). Mirrors the speech image
# (docker/fdb_v3/Dockerfile): the same package versions, the same model
# weights, in a venv and a Hugging Face cache on the persistent volume.
# The repo copy lives in $JANUS_HOME (src, config, scripts, pyproject.toml, .env).
# Usage: scripts/demo_pod/setup.sh        (log: $JANUS_HOME/setup.log)
# Hosts that keep only /home across a pause (e.g. JarvisLabs): link /workspace to
# /home first (`ln -sfn /home /workspace`), and set JANUS_PYTHON to a Python >= 3.11
# kept under /home (`conda create -y -p /home/py311 -c conda-forge python=3.11`).
set -euo pipefail
JANUS_HOME="${JANUS_HOME:-/workspace/janus-demo}"
VENV="${JANUS_VENV:-/workspace/venv}"
export HF_HOME="${HF_HOME:-/workspace/hf}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-/workspace/pip-cache}"
# The venv's base Python: 3.11 or 3.12 (the pinned numpy needs >= 3.11). On a host
# whose system Python is older, point this at one kept on the persistent disk.
PYTHON="${JANUS_PYTHON:-python3}"
cd "$JANUS_HOME"

echo "== system packages $(date -u +%T)"
apt-get update -qq && apt-get install -y -qq --no-install-recommends ffmpeg python3-venv python3-dev build-essential >/dev/null

echo "== venv $(date -u +%T)"
[[ -x "$VENV/bin/python" ]] || "$PYTHON" -m venv "$VENV"
PY="$VENV/bin/python"
"$PY" -m pip install -q --upgrade pip wheel setuptools

echo "== torch $(date -u +%T)"
"$PY" -m pip install -q --timeout 120 --retries 10 "torch==2.14.0" --index-url https://download.pytorch.org/whl/cu126

echo "== livekit, nemo $(date -u +%T)"
"$PY" -m pip install -q --timeout 120 --retries 10 \
    "livekit-agents[openai,google,xai]==1.8.3" \
    "livekit-plugins-silero==1.8.3" \
    "livekit-plugins-openai==1.8.3" \
    "livekit[crypto]==1.1.18" \
    "nemo_toolkit[asr]==3.0.0" \
    "openai==2.54.0" \
    "pydub==0.25.1" "ffmpeg-python==0.2.0" "numpy==2.4.6" "python-dotenv==1.2.3"

echo "== speech $(date -u +%T)"
"$PY" -m pip install -q --timeout 120 --retries 10 \
    "faster-whisper==1.2.1" "ctranslate2==4.8.2" \
    "kokoro==0.9.4" "misaki[en]==0.9.4" "spacy==3.8.16" "soundfile==0.14.0"
"$PY" -m spacy download en_core_web_sm >/dev/null

echo "== janus deps $(date -u +%T)"
"$PY" -m pip install -q pydantic PyYAML

echo "== model weights $(date -u +%T)"
export PYTHONPATH="$JANUS_HOME/src:$JANUS_HOME"
JANUS_SPEECH_PY="$JANUS_HOME/src/prism_rt/voice/speech.py" "$PY" scripts/fdb_v3/prefetch_models.py
"$PY" -m prism_rt.voice.agent download-files || true

echo "== check $(date -u +%T)"
"$PY" -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0))"
"$PY" -c "import nemo, kokoro, livekit.agents; print('nemo', nemo.__version__, 'livekit-agents', livekit.agents.__version__)"
echo "== setup done $(date -u +%T)"
