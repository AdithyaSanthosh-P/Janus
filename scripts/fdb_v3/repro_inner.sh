#!/usr/bin/env bash
# The body of the one-command reproduction; runs INSIDE the janus-fdb-v3:speech
# container (reproduce.sh at the repo root starts it) -- or natively, in a venv
# that has the same dependencies (see README "Native fallback").
#
# The Janus worker and FDB's own runner share this one container on purpose:
# FDB's runner reads the agent's tool calls from /tmp/agent_tool_calls.log.
#
# Inputs (environment):
#   OUT               results directory for this run (required)
#   FDB_CACHE_DIR     where FDB is cloned / its data extracted (default $OUT/../.fdb_cache)
#   IDS               space-separated example ids (e.g. "travel_01 finance_19"); empty = all 100
#   LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET   (required)
#   GEMINI_API_KEY    the kernel's model (JANUS_LLM_PROVIDER=gemini, the default)
#   OPENAI_API_KEY    FDB's LLM judge, and JANUS_LLM_PROVIDER=openai / JANUS_STT=openai
#   REPRO_JUDGE       1/0: run FDB's LLM judge (default: on when OPENAI_API_KEY is set)
#   REPRO_LOW_VRAM=1  dev-laptop workaround (CPU-first NeMo checkpoint load); not needed on >= 16 GB
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
: "${OUT:?OUT is required}"
CACHE="${FDB_CACHE_DIR:-$(dirname "$OUT")/.fdb_cache}"
export PYTHONPATH="$REPO/src:$REPO${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$OUT/data" "$OUT/decisions" "$CACHE"

log() { echo "[repro $(date +%H:%M:%S)] $*"; }

# 1. FDB source (pinned commit) and data (sha256-checked); nothing is vendored.
log "fetching Full-Duplex-Bench at the pinned commit"
bash "$REPO/scripts/fdb_v3/fetch_fdb.sh" "$CACHE" | tee "$OUT/fetch.log"
FDB_ROOT="$CACHE/Full-Duplex-Bench/v3"
DATA_ROOT="$FDB_ROOT/fdb_v3_data_released"
export FDB_V3_ROOT="$FDB_ROOT"

# 2. FDB reads its LiveKit credentials from v3/.env.local
umask 077
{
  echo "LIVEKIT_URL=$LIVEKIT_URL"
  echo "LIVEKIT_API_KEY=$LIVEKIT_API_KEY"
  echo "LIVEKIT_API_SECRET=$LIVEKIT_API_SECRET"
  echo "OPENAI_API_KEY=${OPENAI_API_KEY:-}"
} > "$FDB_ROOT/.env.local"
umask 022

# 3. Weights are baked into the image; this is a no-op there and a one-time
#    download anywhere else.
log "checking model weights"
python "$REPO/scripts/fdb_v3/prefetch_models.py" > "$OUT/prefetch.log" 2>&1

# 4. One real directory per example, with only the two raw inputs symlinked in.
#    FDB writes its derived files (input_mono.wav, output_janus.wav,
#    result_janus.json) next to the input -- never into the shared dataset --
#    and its evaluators find results with Path.rglob(), which skips symlinked
#    directories.
link_example() {
  local d="$1" dest="$OUT/data/$(basename "$1")"
  mkdir -p "$dest"
  for f in input.wav metadata.json; do [[ -f "$d/$f" ]] && ln -sf "$d/$f" "$dest/$f"; done
}
if [[ -z "${IDS:-}" ]]; then
  for d in "$DATA_ROOT"/*/; do link_example "${d%/}"; done
else
  for id in $IDS; do
    found=0
    for d in "$DATA_ROOT/${id}_"*/; do [[ -d "$d" ]] && link_example "${d%/}" && found=1; done
    [[ $found == 1 ]] || { log "no recording for $id"; exit 1; }
  done
fi
log "recordings: $(ls "$OUT/data" | wc -l)"

# 5. The Janus worker, decision log per room.
rm -f /tmp/agent_tool_calls.log
export JANUS_DECISION_LOG_DIR="$OUT/decisions"
export JANUS_WHISPER_COMPUTE="${JANUS_WHISPER_COMPUTE:-float16}"
# Silence that closes a user turn. 1000 ms split mid-sentence pauses ("the ID
# is A B ... C 1 2 3") into separate turns in the 29-30 Sep run; 1500 ms costs
# ~0.5 s of latency per turn. Override with JANUS_EOT_MS.
export JANUS_EOT_MS="${JANUS_EOT_MS:-1500}"
# GPU memory every 10 s for the whole run: the 29-30 Sep run lost its GPU
# Whisper partway through with nothing in the logs to say why.
GPU_MON_PID=""
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=timestamp,memory.used,memory.total,utilization.gpu --format=csv -l 10 \
    > "$OUT/gpu_mem.csv" 2>/dev/null &
  GPU_MON_PID=$!
fi
cd "$REPO"
python -m prism_rt.voice.agent start > "$OUT/agent.log" 2>&1 &
AGENT_PID=$!
trap 'kill $AGENT_PID $GPU_MON_PID 2>/dev/null || true' EXIT

log "waiting for the worker to register (model load can take a few minutes)"
for _ in $(seq 1 240); do
  grep -qi "registered worker" "$OUT/agent.log" && break
  kill -0 $AGENT_PID 2>/dev/null || { tail -40 "$OUT/agent.log"; log "worker exited early"; exit 1; }
  sleep 2
done
grep -qi "registered worker" "$OUT/agent.log" || { tail -40 "$OUT/agent.log"; log "worker never registered"; exit 1; }
log "worker registered"

# 6. Stream every recording through the worker with FDB's own client.
SHIM=()
[[ "${REPRO_LOW_VRAM:-0}" == "1" ]] && SHIM=(env "PYTHONPATH=$REPO/scripts/fdb_v3/cpu_asr_shim")
cd "$FDB_ROOT"
"${SHIM[@]}" python run_tool_benchmark_all_released.py --provider janus --root_dir "$OUT/data" --force \
  | tee "$OUT/runner.log"

# 6b. FDB's client (livekit_inference.py) sometimes dies with SIGABRT (exit -6)
#     and the recording is written as "inference_failed" with nothing scored:
#     2 of 26 on 2 Oct, 9 across all runs. Stream those recordings again, at
#     most twice; without --force the runner skips everything already done.
#     Every retried recording is listed in retried.txt.
for attempt in 1 2; do
  failed=()
  for r in "$OUT"/data/*/result_janus.json; do
    [[ -f "$r" ]] || continue
    grep -q '"status": "inference_failed"' "$r" && failed+=("$(dirname "$r")")
  done
  [[ ${#failed[@]} -eq 0 ]] && break
  log "retry $attempt: ${#failed[@]} recording(s) whose client aborted"
  for d in "${failed[@]}"; do
    echo "attempt $attempt $(basename "$d")" >> "$OUT/retried.txt"
    rm -f "$d/result_janus.json" "$d/output_janus.wav"
  done
  "${SHIM[@]}" python run_tool_benchmark_all_released.py --provider janus --root_dir "$OUT/data" \
    | tee -a "$OUT/runner.log"
done

# 7. FDB's evaluators. REPRO_JUDGE (set by reproduce.sh / native_run.sh) decides whether
#    the LLM judge runs; an OpenAI key may be present for speech-to-text alone.
if [[ -n "${REPRO_JUDGE:-}" ]]; then
  USE_LLM=""; [[ "$REPRO_JUDGE" == "1" ]] && USE_LLM="--use-llm"
else
  USE_LLM=""; [[ -n "${OPENAI_API_KEY:-}" ]] && USE_LLM="--use-llm"
fi
python evaluate_pass_rate.py --benchmark benchmark_data_v2.json --results-dir "$OUT/data" \
  --provider janus --output "$OUT/pass_rate_report.json" $USE_LLM | tee "$OUT/eval_pass_rate.log"
python evaluate_tool_calls.py --benchmark benchmark_data_v2.json --results-dir "$OUT/data" \
  --provider janus --output "$OUT/tool_calls_report.json" $USE_LLM | tee "$OUT/eval_tool_calls.log"
python analyze_tool_latency.py --results-dir "$OUT/data" --provider janus \
  | tee "$OUT/eval_latency.log" || log "latency analysis failed (non-fatal)"

# 8. Provenance: exactly what produced these numbers.
cd "$REPO"
python scripts/fdb_v3/write_run_manifest.py --out "$OUT" --fdb-root "$FDB_ROOT" \
  --judge "${USE_LLM:+on}" > "$OUT/manifest.log" 2>&1 || log "manifest failed (non-fatal)"

# 9. A few-thousand-token digest of the run, so nobody has to read the raw logs.
python "$REPO/scripts/fdb_v3/run_digest.py" "$OUT" --out "$OUT/digest.md" > /dev/null 2>&1 || log "digest failed (non-fatal)"

log "done -> $OUT"
