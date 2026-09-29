#!/usr/bin/env bash
# Re-scores a finished ./reproduce.sh run with FDB's LLM judge, on a machine that
# holds OPENAI_API_KEY. For runs made elsewhere with --no-judge, so the judge key
# never leaves the machine it belongs to: copy results/<timestamp>/ here, then
#
#   OPENAI_API_KEY=... scripts/fdb_v3/rejudge_local.sh <results-dir> [fdb-v3-checkout]
#
# Writes pass_rate_report.judged.json and tool_calls_report.judged.json next to
# the exact-match reports; nothing else in the results directory is modified.
set -euo pipefail
RUN="${1:?usage: rejudge_local.sh <results-dir> [fdb-v3-checkout]}"
FDB="${2:-${FDB_V3_ROOT:-}}"
[[ -d "$RUN/data" ]] || { echo "no data/ directory in $RUN" >&2; exit 2; }
[[ -f "$FDB/evaluate_pass_rate.py" ]] || { echo "FDB v3 checkout not found (pass it as the 2nd argument or set FDB_V3_ROOT)" >&2; exit 2; }
[[ -n "${OPENAI_API_KEY:-}" ]] || { echo "OPENAI_API_KEY is not set" >&2; exit 2; }
python - <<'PY' || { echo "the 'openai' package is required (pip install openai)" >&2; exit 2; }
import openai
PY
RUN="$(cd "$RUN" && pwd)"
cd "$FDB"
python evaluate_pass_rate.py --benchmark benchmark_data_v2.json --results-dir "$RUN/data" \
  --provider janus --output "$RUN/pass_rate_report.judged.json" --use-llm | tee "$RUN/eval_pass_rate.judged.log"
python evaluate_tool_calls.py --benchmark benchmark_data_v2.json --results-dir "$RUN/data" \
  --provider janus --output "$RUN/tool_calls_report.judged.json" --use-llm | tee "$RUN/eval_tool_calls.judged.log"
echo "judged reports written to $RUN"
