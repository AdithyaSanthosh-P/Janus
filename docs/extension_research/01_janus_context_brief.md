# Context brief: "Janus" real-time voice agent (for research / ideation)

## Competition
Samsung PRISM GenAI Hackathon (3rd Edition, 2026), Theme 05: "Interruptible Real-Time Agents". Build a full-duplex voice-native assistant that stays responsive while reasoning, runs tool calls concurrently, and recovers cleanly when the user interrupts, corrects a detail ("Thursday... no wait, Friday"), or switches goals, without duplicate side effects or false "done" claims.
Scoring, Round 1: 60% Full-Duplex-Bench v3 run by organizers via our one-command script over LiveKit; 20% ONE use-case extension that must run end to end and be shown in a 3-5 min video; 20% docs/architecture/video (README, <=8 slide deck). Round 2: live jury demo. Deadline 4 Oct 2026 23:59. The guide explicitly says: one extension done well beats three half-built; judged on relevance, working end to end, and the video showing it work. Examples the organizers give: device troubleshooting with a camera frame, in-car destination change, hands-free kitchen assistant.

## What we have (working, tested; 575 unit tests)
- A deterministic single-writer coordination kernel (Python 3.10-3.12). LLMs are workers returning proposals; the kernel owns all state.
- Reads vs writes: reads can be speculated/parallel/retried; writes pass a commit gate (closed floor, committed user intent, no duplicate fingerprint) and are recorded in an effect ledger, so a booking is never made twice.
- Optimistic concurrency via read sets: when a user corrects a value, dependent in-flight tool calls and proposals are cancelled in the same step; reverted values (Pune->Mumbai->Pune) reuse work.
- Honest completion: if a tool fails the final message says so; deterministic response frames grounded in real tool results.
- Perception: camera frames (image bytes) and audio go to Gemini; claims from vision are tracked as evidence with conflicts.
- Voice stack over LiveKit: local Whisper STT + Kokoro TTS (or hosted), VAD-based turn ending; LLM = Gemini via API.
- Decision-log "X-ray" visualisation of every step (what was cancelled, why).
- Tool layer is a pluggable manifest: each tool declares name, JSON-schema args, READ or WRITE. Adding a toolset needs no kernel change.

## The existing extension: "device care" (camera-grounded device troubleshooting)
Person points the camera at an appliance/router, says what is wrong. Agent reads the LED/indicator from the frame, looks up the error, gives fix steps, checks warranty, and books a technician; mid-sentence corrections ("Thursday morning... no wait, Friday afternoon") produce exactly one booking with the corrected slot.
Tools (all mocked, backed by a small JSON knowledge base): identify_indicator, lookup_error_code, get_fix_steps, check_warranty (reads); book_technician, open_support_ticket (writes). Devices in KB: a Wi-Fi router and a washing machine. Run as: LiveKit Playground (voice + camera) or a scripted live demo.
It is generic (not Samsung-branded). We want to make it Samsung-ecosystem-relevant and credible to Samsung judges.

## Constraints on anything we add
- Time: ~2 days of work remain. Must be implemented as a new toolset (tools + small KB) over the existing kernel, voice+camera path, no kernel rewrite.
- Must be demo-able in a single unedited take, over voice (with optional camera). Mock backends are acceptable if clearly labelled; one real API integration is a bonus only if it needs no business approval, paid plan or special hardware.
- Must not break the benchmark path (the extension is a separate profile).
- No Samsung proprietary SDK access is assumed; the team does not have Samsung developer partnership credentials, a Galaxy dev kit, or a Samsung account with devices (assume: a laptop, an Android phone that is NOT necessarily Samsung, free-tier cloud accounts, a Gemini API key). Anything requiring those must be marked as such.
- Language: English; Indian context is fine (the hackathon is Samsung India / R&D).
