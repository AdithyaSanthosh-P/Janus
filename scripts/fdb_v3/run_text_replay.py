"""T3 text-replay harness (docs/fdb_v3_implementation_plan.md §7, Day-1
exit test): feeds each Full-Duplex-Bench v3 scenario's ground-truth
transcript through Janus's real async entry point (`entry.py.Runtime.
run_scenario` -- no mocks, no ScriptedProvider, no LiveKit, no audio),
executes every admitted tool call against FDB's own, unmodified
`mock_apis.MockAPIRegistry`, and scores the result with FDB's own,
unmodified `evaluate_pass_rate.py` / `evaluate_tool_calls.py` (imported
directly as Python, never edited or vendored).

Known scope trim, documented not silently skipped: the plan's own
wording (§7, T3) asks for transcripts "segmented with real word
timings" -- this first version feeds each scenario's whole transcript as
one chunk followed by one end_of_turn, i.e. no mid-utterance pause
simulation. That's still the right tool for what this script measures
(today's default EOT-anchored pipeline's tool-selection/argument
accuracy on real recordings' transcripts); the settle/pause policy
itself is a Day-2 item (plan §5) this script doesn't exercise.

Requires GEMINI_API_KEY (env or repo-root .env). Uses the real Gemini
API -- costs real quota; use --limit while iterating.

Run:
    cd /home/adi/Desktop/Hackathons/Prism
    source .venv/bin/activate
    PYTHONPATH=src:. python scripts/fdb_v3/run_text_replay.py --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))


def _load_dotenv(path: Path) -> None:
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


_load_dotenv(REPO_ROOT / ".env")

from prism_rt.adapters.fdb_manifest import introspect_file, to_catalog_manifest  # noqa: E402
from prism_rt.adapters.fdb_tool_adapter import FdbToolAdapter, fill_schema_defaults  # noqa: E402
from prism_rt.config import Config  # noqa: E402
from prism_rt.profiles import fdb_v3_config  # noqa: E402
from prism_rt.adapters.transcript_segmenter import segment_transcript  # noqa: E402
from prism_rt.entry import setup  # noqa: E402
from prism_rt.observability import speechlint  # noqa: E402
from prism_rt.workers.gateway import GeminiProvider  # noqa: E402

DEFAULT_FDB_ROOT = os.environ.get(
    "FDB_V3_ROOT", os.path.expanduser("~/Desktop/Hackathons/fdb_work/Full-Duplex-Bench/v3")
)
DEFAULT_DATA_ROOT = os.environ.get(
    "FDB_V3_DATA_ROOT",
    os.path.expanduser("~/Desktop/Hackathons/fdb_work/data_dl/extracted/fdb_v3_data_released"),
)


def _import_fdb_module(fdb_root: str, name: str):
    """Imports one of FDB's own modules by inserting its directory onto
    sys.path -- never copies or edits the file (§11 of the plan: "FDB is
    never vendored or modified")."""
    if fdb_root not in sys.path:
        sys.path.insert(0, fdb_root)
    return importlib.import_module(name)


async def run_one_scenario(
    example_dir: Path,
    manifest: list[dict],
    provider,
    config: Config,
    mock_apis_module,
    *,
    seed: int,
    final_timeout_s: float,
    segmented: bool = False,
    segment_gap_ms: int = 180,
) -> dict:
    metadata = json.loads((example_dir / "metadata.json").read_text())
    transcript_in = metadata["dialogue"][0]["user"]
    room_name = f"eval-{example_dir.name}"

    tool_schemas = {t["name"]: t["params_schema"] for t in manifest}
    log_path = f"/tmp/janus_fdb_text_replay_{example_dir.name}.log"
    if os.path.exists(log_path):
        os.remove(log_path)
    registry = mock_apis_module.MockAPIRegistry(latency_profile="instant", enable_logging=False)
    adapter = FdbToolAdapter(registry, room_name, log_path=log_path, get_params_schema=tool_schemas.get)

    events: "asyncio.Queue[dict | None]" = asyncio.Queue()
    actions: "asyncio.Queue[dict]" = asyncio.Queue()
    runtime = setup(config=config, provider=provider)

    spoken: list[str] = []
    actual_calls: list[dict] = []
    final_event = asyncio.Event()
    errors: list[str] = []

    def _now_us() -> int:
        # config.clock_model defaults to "A" (CoupledClock, real wall
        # time -- `Config.clock_model` in config.py). `HarnessCodec.
        # decode` drops any event with no resolvable `ts_us`
        # (adapters/codec.py: "ts_us is None: return []", P-02 tolerant
        # drop) -- every event this harness synthesizes must carry one,
        # unlike SimHarness's `send()`, which injects it for the caller.
        return int(time.time() * 1_000_000)

    async def consume_actions() -> None:
        while True:
            action = await actions.get()
            atype = action.get("action_type")
            body = action.get("body") or {}
            if atype in ("speak", "clarify", "final"):
                text = body.get("text")
                if text:
                    spoken.append(text)
                if atype == "final":
                    final_event.set()
            elif atype == "tool_call":
                call_id = body.get("call_id")
                tool_name = body.get("tool_name")
                args = body.get("arguments") or {}
                try:
                    result = await adapter.execute(tool_name, args)
                    # Record what the adapter actually logs for FDB (schema
                    # defaults filled in), not the kernel's raw arguments.
                    actual_calls.append({"function": tool_name, "args": fill_schema_defaults(tool_schemas.get(tool_name) or {}, args)})
                    await events.put(
                        {
                            "type": "tool_result",
                            "ts_us": _now_us(),
                            "payload": {"call_id": call_id, "status": "ok", "result": result},
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - one bad call must not kill the scenario
                    errors.append(f"tool_call {tool_name} failed: {exc!r}")
                    await events.put(
                        {
                            "type": "tool_result",
                            "ts_us": _now_us(),
                            "payload": {"call_id": call_id, "status": "error", "result": {"error": str(exc)}},
                        }
                    )
            # cancel: no target to act on in a text replay (no real tool cancellation).

    consumer = asyncio.create_task(consume_actions())

    await events.put({"type": "manifest", "ts_us": _now_us(), "payload": {"tools": manifest}})
    if segmented:
        pieces = segment_transcript(transcript_in) or [transcript_in]
        for i, piece in enumerate(pieces):
            if i:
                await asyncio.sleep(segment_gap_ms / 1000)
            await events.put({"type": "text_chunk", "ts_us": _now_us(), "payload": {"text": piece}})
    else:
        await events.put({"type": "text_chunk", "ts_us": _now_us(), "payload": {"text": transcript_in}})
    await events.put({"type": "end_of_turn", "ts_us": _now_us(), "payload": {}})

    meta = {"seed": seed}
    if config.log_decisions:
        meta["log_path"] = f"/tmp/janus_fdb_decision_log_{example_dir.name}.jsonl"
        if os.path.exists(meta["log_path"]):
            os.remove(meta["log_path"])
    run_task = asyncio.create_task(runtime.run_scenario(events, actions, meta=meta))

    try:
        await asyncio.wait_for(final_event.wait(), timeout=final_timeout_s)
    except asyncio.TimeoutError:
        errors.append("timeout waiting for FINAL")

    await asyncio.sleep(0.2)  # grace period for a trailing SPEAK emitted just before/with FINAL
    await events.put(None)
    summary = await run_task
    consumer.cancel()
    try:
        await consumer
    except asyncio.CancelledError:
        pass

    return {
        "example_id": metadata["id"],
        "folder": example_dir.name,
        "actual_tool_calls": actual_calls,
        "transcript": " ".join(spoken),
        "watchdog_fired": summary.watchdog_fired,
        "step_count": summary.step_count,
        "errors": errors,
        "metadata": metadata,
    }


async def main_async(args: argparse.Namespace) -> None:
    manifest_tools = introspect_file(os.path.join(args.fdb_root, "lk_agent_tool.py"))
    manifest = to_catalog_manifest(manifest_tools)
    print(f"Introspected {len(manifest)} FDB tools from {args.fdb_root}/lk_agent_tool.py")

    mock_apis_module = _import_fdb_module(args.fdb_root, "mock_apis")

    provider = GeminiProvider(model=args.model, thinking_budget=args.thinking_budget)
    if args.profile == "fdb":
        config = fdb_v3_config(log_decisions=args.debug)
    else:
        config = Config(log_decisions=args.debug, g4_exempt_undeclared_mutability=args.g4_exempt_undeclared_mutability)

    example_dirs = sorted(p for p in Path(args.data_root).iterdir() if p.is_dir() and (p / "metadata.json").is_file())
    if args.only:
        example_dirs = [p for p in example_dirs if args.only in p.name]
    if args.limit:
        example_dirs = example_dirs[: args.limit]
    print(f"Running {len(example_dirs)} scenarios from {args.data_root}")

    per_scenario_results: list[dict] = []
    entries: list[dict] = []
    for i, example_dir in enumerate(example_dirs):
        print(f"[{i + 1}/{len(example_dirs)}] {example_dir.name} ...", end=" ", flush=True)
        t0 = time.monotonic()
        try:
            result = await run_one_scenario(
                example_dir, manifest, provider, config, mock_apis_module,
                seed=i, final_timeout_s=args.timeout,
                segmented=args.segmented, segment_gap_ms=args.segment_gap_ms,
            )
        except Exception as exc:  # noqa: BLE001 - one scenario's crash must not kill the whole run
            print(f"CRASHED: {exc!r}")
            result = {
                "example_id": json.loads((example_dir / "metadata.json").read_text())["id"],
                "folder": example_dir.name,
                "actual_tool_calls": [],
                "transcript": "",
                "watchdog_fired": False,
                "step_count": 0,
                "errors": [f"harness crash: {exc!r}"],
                "metadata": json.loads((example_dir / "metadata.json").read_text()),
            }
        elapsed = time.monotonic() - t0
        print(f"{elapsed:.1f}s, {len(result['actual_tool_calls'])} calls, errors={result['errors']}")
        per_scenario_results.append(result)
        entries.append(
            {
                "scenario": result["metadata"],
                "calls": result["actual_tool_calls"],
                "transcript": result["transcript"],
                "result_data": result,
            }
        )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "raw_results.json").write_text(json.dumps(per_scenario_results, indent=2, default=str))

    evaluate_pass_rate = _import_fdb_module(args.fdb_root, "evaluate_pass_rate")
    evaluate_tool_calls = _import_fdb_module(args.fdb_root, "evaluate_tool_calls")

    benchmark_meta = {"benchmark_name": "fdb_v3_data_released (per-folder metadata.json, T3 text replay)"}
    pass_report = evaluate_pass_rate.evaluate_all_pass_rate(benchmark_meta, entries, use_llm=args.use_llm)
    tool_report = evaluate_tool_calls.evaluate_all_v2(benchmark_meta, entries, use_llm=args.use_llm)

    (out_dir / "pass_rate_report.json").write_text(json.dumps(pass_report, indent=2, default=str))
    (out_dir / "tool_calls_report.json").write_text(json.dumps(tool_report, indent=2, default=str))

    # Q11 (win_plan §6.2): a lint summary reusing the same speechlint.lint
    # this project's own EmissionGate applies live (Config.speechlint_
    # enabled) -- run here regardless of whether that flag was on for
    # this particular run, so a run with the live gate *off* still shows
    # how often each violation class would have fired. Linted as one
    # kind="final" pass over each scenario's whole spoken transcript
    # (every ACK/CLARIFY/FINAL concatenated) -- catches jargon/internal-
    # word leaks precisely; won't flag an ACK-specific premature-
    # completion-claim in isolation (that check is kind-scoped and this
    # pass doesn't know which words came from which action), which is a
    # known, documented narrowing of the live check, not a bug.
    lint_by_scenario: dict[str, list[str]] = {}
    violation_counts: dict[str, int] = {}
    for result in per_scenario_results:
        violations = speechlint.lint(result["transcript"], "final").violations
        if violations:
            lint_by_scenario[result["example_id"]] = list(violations)
            for v in violations:
                label = v.split(":", 1)[0]
                violation_counts[label] = violation_counts.get(label, 0) + 1
    lint_summary = {
        "scenarios_with_violations": len(lint_by_scenario),
        "total_violations": sum(violation_counts.values()),
        "violations_by_type": violation_counts,
        "by_scenario": lint_by_scenario,
    }
    (out_dir / "speechlint_report.json").write_text(json.dumps(lint_summary, indent=2, default=str))

    print("\n=== T3 TEXT REPLAY SUMMARY ===")
    print(f"model: {args.model}  thinking_budget: {args.thinking_budget}")
    print(f"scenarios run: {len(entries)}")
    print(f"strict pass rate: {pass_report['overall_pass_rate']:.1%}  ({pass_report['passed']}/{pass_report['total_scenarios']})")
    print(f"turn_take_rate: {tool_report['turn_taking']['turn_take_rate']}")
    print(f"tool_selection_acc: {tool_report['by_metric']['tool_selection_acc']}")
    print(f"argument_acc: {tool_report['by_metric']['argument_acc']}")
    print(f"speechlint: {lint_summary['scenarios_with_violations']}/{len(entries)} scenarios with violations, "
          f"{lint_summary['total_violations']} total ({violation_counts})")

    # Q11: per-bucket failure table -- domain/difficulty breakdown from
    # FDB's own evaluators, printed here since run_text_replay.py's own
    # summary previously showed only the aggregate numbers above.
    print("\nper-bucket pass rate (strict) / tool_selection_acc / argument_acc:")
    for domain, rate in sorted(pass_report.get("by_domain", {}).items()):
        tool_bucket = tool_report.get("by_domain", {}).get(domain, {})
        print(
            f"  {domain:24s} pass={rate:.1%}"
            f"  tool_sel={tool_bucket.get('tool_selection_acc', float('nan')):.1%}"
            f"  arg_acc={tool_bucket.get('argument_acc', float('nan')):.1%}"
        )
    for difficulty, rate in sorted(pass_report.get("by_difficulty", {}).items()):
        tool_bucket = tool_report.get("by_difficulty", {}).get(difficulty, {})
        print(
            f"  {difficulty:24s} pass={rate:.1%}"
            f"  tool_sel={tool_bucket.get('tool_selection_acc', float('nan')):.1%}"
            f"  arg_acc={tool_bucket.get('argument_acc', float('nan')):.1%}"
        )
    print(f"\nreports written to {out_dir}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Janus vs FDB-v3 T3 text-replay harness")
    p.add_argument("--fdb-root", default=DEFAULT_FDB_ROOT)
    p.add_argument("--data-root", default=DEFAULT_DATA_ROOT)
    p.add_argument("--model", default="gemini-3.6-flash")
    p.add_argument("--thinking-budget", type=int, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--only", default=None, help="substring filter on the scenario folder name, for targeted debugging")
    p.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for a scenario's FINAL")
    p.add_argument("--use-llm", action="store_true", help="use the gpt-4o judge (needs OPENAI_API_KEY); default is exact-match")
    p.add_argument("--debug", action="store_true", help="enable per-scenario decision logs")
    p.add_argument(
        "--segmented", action="store_true",
        help="Q10 (win_plan §6.2): split each transcript at pause markers (..., em/en dash, "
             "comma) and send the pieces as separate text_chunk events with a realistic gap, "
             "instead of one whole-transcript chunk -- a cheap proxy for the voice path's "
             "many-chunk/mid-utterance-pause behavior",
    )
    p.add_argument("--segment-gap-ms", type=int, default=180, help="gap between segmented chunks (--segmented only)")
    p.add_argument(
        "--profile", choices=["none", "fdb"], default="none",
        help="Day 2 WP1: 'fdb' applies prism_rt.profiles.fdb_v3_config() (G4 exemption, settle "
             "barrier + incomplete-turn extension, zero retries) instead of the individual flags below",
    )
    p.add_argument(
        "--g4-exempt-undeclared-mutability", action="store_true",
        help="Day 2 (§5.2): don't require explicit commit_intent for tools FDB never declared mutability for",
    )
    p.add_argument("--out-dir", default=str(REPO_ROOT / "run_output" / "fdb_v3_text_replay"))
    return p.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
