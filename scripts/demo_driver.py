"""A scripted "user" for the demo worker: speaks lines into the room with the
same Kokoro voice model, sends failure drills, and prints the agent's
`janus.events` timeline with timestamps and the time to the agent's first sound.

Runs where the worker runs (the speech image, a GPU box):

    python scripts/demo_driver.py correction,unlock_yes,unconfirmed

It joins first, so the worker's room input links to it (the companion app, if
any, joins afterwards as a display). The room is created with this token's
dispatch, so the demo worker (`janus-demo`) is sent to it. Needs LIVEKIT_URL,
LIVEKIT_API_KEY and LIVEKIT_API_SECRET in the environment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import timedelta

import numpy as np
from livekit import api, rtc

from prism_rt.voice.speech import KOKORO_SAMPLE_RATE, load_kokoro

ROOM = "janus-demo-room"
FRAME_MS = 20

# A step is ("say", text) | ("pause", seconds) | ("settle", max_seconds) |
# ("drill", name, on, args) | ("note", text) | ("video", text): what our
# synthetic "camera" shows (a washer display with `text`, "" for a blank one).  `settle` waits until the agent
# has been quiet (no event, no sound) for `quiet` seconds (default 5; a read-back
# or a delayed model is silent for longer, so those scenarios say 15).
SCENARIOS: dict[str, list[tuple]] = {
    "correction": [
        ("note", "a correction: only the corrected value may reach the device"),
        ("say", "Set the thermostat to twenty-two."),
        ("pause", 0.9),
        ("say", "Actually, make it twenty-one."),
        ("settle", 40),
    ],
    "lock": [
        ("note", "make sure the door is locked (the precondition for unlock_yes)"),
        ("say", "Lock the front door."),
        ("settle", 40),
    ],
    "unlock_yes": [
        ("note", "a security write waits for a yes; someone else unlocks it meanwhile"),
        ("say", "Unlock the front door."),
        ("settle", 15),
        ("drill", "external_command", True, {"device": "Janus Lock", "capability": "lock", "command": "unlock"}),
        ("settle", 15),
        ("say", "Yes."),
        ("settle", 25),
    ],
    "unconfirmed": [
        ("note", "a device that never confirms"),
        ("drill", "st_no_confirm", True, {}),
        ("say", "Turn off the switch."),
        ("settle", 60, 15),
        ("drill", "reset", True, {}),
    ],
    "unreachable": [
        ("note", "SmartThings unreachable"),
        ("drill", "st_unreachable", True, {}),
        ("say", "Lock the front door."),
        ("settle", 40),
        ("drill", "reset", True, {}),
    ],
    "modeldown": [
        ("note", "the model is down"),
        ("drill", "model_down", True, {}),
        ("pause", 1.0),
        ("say", "Turn on the switch."),
        ("settle", 40, 12),
        ("drill", "reset", True, {}),
    ],
    "slowmodel": [
        ("note", "the model answers 4 s late: the filler covers it"),
        ("drill", "model_slow", True, {"ms": 4000}),
        ("pause", 1.0),
        ("say", "Turn on the switch."),
        ("settle", 60, 15),
        ("drill", "reset", True, {}),
    ],
    "offer_withdraw": [
        ("note", "Janus offers to close the garage; someone else closes it first"),
        ("drill", "external_command", True, {"device": "Janus Garage", "capability": "doorControl", "command": "open"}),
        ("settle", 15),
        ("drill", "offer", True, {"kind": "garage_open"}),
        ("settle", 15),
        ("drill", "external_command", True, {"device": "Janus Garage", "capability": "doorControl", "command": "close"}),
        ("settle", 20),
    ],
    "offer_yes": [
        ("note", "Janus offers to close the garage; the user accepts"),
        ("drill", "external_command", True, {"device": "Janus Garage", "capability": "doorControl", "command": "open"}),
        ("settle", 15),
        ("drill", "offer", True, {"kind": "garage_open"}),
        ("settle", 15),
        ("say", "Yes, please."),
        ("settle", 40),
    ],
    "offer_no": [
        ("note", "Janus offers to lock the front door; the user declines"),
        ("drill", "external_command", True, {"device": "Janus Lock", "capability": "lock", "command": "unlock"}),
        ("settle", 15),
        ("drill", "offer", True, {"kind": "door_unlocked"}),
        ("settle", 15),
        ("say", "No, leave it."),
        ("settle", 30),
    ],
    "camera": [
        ("note", "the camera reads the washer's code; the code clears while the booking waits"),
        ("video", "4C"),
        ("pause", 3.0),
        ("say", "My washer is showing this error code. Should I get a technician for Thursday morning?"),
        ("settle", 40, 8),
        ("video", ""),
        ("settle", 40, 12),
    ],
    "camera_explain": [
        ("note", "the camera reads the washer's code and Janus explains it"),
        ("video", "4C"),
        ("pause", 3.0),
        ("say", "My washer stopped and it's showing this error. What does it mean?"),
        ("settle", 40, 8),
    ],
    "status": [
        ("say", "Is the front door locked?"),
        ("settle", 30),
    ],
    "garage_self_interrupt": [
        ("note", "Janus offers to close the garage; someone else closes it while Janus is still asking"),
        ("drill", "external_command", True, {"device": "Janus Garage", "capability": "doorControl", "command": "open"}),
        ("settle", 15),
        ("drill", "offer", True, {"kind": "garage_open"}),
        ("await_agent", 15),
        ("pause", 1.2),
        ("drill", "external_command", True, {"device": "Janus Garage", "capability": "doorControl", "command": "close"}),
        ("settle", 20),
    ],
    "barge_in": [
        ("note", "the camera reads 4C; the user cuts Janus off mid-explanation and changes what they want"),
        ("video", "4C"),
        ("pause", 3.0),
        ("say", "My washer stopped and it's showing this error. What does it mean?"),
        ("await_agent", 30),
        ("pause", 2.5),
        ("say", "Wait, just book a technician. Friday afternoon."),
        ("settle", 40, 8),
    ],
    "washer_offer_yes": [
        ("note", "survey B1: Janus offers a technician for the washer's fault; the user accepts"),
        ("drill", "offer", True, {"kind": "washer_error"}),
        ("settle", 20),
        ("say", "Yes, please."),
        ("settle", 40),
    ],
    "washer_offer_fix": [
        ("note", "survey B1: the user changes the offered visit; Janus asks again about the new slot"),
        ("drill", "offer", True, {"kind": "washer_error"}),
        ("settle", 20),
        ("say", "Make it Friday afternoon."),
        ("settle", 25),
        ("say", "Yes."),
        ("settle", 40),
    ],
}


def _load_env() -> None:
    from pathlib import Path

    for path in (Path(".env"), Path("/janus/.env")):
        if path.exists():
            for line in path.read_text().splitlines():
                key, _, value = line.partition("=")
                if key.strip() and key.strip() not in os.environ:
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")


def washer_frame(text: str, width: int = 640, height: int = 480) -> bytes:
    """An RGBA frame of a washer's control panel whose display shows `text`."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (width, height), (225, 228, 232, 255))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((60, 90, 580, 390), radius=24, fill=(40, 44, 52, 255))
    d.ellipse((90, 160, 250, 320), outline=(150, 155, 165, 255), width=10)
    d.rectangle((300, 170, 540, 290), fill=(8, 10, 12, 255))
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 88)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    except OSError:
        font = small = ImageFont.load_default()
    if text:
        d.text((420, 230), text, fill=(255, 70, 50, 255), font=font, anchor="mm")
    d.text((320, 120), "WASHER", fill=(200, 205, 210, 255), font=small, anchor="mm")
    return img.tobytes()


class Driver:
    def __init__(self, voice, room: rtc.Room, source: rtc.AudioSource) -> None:
        self.voice = voice
        self.room = room
        self.source = source
        self.t0 = time.monotonic()
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.last_activity = time.monotonic()
        self.marker: float | None = None  # end of our last line
        self.first_sound: float | None = None
        self.video: rtc.VideoSource | None = None
        self.video_frame = washer_frame("")
        self.agent_lines = 0  # agent_said lines that are real speech (not filler or ack)
        self.mark = 0  # agent_lines at the last say/drill, for "await_agent"

    def log(self, text: str) -> None:
        print(f"+{time.monotonic() - self.t0:7.2f}s  {text}", flush=True)

    # -- audio out: our "microphone" ------------------------------------------
    async def pump(self) -> None:
        step = KOKORO_SAMPLE_RATE * FRAME_MS // 1000 * 2
        silence = bytes(step)
        pending = b""
        while True:
            if not pending:
                try:
                    pending = self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pending = b""
            chunk, pending = (pending[:step], pending[step:]) if pending else (silence, b"")
            chunk = chunk.ljust(step, b"\x00")
            await self.source.capture_frame(rtc.AudioFrame(chunk, KOKORO_SAMPLE_RATE, 1, step // 2))

    async def pump_video(self) -> None:
        while True:
            if self.video is not None:
                self.video.capture_frame(rtc.VideoFrame(640, 480, rtc.VideoBufferType.RGBA, self.video_frame))
            await asyncio.sleep(0.5)

    async def say(self, text: str) -> None:
        pcm = await asyncio.to_thread(self.voice.synthesize_pcm16, text)
        self.log(f"YOU SAY  {text!r} ({len(pcm) / 2 / KOKORO_SAMPLE_RATE:.1f}s)")
        self.mark = self.agent_lines
        self.first_sound = None
        self.marker = None
        self.queue.put_nowait(pcm)
        while not self.queue.empty():
            await asyncio.sleep(0.05)
        await asyncio.sleep(len(pcm) / 2 / KOKORO_SAMPLE_RATE)  # the pump drains it in real time
        self.marker = time.monotonic()
        self.last_activity = self.marker

    # -- the agent's sound ------------------------------------------------------
    async def listen_audio(self, track: rtc.Track) -> None:
        async for event in rtc.AudioStream(track):
            data = np.frombuffer(event.frame.data, dtype=np.int16)
            if data.size and float(np.sqrt(np.mean(data.astype(np.float32) ** 2))) > 300:
                now = time.monotonic()
                self.last_activity = now
                if self.marker is not None and self.first_sound is None:
                    self.first_sound = now - self.marker
                    self.log(f"AGENT FIRST SOUND {self.first_sound:.2f}s after we stopped")

    # -- the agent's events -----------------------------------------------------
    def on_event(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind in ("user_speaking", "turn_closed"):
            return
        self.last_activity = time.monotonic()
        if kind == "agent_said" and ev.get("kind") not in ("filler", "ack"):
            self.agent_lines += 1
        detail = {
            "heard": lambda: repr(ev.get("text")),
            "agent_said": lambda: f"[{ev.get('kind')}] {ev.get('text')!r}",
            "call_planned": lambda: f"{ev.get('tool')} {ev.get('args')} ({ev.get('call_id')})",
            "call_held": lambda: f"{ev.get('call_id')}: {ev.get('reason')}",
            "confirmation_asked": lambda: f"{ev.get('call_id')}: {ev.get('question')!r}",
            "call_cancelled": lambda: f"{ev.get('call_id')}: {ev.get('reason')}",
            "call_sent": lambda: f"{ev.get('call_id')}",
            "call_result": lambda: f"{ev.get('call_id')} {str(ev.get('outcome')).upper()}: {ev.get('summary')}",
            "device_state": lambda: f"{ev.get('label')} = {ev.get('value')}",
            "goal_finished": lambda: f"completed={ev.get('completed')} {ev.get('summary')!r}",
            "ledger_summary": lambda: f"committed={ev.get('committed')} never_sent={ev.get('withdrawn_before_sending')} "
                                       f"unconfirmed={ev.get('unconfirmed')} dup={ev.get('duplicates_blocked')}",
            "session_started": lambda: f"backend={ev.get('backend')} packs={ev.get('packs')}",
        }.get(kind, lambda: "")
        self.log(f"  {kind:<18} {detail()}")

    async def settle(self, max_s: float, quiet_s: float = 5.0) -> None:
        start = time.monotonic()
        while time.monotonic() - start < max_s:
            await asyncio.sleep(0.25)
            if time.monotonic() - self.last_activity >= quiet_s:
                return
        self.log("  (settle: still active at the limit)")

    async def drill(self, name: str, on: bool, args: dict) -> None:
        self.log(f"DRILL    {name} on={on} {args or ''}")
        self.mark = self.agent_lines
        packet = {"v": 1, "type": "drill", "name": name, "on": on, "args": args}
        await self.room.local_participant.publish_data(json.dumps(packet).encode(), reliable=True, topic="janus.control")
        self.last_activity = time.monotonic()

    async def run(self, steps: list[tuple]) -> None:
        for step in steps:
            op = step[0]
            if op == "say":
                await self.say(step[1])
            elif op == "pause":
                await asyncio.sleep(step[1])
            elif op == "settle":
                await self.settle(step[1], step[2] if len(step) > 2 else 5.0)
            elif op == "drill":
                await self.drill(step[1], step[2], step[3])
            elif op == "video":
                if self.video is None:
                    self.video = rtc.VideoSource(640, 480)
                    await self.room.local_participant.publish_track(
                        rtc.LocalVideoTrack.create_video_track("camera", self.video),
                        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA),
                    )
                    asyncio.ensure_future(self.pump_video())
                self.video_frame = washer_frame(step[1])
                self.log(f"CAMERA   shows {step[1]!r}" if step[1] else "CAMERA   shows a blank display")
            elif op == "await_agent":
                # Wait until Janus says a real line after our last say/drill (it
                # is speaking from about now on), so the next step lands over it.
                start = time.monotonic()
                while self.agent_lines <= self.mark and time.monotonic() - start < step[1]:
                    await asyncio.sleep(0.05)
                self.log("  (await_agent: Janus is speaking)" if self.agent_lines > self.mark else "  (await_agent: timed out)")
            elif op == "note":
                self.log(f"-- {step[1]}")


async def _ensure_agent(room: rtc.Room, room_name: str, driver: "Driver") -> None:
    """LiveKit dispatches the agent only when a room is created. A room left
    over from a previous run still exists, so ask for the agent explicitly if
    none joined within a few seconds."""
    for _ in range(40):
        if any(p.identity.startswith("agent-") for p in room.remote_participants.values()):
            driver.log("worker is in the room")
            return
        await asyncio.sleep(0.25)
    driver.log("no worker in the room: requesting one")
    lk = api.LiveKitAPI(os.environ["LIVEKIT_URL"], os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
    try:
        await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(agent_name="janus-demo", room=room_name))
    finally:
        await lk.aclose()
    for _ in range(80):
        if any(p.identity.startswith("agent-") for p in room.remote_participants.values()):
            driver.log("worker is in the room")
            return
        await asyncio.sleep(0.25)
    raise SystemExit("no worker joined the room; is the demo worker running?")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", help="comma-separated: " + ", ".join(SCENARIOS))
    ap.add_argument("--room", default=ROOM)
    ap.add_argument("--join-wait", type=float, default=8.0, help="seconds to let the worker join before speaking")
    ap.add_argument("--keep-open", type=float, default=0.0, help="seconds to stay in the room afterwards (for the app)")
    args = ap.parse_args()
    _load_env()
    names = [n.strip() for n in args.scenarios.split(",") if n.strip()]
    for n in names:
        if n not in SCENARIOS:
            raise SystemExit(f"unknown scenario {n!r}; known: {', '.join(SCENARIOS)}")

    voice = load_kokoro()
    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity("speaker-test")
        .with_ttl(timedelta(hours=2))
        .with_grants(api.VideoGrants(room_join=True, room=args.room, can_publish=True, can_publish_data=True))
        .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name="janus-demo")]))
    )
    room = rtc.Room()
    source = rtc.AudioSource(KOKORO_SAMPLE_RATE, 1, queue_size_ms=100)
    driver = Driver(voice, room, source)

    @room.on("data_received")
    def _data(packet: rtc.DataPacket) -> None:
        if packet.topic == "janus.events":
            try:
                driver.on_event(json.loads(bytes(packet.data).decode()))
            except Exception as exc:  # noqa: BLE001 - a bad packet must not stop the run
                print("bad event", exc, flush=True)

    @room.on("track_subscribed")
    def _track(track: rtc.Track, publication, participant: rtc.RemoteParticipant) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO and participant.identity.startswith("agent-"):
            asyncio.ensure_future(driver.listen_audio(track))

    await room.connect(os.environ["LIVEKIT_URL"], token.to_jwt())
    await room.local_participant.publish_track(
        rtc.LocalAudioTrack.create_audio_track("mic", source),
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
    )
    pump = asyncio.ensure_future(driver.pump())
    driver.log(f"joined {args.room}; waiting for the worker")
    await _ensure_agent(room, args.room, driver)
    await asyncio.sleep(args.join_wait)
    try:
        for name in names:
            driver.log(f"===== scenario: {name}")
            await driver.run(SCENARIOS[name])
        if args.keep_open:
            driver.log(f"done; staying {args.keep_open:.0f}s")
            await asyncio.sleep(args.keep_open)
    finally:
        pump.cancel()
        await room.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
