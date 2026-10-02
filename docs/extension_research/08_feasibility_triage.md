# Feasibility triage: every idea from the Gemini and GPT research (2 Oct 2026)

Task 1 of `04_prompt_opus_feasibility.md`. Sources: `05_gemini_deep_research_result.md`
(10 shortlisted scenarios plus the ecosystem surfaces), `06_gpt_creative_ideas_result.md`
(15 ideas). Decision and the build list: `07_decision_what_we_build.md`.

Effort, in this codebase: **S** = new tools/KB entries in `src/prism_rt/devicecare/` and a
demo turn, under 2 h. **M** = a new toolset + KB + demo story + tests, 3–6 h.
**L** = new modality, new client, real external integration, or a kernel change, over 6 h or blocked.

Every candidate below can be "a new toolset + KB" behind `JANUS_MODE=demo` / `demo_config()`
unless the Kernel column says otherwise. No candidate needs a change to the benchmark profile.

## API facts, checked against official pages

| Claim (report) | Checked | Result |
|---|---|---|
| SmartThings PATs made after 30 Dec 2024 expire after 24 h (Gemini) | developer.smartthings.com/docs/getting-started/authorization-and-permissions | **True**: "A PAT is valid for 24 hours from the creation date." Recommended alternative: a Service Integration (OAuth). |
| SmartThings CLI can create virtual devices (Gemini) | github.com/SmartThingsCommunity/smartthings-cli | **True**: `virtualdevices:create` (from a device profile) and `virtualdevices:create-standard` (standard prototypes). |
| SmartThings rate limit 200 req/min (Gemini) | Not on the authorization page; the cited source is a Scribd copy | **Unverified**; irrelevant while the backend is a mock. |
| Samsung washer codes 4C / 5C / UB / dC (Gemini's "4C = water supply") | samsung.com support pages (uk, ie, ae) | **True**: 4C water supply, 5C drain, UB unbalanced, dC door. LE on Samsung = leak (our old KB had it as door lock; fixed). |
| SmartThings washer reports machineState / jobState / completionTime | Home Assistant / openHAB integrations of the SmartThings API | **True** (`washerOperatingState`). |
| Samsung Health Data SDK needs partner approval; Android-only | Gemini cites developer.samsung.com | Plausible, **not re-checked**; irrelevant (rejected anyway). |
| "120 s wall-clock cap", "48-hour window", "Pass@1 latency target 1.5 s", "extension = 40%" | Our guide | **Wrong for us.** Round 1 = 60 / 20 / 20. Do not repeat these. |

## Gemini shortlist (10)

| # | Idea | Effort | Files that change | Kernel change? | Real or mock | Demo risk | Verdict |
|---|---|---|---|---|---|---|---|
| G1 | Appliance vision troubleshooting (SmartThings + Care) | **S** (mostly built) | `devicecare/tools.py`, `kb.json`, demo script | No | Mock (SmartThings-shaped) | Low; camera turn verified live 29–30 Sep | **PICKED** (it is our extension) |
| G2 | Ambient home correction ("AC 22 … just the fan and dim the lights") | M | new `smarthome/` toolset + KB, demo | No | Mock; real possible via CLI virtual devices | Low–medium | Not now; overlaps with G1 + `control_appliance` |
| G3 | Health goal re-calibration (calories) | M | new toolset | No | Mock only (SDK blocked) | Low, but voice-only, weak on camera | Rejected: no visual, SDK claim invites questions |
| G4 | Scene automation ("movie mode … leave the blinds open") | M | new toolset | **Probably**: removing one action from a multi-action plan mid-request is not a tested kernel path | Mock / virtual devices | Medium | Rejected (kernel risk) |
| G5 | Car destination pivot + message | M | new toolset | No | Mock | Medium; voice-only | Rejected: no Samsung hook we can show |
| G6 | Wallet boarding-pass re-booking | M | new toolset | No | Mock (API needs partner signing) | Low reward | Rejected |
| G7 | Digital key / Knox revocation ("John … no, Sarah") | M | new toolset | No | Mock (Knox is enterprise-licensed) | Medium | Rejected: security claims we cannot back |
| G8 | Family Hub inventory ("add milk … check oat milk first") | M | new toolset + KB | No | Mock | Medium; turning an add into a check is a hard interpretation | Rejected |
| G9 | TV media search pivot | M–L | new toolset | No | Real needs a physical TV | High | Rejected |
| G10 | Buds translation control | M | new toolset | No | Mock | High; nothing audible over WebRTC | Rejected |

## GPT ideas (15)

| # | Idea | Effort | Kernel change? | Real or mock | Demo risk | Verdict |
|---|---|---|---|---|---|---|
| 1 | Festival Kitchen Conductor | M–L: 3 appliances, 6–7 tools, new camera scene | No, but the multi-write correction it relies on (target change cancels 3 writes) is untested live | Mock | **Medium–high**: new camera props and new correction flows, 2 days out | Rejected for now; **its demo pattern is adopted** (pending writes → correction → stale one withdrawn → ledger summary) |
| 2 | Power-cut recovery (4 restores, "leave the AC off") | M | **Yes, probably**: dropping one action from a pending batch (same as G4) | Mock | Medium | Rejected (kernel risk) |
| 3 | Monsoon laundry rescue | M | Same "drop one action" path | Mock | Medium | Rejected; the laundry framing is reused in our washer story |
| 4 | Family Hub fridge correction | M | No | Mock | Medium | Rejected (same as G8) |
| 5 | Destination change mid-drive | M | No | Mock | Medium | Rejected (same as G5) |
| 6 | Buds message undo ("Rahul … Rohan") | S–M | No | Mock | Low, but no camera and no Samsung hook on screen | Rejected |
| 7 | Grandma check-in | M | No | Mock | Medium; emotionally strong, ecosystem weak | Rejected |
| 8 | Voice-first accessibility settings | M | No | Mock | Medium | Rejected |
| 9 | Hinglish appliance repair (camera vs speech conflict) | S for the conflict (built: camera/user disagreement asks), L for Hinglish STT | No | Mock | High (STT) | Partially there already; not extended |
| 10 | Night mode, "the TV stays on" | M | Yes, probably (drop one action) | Mock | Medium | Rejected |
| 11 | Router LED detective | **S** (built) | No | Mock | Low; verified live | **In the extension** (router story) |
| 12 | Diwali guest mode | M | Yes, probably | Mock | Medium | Rejected |
| 13 | Inverter triage | M | Yes, probably | Mock | Medium | Rejected |
| 14 | Pressure-cooker indicator (vision vs speech conflict) | S–M | No (conflict-asking is built) | Mock | Medium; new camera prop | Rejected for now |
| 15 | Find-my-Buds ping | S | No | Mock | Low; tiny | Rejected: too small to carry the video |

## Also considered

| Idea | Effort | Kernel change? | Verdict |
|---|---|---|---|
| Live SmartThings REST adapter (virtual washer via `virtualdevices:create`) | M + a Samsung account | No | **Deferred.** Credentials needed (24 h PAT before every recording), network during the demo; reproduction must never depend on it. Ask the team. |
| `cancel_booking` tool (Gemini's last beat) | S–M | Touches the repeat-write guard (G6 lineage) | Deferred; the current honest reply ("I can't cancel it from here") is tested. |
| `control_appliance` declared as repeatable, so "resume" after "stop" is not treated as a repeat of a write that already went through | S | Yes: `ToolSpec.repeatable` (manifest), `replies.earlier_confirmed_writes` skips it, the write fingerprint is scoped to the request (G5 still dedupes within one), `TraceChecker` CS-13 likewise | **Done 2 Oct.** No benchmark tool declares it. |
| The 15 s stall rescue firing while the agent's own "shall I go ahead?" question is open ("I couldn't finish this in time") | S | Yes (`kernel/task.py._maybe_salvage_stalled_turn`) | **Done 2 Oct**, same rule as the existing clarify exemption. |

## What is built (2 Oct)

- KB: Samsung washer codes (4C/4E/E1, 5C/5E/E2, UB/UE, dC/dE) and a SmartThings-shaped washer status.
- `get_device_status` (READ) and `control_appliance` (WRITE: pause / resume / stop; refuses resume while an error is showing).
- `observability/effects.py`: committed / withdrawn before sending / refused or failed / duplicates; printed by the demo script and logged by the voice worker at session end (demo mode).
- `demo/run_device_care_demo.py --story washer` (the 3-minute script's order); README extension section with the real/simulated table; 10 new deterministic tests; suite 586/586.
- Live (Gemini, text demo): router story and washer story both correct (see `09_demo_script_3min.md`). **NOT VERIFIED:** any of it over LiveKit voice with the camera.
