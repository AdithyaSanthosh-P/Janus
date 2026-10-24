#!/bin/bash
# Start (or restart) the Janus demo worker on a GPU pod without Docker, with
# the same settings as scripts/demo_vm/run_worker.sh. Run scripts/demo_pod/setup.sh once first.
# Extra settings as NAME=value arguments; the last one wins:
#   scripts/demo_pod/run_worker.sh JANUS_STT=parakeet HF_HUB_OFFLINE=1
# Log: $JANUS_HOME/logs/worker.log
JANUS_HOME="${JANUS_HOME:-/workspace/janus-demo}"
VENV="${JANUS_VENV:-/workspace/venv}"
cd "$JANUS_HOME" || exit 1
mkdir -p logs
pkill -f "prism_rt.voice.agent start" 2>/dev/null && sleep 3
set -a; . ./.env; set +a
export HF_HOME="${HF_HOME:-/workspace/hf}" PYTHONPATH="$JANUS_HOME/src:$JANUS_HOME" PYTHONUNBUFFERED=1
export JANUS_MODE=demo JANUS_ST_MODE=real JANUS_DRILLS=1 JANUS_STT=openai \
  JANUS_WATCHDOG_MS=0 JANUS_WORLD_POLL_S=6 JANUS_ST_CONFIRM_WAITS=0.5,1,1.5,2 JANUS_AEC_WARMUP_S=0 \
  JANUS_DECISION_LOG_DIR="$JANUS_HOME/logs"
for setting in "$@"; do export "$setting"; done
nohup "$VENV/bin/python" -m prism_rt.voice.agent start > logs/worker.log 2>&1 &
echo "worker started (pid $!); log: $JANUS_HOME/logs/worker.log"
