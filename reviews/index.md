# Review Catalog

Four independent-review rounds so far, in order. Each was given full repo
and terminal access and asked to verify claims, check invariants, and hunt
for real bugs — not to trust `currentStatus.md`'s own self-assessment.
Every finding below was independently re-verified by direct reproduction
before any fix shipped; a review's own "I confirmed this" was never taken
at face value (see `currentStatus.md`'s Implemented section for the full
mechanism of each fix, and the per-round notes below for what *didn't*
hold up).

| Date | Reviewer | Scope reviewed | Outcome | Files |
|------|----------|-----------------|---------|-------|
| 2026-09-18 (planned), written later, now stale | Nemotron-3-Ultra (opencode) | V0–V4 (`v4-hardened`) | Real findings at the time (F1 audio gap, F2 codec strictness — both since resolved in Phase A/2). **Written after the 3 rounds below had already landed their fixes, then the run was cancelled partway through once that was noticed — treat this as a snapshot of V4, not a current bug list.** No `v4/2026-09-18_independent_review.md` was ever produced (planned, never written). | `v4/nemotron_audit.md` |
| 2026-09-19 | Claude Opus 4.6 (Thinking), Antigravity IDE | Post-Phase-6 codebase (`v10-response-latency`) | 1 real bug found and fixed (correction-race stale-COMPOSE), tag `v10-1-correction-race-fix` | `opus/` |
| 2026-09-19 | Claude Sonnet (fresh session) | Post-`v10-1` codebase, told to avoid re-covering round 1's ground | 2 real bugs found and fixed (retry-exhaustion false-completion-claim, S-07 livelock), tag `v10-2-failure-honesty-fix` | `sonnet/` |
| 2026-09-19 | Claude Sonnet, continued on Gemini 3.1 Pro after Sonnet's budget ran out | Post-`v10-2` codebase, told to hunt the "who sets this flag" bug class specifically | 2 stated findings (STALE/RETAINED write reconciliation gap, false `EFFECT_DONE` claim) did **not** hold up under independent re-verification — see `gemini/review-2026-09-19.md` and the regression test's own docstring for why. Chasing the same lead surfaced a real, unreported third bug instead (`CommitGate` G6 permanently blocking a corrected write), fixed, tag `v10-3-write-lineage-fix`. This session also edited `src/prism_rt/sim/checker.py` on its own initiative (not requested) and overclaimed "all 34 CS checks done" — corrected in `docs/post_v4_implementation_plan.md`; only 4 were actually added. | `gemini/` |

**Lesson so far, four rounds in**: every round found *something* real, but never exactly what it thought it found, and at least one round (Gemini) both overclaimed a separate piece of unrelated work and produced a finding that its own test contradicted. Keep verifying independently — a review's self-report is a lead, not a fact.
