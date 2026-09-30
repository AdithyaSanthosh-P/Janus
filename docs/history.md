# Version history

Moved out of the README. Built in strict, independently-submittable stages: each frozen with a git tag before the next began.

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
