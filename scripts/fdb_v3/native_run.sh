#!/usr/bin/env bash
# Native (no-Docker) counterpart of ./reproduce.sh: the same run, the same
# scripts/fdb_v3/repro_inner.sh, in the venv made by native_setup.sh.
#
#   cp .env.example .env     # LIVEKIT_URL/KEY/SECRET and GEMINI_API_KEY (no OpenAI key needed)
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
missing=()
for v in LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET GEMINI_API_KEY; do [[ -n "${!v:-}" ]] || missing+=("$v"); done
[[ $JUDGE == 0 ]] && unset OPENAI_API_KEY || { [[ -n "${OPENAI_API_KEY:-}" ]] || missing+=("OPENAI_API_KEY"); }
[[ ${#missing[@]} -eq 0 ]] || die "missing in the environment or .env: ${missing[*]}"
echo "==> keys present: LiveKit, Gemini$([[ $JUDGE == 1 ]] && echo ', OpenAI judge')"

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
