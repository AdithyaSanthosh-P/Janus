# Running the device-care demo (voice + camera)

The extension: a voice agent that reads a device from the camera, diagnoses it, and books exactly one technician visit, with the safety kernel underneath. The device backend (a Wi-Fi router and a Samsung-style washing machine) is a simulation; the agent layer is real.

**No GPU or cloud machine is needed.** Reasoning and camera understanding (Gemini) and speech-to-text (OpenAI) are hosted; LiveKit Cloud carries audio and video. Only two small models run locally: Silero VAD and Kokoro-82M text-to-speech, which use a GPU if there is one and the CPU otherwise.

## You need
- Linux, macOS or Windows with WSL; Python 3.11 or 3.12
- A webcam (or a phone joined to the same room), headphones, Chrome or Firefox
- Your own keys in `.env` (copy `.env.example`): a LiveKit Cloud project (`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`), `GEMINI_API_KEY`, `OPENAI_API_KEY`
- `demo/router_panel.png` open on a phone or printed: it is the router the camera looks at

## 1. Install (once, about 10–15 minutes)
```bash
git clone -b fdb-v3-integration https://github.com/AdithyaSanthosh-P/Janus.git
cd Janus
python3.11 -m venv .venv && source .venv/bin/activate
# NVIDIA GPU (any recent card with 4 GB or more is enough):
pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cu126
# no GPU: pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-speech.txt
python -m spacy download en_core_web_sm
cp .env.example .env        # then fill in the keys
```

## 2. Check it works (text only, one minute)
```bash
PYTHONPATH=src:. python demo/run_device_care_demo.py --story washer
```
It ends with `Effect ledger: 3 committed, 0 withdrawn before sending, 1 refused or failed, 0 duplicates`. If the agent prints nothing, the Gemini key has no credit (HTTP 402).

## 3. Run it live
```bash
# Terminal 1: the agent (the first start downloads two small models)
JANUS_MODE=demo JANUS_STT=openai JANUS_DECISION_LOG_DIR=run_output/demo \
  PYTHONPATH=src:. python -m prism_rt.voice.agent start
# Terminal 2: a join token
PYTHONPATH=src:. python scripts/make_demo_token.py
```
`JANUS_STT=openai` matters: the agent's default is local Whisper, which needs a GPU.

Open <https://agents-playground.livekit.io>, choose manual connect, paste the URL and token, allow the microphone and camera, and put headphones on. Keep the token and `.env` off any screen recording.

## 4. What to say
The full 3-minute script is `docs/extension_research/09_demo_script_3min.md`.

**Router (camera):** hold the router image steady in frame for a second, then:
1. "My router has a light that isn't green. What does it mean?" → it reads the orange INTERNET light from the camera.
2. "How do I fix it?"

**Washer (voice):**
3. "My washing machine stopped in the middle of a wash. What's wrong with it?" → paused, error 4C (no water supply).
4. "How do I fix that?"
5. "Can you resume the wash?" → it refuses honestly while 4C is still showing.
6. "Okay, then stop the wash."
7. "Open an urgent ticket and book a technician for Friday afternoon… no wait, make it Saturday morning." → one ticket and **one** booking, Saturday morning; Friday is never booked.

## If something goes wrong
| Symptom | Fix |
|---|---|
| The agent never speaks | The Gemini key is out of credit (HTTP 402), or `.env` is missing a key |
| It answers itself or talks over you | Wear headphones |
| It asks for the light colour instead of reading it | Hold the image steady for 1–2 seconds before asking |
| You want a clean start (washer back to "paused, 4C", nothing booked) | Make a token for a new room: `python scripts/make_demo_token.py --room take2`; each room starts fresh |
| The Playground will not connect | The token expired (12 hours by default): make a new one |

Replies take a few seconds: the turn closes after 1.5 s of silence, transcription is hosted, and writes wait a short settle window so a late correction can still cancel them. Do not start the benchmark worker (without `JANUS_MODE=demo`) on a laptop; it is meant for a GPU machine.
