# Janus companion app: integration contract (event feed v1)

This is the contract between the Janus voice agent and the companion app. The agent publishes what it hears, decides and does; the app shows it. **The app never decides anything and never calls a device or a tool itself.**

Status: the agent side of this feed is implemented (`src/prism_rt/adapters/event_feed.py`, demo mode of the voice worker; the schema below is unchanged) but has not yet been run live over LiveKit. Until that is confirmed, build against `app/sample_events.jsonl`, either by playing the file inside the app ("demo file" mode) or by publishing it into a LiveKit room with `scripts/replay_app_events.py`. The schema below is fixed; the agent will emit exactly these events.

## 1. How the app connects

| Item | Value |
|---|---|
| Transport | A LiveKit room (LiveKit Cloud). The app is an ordinary participant |
| Room | `janus-demo-room` unless told otherwise |
| Token | Minted by us: `PYTHONPATH=src:. python scripts/make_demo_token.py --identity app-<yourname>`. It prints `URL` and `TOKEN`. The token also dispatches the agent (`janus-demo`) into the room. **Never put LiveKit API keys or secrets in the app.** Paste the URL and token into the app's connect screen, or scan them from a QR code |
| Audio | Publish the phone microphone; play the agent's audio track |
| Camera | Optional: publish the back camera for the device-care scene (the agent reads one frame per second) |
| Events | Subscribe to data packets with **topic `janus.events`**. Each packet is one UTF-8 JSON object |
| Agent identity | The agent joins as a participant whose identity starts with `agent-`; only accept `janus.events` packets from it (or from `janus-replay` when testing with the replay script) |

Use headphones or Galaxy Buds in the demo. The agent has no echo cancellation, so its own voice from the phone speaker can be heard as the user talking.

## 2. Event format

Every event has these fields:

| Field | Type | Meaning |
|---|---|---|
| `v` | int | Schema version, `1` |
| `seq` | int | Increases by 1 per event in a session. Sort by it; drop duplicates by it |
| `t_ms` | int | Milliseconds since the session started |
| `type` | string | One of the types below |

Rules for the app:
- **Ignore unknown `type` values and unknown fields.** The agent may add fields later.
- **Key everything about a tool call by `call_id`.** A call moves through states; update one card, don't append a new one per event.
- A call's card is final when it receives `call_cancelled` or `call_result`.
- **Never show an action as done unless `call_result.outcome` is `confirmed` or `already`.** `unconfirmed` means it was sent but Samsung's cloud did not confirm the change; show it as "Sent, not confirmed", never as done. This honesty is the point of the system.

### Event types

| `type` | Extra fields | Show it as |
|---|---|---|
| `session_started` | `packs` (list of strings, e.g. `["smartthings", "device_care"]`), `backend` (`"real"` or `"mock"`) | A badge: **REAL · SmartThings cloud (virtual devices)** or **MOCK · simulated**. Always visible |
| `user_speaking` | `active` (bool) | A mic/level indicator |
| `heard` | `turn_id`, `text` | The user's words as a caption (may arrive in pieces within one turn) |
| `turn_closed` | `turn_id` | End of the user's turn (optional subtle marker) |
| `agent_said` | `text`, `kind`: `filler` / `ack` / `clarify` / `inform` / `final` | Agent caption. `clarify` is a question to the user: highlight it |
| `agent_interrupted` | — | The user talked over the agent and it stopped. Optional marker |
| `call_planned` | `call_id`, `tool`, `label`, `kind` (`read` / `write`), `args` (object), `security` (bool) | New card: "Planned: Lock front door". `security: true` → a lock or shield icon |
| `call_held` | `call_id`, `reason_code`, `reason` | Card state "Held", with the human `reason` (see table below). Repeats only when the reason changes |
| `confirmation_asked` | `call_id`, `question` | Card state "Waiting for your yes", show the question |
| `call_cancelled` | `call_id`, `reason` | Card state **"Never sent"**, struck through, with the reason ("You changed it to 26") |
| `call_sent` | `call_id`, `args` | Card state "Sent" (writes only; reads may skip straight to `call_result`) |
| `call_result` | `call_id`, `outcome`: `confirmed` / `already` / `unconfirmed` / `refused` / `failed`, `summary` | Final card state. `confirmed` → done (green). `already` → "Already locked, not sent again" (green, no new action). `unconfirmed` → "Sent, not confirmed" (amber). `refused` / `failed` → red, with `summary` |
| `device_state` | `device` (id), `label` (e.g. "Front door"), `capability`, `attribute`, `value`, `backend` | Update a device tile: Front door Locked, AC 26 °C, Garage Closed, Washer Paused · 4C |
| `goal_finished` | `goal_id`, `completed` (bool), `summary` | End of one request. Optional divider in the timeline |
| `ledger_summary` | `committed`, `withdrawn_before_sending`, `unconfirmed`, `failed`, `duplicates_blocked` | A footer with counters, e.g. "3 committed · 1 never sent · 0 duplicates". Sent after every finished request |

`reason_code` values for `call_held`, and the text the agent sends as `reason`:

| `reason_code` | `reason` (sent by the agent; display as is) |
|---|---|
| `user_speaking` | Waiting for you to finish speaking |
| `settling` | Waiting a moment in case you change your mind |
| `needs_go_ahead` | Waiting for your go-ahead |
| `needs_confirmation` | Security action: waiting for your yes |
| `duplicate` | Already done in this conversation; not repeating it |
| `outcome_unknown` | An earlier attempt was not confirmed; not resending blindly |
| `other` | Waiting |

The app must not hardcode reason text by tool: always display `reason` as received.

## 3. What the app should look like

Three areas on one screen (phone portrait):
1. **Captions:** what the user said and what Janus said, newest at the bottom, with a mic indicator.
2. **Action timeline:** one card per `call_id`, showing its current state:

   ```
   Planned → Held (why) → Waiting for your yes → Sent → Confirmed / Already / Sent, not confirmed
                                 └────────────→ Never sent
   ```

3. **Home tiles:** one tile per device from `device_state` (Front door, Garage, AC, Living room light, Washer), plus the REAL / MOCK badge and the ledger counters.

A connect screen (URL + token, or QR) comes before it. A camera preview is optional, for the device-care scene.

Style: clean and close to the look of a modern Android / Galaxy phone, but **no Samsung logos, product names as branding, or Samsung brand assets**. Call the app "Janus". The official SmartThings app runs next to it in the video to show that the devices really changed. Our app shows *why* and *what was stopped*, which SmartThings cannot.

## 4. Rules for a smooth integration

1. Display only. No tool calls, no SmartThings calls, no LLM calls from the app.
2. No secrets in the app or its repo. The token is pasted or scanned at runtime and expires.
3. Accept events only on topic `janus.events`, only from the agent (or `janus-replay`), only with `v == 1`.
4. Be idempotent: an event may be delivered again after a reconnect; dedupe by `seq`, update cards by `call_id`.
5. Reconnect automatically (LiveKit SDKs do this); after reconnecting, keep the existing timeline.
6. Don't block the UI on events; render them as they arrive (they come in bursts during a turn).
7. Never infer "done": only `call_result` with `confirmed` or `already` is done.
8. Keep the demo file mode (play `sample_events.jsonl` with its `t_ms` timing) in the final app. It is our fallback if the network fails during the demo.
9. Test on the actual phone used for the video, with headphones, before 21 Oct.

## 5. Testing without the agent

- **In-app demo file mode:** load `app/sample_events.jsonl` and play events using `t_ms` gaps (with a 1×/2× speed switch).
- **Through LiveKit:** join `janus-demo-room` with your token, then publish the sample file into the room:

  ```bash
  PYTHONPATH=src:. python scripts/replay_app_events.py --file app/sample_events.jsonl --room janus-demo-room
  ```

  This needs `.env` with the LiveKit keys and the `livekit` Python package (`pip install -r requirements-speech.txt`). It joins as `janus-replay` and publishes each event on `janus.events` with the original timing.

## 6. Failure drills (app → agent, topic `janus.control`)

The one message the app sends to the agent: a drill, so the demo can show how Janus behaves when something breaks. The agent applies it only when it runs in demo mode with `JANUS_DRILLS=1`, only from a participant that is not the agent, and only with `v == 1`; anything else is ignored (and logged on the agent). `janus.events` is unchanged: the effect of a drill shows up through the ordinary events (`call_result` failed / unconfirmed / already, `device_state`, `agent_said`).

Transport: a LiveKit data packet, reliable, **topic `janus.control`**, one UTF-8 JSON object:

```json
{"v": 1, "type": "drill", "name": "st_no_confirm", "on": true, "args": {}}
```

| `name` | `on` | `args` | What happens |
|---|---|---|---|
| `st_unreachable` | toggle | — | Every SmartThings call fails as if the network were down. Nothing is sent |
| `st_no_confirm` | toggle | — | Commands are accepted but the device never changes, so the result is "Sent, not confirmed" |
| `st_latency` | toggle | `{"ms": 3000}` (default 3000, max 15000) | Every SmartThings call is delayed |
| `model_slow` | toggle | `{"ms": 4000}` (default 4000, max 15000) | Every model call is delayed; the filler covers the wait |
| `model_down` | toggle | — | Every model call fails; the agent says it can't reach its reasoning service and that nothing was done |
| `external_command` | `true` (one-shot) | `{"device": "Janus Lock", "capability": "lock", "command": "unlock"}` | One command sent straight to SmartThings by "someone else", outside Janus. A later request for the same state is answered "already", nothing sent |
| `offer` | `true` (one-shot) | `{"kind": "garage_open"}` or `{"kind": "door_unlocked"}` | Janus speaks first with an offer ("The garage is open. Shall I close it?"), only if the device is in a state worth offering about; the offer then shows up as an ordinary planned call waiting for the user's yes. If someone else gets the device there first, the card ends "Never sent" with the reason |
| `reset` | `true` | — | Every drill off |

`external_command` values for the demo devices: lock `lock`/`unlock` (capability `lock`), garage `open`/`close` (capability `doorControl`; the agent uses whichever garage capability the device has), switch `on`/`off` (capability `switch`).

The app keeps its own toggle state (the agent sends no drill events). Put the drills in a hidden drawer, and send `reset` when the drawer's "all off" is pressed or a new session starts.
