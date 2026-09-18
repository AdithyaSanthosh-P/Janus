# Janus

A single-writer coordination kernel for a full-duplex, interruptible real-time conversational agent — built for the **Samsung PRISM GenAI Hackathon 3rd Edition, Theme 05: "Interruptible Real-Time Agents."**

The Python package import path is `prism_rt` (the distribution is named `janus`) — a deliberate naming mismatch, not a leftover.

## What this is

The agent stays responsive while reasoning (a fast path answers immediately from deterministic cues) and runs tool calls and LLM reasoning concurrently (a slow path), while recovering cleanly when the user interrupts, corrects a slot, or switches goals mid-task — without duplicate side effects or false completion claims.

Architecturally, no model ever mutates session state or emits output directly. A single synchronous **kernel step** (`kernel/step.py`) owns all state mutation; everything else — interpretation, planning, composition, vision — runs as async **workers** that return proposals the kernel validates against an optimistic-concurrency read-set before accepting. See `CLAUDE.md` and `currentStatus.md` for the full architecture and current state.

## Setup

```bash
python3 -m venv .venv          # Python 3.10-3.12 (Docker target: 3.11)
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running the tests

```bash
python -m pytest tests/ -v                 # full suite (78 tests: V0-V4 + post-V4 Phase A + Phase 2)
python -m pytest tests/test_v3.py -v        # a single version's scenarios
```

Tests are fully deterministic: a stepped clock (no real sleeps), a scripted LLM provider (no live model calls), mocked tools, and a seeded ID generator. Every test also runs `TraceChecker` against the recorded decision log (no-wall-clock scan, cancellation/duplicate-write/replay-identity invariants).

## Running with Docker

```bash
docker build -t janus .
docker run --rm janus              # runs the full test suite
```

This is also the only environment this project's Python 3.10–3.12 compatibility claim has actually been verified against — day-to-day development happened on Python 3.14.

## Live demo (optional, needs a Gemini API key)

The kernel/workers are provider-agnostic; `demo/run_v1_demo.py` uses a scripted (canned) LLM for a fully offline, reproducible trace. `demo/run_v1_live_demo.py` runs the identical scenario against a real model:

```bash
echo 'GEMINI_API_KEY=your_key_here' > .env      # https://aistudio.google.com/apikey (free tier)
source .venv/bin/activate
PYTHONPATH=src:. python demo/run_v1_demo.py         # scripted
PYTHONPATH=src:. python demo/run_v1_live_demo.py    # live
```

## Versions

Built in strict, independently-submittable stages — each frozen with a git tag before the next began, so any tag is a safe fallback if later work runs out of time.

| Tag | Branch | Adds |
|---|---|---|
| `v0-skeleton` | — | Deterministic kernel skeleton: 7-phase step, versioned fact store, read-set validation, same-step cancellation. No LLM. |
| `v1-text-agent` | `release/v1` | Real LLM workers (Interpreter/Planner/Composer), turn management, task state machine, CommitGate, FastResponder. **First submission-capable version.** |
| `v2-robust-recovery` | `release/v2` | Transitive invalidation, settle barrier, rebinder, absence read-sets, claim grades, unconditional reconciliation. |
| `v3-multimodal` | `release/v3` | Vision-grounded questions/claims/conflicts over video frames, gated behind `Config.vision_enabled`. |
| `v4-hardened` | `release/v4` | Reference-bound write identifiers (C10), targeted timing sweeps, Docker, this README. |
| `v5-integration` | `release/v5` | A genuinely working async entry point (`entry.py`), a tolerant wire-format codec, `audio_clip` ingestion, watchdog salvage. See `docs/post_v4_implementation_plan.md`. |
| `v6-audio` | `release/v6` | An audio/ASR pipeline (`kernel/audio.py`, `workers/asr.py`) — audio-only tasks complete end-to-end, gated behind `Config.asr_enabled`. |

Every later version's new behavior is gated behind an explicit `Config` flag defaulting to the prior version's behavior — disabling V2's/V3's/V4's flags reproduces the earlier version exactly, and every frozen tag's own tests still pass unmodified on `main`.

## Documentation map

Read `currentStatus.md` first, every session — it's the live handoff record (current state, what's tested, what's deferred, the next task). `CLAUDE.md` has the architecture summary and commands. `docs/post_v4_implementation_plan.md` is the authoritative sequencing for everything after V4. `docs/integration.md` covers plugging a real harness in. `docs/sonnet_implementation_plan.md` is the executable coding plan for V0-V4; the other `docs/` files are the design rationale. `docs/measurements.md` has the per-version test/latency numbers.

## Known limitations

- The evaluation kit's wire format is unreleased (last checked 18 Sep 2026); `adapters/codec.py`'s schema is a provisional guess, but a deliberately *tolerant* one — see `docs/integration.md` for what dialect variance it survives.
- Live LLM validation covers only the text path (`GeminiProvider`, `demo/run_v1_live_demo.py`) — `workers/vision.py` has never been called against a real vision model; the `Provider` protocol is text-only, so frames are always passed by reference, never as pixel data. No ASR exists yet either.
- Some scope items from `docs/prototype_version_plan.md`/`docs/post_v4_implementation_plan.md` remain deliberately deferred: audio transcription, chunk-anchored interruption cancellation, response frames (C2), inert-tail promotion / speculative interpretation (C1), the full 34-invariant `TraceChecker`, and kit wire-format integration (blocked on the kit itself being unreleased). See `currentStatus.md`'s Deferred section for the full list and `docs/post_v4_implementation_plan.md` for the sequencing.
