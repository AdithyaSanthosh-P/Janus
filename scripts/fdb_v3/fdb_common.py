"""Shared plumbing for the FDB-v3 harnesses (T3 text replay, T4 audio
replay): .env loading, importing FDB's own modules without vendoring
them, and scoring+report-writing against FDB's own, unmodified
evaluators. Factored out per docs/fdb_v3_day2_plan.md WP5's own
instruction, rather than copying run_text_replay.py's version.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_FDB_ROOT = os.environ.get(
    "FDB_V3_ROOT", os.path.expanduser("~/Desktop/Hackathons/fdb_work/Full-Duplex-Bench/v3")
)
DEFAULT_DATA_ROOT = os.environ.get(
    "FDB_V3_DATA_ROOT",
    os.path.expanduser("~/Desktop/Hackathons/fdb_work/data_dl/extracted/fdb_v3_data_released"),
)


def load_dotenv(path: Path) -> None:
    """Minimal `.env` loader: KEY=VALUE lines, skips blanks/comments,
    never overrides a variable already set in the real environment.
    Same pattern as demo/run_v1_live_demo.py."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()


def import_fdb_module(fdb_root: str, name: str):
    """Imports one of FDB's own modules by inserting its directory onto
    sys.path -- never copies or edits the file (§11 of the plan: "FDB is
    never vendored or modified")."""
    if fdb_root not in sys.path:
        sys.path.insert(0, fdb_root)
    return importlib.import_module(name)


def score_and_write_reports(
    entries: list[dict],
    *,
    fdb_root: str,
    out_dir: Path,
    benchmark_name: str,
    use_llm: bool,
) -> tuple[dict, dict]:
    """`entries`: FDB's own evaluator entry shape,
    `[{"scenario", "calls", "transcript", "result_data"}, ...]`. Scores
    with FDB's own, unmodified `evaluate_pass_rate.py`/
    `evaluate_tool_calls.py`, writes both reports into `out_dir`, prints
    a summary, and returns (pass_report, tool_report)."""
    evaluate_pass_rate = import_fdb_module(fdb_root, "evaluate_pass_rate")
    evaluate_tool_calls = import_fdb_module(fdb_root, "evaluate_tool_calls")

    benchmark_meta = {"benchmark_name": benchmark_name}
    pass_report = evaluate_pass_rate.evaluate_all_pass_rate(benchmark_meta, entries, use_llm=use_llm)
    tool_report = evaluate_tool_calls.evaluate_all_v2(benchmark_meta, entries, use_llm=use_llm)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pass_rate_report.json").write_text(json.dumps(pass_report, indent=2, default=str))
    (out_dir / "tool_calls_report.json").write_text(json.dumps(tool_report, indent=2, default=str))

    print(f"\n=== {benchmark_name} SUMMARY ===")
    print(f"scenarios run: {len(entries)}")
    print(f"strict pass rate: {pass_report['overall_pass_rate']:.1%}  ({pass_report['passed']}/{pass_report['total_scenarios']})")
    print(f"turn_take_rate: {tool_report['turn_taking']['turn_take_rate']}")
    print(f"tool_selection_acc: {tool_report['by_metric']['tool_selection_acc']}")
    print(f"argument_acc: {tool_report['by_metric']['argument_acc']}")
    print(f"reports written to {out_dir}")
    return pass_report, tool_report
