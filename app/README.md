# Janus companion app

A phone app for the Round 2 demo. It joins the Janus voice agent's LiveKit room and shows, live, what Janus heard, what it planned, what it held back and why, what it **never sent** because you corrected yourself, and what Samsung's SmartThings cloud actually confirmed.

The agent does all the work. The app is a window onto it: it sends your voice (and optionally the camera) and displays the event feed. If the app is not ready, the demo still runs (LiveKit Playground + the SmartThings app), so nothing else depends on it.

## Files

| File | What it is |
|---|---|
| `INTEGRATION.md` | **The contract:** how to connect, the event format, what each event means, how to display it, and the integration rules. Read it first |
| `sample_events.jsonl` | A recorded demo session in that format (leaving home with a correction, a duplicate request, a security confirmation declined, an unconfirmed command, a washer diagnosis). Build and test against it |
| `../scripts/replay_app_events.py` | Publishes the sample file into a LiveKit room, so you can test the real data channel without the agent |
| `../scripts/make_demo_token.py` | Mints the join token (ask the Janus maintainer to run it; tokens expire) |

## Suggested stack

Any LiveKit client SDK works (Web/JavaScript, Android/Kotlin, Flutter, React Native). The fastest route is a **web app (React + `livekit-client`)**, opened in the phone's browser or installed as a PWA. If an installable APK is wanted, wrap the same web app with Capacitor. LiveKit publishes open-source agent starter apps for web and Android that already handle connecting, the mic and playing the agent's audio; starting from one and adding the timeline is a good shortcut (check the current repos on github.com/livekit-examples).

## Milestones

1. **Demo file mode:** the app plays `sample_events.jsonl` and renders captions, the action timeline and the home tiles correctly. No network needed.
2. **Live data channel:** connect to the room with a token, run `scripts/replay_app_events.py`, and see the same session arrive live.
3. **Voice:** publish the mic, play the agent's audio, and talk to the real agent (once the agent side is ready; ask the maintainer to start it).
4. **Polish and rehearsal** on the phone used for the video, with headphones.

Target: milestones 1–2 by 20 Oct, 3–4 by 21 Oct (the video is recorded on 21–22 Oct; the demo round is 23 Oct).

## Do not

- Put any API key, secret or token in the code or the repo.
- Call SmartThings, the tools or any AI model from the app.
- Use Samsung logos or brand assets.
- Show anything as done unless the event feed says `confirmed` or `already`.
