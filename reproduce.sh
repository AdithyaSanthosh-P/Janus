#!/usr/bin/env bash
# One-command reproduction of Janus on Full-Duplex-Bench v3 (FDB-v3).
#
#   cp .env.example .env    # fill in the keys (see README "Keys")
#   ./reproduce.sh          # all 100 scenarios, LLM judge on
#
# Options:
#   --ids "travel_01 finance_19"   run only these examples (a quick smoke test)
#   --no-judge                     exact-match scoring only (the judge is then off; speech-to-text still uses OPENAI_API_KEY)
#   --skip-build                   reuse an already built image
#   --low-vram                     for GPUs < 16 GB shared with FDB's scorer
#
# Keys (.env): LIVEKIT_URL/KEY/SECRET, GEMINI_API_KEY (decisions), OPENAI_API_KEY
# (speech-to-text and the judge).
# Environment (optional): JANUS_LLM_PROVIDER=gemini|openai, JANUS_STT=openai|local.
# With both set to openai the run needs only LiveKit and OpenAI keys.
#
# What it does: checks the environment (never printing key values) ->
# builds the pinned Docker image (model weights baked in) -> inside the
# container: clones FDB at its pinned commit and downloads + sha256-checks its
# data -> starts the Janus LiveKit worker -> runs FDB's own runner and its
# three evaluators -> writes results/<timestamp>/ (reports, logs, decision
# logs, manifest.json with commit/versions/config, PROVIDER.txt).
#
# Needs: Docker with the NVIDIA container toolkit, an NVIDIA GPU (driver >= 525;
# the image uses CUDA 12 builds; measured peak ~10 GB VRAM), ~40 GB free disk,
# internet (LiveKit Cloud, Gemini API, OpenAI judge, Hugging Face at build).
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
IMAGE="${JANUS_IMAGE:-janus-fdb-v3:speech}"
IDS=""; JUDGE=1; BUILD=1; LOW_VRAM=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ids) IDS="$2"; shift 2 ;;
    --no-judge) JUDGE=0; shift ;;
    --skip-build) BUILD=0; shift ;;
    --low-vram) LOW_VRAM=1; shift ;;
    -h|--help) sed -n 2,22p "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

say() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

# 1. Environment ------------------------------------------------------------
if [[ -f "$REPO/.env" ]]; then set -a; source "$REPO/.env"; set +a; fi
# Which hosted services this run uses decides which keys it needs.
#   JANUS_LLM_PROVIDER  gemini (default) | openai   -- the model behind the kernel's decisions
#   JANUS_STT           openai (default) | local    -- speech-to-text (openai = gpt-4o-mini-transcribe; local = faster-whisper on the GPU)
LLM_PROVIDER="${JANUS_LLM_PROVIDER:-gemini}"; STT_BACKEND="${JANUS_STT:-openai}"
missing=()
for v in LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET; do [[ -n "${!v:-}" ]] || missing+=("$v"); done
case "$LLM_PROVIDER" in
  gemini) [[ -n "${GEMINI_API_KEY:-}" ]] || missing+=("GEMINI_API_KEY (or JANUS_LLM_PROVIDER=openai)") ;;
  openai) [[ -n "${OPENAI_API_KEY:-}" ]] || missing+=("OPENAI_API_KEY (JANUS_LLM_PROVIDER=openai)") ;;
  *) die "JANUS_LLM_PROVIDER must be gemini or openai" ;;
esac
case "$STT_BACKEND" in
  local) ;;
  openai) [[ -n "${OPENAI_API_KEY:-}" ]] || missing+=("OPENAI_API_KEY (speech-to-text; or JANUS_STT=local for on-GPU Whisper)") ;;
  *) die "JANUS_STT must be local or openai" ;;
esac
if [[ $JUDGE == 1 && -z "${OPENAI_API_KEY:-}" ]]; then missing+=("OPENAI_API_KEY (or pass --no-judge)"); fi
if [[ $JUDGE == 0 && $LLM_PROVIDER != openai && $STT_BACKEND != openai ]]; then unset OPENAI_API_KEY; fi
[[ ${#missing[@]} -eq 0 ]] || die "missing in the environment or .env: ${missing[*]}"
say "keys present: LiveKit; reasoning: $LLM_PROVIDER; speech-to-text: $STT_BACKEND; judge: $([[ $JUDGE == 1 ]] && echo on || echo off)"

command -v docker >/dev/null || die "docker not found"
docker info >/dev/null 2>&1 || die "cannot talk to the docker daemon (permissions? try sudo or the docker group)"

# 2. Image ---------------------------------------------------------------------
if [[ $BUILD == 1 ]]; then
  say "building $IMAGE (first build downloads ~15 GB of dependencies and ~3 GB of weights; later builds are cached)"
  docker build -f "$REPO/docker/fdb_v3/Dockerfile" -t "$IMAGE" "$REPO"
else
  docker image inspect "$IMAGE" >/dev/null 2>&1 || die "image $IMAGE not found; run without --skip-build"
fi

# 3. GPU -------------------------------------------------------------------------
say "checking the GPU inside the container"
docker run --rm --gpus all "$IMAGE" -c \
  "nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader && python -c 'import torch,sys; sys.exit(0 if torch.cuda.is_available() else 3)'" \
  || die "no usable GPU in the container (an NVIDIA driver >= 525 and the NVIDIA container toolkit are required)"

# 4. Run -------------------------------------------------------------------------
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$REPO/results/$STAMP"
mkdir -p "$OUT"
COMMIT="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
DIRTY="$(git -C "$REPO" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
say "run directory: $OUT   (commit ${COMMIT:0:8}, $DIRTY uncommitted files)"

set +e
docker run --rm --gpus all --network host \
  -v "$REPO:/janus" \
  -e OUT="/janus/results/$STAMP" -e FDB_CACHE_DIR=/janus/.fdb_cache \
  -e IDS="$IDS" -e REPRO_LOW_VRAM="$LOW_VRAM" \
  -e JANUS_COMMIT="$COMMIT" -e JANUS_DIRTY="$DIRTY" \
  -e LIVEKIT_URL -e LIVEKIT_API_KEY -e LIVEKIT_API_SECRET -e GEMINI_API_KEY \
  -e OPENAI_API_KEY -e REPRO_JUDGE="$JUDGE" \
  -e JANUS_LLM_PROVIDER="$LLM_PROVIDER" -e JANUS_STT="$STT_BACKEND" \
  ${OPENAI_BASE_URL:+-e OPENAI_BASE_URL} ${JANUS_STT_MODEL:+-e JANUS_STT_MODEL} \
  ${JANUS_WHISPER_COMPUTE:+-e JANUS_WHISPER_COMPUTE} ${JANUS_LLM_MODEL:+-e JANUS_LLM_MODEL} \
  "$IMAGE" -c "bash /janus/scripts/fdb_v3/repro_inner.sh" 2>&1 | tee "$OUT/reproduce.log"
status=${PIPESTATUS[0]}
set -e

# Files written from inside the container are root-owned; hand them back.
docker run --rm -v "$REPO:/janus" "$IMAGE" -c \
  "chown -R $(id -u):$(id -g) /janus/results /janus/.fdb_cache 2>/dev/null || true" >/dev/null 2>&1 || true

[[ $status -eq 0 ]] || die "run failed (exit $status); see $OUT/reproduce.log and $OUT/agent.log"
say "done. Results: $OUT"
[[ -f "$OUT/SUMMARY.md" ]] && cat "$OUT/SUMMARY.md"
