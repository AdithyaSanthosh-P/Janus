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
import datetime
import importlib
import importlib.util
import json
import os
import subprocess
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
from prism_rt.profiles import apply_overrides, fdb_v3_config  # noqa: E402
from prism_rt.adapters.transcript_segmenter import segment_transcript  # noqa: E402
from prism_rt.entry import setup  # noqa: E402
from prism_rt.observability import speechlint  # noqa: E402
from prism_rt.workers.gateway import GeminiProvider, OpenAIProvider  # noqa: E402

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
    # Wall-clock marks (seconds) for the per-scenario latency fields --
    # harness-side only, never read by the kernel. `eot_s` is when the
    # end_of_turn event was queued; the others are when the matching
    # action reached this consumer.
    marks: dict[str, float] = {}

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
                    marks.setdefault("first_speech_s", time.time())
                if atype == "final":
                    marks.setdefault("final_s", time.time())
                    final_event.set()
            elif atype == "tool_call":
                marks.setdefault("first_call_s", time.time())
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
    marks["eot_s"] = time.time()
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

    def _since_eot(mark: str) -> float | None:
        if mark not in marks or "eot_s" not in marks:
            return None
        return round(marks[mark] - marks["eot_s"], 3)

    return {
        "example_id": metadata["id"],
        "folder": example_dir.name,
        "actual_tool_calls": actual_calls,
        "transcript": " ".join(spoken),
        "watchdog_fired": summary.watchdog_fired,
        "step_count": summary.step_count,
        "errors": errors,
        # S3 Increment 0: harness-side latency evidence (the PLAN-skip
        # saving). None when the event never happened (e.g. no tool call).
        "eot_to_first_speech_s": _since_eot("first_speech_s"),
        "eot_to_first_call_s": _since_eot("first_call_s"),
        "eot_to_final_s": _since_eot("final_s"),
        "metadata": metadata,
    }


# FDB's exact_match_args explanations (evaluate_pass_rate.py) -- any
# argument explanation that isn't one of these came from the LLM judge.
_EXACT_EXPLANATIONS = ("All arguments match", "Missing argument:", "Mismatch '")


def _judge_preflight(args: argparse.Namespace) -> None:
    """`--use-llm` must really mean FDB's gpt-4o judge. FDB's own
    `llm_judge_argument` silently falls back to exact-match on *any* error
    (no key, no `openai` package, a failed request), so without this an
    A/B arm could quietly be scored exact-match and still look judged."""
    if not args.use_llm:
        return
    problems = []
    if not os.environ.get("OPENAI_API_KEY"):
        problems.append("OPENAI_API_KEY is not set (env or repo-root .env)")
    if importlib.util.find_spec("openai") is None:
        problems.append("the `openai` package is not installed in this Python environment")
    if problems:
        sys.exit("--use-llm refused: " + "; ".join(problems))


def _count_explanations(pass_report: dict) -> tuple[int, int]:
    """(judge, exact) counts over every paired call's argument explanation.
    Unpaired expected calls carry `reason: "Not called"`, not an
    `explanation`, and aren't counted."""
    judge = exact = 0
    for scenario in pass_report.get("scenario_results", []):
        for detail in scenario.get("checks", {}).get("argument_accuracy", {}).get("details", []):
            if "explanation" not in detail:
                continue
            if str(detail["explanation"]).startswith(_EXACT_EXPLANATIONS):
                exact += 1
            else:
                judge += 1
    return judge, exact


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        )
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True).stdout
        return out.stdout.strip() + ("-dirty" if dirty.strip() else "")
    except (OSError, subprocess.CalledProcessError):
        return None


def _build_config(args: argparse.Namespace) -> Config:
    if args.profile == "fdb":
        config = fdb_v3_config(log_decisions=args.debug)
    else:
        config = Config(log_decisions=args.debug, g4_exempt_undeclared_mutability=args.g4_exempt_undeclared_mutability)
    return apply_overrides(config, args.set or [])


async def run_all(args: argparse.Namespace, config: Config) -> list[dict]:
    manifest_tools = introspect_file(os.path.join(args.fdb_root, "lk_agent_tool.py"))
    manifest = to_catalog_manifest(manifest_tools)
    print(f"Introspected {len(manifest)} FDB tools from {args.fdb_root}/lk_agent_tool.py")

    mock_apis_module = _import_fdb_module(args.fdb_root, "mock_apis")
    if args.provider == "openai":
        provider = OpenAIProvider(model=args.model)
    else:
        provider = GeminiProvider(model=args.model, thinking_budget=args.thinking_budget)

    example_dirs = sorted(p for p in Path(args.data_root).iterdir() if p.is_dir() and (p / "metadata.json").is_file())
    if args.only:
        example_dirs = [p for p in example_dirs if args.only in p.name]
    if args.ids:
        wanted = [i.strip() for i in args.ids.split(",") if i.strip()]
        example_dirs = [p for p in example_dirs if any(p.name.startswith(i + "_") for i in wanted)]
    if args.limit:
        example_dirs = example_dirs[: args.limit]
    print(f"Running {len(example_dirs)} scenarios from {args.data_root}")

    per_scenario_results: list[dict] = []
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
    return per_scenario_results


def score_and_report(args: argparse.Namespace, per_scenario_results: list[dict], out_dir: Path, run_meta: dict) -> None:
    entries = [
        {
            "scenario": result["metadata"],
            "calls": result["actual_tool_calls"],
            "transcript": result["transcript"],
            "result_data": result,
        }
        for result in per_scenario_results
    ]

    evaluate_pass_rate = _import_fdb_module(args.fdb_root, "evaluate_pass_rate")
    evaluate_tool_calls = _import_fdb_module(args.fdb_root, "evaluate_tool_calls")

    benchmark_meta = {"benchmark_name": "fdb_v3_data_released (per-folder metadata.json, T3 text replay)"}
    pass_report = evaluate_pass_rate.evaluate_all_pass_rate(benchmark_meta, entries, use_llm=args.use_llm)
    tool_report = evaluate_tool_calls.evaluate_all_v2(benchmark_meta, entries, use_llm=args.use_llm)

    judged, exact = _count_explanations(pass_report)
    if not args.use_llm:
        judge_mode = "exact"
    elif judged == 0 and exact > 0:
        judge_mode = "gpt-4o FAILED (every argument fell back to exact-match)"
    else:
        judge_mode = "gpt-4o" + (f" ({exact} of {judged + exact} fell back to exact-match)" if exact else "")
    run_meta = {**run_meta, "judge": judge_mode, "judge_explanations": judged, "exact_explanations": exact}

    (out_dir / "pass_rate_report.json").write_text(json.dumps(pass_report, indent=2, default=str))
    (out_dir / "tool_calls_report.json").write_text(json.dumps(tool_report, indent=2, default=str))
    (out_dir / "run_meta.json").write_text(json.dumps(run_meta, indent=2, default=str))

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
    print(f"model: {run_meta.get('model')}  thinking_budget: {run_meta.get('thinking_budget')}")
    print(f"profile: {run_meta.get('profile')}  overrides: {run_meta.get('overrides')}")
    print(f"judge: {judge_mode}")
    print(f"scenarios scored: {len(entries)}")
    print(f"strict pass rate: {pass_report['overall_pass_rate']:.1%}  ({pass_report['passed']}/{pass_report['total_scenarios']})")
    print(f"turn_take_rate: {tool_report['turn_taking']['turn_take_rate']}")
    print(f"tool_selection_acc: {tool_report['by_metric']['tool_selection_acc']}")
    print(f"argument_acc: {tool_report['by_metric']['argument_acc']}")
    print(f"speechlint: {lint_summary['scenarios_with_violations']}/{len(entries)} scenarios with violations, "
          f"{lint_summary['total_violations']} total ({violation_counts})")
    latencies = sorted(r["eot_to_first_call_s"] for r in per_scenario_results if r.get("eot_to_first_call_s") is not None)
    if latencies:
        print(f"eot_to_first_call_s: median {latencies[len(latencies) // 2]:.2f}  (n={len(latencies)})")

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
    for feature, rate in sorted(pass_report.get("by_disfluency_feature", {}).items()):
        print(f"  {feature:24s} pass={rate:.1%}")
    print(f"\nreports written to {out_dir}")
    if args.use_llm and judged == 0 and exact > 0:
        sys.exit("--use-llm: the gpt-4o judge never answered -- every argument fell back to exact-match")


async def main_async(args: argparse.Namespace) -> None:
    _judge_preflight(args)
    started_at = datetime.datetime.now().isoformat(timespec="seconds")

    if args.rescore:
        # S3 Increment 0: score an existing run's raw_results.json again
        # (e.g. with the gpt-4o judge) without re-running the agent.
        src = Path(args.rescore)
        per_scenario_results = json.loads((src / "raw_results.json").read_text())
        out_dir = Path(args.out_dir) if args.out_dir else src / f"rescore_{'llm' if args.use_llm else 'exact'}"
        out_dir.mkdir(parents=True, exist_ok=True)
        prior_meta_path = src / "run_meta.json"
        prior_meta = json.loads(prior_meta_path.read_text()) if prior_meta_path.is_file() else {}
        run_meta = {**prior_meta, "rescored_from": str(src), "rescored_at": started_at}
        score_and_report(args, per_scenario_results, out_dir, run_meta)
        return

    config = _build_config(args)
    out_dir = Path(args.out_dir or REPO_ROOT / "run_output" / "fdb_v3_text_replay")
    out_dir.mkdir(parents=True, exist_ok=True)
    run_meta = {
        "started_at": started_at,
        "git_commit": _git_commit(),
        "provider": args.provider,
        "model": args.model,
        "thinking_budget": args.thinking_budget,
        "profile": args.profile,
        "overrides": list(args.set or []),
        "segmented": args.segmented,
        "only": args.only,
        "limit": args.limit,
    }
    per_scenario_results = await run_all(args, config)
    (out_dir / "raw_results.json").write_text(json.dumps(per_scenario_results, indent=2, default=str))
    score_and_report(args, per_scenario_results, out_dir, run_meta)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Janus vs FDB-v3 T3 text-replay harness")
    p.add_argument("--fdb-root", default=DEFAULT_FDB_ROOT)
    p.add_argument("--data-root", default=DEFAULT_DATA_ROOT)
    p.add_argument("--provider", choices=["gemini", "openai"], default="gemini")
    p.add_argument("--model", default="gemini-3.6-flash")
    p.add_argument("--ids", default=None, help="comma-separated example ids (e.g. travel_01,housing_10); every speaker recording of each")
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
    p.add_argument(
        "--set", action="append", metavar="KEY=VALUE",
        help="S3: override one Config field on top of --profile (repeatable), e.g. "
             "--set action_plans_enabled=false. Unknown keys are rejected.",
    )
    p.add_argument(
        "--rescore", metavar="RUN_DIR", default=None,
        help="S3: re-score RUN_DIR/raw_results.json (e.g. with --use-llm) without re-running the "
             "agent; reports go to --out-dir, or RUN_DIR/rescore_<llm|exact>/ by default",
    )
    p.add_argument(
        "--out-dir", default=None,
        help="output directory (default: run_output/fdb_v3_text_replay, or RUN_DIR/rescore_* with --rescore)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
