"""Pass 1: Crash/invariant fuzzer for Janus SimHarness.

Fires random-ish sequences of events at random-ish timing offsets,
including malformed/out-of-order ones, then checks:
1. No unhandled exception raised.
2. TraceChecker reports no violations.

When a seed trips either check, it's a real finding. Typical clean runs
produce nothing; that's expected.

Usage:
    PYTHONPATH=src:. python reviews/sonnet/pass1_fuzzer.py [--seeds N] [--verbose]
"""
from __future__ import annotations

import random
import sys
import traceback
import argparse
from typing import Any

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from prism_rt.config import Config
from prism_rt.model.types import ActionType, JobKind
from prism_rt.sim.checker import TraceChecker
from prism_rt.sim.harness import SimHarness
from prism_rt.workers.gateway import ScriptedProvider

# ── Tool catalog ──────────────────────────────────────────────────────────────

SEARCH_TOOL = {
    "name": "search_flights",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {
            "destination": {"type": "string", "enum": ["Pune", "Mumbai", "Goa"]},
            "date": {"type": "string"},
        },
        "required": ["destination"],
    },
}

BOOK_TOOL = {
    "name": "book_flight",
    "mutability": "state_changing",
    "parameters": {
        "type": "object",
        "properties": {"flight_id": {"type": "string"}},
        "required": ["flight_id"],
    },
}

WEATHER_TOOL = {
    "name": "get_weather",
    "mutability": "read_only",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}

ALL_TOOLS = [SEARCH_TOOL, BOOK_TOOL, WEATHER_TOOL]

# ── Provider helpers ──────────────────────────────────────────────────────────

DESTINATIONS = ["Pune", "Mumbai", "Goa"]
INTENTS = ["search_flights", "book_flight", "get_weather"]


def make_provider(rng: random.Random) -> ScriptedProvider:
    p = ScriptedProvider()
    for dest in DESTINATIONS:
        for phrase in [f"flights to {dest}", f"book flight to {dest}", f"search {dest}", dest]:
            p.register(
                "interpret", phrase,
                {
                    "act": "new_goal",
                    "intent": "search_flights",
                    "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": dest}],
                    "commit_intent": rng.choice([True, False]),
                },
            )
        p.register(
            "interpret", f"actually {dest}",
            {
                "act": "slot_update",
                "slot_deltas": [{"name": "destination", "scope": "goal", "op": "set", "value": dest}],
            },
        )
    p.register(
        "interpret", "cancel",
        {"act": "abort"},
    )
    p.register(
        "interpret", "ok",
        {"act": "backchannel"},
    )
    p.register("plan", "search_flights", {
        "steps": [
            {"local_id": "s1", "tool": "search_flights", "kind": "read",
             "bindings": {"destination": {"type": "fact", "key": "slot.$G.destination"}}, "after": []},
        ]
    })
    p.register("plan", "book_flight", {
        "steps": [
            {"local_id": "s1", "tool": "book_flight", "kind": "write",
             "bindings": {"flight_id": {"type": "literal", "value": "AI-9"}}, "after": []},
        ]
    })
    p.register("compose", "s1", {"text": "Here are the results.", "claims": ["result:s1"]})
    return p


def make_config(rng: random.Random) -> Config:
    """Randomly flip feature flags — exercises all combinations."""
    return Config(
        transitive_invalidation=rng.choice([True, False]),
        settle_barrier_enabled=rng.choice([True, False]),
        settle_ms=rng.choice([50, 100, 200]),
        absence_read_sets=rng.choice([True, False]),
        claim_grades_enabled=rng.choice([True, False]),
        rebinder_enabled=rng.choice([True, False]),
        commit_last_ordering=rng.choice([True, False]),
        commit_last_wait_cap_ms=rng.choice([500, 2000, 5000]),
        speculative_interpretation_enabled=rng.choice([True, False]),
        frame_rendering_enabled=rng.choice([True, False]),
        asr_enabled=rng.choice([True, False]),
        vision_enabled=False,  # don't bother with vision complexity in fuzzer
        reference_bound_identifiers=rng.choice([True, False]),
    )


# ── Event generators ──────────────────────────────────────────────────────────

def gen_event(rng: random.Random) -> dict[str, Any]:
    """Return a random raw event dict, including malformed/unknown ones."""
    choice = rng.randint(0, 19)

    if choice == 0:
        return {"type": "manifest", "payload": {"tools": rng.choice([ALL_TOOLS, [SEARCH_TOOL], [BOOK_TOOL]])}}
    elif choice <= 4:
        dest = rng.choice(DESTINATIONS + ["", None, 12345])
        if dest is None or isinstance(dest, int):
            return {"type": "text_chunk", "payload": {"text": dest}}  # malformed
        phrase = rng.choice([
            f"flights to {dest}", f"actually {dest}", f"book {dest}",
            "cancel", "ok", "find me a flight",
        ])
        return {"type": "text_chunk", "payload": {"text": phrase}}
    elif choice == 5:
        return {"type": "end_of_turn", "payload": {}}
    elif choice == 6:
        return {"type": "interruption", "payload": {}}
    elif choice == 7:
        # tool_result with plausible but out-of-order call_id
        return {
            "type": "tool_result",
            "payload": {
                "call_id": f"call-{rng.randint(1, 50)}",
                "status": rng.choice(["ok", "error"]),
                "result": {"flight_id": "AI-9", "flights": [{"id": "AI-1"}]},
                "error": None,
            },
        }
    elif choice == 8:
        return {
            "type": "video_frame",
            "payload": {"frame_id": f"frame-{rng.randint(1, 10)}"},
        }
    elif choice == 9:
        return {
            "type": "audio_clip",
            "payload": {"clip_id": f"clip-{rng.randint(1, 10)}"},
        }
    elif choice == 10:
        # Unknown event type — should be silently dropped (R-01)
        return {
            "type": rng.choice(["quantum_event", "hyperspace_jump", "unknown_type_xyz", ""]),
            "payload": {"data": "ignored"},
        }
    elif choice == 11:
        # Manifest with empty tools
        return {"type": "manifest", "payload": {"tools": []}}
    elif choice == 12:
        # Malformed text_chunk (missing text field)
        return {"type": "text_chunk", "payload": {}}
    elif choice == 13:
        # text_chunk with numeric text
        return {"type": "text_chunk", "payload": {"text": 99999}}
    elif choice == 14:
        # end_of_turn without any prior chunk (empty turn)
        return {"type": "end_of_turn", "payload": {}}
    elif choice == 15:
        # Double interruption
        return {"type": "interruption", "payload": {}}
    elif choice == 16:
        # tool_result with None call_id
        return {
            "type": "tool_result",
            "payload": {"call_id": None, "status": "ok", "result": {}, "error": None},
        }
    elif choice == 17:
        # Manifest with a completely malformed tool entry
        return {"type": "manifest", "payload": {"tools": [{"bad": "entry"}]}}
    elif choice == 18:
        # Double end_of_turn
        return {"type": "end_of_turn", "payload": {}}
    else:
        # Valid search chunk
        dest = rng.choice(DESTINATIONS)
        return {"type": "text_chunk", "payload": {"text": f"search {dest}"}}


def gen_sequence(rng: random.Random, min_events: int = 3, max_events: int = 15) -> list[tuple[int, dict]]:
    """Generate a timed sequence of (ts_us, event) pairs."""
    n = rng.randint(min_events, max_events)
    events = []
    ts = 0
    for _ in range(n):
        ts += rng.randint(0, 200_000)  # 0–200ms gap
        events.append((ts, gen_event(rng)))
    return events


# ── Runner ────────────────────────────────────────────────────────────────────

def run_sequence(seed: int, verbose: bool = False) -> tuple[bool, list[str]]:
    """Run one fuzz sequence. Returns (passed, list_of_problems)."""
    rng = random.Random(seed)
    config = make_config(rng)
    provider = make_provider(rng)
    sequence = gen_sequence(rng)

    worker_latency = {
        JobKind.INTERPRET: rng.randint(5_000, 30_000),
        JobKind.PLAN: rng.randint(5_000, 30_000),
        JobKind.COMPOSE: rng.randint(5_000, 30_000),
    }

    tools = {
        "search_flights": {"latency_ms": rng.randint(10, 100), "response": {"flights": [{"id": "AI-1"}]}},
        "book_flight": {"latency_ms": rng.randint(10, 100), "response": {"booking_id": "BK-1"}},
        "get_weather": {"latency_ms": rng.randint(10, 100), "response": {"temp": 25}},
    }

    problems: list[str] = []

    try:
        h = SimHarness(config, seed=seed % 100, tools=tools, provider=provider,
                       worker_latency_us=worker_latency)

        # Send initial manifest
        h.send(0, [{"type": "manifest", "payload": {"tools": ALL_TOOLS}}])

        # Replay the random sequence
        for ts_us, event in sequence:
            h.send(ts_us + 1_000, [event])  # +1_000 to stay > 0

        # Drain to a safe time (6 seconds out)
        max_ts = (sequence[-1][0] if sequence else 0) + 6_000_000
        step_us = 30_000
        t = h.clock.now_us()
        while t < max_ts:
            t += step_us
            h.advance(t)

        # TraceChecker
        checker = TraceChecker()
        violations = checker.check(h.run_log.reports, store=h.store)
        for v in violations:
            problems.append(f"TRACE_VIOLATION {v.invariant}: {v.message}")

    except Exception:
        tb = traceback.format_exc()
        problems.append(f"EXCEPTION:\n{tb}")

    if verbose or problems:
        print(f"Seed {seed:6d}: {'FAIL' if problems else 'ok':4s}  events={len(sequence)}")
        for p in problems:
            print(f"  >> {p[:300]}")

    return not problems, problems


def main():
    parser = argparse.ArgumentParser(description="Pass 1 crash/invariant fuzzer for Janus")
    parser.add_argument("--seeds", type=int, default=200, help="Number of random seeds to try")
    parser.add_argument("--verbose", action="store_true", help="Print each seed's result")
    args = parser.parse_args()

    print(f"Running {args.seeds} fuzz sequences...")
    failures: list[tuple[int, list[str]]] = []

    for seed in range(args.seeds):
        ok, problems = run_sequence(seed, verbose=args.verbose)
        if not ok:
            failures.append((seed, problems))

    print(f"\n{'='*60}")
    print(f"Results: {args.seeds - len(failures)}/{args.seeds} clean, {len(failures)} failures")

    if failures:
        print("\nFailing seeds (minimize these):")
        for seed, problems in failures[:10]:  # cap output
            print(f"\n  Seed {seed}:")
            for p in problems:
                print(f"    {p[:500]}")
    else:
        print("Pass 1 CLEAN: no unhandled exceptions or TraceChecker violations found.")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
