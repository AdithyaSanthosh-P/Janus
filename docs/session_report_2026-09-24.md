# Session Report — 24 September 2026

A record of one long working session on Janus: what was asked, what was found, what
was changed, what was measured, and what is still open. Written for the team and for
any future session picking this up.

- **Branch:** `fdb-v3-integration` (commit `ba85d59`), **not pushed**. `main` is unchanged at `57bc9a8`.
- **Deadline:** 30 September 2026 (postponed from 25 September).
- **Companion document:** `docs/fdb_v3_implementation_plan.md` — the detailed build plan.

---

## Part 1 — Plain-language summary

**Where we started.** Janus had a strong, well-tested "brain" (334 automated checks)
but no real async entry point had ever been executed, and a real event-loop-starvation
bug (D5, below) had never surfaced.

**What happened, in order:**

1. **Audit.** The real entry point (`entry.py.Runtime.run_scenario`) had never once
   been executed. Running it exposed a real bug: after any tool finished, the process
   froze — the whole event loop, not just Janus's step. It was fixed.
2. **The rules changed.** Samsung sent an updated guide. Scoring is now a **voice**
   benchmark (Full-Duplex-Bench v3) run over LiveKit, an online voice-call service.
3. **Investigation and plan.** The new benchmark was downloaded and measured,
   LiveKit's code was read, and speech and AI options were timed. A day-by-day plan
   was written.

**Where Janus is now.** The brain is solid. There is no voice layer yet: Janus
cannot hear, speak, or join a call. If tested today it would score **0** on the
benchmark, which is 60 % of the marks.

**Where it could be by 30 Sep.** With ears (speech-to-text), a mouth
(text-to-speech), a call connection, and one key rule ("don't act until the
person has clearly finished speaking"), Janus would:

- reply within about 1 second;
- act only once the person is done;
- answer fully within about 5–8 seconds, against a limit of about 30 seconds.

**Chance of winning.** Real, but not guaranteed. The benchmark's hardest part is
people correcting themselves mid-sentence, which is exactly what Janus was designed
for. The risks are time (6 days) and teams using big ready-made voice AIs.

**Needed from the team:**

- a LiveKit Cloud account (free);
- an OpenAI API key (the benchmark's grader requires it);
- the team name;
- the demo video;
- final approval before tagging the submission.

---

## Part 2 — Timeline of the session

| # | Request | Outcome |
|---|---|---|
| 1 | Audit of the real async entry point | Confirmed `run_scenario` had never been exercised; found and fixed the D5 event-loop-starvation bug (below) |
| 2 | "Is ECC working / does it have the context?" | ECC hooks were active; its own session summary was thin. Context lives in `currentStatus.md` and these docs |
| 3 | Read-only reconciliation with Samsung's updated guide | Scoring is now FDB-v3 (voice, LiveKit) |
| 4 | Deadline moved to 30 Sep; "make it a winning project" | Committed the integration work to branch `fdb-v3-integration`. Saved the deadline to memory |
| 5 | Three investigations, then the implementation plan | Recordings, LiveKit source and speech/AI options measured. `docs/fdb_v3_implementation_plan.md` written. **No features implemented** |
| 6 | Leftover folder outside the project | `~/Desktop/Hackathons/fdb_work/`: partial leftover of a rejected command. Unused, safe to delete (awaiting your OK) |
| 7 | Jev (TypeSafe AI) idea | Fits Janus as an "advisor" for "is the user done speaking?". Proposed as a day-4 comparison against a timer and LiveKit's end-of-turn model |

---

## Part 3 — Findings in detail

### 3.1 Janus (the codebase)

- **Architecture.** A single "kernel" makes every decision in fixed steps. AI models
  only propose. Each piece of work remembers which facts it depended on, so when a fact
  changes (a correction), dependent work is discarded in the same step. A **commit
  gate** stops risky actions: duplicates, actions while the user is still talking,
  actions before a correction is processed.
- **Prior bug history.**
  - D1–D4 (from an earlier audit) are fixed in code.
  - **D5** (new this session, fixed): every tool call scheduled a timer that was never
    removed. Once it was overdue, the main loop spun forever and froze everything.
    Fix: the kernel retires overdue timers each step, and the
    driver yields to the event loop. Two regression tests fail if the fix is removed.
- **Test suite.** 288 original tests + 16 new pass. Verified locally (Python 3.14) and
  in Docker (Python 3.11).
- **Known gaps.**
  - The AI model defaults to `gemini-3.6-flash` with "thinking" on (~5.5 s per step).
  - The interpreter prompt shows only required parameter names, never descriptions or
    optional parameters.
  - No spoken reply to pure small talk.
  - Chained tool arguments guess the result's field location wrong.

### 3.2 The new benchmark — Full-Duplex-Bench v3 (what Samsung scores)

Source: repo commit `3e799c45`; recordings sha256 `37545bd8…7d672f`.

- **How it runs.**
  - A script plays each recording into a fresh LiveKit voice room.
  - Our agent listens and speaks.
  - The agent's speech is recorded and transcribed by NVIDIA's Parakeet speech model.
  - Tool actions are read from a log file the agent writes itself.
- **Scoring.**
  - **Strict pass:** exactly the expected set of tools, each with correct details. Any
    extra call, including repeating the same tool, fails the question.
  - The grader is `gpt-4o`, and it needs an OpenAI key.
  - Also reported: tool accuracy, argument accuracy, spoken-answer quality, latency.
- **The recordings** (measured):
  - 100 recordings of 79 scenarios; each is one spoken request, with no small talk.
  - 17 contain a self-correction ("Paris — actually, no, Berlin").
  - **Speech lasts 6–26 s, followed by about 30 s of silence.** That silence is the
    agent's time to respond.
- **The big trap.** People pause mid-request:

  | Pause at least | Recordings |
  |---|---|
  | 0.55 s (the benchmark sample agent's "done" threshold) | 72 of 100 |
  | 1 s | 25 |
  | 2 s | 5 |
  | 3 s | 2 (longest 3.46 s) |

  An agent that acts at the first pause fails many questions, because actions cannot
  be undone.
- **Other facts.**
  - No random seeds are set anywhere in the benchmark.
  - Three audio formats, all needing `ffmpeg`.
  - Licence CC BY-NC 4.0.

### 3.3 LiveKit (the voice-call framework)

Verified in `livekit-agents` 1.8.3 source:

- A session can run **without** a built-in AI model and still hear and transcribe the
  user and speak any text we give it. So Janus can stay the brain behind a thin
  voice layer.
- Each benchmark call runs in its own process. That keeps scenarios isolated.
- Ready-made connectors exist for Silero voice detection and Gemini speech. A local
  Whisper speech-to-text needs a small custom wrapper, which LiveKit supports.
- FDB's install instruction (`~=1.3`) floats to the newest 1.x, so we must pin exact
  versions.

### 3.4 Measurements (this machine: RTX 4060 laptop GPU; small samples as stated)

**AI model, per step:**

| Model | Understanding step | Planning step |
|---|---|---|
| gemini-3.6-flash (current default) | 5.5 s | — |
| same, thinking off | 2.2 s | 2.8 s |
| **gemini-3.5-flash-lite** (chosen) | **1.5 s** | **1.9 s** |

- Both models understood self-corrections correctly when given the whole sentence.
- `gemini-2.5-*` models returned "not found" on this key.

**Speech-to-text:**

- **Local Whisper large-v3-turbo on the GPU (chosen).** It transcribed all 100
  recordings (78 minutes of audio) in 41 s, with no failures. It **must** run with
  voice filtering, otherwise it invents sentences in silence.
- **Gemini live transcription.** Same accuracy, but about 1.7–2 s slower at the end
  of speech, and 13 of 100 batch requests failed.

**Text-to-speech:**

- **Kokoro (local, chosen):** 0.09 s to first sound.
- **Gemini TTS:** 1.06 s to first sound.
- Both kept IDs, dates and prices intact when played back through a speech
  recogniser.

**Projected timing** from the end of the user's speech, not yet measured end to end:

| Event | Time |
|---|---|
| First spoken reply | ≈ 1.1 s |
| First action | ≈ 3.5–4.5 s |
| Full answer | ≈ 5–8 s |

### 3.5 The chosen design (details in the plan)

```
LiveKit call  →  ears (Whisper + voice detection)  →  translator  →  Janus core (unchanged)
LiveKit call  ←  mouth (Kokoro)  ←  translator  ←  Janus core
                           tool runner (acts only after Janus's commit gate) → benchmark log
```

**The one new rule inside Janus** (switched on only for the voice setup): treat every
tool call as permanent.

- Wait 2 s of silence (3.5 s if the sentence sounds unfinished) before acting.
- Never run the same tool twice.
- No retries.
- Corrections that arrive before the action cost nothing, because Janus already
  discards outdated work.

### 3.6 Jev (TypeSafe AI)

According to its maker's blog (not independently verified):

- a fast judge that answers yes/no or multiple-choice questions with probabilities,
  e.g. "is the user done speaking?";
- claimed up to 200× faster and 400× cheaper than a normal AI model for such
  decisions.

**Fit.** It suits Janus's "AI advises, core decides" design.

**Costs.** One more online service and key at test time, and unproven on our data.

**Recommendation.** Day-4 comparison: simple timer vs LiveKit's end-of-turn model vs
Jev. Keep whichever scores best. It needs a TypeSafe AI account if we want to test it.

---

## Part 4 — Open items

| Item | Owner |
|---|---|
| LiveKit Cloud account (URL, key, secret into `.env`) | you |
| OpenAI API key into `.env` (benchmark grader) | you |
| Team name (`CollegeName_TeamName`) | you |
| Demo video recording; final submission tag | you |
| Delete `fdb_work/`? Keep or remove the Docker images? | you |
| Extension choice: camera device troubleshooting (recommended) or in-car destination change | you / team |
| Whether to trial Jev on day 4 (needs a TypeSafe AI key) | you |
| Commit the plan and this report | on your OK |
| Days 1–2 implementation per the plan (no accounts needed) | ready to start on approval |

---

## Part 5 — ECC (the Claude Code plugin) and model/effort switching

Answering "does Claude ECC automatically switch between models and effort?" Verified
in the installed plugin, `~/.claude/plugins/cache/ecc/ecc/2.2.2`.

**No automatic switching for the main session.**

- The model is whatever `/model` selects. This session ran on Opus 5.5 throughout.
- Effort is whatever `/effort` selects. You set "high" at the start; a later `/effort`
  was cancelled.
- ECC's `/model-route` command only **recommends** a model (haiku / sonnet / opus by
  task type). It does not switch anything.

**ECC's helper agents do pin their own models.** Of 68 agent definitions, 58 use
sonnet, 4 use opus and 6 use haiku. Such an agent runs on its pinned model when it is
launched. None were launched in this session; all work was done directly.

**What ECC did do automatically this session:**

- the fact-checking gate before edits and commands;
- context-size warnings;
- cost warnings;
- the session summary file in `~/.claude/session-data/`.
