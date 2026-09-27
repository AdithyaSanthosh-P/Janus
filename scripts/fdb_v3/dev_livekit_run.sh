#!/usr/bin/env bash
# Development loop: stream chosen FDB-v3 recordings through the Janus LiveKit
# worker with FDB's own client, then score them with FDB's own evaluators.
#
#   scripts/fdb_v3/dev_livekit_run.sh travel_01 finance_19 ...     # by example id
#   scripts/fdb_v3/dev_livekit_run.sh --all                        # every recording
#
# LiveKit: uses LIVEKIT_URL/LIVEKIT_API_KEY/LIVEKIT_API_SECRET from the
# environment or .env; if LIVEKIT_URL is unset it starts a local dev server.
# The agent and FDB's runner share one container, so both see the same /tmp
# (FDB reads tool calls from /tmp/agent_tool_calls.log).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
FDB_ROOT="${FDB_V3_ROOT:-$HOME/Desktop/Hackathons/fdb_work/Full-Duplex-Bench/v3}"
DATA_ROOT="${FDB_V3_DATA_ROOT:-$HOME/Desktop/Hackathons/fdb_work/data_dl/extracted/fdb_v3_data_released}"
IMAGE="${JANUS_IMAGE:-janus-fdb-v3:speech}"
AGENT=janus-agent-dev

if [[ -f "$REPO/.env" ]]; then set -a; source "$REPO/.env"; set +a; fi

if [[ -z "${LIVEKIT_URL:-}" ]]; then
  if ! docker ps --format '{{.Names}}' | grep -qx janus-lk-dev; then
    docker run -d --rm --name janus-lk-dev --network host livekit/livekit-server --dev --bind 0.0.0.0 >/dev/null
    sleep 2
  fi
  export LIVEKIT_URL=ws://127.0.0.1:7880 LIVEKIT_API_KEY=devkey LIVEKIT_API_SECRET=secret
fi

OUT="$REPO/run_output/livekit_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT/data" "$OUT/tmp" "$OUT/decisions"
# Each example gets its own REAL directory here (not a symlinked recording
# folder) with only the two raw inputs (input.wav, metadata.json) symlinked
# in. FDB's own runner writes its derived outputs (input_mono.wav,
# output_janus.wav, result_*.json) next to whatever it's given -- pointing it
# straight at the released dataset folder would write those files into the
# canonical, shared dataset directory itself (confirmed: it does, as root,
# permanently). This also matters for scoring: FDB's evaluators discover
# results via Path.rglob(), which does not descend into symlinked
# directories, so a symlinked recording folder makes every result
# invisible to them even though it's the right file (found the hard way).
_link_example() {
  local d="$1" name dest
  name="$(basename "$d")"
  dest="$OUT/data/$name"
  mkdir -p "$dest"
  for f in input.wav metadata.json; do
    [[ -f "$d/$f" ]] && ln -s "$d/$f" "$dest/$f"
  done
}
if [[ "${1:-}" == "--all" ]]; then
  for d in "$DATA_ROOT"/*/; do _link_example "${d%/}"; done
else
  for id in "$@"; do
    found=0
    for d in "$DATA_ROOT/${id}_"*/; do [[ -d "$d" ]] && _link_example "${d%/}" && found=1; done
    [[ $found == 1 ]] || { echo "no recording for $id" >&2; exit 1; }
  done
fi
echo "recordings: $(ls "$OUT/data" | wc -l)  -> $OUT"

# Host paths are mounted at the same paths inside the container, so the
# symlinks in $OUT/data and every path handed to FDB's scripts resolve as-is.
MOUNTS=(-v "$REPO:/janus" -v "$REPO:$REPO" -v "$FDB_ROOT:$FDB_ROOT")
case "$DATA_ROOT" in "$FDB_ROOT"/*) ;; *) MOUNTS+=(-v "$DATA_ROOT:$DATA_ROOT") ;; esac

docker rm -f "$AGENT" >/dev/null 2>&1 || true
docker run -d --name "$AGENT" --gpus all --network host \
  "${MOUNTS[@]}" \
  -v janus_invest_hf:/root/.cache/huggingface \
  -v "$OUT/tmp:/tmp" \
  -e LIVEKIT_URL -e LIVEKIT_API_KEY -e LIVEKIT_API_SECRET -e GEMINI_API_KEY -e OPENAI_API_KEY \
  -e FDB_V3_ROOT="$FDB_ROOT" -e JANUS_DECISION_LOG_DIR=/janus/run_output/"$(basename "$OUT")"/decisions \
  "$IMAGE" -c "cd /janus && python -m prism_rt.voice.agent start" >/dev/null

echo -n "waiting for the worker to register"
for _ in $(seq 1 180); do
  if docker logs "$AGENT" 2>&1 | grep -qi "registered worker"; then echo " ok"; break; fi
  if ! docker ps --format '{{.Names}}' | grep -qx "$AGENT"; then echo; docker logs "$AGENT" | tail -40; exit 1; fi
  echo -n "."; sleep 2
done

# FDB's own scorer loads a second ASR model (NeMo Parakeet, fp32) purely to
# transcribe recorded audio for its report. Its steady state (~2.5 GB) fits
# next to the agent's Whisper+Kokoro on an 8 GB GPU, but NeMo's checkpoint
# restore briefly holds two copies (~5 GB) and OOM'd. The shim makes
# torch.load land the checkpoint on the CPU first, so the GPU only ever
# holds one copy -- same fp32 precision as the organizers' own scoring.
# (Running the scorer on the CPU instead moved ~5 GB into system RAM and
# got the terminal OOM-killed on this 14 GB machine.)
docker exec -e LIVEKIT_URL -e LIVEKIT_API_KEY -e LIVEKIT_API_SECRET \
  -e PYTHONPATH="$REPO/scripts/fdb_v3/cpu_asr_shim" "$AGENT" bash -c \
  "cd '$FDB_ROOT' && python run_tool_benchmark_all_released.py --provider janus --root_dir '$OUT/data' --force" \
  | tee "$OUT/runner.log"

USE_LLM=""; [[ -n "${OPENAI_API_KEY:-}" ]] && USE_LLM="--use-llm"
docker exec -e OPENAI_API_KEY "$AGENT" bash -c "cd '$FDB_ROOT' && \
  python evaluate_pass_rate.py --benchmark benchmark_data_v2.json --results-dir '$OUT/data' --provider janus --output '$OUT/pass_rate_report.json' $USE_LLM && \
  python evaluate_tool_calls.py --benchmark benchmark_data_v2.json --results-dir '$OUT/data' --provider janus --output '$OUT/tool_calls_report.json' $USE_LLM" \
  | tee "$OUT/eval.log" || true

docker logs "$AGENT" > "$OUT/agent.log" 2>&1 || true
docker rm -f "$AGENT" >/dev/null
echo "done: $OUT"
