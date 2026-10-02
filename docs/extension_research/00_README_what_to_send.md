# Samsung-ecosystem research pipeline (local only)

Flow: Gemini Deep Research (what exists) -> GPT (creative ideas) -> you merge -> Opus in Claude Code (feasibility + build).

## Attach to Gemini (Deep Research)
1. `01_janus_context_brief.md`   (self-contained; names no other team)
2. `docs/Theme05_Participant_Guide.md` (repo)   - scoring + what the extension must be
3. `guidelines/Theme_5_Guide.md` (repo)         - official theme spec
4. optional: `guidelines/Samsung_PRISM_Y2026_GenAI_Hackathon_3rd_Edition.md` (rules)
Prompt: paste `02_prompt_gemini_deep_research.md`.

## Attach to GPT
1. `01_janus_context_brief.md`
2. `docs/Theme05_Participant_Guide.md`
Prompt: paste `03_prompt_gpt_creative_ideas.md`. (Run it WITHOUT Gemini's output first so ideas stay independent; optionally a second pass with the Gemini report attached.)

## Later, for Opus (Claude Code, in the repo)
Give it: Gemini's report, GPT's ideas, and `04_prompt_opus_feasibility.md`. Opus can read the code itself, so no file attachments are needed.

## Do NOT upload
.env / API keys, other private files, audits, tgz/log files, the whole repo. The brief is deliberately enough.

## Reality check (read before spending the day on this)
- Extension is 20% of Round 1, scored on relevance, END-TO-END working, and shown in the video. The guide says one done well beats three half-built. The 60% is the benchmark run via `./reproduce.sh` (still unrun end to end). Samsung flavour is worth adding only if it is cheap, runnable and filmable by 4 Oct 23:59 (it is 2 Oct).
- Best expected value: re-skin/extend the existing device-care extension toward Samsung devices (Galaxy phone, SmartThings appliances, Samsung Care booking), with mock backends clearly labelled, and ONE real integration only if a free public API (e.g. SmartThings) works with no approvals.

## Results and the Opus pass (2 Oct, done)

- `05_gemini_deep_research_result.md`, `06_gpt_creative_ideas_result.md`: the two research results.
- `07_decision_what_we_build.md`: the pick and what was rejected.
- `08_feasibility_triage.md`: every idea scored (effort, files, kernel change, real or mock, risk), and API claims checked.
- `09_demo_script_3min.md`: the 3-minute video script, real vs mock table, verification status, remaining work.
