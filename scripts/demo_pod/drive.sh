#!/bin/bash
# Run scripts/demo_driver.py (a scripted spoken user) on a GPU pod without Docker.
# Usage: scripts/demo_pod/drive.sh scenario[,scenario] [driver args]
JANUS_HOME="${JANUS_HOME:-/workspace/janus-demo}"
VENV="${JANUS_VENV:-/workspace/venv}"
cd "$JANUS_HOME" || exit 1
set -a; . ./.env; set +a
export HF_HOME="${HF_HOME:-/workspace/hf}" PYTHONPATH="$JANUS_HOME/src:$JANUS_HOME" PYTHONUNBUFFERED=1
"$VENV/bin/python" scripts/demo_driver.py "$@" 2>&1 | grep -vE 'huggingface|Fetching|Warning|warn|weight_norm|LSTM|WeightNorm|Nvidia'
