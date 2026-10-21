#!/bin/bash
# Run scripts/demo_driver.py (a scripted spoken user) in the speech image on the GPU VM.
# Usage: scripts/demo_vm/drive.sh scenario[,scenario] [driver args]
cd ~/janus-demo
set -a; . ./.env; set +a
docker run --rm --gpus all --network host -v "$HOME/janus-demo:/janus" \
  -e LIVEKIT_URL -e LIVEKIT_API_KEY -e LIVEKIT_API_SECRET \
  janus-fdb-v3:speech -c "python scripts/demo_driver.py $* 2>&1 | grep -vE 'huggingface|Fetching|Warning|warn|weight_norm|LSTM|WeightNorm|Nvidia'"
