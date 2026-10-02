# Decision: what we take from the Gemini and GPT research (2 Oct 2026)

Inputs: `05_gemini_deep_research_result.md`, `06_gpt_creative_ideas_result.md`.
Constraint: about 2 days to the 4 Oct 23:59 deadline, and the judged full-100
voice run through `./reproduce.sh` (60% of the score) is still not done. The
extension is 20%; it must run end to end and appear in the video.

## Pick: keep the device-care extension, make it a Samsung-home story

Gemini's #1 ("Appliance Vision Troubleshooting, SmartThings + Care") is what we
already built and verified live (camera diagnosis, fix steps, "Thursday
morning ... no, Friday afternoon" -> exactly one booking). GPT's top pick
(Festival Kitchen Conductor) is a new story: new tools, a new camera setup,
new correction flows, all unverified with live Gemini. Two days out, swapping
the extension is the bigger risk. We keep the verified core and add the parts
of both reports that are cheap and make the kernel visible.

### Build (in order)

1. **Real Samsung washer codes.** Samsung's own support pages list 4C (no
   water supply), 5C (drain), UB (unbalanced), dC (door). Our KB had generic
   E1/E3/UE/LE, and LE is wrong (on Samsung it means a leak). The old codes
   stay as aliases (older Samsung models show E1 for water and E2 for the drain).
2. **`get_device_status` (READ), SmartThings-shaped.** It returns the washer's live status:
   machine state (run/pause/stop), cycle phase, time remaining, and the error
   code on its display. These are the fields a SmartThings washer reports
   (`washerOperatingState`: machineState, washerJobState, completionTime). "My washer stopped,
   what's wrong?" is then answered without the camera. The backend is a mock,
   labelled as simulated.
3. **`control_appliance` (WRITE): pause / resume / stop.** This gives GPT's
   strongest demo pattern: two pending writes, the user corrects one, and only
   that one changes ("stop the washer and book a technician ... actually don't
   stop it, just pause it"). It also gives an honest failure: "resume it"
   while 4C is showing is refused by the appliance, and the agent says so.
4. **Effect-ledger summary**, GPT's closing shot: "N committed, M cancelled
   before commit, 0 duplicates". Printed at the end of the demo script and
   logged per session by the voice worker.

### Not now (recorded)

- **Live SmartThings REST adapter** (`SMARTTHINGS_TOKEN`, virtual device via the
  CLI). Real, free and low effort per Gemini (verified: personal access tokens made after 30 Dec 2024
  expire after 24 h), but it needs a Samsung account, a token refreshed before
  every recording, and network access during the demo. It could be added later behind an env
  var with the mock as the default; reproduction must never depend on it.
- `cancel_booking` tool (Gemini's second beat). The current honest reply ("I
  can't cancel it from here") is tested and safe; a cancel tool interacts with
  the repeat-write guard (G6 lineage) and is not worth the risk now.
- Health, Wallet, Knox, Buds, TV, car: APIs blocked by partner approval or
  hard to show on camera (Gemini's own matrix). Kitchen/power-cut/laundry
  stories from GPT: good, but each is a new extension.

### Claims in the Gemini report that are wrong or unverified (do not repeat)

- "120-second wall-clock cap", "48-hour window", "Pass@1 latency target
  1.5 s": not from our guide.
- "Round 2 extension = 40%": Round 1 is 60/20/20.
- Its demo cancels a booking with a tool we do not have.
- The citations to arXiv 2609.* papers were not checked.

### Framing (from GPT, adopted)

Say plainly: "Samsung-style connected home, simulated SmartThings / Samsung
Care backend. Our contribution is the coordination layer; the device adapter
is replaceable through the tool manifest." Never claim a live Samsung SDK
integration.
