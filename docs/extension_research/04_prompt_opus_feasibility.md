(For Claude Code / Opus in this repo, after you have Gemini's report and GPT's ideas. Paste both below the prompt or point to their files.)

Read currentStatus.md first (read-order, state, handoff), then src/prism_rt/devicecare/ (tools.py, kb.json), profiles.py demo_config(), voice/agent.py JANUS_MODE=demo, and demo/run_device_care_demo.py. Attached are (1) a Samsung-ecosystem research report and (2) a creative idea list. The team has until 4 Oct 23:59; the 60% benchmark reproduction is still the top priority, so this must be small.

Tasks:
1. Feasibility triage: for every candidate feature/idea, state S/M/L effort in this codebase, which files change, whether it needs a kernel change (reject if so), whether it can be a new toolset + KB, real vs mock backend, and demo risk. Verify any claim about an API by reading its official docs yourself; do not trust the report blindly.
2. Pick ONE extension (or a minimal bundle) maximizing (Samsung relevance x end-to-end reliability x interruption story). Write the exact tool manifest (name, args, READ/WRITE), KB additions, and a 3-minute demo script with the interruptions. Say what is real and what is mocked, and label mocks honestly in code and README.
3. Implement it behind the demo profile only, with tests (deterministic, ScriptedProvider) including a correction-cancels-pending-write scenario, then run the full suite and the live demo. Report NOT VERIFIED for anything not run.
4. Update currentStatus.md (handoff) per its rules. Keep committed wording neutral and do not mention any other team. Do not touch the FDB/benchmark profile or kernel behaviour.
Stop and ask if a feature needs credentials, hardware, or a kernel change.
