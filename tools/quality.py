#!/usr/bin/env python3
"""Transcript quality lint: what the LLM judge will read, checked before it does.

    python tools/quality.py                      # public + conformance, lint only
    python tools/quality.py --show               # also print every transcript
    python tools/quality.py --generated 30 --seed 5
    python tools/quality.py --scenarios scenarios/pub_02_text_interrupt.json

The automated score cannot see most of what makes an agent sound broken. The
first read-through found a garbled capabilities list spoken as a final answer
in six of seventeen scenarios - every one of which still scored 100. The
quality judge sees it, and its multiplier (x0.90-1.10) applies to every
hidden scenario.

The real judge prompt is unpublished; the four dimensions are the contract
(relevance, truthfulness, naturalness, non-redundancy). Each rule below is a
mechanical symptom of failing one of them, chosen so that a clean transcript
can still fail the judge but a flagged one almost certainly will.

Exit status is non-zero if any rule fires, so this doubles as a gate.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import importlib
import json
import os
import random
import re
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from harness.runner import EvaluationHarness  # noqa: E402
from harness.scenario_gen import TEMPLATES  # noqa: E402
from harness.scorer import score_scenario  # noqa: E402

SPOKEN = ("filler_speech", "clarification_request", "final_response")
USER_EVENTS = ("user_speech_chunk", "user_audio_chunk", "interruption", "video_frame")

# Two utterances this close together read as the agent talking over itself.
STACKED_MS = 150.0

# A clause that ends on a function word is a sentence that was cut off:
# "I can help you search flights to, book a specific, ..."
_DANGLING = re.compile(
    r"\b(to|a|an|the|of|for|from|with|by|specific|existing|given)\s*[,.;!?]",
    re.I)


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------
def load_agent(spec: str):
    module_name, _, class_name = spec.partition(":")
    return getattr(importlib.import_module(module_name), class_name)


def run_trace(scenario: Dict[str, Any], cls, time_scale: float) -> List[Dict[str, Any]]:
    async def go():
        h = EvaluationHarness(scenario, lambda a, b: cls(a, b),
                              time_scale=time_scale, verbose=False)
        await h.prepare()
        return await h.run()
    return asyncio.run(go())


# ---------------------------------------------------------------------------
# lint rules
# ---------------------------------------------------------------------------
def _spoken(trace):
    for e in trace:
        if e.get("kind") == "action" and e.get("action") in SPOKEN:
            yield e


def _text(entry) -> str:
    return str((entry.get("payload") or {}).get("text") or "")


# A tool name read aloud is only unnatural when it contains its own verb:
# "flight search", "lookup manual", "weather lookup". An all-noun name such as
# "hotel booking quote" is ordinary English and saying it is fine.
_ACTION_WORDS = {"search", "lookup", "look", "find", "get", "fetch", "list",
                 "check", "book", "create", "cancel", "open", "set", "query",
                 "retrieve", "reserve", "update", "delete", "send", "run"}


def _tool_names(scenario, trace) -> List[str]:
    names = set()
    for e in trace:
        if e.get("kind") == "event" and e.get("event_type") == "tool_manifest":
            names.update((e.get("payload") or {}).get("tools") or [])
    names.update((scenario.get("tool_manifest") or {}).keys())
    return sorted(n for n in names if isinstance(n, str) and "_" in n
                  and set(n.lower().split("_")) & _ACTION_WORDS)


def lint(scenario: Dict[str, Any], trace: List[Dict[str, Any]]) -> List[str]:
    """Return human-readable problems with this transcript. Empty = clean."""
    problems: List[str] = []
    spoken = list(_spoken(trace))

    # 1. Verbatim repetition, any spoken kind (non-redundancy).
    seen: Dict[str, float] = {}
    for e in spoken:
        key = _text(e).strip().lower()
        if key in seen:
            problems.append("repeat: %r at %dms and %dms"
                            % (_text(e), seen[key], e["t_ms"]))
        seen[key] = e["t_ms"]

    # 2. Internal tool names read aloud (naturalness).
    names = _tool_names(scenario, trace)
    for e in spoken:
        low = _text(e).lower()
        for name in names:
            if name in low or name.replace("_", " ") in low:
                problems.append("tool name spoken: %r in %r" % (name, _text(e)))
                break

    # 3. More than one final answer to the same user input (relevance).
    boundaries = [e["t_ms"] for e in trace
                  if e.get("kind") == "event" and e.get("event_type") in USER_EVENTS]
    finals = [e for e in spoken if e.get("action") == "final_response"]
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else float("inf")
        inside = [f for f in finals if start <= f["t_ms"] < end]
        if len(inside) > 1:
            problems.append("%d final answers after the event at %dms: %s"
                            % (len(inside), start,
                               " | ".join(repr(_text(f)) for f in inside)))

    # 4. Two utterances on top of each other (naturalness, non-redundancy) -
    #    the agent talking over itself, i.e. with no user input in between.
    #    Two quick acknowledgments of two quick interruptions are correct.
    for a, b in zip(spoken, spoken[1:]):
        user_between = any(a["t_ms"] < t <= b["t_ms"] for t in boundaries)
        if b["t_ms"] - a["t_ms"] < STACKED_MS and not user_between:
            problems.append("stacked speech %dms apart: %r then %r"
                            % (b["t_ms"] - a["t_ms"], _text(a), _text(b)))

    # 5. Cut-off clauses and code-shaped text (naturalness).
    for e in spoken:
        text = _text(e)
        if _DANGLING.search(text):
            problems.append("dangling clause: %r" % text)
        if "_" in text:
            problems.append("underscore in speech: %r" % text)
        if not text.isascii():
            problems.append("non-ASCII speech: %r" % text)

    # 7. Silent action mistakes the score does not charge for. An irreversible
    #    call that fails without the scenario injecting that failure means we
    #    sent it with wrong arguments - found when a flight id was passed to
    #    cancel_booking as a "follow-up" and scored 100 because it errored.
    from harness.mock_env import TOOL_REGISTRY
    kinds = {n: s.get("kind") for n, s in TOOL_REGISTRY.items()}
    kinds.update({n: (s or {}).get("kind") for n, s in (scenario.get("tool_manifest") or {}).items()})
    injected = {n for n, ovs in (scenario.get("tool_overrides") or {}).items()
                if any("error" in ov for ov in ovs)}
    calls = {e.get("call_id"): e for e in trace
             if e.get("kind") == "action" and e.get("action") == "tool_call"}
    for e in trace:
        if e.get("kind") != "tool_completed" or e.get("status") == "success":
            continue
        api = e.get("api_name")
        call = calls.get(e.get("call_id")) or {}
        args = {k: v for k, v in (call.get("args") or e.get("args") or {}).items()
                if not isinstance(v, list)}
        if kinds.get(api) == "state_modifying" and api not in injected:
            problems.append("state-modifying call failed: %s(%s)" % (api, args))

    # 6. Filler budget (safety, and the judge's non-redundancy).
    budget = int(((scenario.get("ground_truth") or {}).get("safety") or {})
                 .get("max_fillers", 4))
    fillers = sum(1 for e in spoken if e.get("action") == "filler_speech")
    if fillers > budget:
        problems.append("%d fillers over a budget of %d" % (fillers, budget))

    return problems


def transcript_lines(trace: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for e in trace:
        kind = e.get("kind")
        p = e.get("payload") or {}
        t = "%7d" % int(e.get("t_ms", 0))
        if kind == "event":
            et = e.get("event_type")
            if et in ("user_speech_chunk", "interruption"):
                lines.append(t + "  USER    " + str(p.get("text")))
            elif et == "user_audio_chunk":
                lines.append(t + "  AUDIO   " + str(p.get("audio_ref")))
            elif et == "video_frame":
                lines.append(t + "  FRAME   " + str(p.get("image_ref")))
            elif et == "scenario_end":
                lines.append(t + "  END")
        elif kind == "action":
            a = e.get("action")
            if a in SPOKEN:
                label = {"filler_speech": "FILLER", "clarification_request": "ASK",
                         "final_response": "FINAL"}[a]
                lines.append(t + "  " + label.ljust(8) + _text(e))
            elif a == "tool_call":
                args = {k: v for k, v in (e.get("args") or {}).items()
                        if not isinstance(v, list)}
                lines.append(t + "  CALL    " + str(e.get("api_name")) + " "
                             + json.dumps(args))
        elif kind == "tool_cancelled":
            # The harness records a cancel as its effect, not as an action.
            lines.append(t + "  CANCEL  " + str(e.get("call_id")))
    return lines


# ---------------------------------------------------------------------------
def default_paths() -> List[str]:
    return (sorted(glob.glob(os.path.join(ROOT, "scenarios", "*.json")))
            + sorted(glob.glob(os.path.join(ROOT, "tests", "conformance", "*.json"))))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", default="agent.agent:ParticipantAgent")
    ap.add_argument("--scenarios", nargs="*", default=None,
                    help="scenario files (default: public + conformance)")
    ap.add_argument("--generated", type=int, default=0,
                    help="also lint N scenarios from the kit's templates")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--time-scale", type=float, default=1.0)
    ap.add_argument("--show", action="store_true", help="print every transcript")
    ap.add_argument("--out", default="", help="write transcripts + problems as JSON")
    args = ap.parse_args(argv)

    scenarios: List[Dict[str, Any]] = []
    for path in (args.scenarios if args.scenarios else default_paths()):
        with open(path, "r", encoding="utf-8") as fh:
            scenarios.append(json.load(fh))
    if args.generated:
        rng = random.Random(args.seed)
        names = sorted(TEMPLATES)
        for i in range(args.generated):
            scenarios.append(TEMPLATES[names[i % len(names)]](rng, i))

    cls = load_agent(args.agent)
    report = []
    flagged = 0
    utterances = 0
    for scenario in scenarios:
        sid = scenario.get("scenario_id", "?")
        trace = run_trace(scenario, cls, args.time_scale)
        score = score_scenario(scenario, trace)["total"]
        problems = lint(scenario, trace)
        utterances += sum(1 for _ in _spoken(trace))
        flagged += bool(problems)
        status = "ok  " if not problems else "FAIL"
        print("%s %5.1f  %s" % (status, score, sid))
        for problem in problems:
            print("        - " + problem)
        if args.show:
            for line in transcript_lines(trace):
                print("        " + line)
        report.append({"scenario_id": sid, "score": score, "problems": problems,
                       "transcript": transcript_lines(trace)})

    print("\n%d scenario(s), %d utterance(s), %d flagged"
          % (len(scenarios), utterances, flagged))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
