# Samsung PRISM GenAI Hackathon — Theme 05
## Samsung-Ecosystem Extension Ideas for an Interruptible Real-Time Voice Agent

### Strategic framing

The existing Janus kernel already has the hard part: speculative reads, commit-gated writes, same-step invalidation of dependent work, a write/effect ledger, honest failure reporting, camera evidence with conflicts, and an X-ray decision log.

The product strategy should therefore **not** be to build a bigger assistant. Build one tiny, emotionally legible situation where a human changes their mind and the system visibly changes reality with them.

The strongest demo pattern is:

> **proposal/read → pending writes visibly appear → user interrupts → stale writes visibly disappear → corrected write commits once**

That makes the interruption/recovery mechanism the hero instead of treating it as a conversational footnote.

---

# A+B. 15 raw ideas + the 30-second “wow” + interruption proof

| # | Scenario | Samsung ecosystem framing | First-30-sec wow moment | The interruption that proves Janus |
|---|---|---|---|---|
| **1** | **Festival Kitchen Conductor** | Family Hub + Bespoke oven/air fryer/chimney + Galaxy voice | User points camera at a kitchen setup and says: “Start dinner prep,” while multiple appliances begin getting checked in parallel. | “Wait—no, that’s the air fryer. Don’t preheat the oven; use 180°C for 8 minutes.” **Pending oven write is cancelled before commit.** |
| **2** | **Power-Cut Recovery Mode** | SmartThings-style connected home + Galaxy | Camera sees an inverter/power panel; agent reads power status and prepares a coordinated home restore. | “Actually, leave the AC off—Dad’s sleeping.” One pending device action disappears while router/fan still proceed. |
| **3** | **Monsoon Laundry Rescue** | Bespoke washer/dryer + SmartThings | Agent sees the washing machine panel and immediately identifies cycle/state from camera. | “No, wait—don’t use the dryer. It’s raining, I’ll hang it inside.” **Dryer action is invalidated while wash plan survives.** |
| **4** | **Family Hub Fridge Correction** | Family Hub inventory + grocery list | Camera sees two milk cartons; agent says one is already in stock and prepares the grocery list accordingly. | “Actually, add milk—we used the last carton.” A previous **READ-derived grocery proposal is invalidated and rewritten.** |
| **5** | **Destination That Changes Mid-Drive** | Galaxy / simulated connected-car voice UI | “Take me to the airport” immediately triggers route lookup while the agent keeps speaking naturally. | “No—wait—hospital instead.” Airport route work is cancelled and only the hospital destination is committed. |
| **6** | **Buds Message Undo** | Galaxy Buds + Galaxy phone | “Text Rahul I’m running ten minutes late” while hands-free. | “Wait, don’t send that—I meant Rohan.” The first pending message write is cancelled; **no duplicate message exists.** |
| **7** | **Grandma Check-In** | Galaxy phone/watch + Family connectivity | “Check whether Grandma’s home routine ran and send her a good-night message.” Reads happen concurrently. | “Actually, don’t message her—she’s already asleep.” Pending notification write disappears. |
| **8** | **Accessibility: Voice-First Control** | One UI accessibility framing | User controls several phone settings entirely by voice while looking away from the screen. | “Turn captions on in English… no, Hindi.” The first settings write is cancelled before the corrected one commits. |
| **9** | **Hinglish/Regional-Language Appliance Repair** | Galaxy voice + Bespoke appliance care | User mixes English with a regional appliance term; camera identifies the physical control/indicator. | “No, I said the *other* mode.” Camera evidence and spoken correction conflict; agent pauses instead of blindly writing. |
| **10** | **Night Mode With a Child Interruption** | SmartThings home routine | “Good night” triggers a multi-device scene: lights, TV, AC, charger. | “Wait—the TV stays on, my daughter’s watching.” **Only the TV-off write is cancelled; the rest of the scene survives.** |
| **11** | **Router LED Detective** | Galaxy camera + SmartThings connectivity | Camera sees a blinking WAN/Internet indicator; agent diagnoses the likely issue and proposes a fix. | “Actually, the Internet light is green now.” The earlier fault hypothesis becomes stale, so the dependent repair action is cancelled. |
| **12** | **Diwali Guest Mode** ★ weird-ish | SmartThings + Galaxy | “Guests are coming.” Agent prepares lights, AC, TV input and door-area routine in parallel. | “No, wait—keep the living-room fan off; the baby is sleeping there.” A single branch of a multi-action routine disappears. |
| **13** | **Inverter Triage** ★ weird | SmartThings-style home + camera | User points at an inverter and says, “Restore the house.” Agent reads battery/power state before choosing actions. | “Hold on, only power the router.” All nonessential restoration writes are discarded. **India-specific and visually distinctive.** |
| **14** | **Pressure-Cooker Panic Button** ★ weird | Family Hub/Galaxy camera assistant | Camera sees an appliance indicator and the agent explains what it believes the indicator means. | User says, “No, that light is for keep-warm, not pressure.” **Vision evidence conflicts with speech; agent does not pretend certainty.** |
| **15** | **Find-the-Lost-Buds Ping** ★ weird | Galaxy Buds + Galaxy phone/watch | “Find my Buds” starts a nearby-device search and prepares an audible ping. | “Wait—don’t ping them, everyone’s asleep.” Pending audible-alert write is cancelled. Tiny scenario, but the interruption is instantly understandable. |

### What makes the ideas strong

The strongest ones have **one obvious side effect** the audience understands immediately: start appliance, send message, change destination, turn AC on, send family notification.

The weakest demo pattern would be:

> user asks something → agent thinks → user interrupts → agent says “sure”

That demonstrates conversation, not the kernel.

The stronger pattern is:

> **proposal/read → pending writes visibly appear → user interrupts → stale writes visibly disappear → corrected write commits once**

This directly demonstrates the core interruption-recovery behavior the team has already implemented.

---

# C. Three ideas that specifically exploit the unique Janus capabilities

## C1. Festival Kitchen Conductor — the “stale action disappears” demo

This is the most complete exploitation of the stack because it combines **camera evidence + parallel reads + write cancellation + ledger + decision log**.

### Suggested visible UI

A right-side X-ray panel with rows like:

```text
STEP 12 — Kitchen setup

READ
✓ identify_kitchen_device
  evidence: "air fryer / oven ambiguity"

READ
✓ get_device_state(oven)
✓ get_device_state(chimney)

PENDING WRITES
⏳ set_appliance(oven, 220°C)
⏳ set_fan(chimney, medium)
⏳ create_timer(12 min)

USER CORRECTION
"That's the air fryer..."

CANCELLED
✕ set_appliance(oven, 220°C)
  reason: read-set invalidated: device target changed

✕ set_fan(chimney, medium)
  reason: user explicitly changed plan

COMMITTED
✓ set_appliance(air_fryer, 180°C)
✓ create_timer(8 min)
```

The mechanism becomes the visual spectacle.

---

## C2. Power-Cut Recovery Mode — the ledger becomes the hero

This is the cleanest demonstration of the **write-once effect ledger**.

The agent is preparing four state-changing actions:

```text
RESTORE ROUTER       WRITE → pending
TURN ON FAN          WRITE → pending
TURN ON AC           WRITE → pending
RESTORE TV           WRITE → pending
```

User interrupts:

> “Actually, leave the AC off.”

The UI shows:

```text
AC WRITE
PENDING → CANCELLED

reason:
user intent superseded before commit
```

Then:

```text
ROUTER → COMMITTED
FAN    → COMMITTED
TV     → COMMITTED
```

And, if the UI permits:

```text
Effect ledger
0 duplicate writes
3 committed
1 cancelled
```

The key point is to **show a case where duplicate effects would be easy to create and visibly don't happen.**

---

## C3. CareCircle — read-set invalidation + honest failure

A family-care scenario can demonstrate something different:

> “Check Dad’s evening routine status and send him a check-in.”

The agent reads:

```text
get_routine_status(Dad)       READ
get_last_checkin(Dad)         READ
```

Then prepares:

```text
send_checkin(Dad)             WRITE
```

User interrupts:

> “Wait. Don't send it to Dad. Send it to Mom.”

Because the recipient is part of the read/write dependency, the stale notification is invalidated.

Then deliberately have the mock backend return:

```text
send_checkin(Mom)
→ FAILED
```

The agent must say:

> “I couldn't send the message to Mom. Nothing was sent.”

That gives a second trust behavior: **not just cancelling the wrong thing, but refusing to claim success when the tool failed.**

---

# D. Top 3 under the two-day + one-take constraint

The extension should favor **few tools, deterministic mock state, and visually obvious state changes**.

---

## 1. Festival Kitchen Conductor — top pick

### Why

It gives **Samsung ecosystem relevance + India context + camera + multiple tools + an emotionally obvious correction** without requiring actual Samsung hardware.

Use an explicit label:

> **Samsung-style connected kitchen — simulated SmartThings/Bespoke device backend**

Do not present this as a live SmartThings integration.

### Suggested tool manifest

| Tool | Type | Example args |
|---|---|---|
| `identify_kitchen_device` | READ | `{"frame_id":"frame_17"}` |
| `get_device_state` | READ | `{"device_id":"oven_01"}` |
| `get_device_state` | READ | `{"device_id":"airfryer_01"}` |
| `get_device_state` | READ | `{"device_id":"chimney_01"}` |
| `set_appliance` | WRITE | `{"device_id":"oven_01","mode":"preheat","temperature_c":220}` |
| `set_fan` | WRITE | `{"device_id":"chimney_01","level":"medium"}` |
| `create_timer` | WRITE | `{"label":"kebabs","duration_sec":720}` |

Keep the KB tiny: 3 appliances, 4–5 supported states, and a handful of realistic cooking modes.

### Demo script

#### 0:00 — camera

Point the camera at the mock appliance setup.

**User:**

> “I’m making kebabs. Preheat the oven to 220, turn the chimney to medium, and set a 12-minute timer.”

#### 0:02

**Agent:**

> “Got it. I’m checking the kitchen setup first.”

#### 0:03–0:05 — reads

```text
identify_kitchen_device
READ
{"frame_id":"frame_17"}

get_device_state
READ
{"device_id":"oven_01"}

get_device_state
READ
{"device_id":"chimney_01"}

get_device_state
READ
{"device_id":"airfryer_01"}
```

Show the three reads happening concurrently.

#### 0:06 — pending writes

```text
set_appliance
WRITE
{"device_id":"oven_01","mode":"preheat","temperature_c":220}
PENDING

set_fan
WRITE
{"device_id":"chimney_01","level":"medium"}
PENDING

create_timer
WRITE
{"label":"kebabs","duration_sec":720}
PENDING
```

### 0:08 — interrupt while writes are visibly pending

**User:**

> “Wait, no—the thing I’m pointing at is the air fryer. Don’t preheat the oven. Set the air fryer to 180 for 8 minutes, and leave the chimney off.”

**Agent:**

> “Got it. I’m cancelling the oven and chimney actions and switching the target to the air fryer.”

### 0:09–0:10 — cancellation X-ray

```text
CANCELLED
✕ oven → 220°C
reason: target superseded

CANCELLED
✕ chimney → medium
reason: explicit user correction

INVALIDATED
✕ timer → 12 min
reason: cooking plan changed
```

### 0:10–0:11 — corrected writes

```text
identify_kitchen_device
READ
{"frame_id":"frame_18"}

set_appliance
WRITE
{"device_id":"airfryer_01","mode":"cook","temperature_c":180,"duration_min":8}

create_timer
WRITE
{"label":"kebabs","duration_sec":480}
```

**Agent:**

> “Air fryer at 180 for eight minutes. The earlier oven and chimney actions were cancelled before they committed.”

### Visible ledger

```text
EFFECT LEDGER

committed: 2
cancelled: 3
duplicates: 0
```

### Optional second beat: honest failure

**User:**

> “Now start it.”

Mock tool:

```text
set_appliance(...)
→ ERROR: appliance busy
```

**Agent:**

> “I couldn’t start the air fryer because the appliance reported busy. I haven't marked it as started.”

### Fallback if live APIs fail

Run entirely against deterministic mock backends and a local device-state JSON.

Put a small, explicit label on screen:

> `SIMULATED DEVICE BACKEND`

The product being demonstrated is the **agent coordination layer**, not live SmartThings connectivity.

---

# 2. Power-Cut Recovery Mode

This is the strongest India-specific story.

### Tool manifest

| Tool | Type | Args |
|---|---|---|
| `read_power_status` | READ | `{"frame_id":"power_panel_03"}` |
| `get_device_state` | READ | `{"device_id":"router_01"}` |
| `get_device_state` | READ | `{"device_id":"fan_01"}` |
| `get_device_state` | READ | `{"device_id":"ac_01"}` |
| `get_device_state` | READ | `{"device_id":"tv_01"}` |
| `restore_device` | WRITE | `{"device_id":"router_01","power":"on"}` |
| `restore_device` | WRITE | `{"device_id":"fan_01","power":"on"}` |
| `restore_device` | WRITE | `{"device_id":"ac_01","power":"on"}` |
| `restore_device` | WRITE | `{"device_id":"tv_01","power":"on"}` |

### Demo

**User:**

> “Power just came back. Restore the router, fan, AC and TV.”

**Agent:**

> “Checking the power state and current device states.”

Reads happen.

Pending writes:

```text
router   ON   PENDING
fan      ON   PENDING
AC       ON   PENDING
TV       ON   PENDING
```

### Interrupt

> “Wait—leave the AC off. Dad’s sleeping.”

**Agent:**

> “Understood. Cancelling the AC action; the others can continue.”

### Log

```text
✕ AC ON
  cancelled: user intent changed

✓ router ON
✓ fan ON
✓ TV ON
```

### Optional failure

Mock one device as unavailable:

```text
restore_device(tv_01)
→ DEVICE_OFFLINE
```

**Agent:**

> “The TV was offline, so I restored the router and fan but not the TV.”

Again, no false “everything is done.”

---

# 3. Monsoon Laundry Rescue

This lets the team reuse the existing appliance/camera grounding rather than constructing an entirely new modality.

### Tool manifest

| Tool | Type | Args |
|---|---|---|
| `identify_appliance` | READ | `{"frame_id":"frame_42"}` |
| `get_machine_state` | READ | `{"device_id":"washer_01"}` |
| `get_weather_context` | READ | `{"city":"Mumbai"}` |
| `set_wash_cycle` | WRITE | `{"device_id":"washer_01","cycle":"mixed","temperature_c":40}` |
| `start_dryer` | WRITE | `{"device_id":"dryer_01","mode":"normal"}` |
| `create_reminder` | WRITE | `{"label":"hang laundry","delay_min":70}` |

### Demo

**User:**

> “Start a mixed wash at 40 degrees, then dry it normally when the wash is done.”

**Agent:**

> “Checking the machine and the current conditions.”

Reads.

Pending:

```text
wash 40°C              WRITE PENDING
dryer normal           WRITE PENDING
```

### Interrupt

> “Wait—don't use the dryer. It’s raining and I’ll hang it inside.”

**Agent:**

> “Okay. I’m cancelling the dryer action and keeping the wash.”

### Log

```text
✕ dryer → cancelled
  reason: user superseded drying plan

✓ washer → committed
```

Then:

```text
create_reminder
WRITE
{"label":"hang laundry","delay_min":70}
```

**Agent:**

> “The wash is still scheduled. The dryer was not started.”

### The technical point

The monsoon context is the story. The technical demonstration is **selective invalidation of a multi-step plan**.

---

# E. Naming and framing

## Recommended name: HomeFlow

### One-sentence pitch

> **HomeFlow is a voice-native connected-living agent that keeps every pending home action aligned with what you mean—even when you change your mind halfway through saying it.**

### Samsung framing

Use this title-card language:

> **HomeFlow — an AI for All connected-living concept, demonstrated with a simulated SmartThings/Bespoke device layer.**

And, if asked:

> “We’re not claiming a Samsung SDK integration here. We built the agent layer that could sit above a connected-device ecosystem.”

This keeps the positioning Samsung-relevant without making an unsupported integration claim.

---

# F. Self-critique: what a skeptical Samsung engineer could challenge

## 1. Festival Kitchen Conductor

### Skeptical reaction

**“Those aren't really Samsung appliances.”**

Correct. Do not hide that.

**“Your camera probably already knows what's in the frame because you staged the scene.”**

Fair criticism.

**“Cancelling a mock function call isn't the same as cancelling a real device side effect.”**

This is probably the strongest technical objection.

### Pre-emption

Show the exact boundary:

```text
Camera / LLM
      ↓
proposal
      ↓
Janus kernel          ← OUR CLAIM
      ↓
device adapter        ← SIMULATED
      ↓
mock device state
```

Then say:

> “The simulated adapter is intentional. Our contribution is the coordination semantics between perception, intent correction and side-effect commit; the downstream device API is replaceable through the tool manifest.”

---

## 2. Power-Cut Recovery Mode

### Skeptical reaction

**“This is just a hard-coded list of devices and a fake SmartThings dashboard.”**

Potentially, yes.

The danger is that the connected-home aspect becomes decorative.

### Pre-emption

Do not spend most of the demo showing pretty device cards.

Spend the key seconds showing **the same user correction affecting four pending writes**.

Put the actual manifest on one small overlay:

```text
restore_device() → WRITE
get_device_state() → READ
read_power_status() → READ
```

Then show that the decision log is generated from actual execution state, not pre-recorded animation.

---

## 3. Monsoon Laundry Rescue

### Skeptical reaction

**“The weather is irrelevant—the user simply said not to use the dryer.”**

Exactly. Don't oversell the weather intelligence.

The true innovation is:

> **a plan containing multiple side effects can be partially invalidated when one assumption changes.**

### Pre-emption

Make the dependency visible:

```text
WASH
  ↓
DRY

User changes drying plan
  ↓
DRY invalidated
WASH remains valid
```

Then say:

> “The monsoon context is the story. The technical demonstration is selective invalidation of a multi-step plan.”

---

# Final recommendation

## Build: HomeFlow / Festival Kitchen Conductor

Not because it has the most features, but because it provides the cleanest 3-minute narrative:

**camera sees appliance → agent starts reasoning → multiple writes become pending → human interrupts → stale actions visibly disappear → corrected action commits once → optional failure proves honesty**

The incremental work is mostly:

- Reframe the existing appliance extension.
- Add 2–4 kitchen appliance tools.
- Add a tiny kitchen KB.
- Make the X-ray panel visually strong enough to be the protagonist.
- Keep the Samsung layer explicitly simulated.

## Ideal closing shot

```text
WHAT JANUS DID

✓ 2 actions committed
✕ 3 stale actions cancelled
↻ 1 dependent plan invalidated
✓ 0 duplicate side effects
⚠ 0 false completion claims
```

Then the final spoken line:

> **“The important part isn't that the agent heard the correction. It's that the system changed what it was about to do.”**
