#!/bin/bash
# Start the Janus demo worker (JANUS_MODE=demo) in the speech image on the GPU VM.
# The repo copy lives in ~/janus-demo (src, config, scripts, pyproject.toml, .env).
# Usage: scripts/demo_vm/run_worker.sh [extra docker -e flags]
cd ~/janus-demo
set -a; . ./.env; set +a
docker rm -f janus-demo >/dev/null 2>&1
docker run -d --name janus-demo --gpus all --network host \
  -v "$HOME/janus-demo:/janus" \
  -e LIVEKIT_URL -e LIVEKIT_API_KEY -e LIVEKIT_API_SECRET -e GEMINI_API_KEY -e OPENAI_API_KEY -e ST_PAT \
  -e JANUS_MODE=demo -e JANUS_ST_MODE=real -e JANUS_DRILLS=1 -e JANUS_STT=openai \
  -e JANUS_WATCHDOG_MS=0 -e JANUS_WORLD_POLL_S=6 -e JANUS_ST_CONFIRM_WAITS=0.5,1,1.5,2 -e JANUS_AEC_WARMUP_S=0 \
  -e JANUS_DECISION_LOG_DIR=/janus/logs "$@" \
  janus-fdb-v3:speech -c "python -m prism_rt.voice.agent start"
