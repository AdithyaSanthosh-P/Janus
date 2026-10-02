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
| 1:35 | User | "Okay, then stop the wash." | `control_appliance(washer, stop)`: a second command to the same washer, run as a new command |
| 1:40 | User | "Okay. Open an urgent ticket and book a technician for Friday afternoon." *(stop)* … "No wait, make the technician Saturday morning." | Two writes queued; the correction lands within about 1 s of the first turn ending |
| 1:50 | Agent | Ticket opened, technician booked for Saturday morning. | `open_support_ticket(urgent)` once; `book_technician(Saturday, morning)` once; Friday is never booked |
| 2:05 | User | "Book it for Saturday morning again." | The repeat guard: "You already have … confirmed. Do you want a second one?" (answer "no") |
| 2:20 | Closing card | Effect ledger: **3 committed, 1 refused, 0 duplicates** | Printed by `effect_summary` |
| 2:30 | Narration | "The important part isn't that the agent heard the correction. It's that the system changed what it was about to do." | Show the decision-log rows for the cancelled Friday request |
| 3:00 | End | | |

### Second device command

"Stop" after the refused "resume" used to be held by the repeat-write guard (a nonsensical "do you want a
second one?") and then hit the 15 s stall salvage. Fixed 2 Oct: `control_appliance` is declared
`repeatable` in the manifest (each request is a new command; bookings are still guarded), and the salvage
waits while a "shall I go ahead?" question is open.

## Verification status (updated 2 Oct, late)

| Step | Status |
|---|---|
| Router story (camera frame, fix steps, Thursday morning → Friday afternoon, one booking) | **Verified live** (text demo, Gemini, synthetic router photo), after the 2 Oct changes |
| Washer story in this order (status 4C, fix steps, resume refused, stop, ticket plus booking with the Saturday correction) | **Verified live** (text demo, Gemini): refusal reported honestly, stop runs as a second command, one ticket, one Saturday booking, ledger 3 committed / 1 refused / 0 duplicates |
| Repeat-booking question | Tested (`test_a_new_booking_request_after_one_went_through_is_confirmed_first`) |
| Ledger summary at the end of a voice session | **Built** (`voice/agent.py`, demo mode, logged at shutdown; `entry.py` `meta["on_session"]` hook, tested); **NOT VERIFIED** in a real voice session |
| Full voice plus camera session in the Playground with this script | **NOT VERIFIED** (needs a person, headphones and a camera) |
| A second `control_appliance` call after one went through | **Fixed** (`repeatable` manifest flag); tested, and verified live in the washer story |
| Router story flakiness | Before 2 Oct late: 3 of 4 live text runs correct; in one the model did not flag the camera and the agent asked for the colour. Cause: the text demo sent one frame at the start, so by turn 1 it was older than the 5 s "look before asking" window (a live camera sends about one per second). Turns 2–3 were then appended to the goal waiting on that question and blocked behind it. Fixed in the demo (a frame per second); after the fix 6 of 6 live runs correct |

## Remaining work

1. One rehearsal in the Playground with camera and headphones (`JANUS_MODE=demo`, `scripts/make_demo_token.py`), then record.
2. Known, not changed: a new request made while the agent's question is still unanswered is appended to that goal and waits behind the question (FDB follow-ups rely on the append). The live camera avoids the question in the demo.
