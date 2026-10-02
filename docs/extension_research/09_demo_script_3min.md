# 3-minute extension demo script (Task 2 of the Opus prompt)

**Extension:** Samsung-style home care. Camera plus voice troubleshooting for a router, and a
Samsung washer on a **simulated** SmartThings / Samsung Care backend. One take, LiveKit Playground
(`JANUS_MODE=demo python -m prism_rt.voice.agent start`), X-ray / decision log visible beside it.

## What is real and what is mocked (say it on screen, and put it in the README)

| Part | Real or mock |
|---|---|
| Speech in and out, turn-taking, camera frames over LiveKit | Real |
| Reasoning (Gemini, live API), vision reading the LED | Real |
| Janus kernel: corrections, commit gate, effect ledger, decision log | Real; this is the contribution |
| Router indicator guide, error-code lists, fix steps, warranty | Mock KB (`devicecare/kb.json`). The washer codes are Samsung's published ones. |
| Washer status and control (SmartThings) | **Mock**, shaped like a SmartThings washer (`washerOperatingState`) |
| Technician booking, support tickets (Samsung Care) | **Mock** |

On-screen label for the whole demo: **"Simulated SmartThings / Samsung Care backend. The agent layer is
real; the device adapter is replaceable through the tool manifest."**

## Setup

- Laptop camera pointed at a printed router front panel: POWER green, INTERNET orange, WIFI green.
  `demo/run_device_care_demo.py` draws one (`router_photo()`).
- Headphones on. Without them the agent hears itself; noisy segments are filtered, but don't rely on that.
- Fresh worker session: the mock washer starts paused with 4C, nothing booked.

## Script

| Time | Who | Line | What the audience sees |
|---|---|---|---|
| 0:00 | Title card | "Janus home care: an agent that changes what it is about to do when you change your mind." | Mock label on screen |
| 0:10 | User (camera on router) | "My router has a light that isn't green. What does it mean?" | Camera frame analysed; `identify_indicator(router, orange, internet)` |
| 0:20 | Agent | Internet light orange: the router can't reach the internet. | READ in the X-ray |
| 0:30 | User | "How do I fix it?" | `get_fix_steps` |
| 0:45 | User | "Actually, it's my washing machine I'm worried about. It stopped in the middle of a wash. What's wrong with it?" | `get_device_status(washer)` → paused, washing, 34 min left, **4C** |
| 0:55 | Agent | Paused with error 4C: water isn't reaching the drum; a technician may be needed. | Samsung code, SmartThings-shaped status |
| 1:05 | User | "How do I fix that?" | Fix steps: tap fully open, inlet hose kinks, inlet filter |
| 1:20 | User | "Can you resume the wash?" | `control_appliance(washer, resume)` → **the washer refuses** |
| 1:25 | Agent | It won't resume while 4C is showing; fix the water supply first. | **Honest failure**: no "done" claim. Ledger counts it as refused, not committed. |
| 1:40 | User | "Okay. Open an urgent ticket and book a technician for Friday afternoon." *(stop)* … "No wait, make the technician Saturday morning." | Two writes queued; the correction lands within about 1 s of the first turn ending |
| 1:50 | Agent | Ticket opened, technician booked for Saturday morning. | `open_support_ticket(urgent)` once; `book_technician(Saturday, morning)` once; Friday is never booked |
| 2:05 | User | "Book it for Saturday morning again." | The repeat guard: "You already have … confirmed. Do you want a second one?" (answer "no") |
| 2:20 | Closing card | Effect ledger: **2 committed, 1 refused, 0 duplicates** | Printed by `effect_summary` |
| 2:30 | Narration | "The important part isn't that the agent heard the correction. It's that the system changed what it was about to do." | Show the decision-log rows for the cancelled Friday request |
| 3:00 | End | | |

### Why the turns are in this order

`control_appliance` must be called only once per session. A second call (for example "stop" after
"resume") currently hits the repeat-write guard and asks a nonsensical confirmation question. Fixing that
is a kernel change (`repeatable` tool flag) and needs team approval; see `08_feasibility_triage.md`.
The order above was checked in the deterministic harness on 2 Oct: ticket and booking once,
Saturday morning, resume refused; ledger 2 committed / 1 refused / 0 duplicates.

## Verification status (updated 2 Oct, late)

| Step | Status |
|---|---|
| Router story (camera frame, fix steps, Thursday morning → Friday afternoon, one booking) | **Verified live** (text demo, Gemini, synthetic router photo), after the 2 Oct changes |
| Washer story in this order (status 4C, fix steps, resume refused, ticket plus booking with the Saturday correction) | **Verified live** (text demo, Gemini): refusal reported honestly, one ticket, one Saturday booking, ledger 2 committed / 1 refused / 0 duplicates |
| Repeat-booking question | Tested (`test_a_new_booking_request_after_one_went_through_is_confirmed_first`) |
| Ledger summary at the end of a voice session | **Built** (`voice/agent.py`, demo mode, logged at shutdown; `entry.py` `meta["on_session"]` hook, tested); **NOT VERIFIED** in a real voice session |
| Full voice plus camera session in the Playground with this script | **NOT VERIFIED** (needs a person, headphones and a camera) |
| A second `control_appliance` call after one went through | Held by the repeat-write guard (see `08_feasibility_triage.md`); the script avoids it |

## Remaining work

1. One rehearsal in the Playground with camera and headphones (`JANUS_MODE=demo`, `scripts/make_demo_token.py`), then record.
2. Optional, needs team approval: the two small kernel fixes listed in `08_feasibility_triage.md`.
