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
python -m pytest tests/ -v                 # full suite (134 tests: V0-V4 + post-V4 Phase A + Phase 2 + Phase 3 + Phase 4 + Phase 5 + Phase 6 + 3 bugfix rounds + Phase 7 in progress + live-reliability fixes)
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
| `v7-live-multimodal` | `release/v7` | Real image/audio bytes flow to the live model (`Provider.complete_json`'s `media` param, `workers/runner.py`'s `blob_resolver`) instead of by-reference IDs. Verified against the live Gemini API. |
| `v8-chunk-anchor` | `release/v8` | CHUNK cancellation anchor: a HIGH-confidence slot correction cancels an in-flight call in the same step the chunk arrives, not at end-of-turn (`kernel/detector.py.detect_values`, `kernel/turns.py._detect_chunk_anchor`, `StoreTxn.mark_chunk_anchor`). |
| `v9-task-completion` | `release/v9` | C2 response frames (`kernel/frames.py.FrameScheduler`) — a deterministic, kernel-rendered FINAL grounded in the real tool result, no COMPOSE round-trip — and C9 commit-last ordering (`kernel/executor.py._commit_last_blocked`) — a write waits for unrelated plan reads to settle first. |
| `v10-response-latency` | `release/v10` | Speculative interpretation (`kernel/task.py._speculative_interpret`, `kernel/turns.py._try_promote_speculative`) — an INTERPRET job runs on the transcript prefix while the user is still speaking, applied at EOT with zero additional model latency when the digest matches — and content-bearing ACK. Measured 300,000µs → 0µs TTFS(EOT) on a T-06-shaped scenario. |
| `v10-1-correction-race-fix` | — (bugfix, no release branch) | Fixes a real V1-era correctness bug found by an independent review: a correction landing while a goal was finishing up could let a stale, pre-correction COMPOSE result produce a false-completion FINAL. See `currentStatus.md` for the mechanism and `tests/test_correction_race_regression.py` for the regression test. |
| `v10-2-failure-honesty-fix` | — (bugfix, no release branch) | Fixes two more real bugs found by a follow-up independent review: a tool failing past its retry limit produced a FINAL admitting failure while still claiming `task_completed=True`; a plan referencing an unknown tool silently livelocked forever (S-07) instead of failing honestly. See `currentStatus.md` and `tests/test_failure_honesty_regression.py`. |
| `v10-3-write-lineage-fix` | — (bugfix, no release branch) | Fixes a third real bug found while independently verifying a follow-up review's own (ultimately disproven) findings: a corrected WRITE could get stuck `PROPOSED` forever once its cancelled original still confirmed on the same plan-step lineage (`CommitGate` G6 doesn't distinguish a same-fingerprint retry from a different-fingerprint correction). Fixed with an honest failure naming what already went through, per `docs/prompt 2.txt` §8.8's own worked example. See `currentStatus.md` and `tests/test_write_lineage_regression.py`. |

Every later version's new behavior is gated behind an explicit `Config` flag defaulting to the prior version's behavior — disabling V2's/V3's/V4's flags reproduces the earlier version exactly, and every frozen tag's own tests still pass unmodified on `main`.

## Documentation map

Read `currentStatus.md` first, every session — it's the live handoff record (current state, what's tested, what's deferred, the next task). `CLAUDE.md` has the architecture summary and commands. `docs/post_v4_implementation_plan.md` is the authoritative sequencing for everything after V4. `docs/integration.md` covers plugging a real harness in. `docs/sonnet_implementation_plan.md` is the executable coding plan for V0-V4; the other `docs/` files are the design rationale. `docs/measurements.md` has the per-version test/latency numbers.

## Known limitations

- The evaluation kit's wire format is unreleased (last checked 18 Sep 2026); `adapters/codec.py`'s schema is a provisional guess, but a deliberately *tolerant* one — see `docs/integration.md` for what dialect variance it survives.
- Live LLM validation: text (`demo/run_v1_live_demo.py`), vision, and audio (`demo/run_live_multimodal_demo.py`, real image/audio bytes via `MediaPart`/`blob_resolver`) are all verified working end-to-end against the live Gemini API, including the full kernel-orchestrated path (INTERPRET naming a visual target -> VISION claim -> PLAN binding a tool call to the resulting slot fact -> COMPOSE), not just the provider call in isolation. `AnthropicProvider` (image-only forwarding, no audio-input modality) remains untested live.
- Some scope items from `docs/prototype_version_plan.md`/`docs/post_v4_implementation_plan.md` remain deliberately deferred: audio transcription, chunk-anchored interruption cancellation, response frames (C2), inert-tail promotion / speculative interpretation (C1), the full 34-invariant `TraceChecker`, and kit wire-format integration (blocked on the kit itself being unreleased). See `currentStatus.md`'s Deferred section for the full list and `docs/post_v4_implementation_plan.md` for the sequencing.
