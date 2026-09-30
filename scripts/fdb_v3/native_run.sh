#!/usr/bin/env bash
# Native (no-Docker) counterpart of ./reproduce.sh: the same run, the same
# scripts/fdb_v3/repro_inner.sh, in the venv made by native_setup.sh.
#
#   cp .env.example .env     # LIVEKIT_URL/KEY/SECRET, GEMINI_API_KEY and OPENAI_API_KEY (speech-to-text)
#   scripts/fdb_v3/native_run.sh --ids "travel_01 finance_19 housing_10 ecommerce_01 travel_02"   # smoke
#   scripts/fdb_v3/native_run.sh                                                                  # all 100
#
# The LLM judge is OFF unless --judge is passed (which needs OPENAI_API_KEY): score the
# results elsewhere with scripts/fdb_v3/rejudge_local.sh so that key stays on its own machine.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
VENV="${JANUS_VENV:-$REPO/.venv-native}"
IDS=""; JUDGE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ids) IDS="$2"; shift 2 ;;
    --judge) JUDGE=1; shift ;;
    -h|--help) sed -n 2,11p "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
die() { echo "ERROR: $*" >&2; exit 1; }
[[ -x "$VENV/bin/python" ]] || die "no venv at $VENV; run scripts/fdb_v3/native_setup.sh first"
if [[ -f "$REPO/.env" ]]; then set -a; source "$REPO/.env"; set +a; fi
# Same key rules as ./reproduce.sh: JANUS_LLM_PROVIDER=gemini|openai, JANUS_STT=local|openai.
# JANUS_STT=openai with OPENAI_BASE_URL pointing at scripts/fdb_v3/openai_stt_relay.py keeps
# the real OpenAI key on another machine (OPENAI_API_KEY is then only a placeholder).
LLM_PROVIDER="${JANUS_LLM_PROVIDER:-gemini}"; STT_BACKEND="${JANUS_STT:-openai}"
missing=()
for v in LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET; do [[ -n "${!v:-}" ]] || missing+=("$v"); done
case "$LLM_PROVIDER" in
  gemini) [[ -n "${GEMINI_API_KEY:-}" ]] || missing+=("GEMINI_API_KEY") ;;
  openai) [[ -n "${OPENAI_API_KEY:-}" ]] || missing+=("OPENAI_API_KEY") ;;
  *) die "JANUS_LLM_PROVIDER must be gemini or openai" ;;
esac
case "$STT_BACKEND" in
  local) ;;
  openai) [[ -n "${OPENAI_API_KEY:-}" ]] || missing+=("OPENAI_API_KEY (JANUS_STT=openai; a placeholder is fine behind the relay)") ;;
  *) die "JANUS_STT must be local or openai" ;;
esac
if [[ $JUDGE == 1 && -z "${OPENAI_API_KEY:-}" ]]; then missing+=("OPENAI_API_KEY (--judge)"); fi
if [[ $JUDGE == 0 && $LLM_PROVIDER != openai && $STT_BACKEND != openai ]]; then unset OPENAI_API_KEY; fi
[[ ${#missing[@]} -eq 0 ]] || die "missing in the environment or .env: ${missing[*]}"
export JANUS_LLM_PROVIDER="$LLM_PROVIDER" JANUS_STT="$STT_BACKEND" REPRO_JUDGE="$JUDGE"
echo "==> keys present: LiveKit; reasoning: $LLM_PROVIDER; speech-to-text: $STT_BACKEND; judge: $([[ $JUDGE == 1 ]] && echo on || echo off)"

# shellcheck disable=SC1091
source "$VENV/bin/activate"
# CTranslate2 (faster-whisper) needs the cuBLAS/cuDNN 12 libraries that PyTorch's wheels ship
# inside site-packages; the Docker image gets them from its CUDA base image instead.
NVLIBS="$(python - <<'PY'
import glob, os, site
paths = []
for sp in site.getsitepackages():
    paths += sorted(glob.glob(os.path.join(sp, "nvidia", "*", "lib")))
print(":".join(paths))
PY
)"
export LD_LIBRARY_PATH="${NVLIBS}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

STAMP="$(date +%Y%m%d_%H%M%S)"
export OUT="$REPO/results/$STAMP" FDB_CACHE_DIR="$REPO/.fdb_cache" IDS
export JANUS_COMMIT="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
export JANUS_DIRTY="$(git -C "$REPO" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
export REPRO_LOW_VRAM=0
mkdir -p "$OUT"
echo "==> run directory: $OUT (native, no Docker)"
set +e
bash "$REPO/scripts/fdb_v3/repro_inner.sh" 2>&1 | tee "$OUT/reproduce.log"
status=${PIPESTATUS[0]}
set -e
[[ $status -eq 0 ]] || die "run failed (exit $status); see $OUT/reproduce.log and $OUT/agent.log"
echo "==> done. Results: $OUT"
[[ -f "$OUT/SUMMARY.md" ]] && cat "$OUT/SUMMARY.md"
