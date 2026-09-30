"""Device-care extension demo: camera-grounded troubleshooting, one safe booking.

Runs the real Janus kernel with the live Gemini API (text in, text out -- the
voice/camera path is the LiveKit worker, `JANUS_MODE=demo`, see README) over
the mock device-care tools in `prism_rt.devicecare`.

Story (three user turns):
  1. The camera sees a router whose INTERNET light is orange. "It has a
     blinking light that is not green -- what does it mean?"  -> the frame is analysed, the light is looked up.
  2. "How do I fix it?"    -> the fix steps for the diagnosed issue.
  3. "Book a technician for Thursday morning -- no wait, make it Friday
     afternoon."             -> ONE booking, for Friday afternoon; the Thursday
     request is cancelled before anything is booked.

Run (needs GEMINI_API_KEY in the environment or .env, and Pillow for the
synthetic router photo; or pass --image your.jpg):
    PYTHONPATH=src:. python demo/run_device_care_demo.py [--image photo.jpg] [--turn N]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from io import BytesIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(REPO_ROOT / ".env")

from prism_rt.devicecare import DeviceCareToolset  # noqa: E402
from prism_rt.model.types import ActionType, JobKind  # noqa: E402
from prism_rt.profiles import demo_config  # noqa: E402
from prism_rt.sim.harness import SimHarness  # noqa: E402
from prism_rt.workers.gateway import GeminiProvider, MediaPart  # noqa: E402

RULE = "-" * 78


def router_photo() -> bytes:
    """A synthetic router front panel: POWER green, INTERNET orange, WIFI green."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 360), (30, 32, 36))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 90, 600, 270), radius=24, fill=(235, 235, 238), outline=(90, 90, 96), width=4)
    d.text((70, 105), "Aria R500", fill=(60, 60, 66))
    for x, label, colour in ((150, "POWER", (40, 200, 90)), (320, "INTERNET", (255, 140, 0)), (490, "WIFI", (40, 200, 90))):
        d.ellipse((x - 26, 160, x + 26, 212), fill=colour, outline=(50, 50, 55), width=3)
        d.text((x - 28, 230), label, fill=(40, 40, 46))
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="a photo of the device (default: a synthetic router panel)")
    ap.add_argument("--trace", action="store_true", help="print every model call and its JSON answer")
    ap.add_argument("--turn", type=int, default=0, help="run only this turn (1-3); 0 = all")
    args = ap.parse_args()

    if args.image:
        path = Path(args.image)
        photo = MediaPart(mime_type="image/png" if path.suffix.lower() == ".png" else "image/jpeg", data=path.read_bytes())
    else:
        photo = MediaPart(mime_type="image/png", data=router_photo())

    toolset = DeviceCareToolset()
    tools = {t["name"]: {"latency_ms": 150, "response": None} for t in toolset.manifest}
    live_exec = toolset.make_executor()

    import asyncio

    def run_tool(name: str, arguments: dict) -> dict:
        return asyncio.run(live_exec(name, arguments))

    provider = GeminiProvider()
    if args.trace:
        inner = provider.complete_json

        def traced(kind, prompt, schema, *, media=None):
            out = inner(kind, prompt, schema, media=media)
            print(f"            <{kind}{' +image' if media else ''}> -> {json.dumps(out)[:700]}")
            return out

        provider.complete_json = traced  # type: ignore[method-assign]

    h = SimHarness(
        demo_config(),
        seed=7,
        provider=provider,
        worker_latency_us={k: 15_000 for k in JobKind},
        tools={name: {"latency_ms": 150, "handler": (lambda args, n=name: run_tool(n, args))} for name in tools},
        blob_resolver=lambda frame_id: photo if frame_id.startswith("cam-") else None,
    )

    print(RULE)
    print("Device-care demo: camera + voice troubleshooting, one safe booking")
    print(RULE)

    h.send(0, [{"type": "manifest", "payload": {"tools": toolset.manifest}}])
    h.send(20_000, [{"type": "video_frame", "payload": {"frame_id": "cam-1"}}])

    turns = [
        [("my router has a blinking light that is not green. what does it mean?", 0)],
        [("okay, how do I fix it?", 0)],
        [("book a technician for Thursday morning", 0), ("no wait, make it Friday afternoon", 1_500_000)],
    ]
    t = h.clock.now_us() + 200_000
    for n, chunks in enumerate(turns, start=1):
        if args.turn and n != args.turn:
            continue
        print(f"\nTurn {n}")
        for text, gap in chunks:
            t += gap
            print(f"  USER      {text}")
            h.send(t, [{"type": "text_chunk", "payload": {"text": text}}])
        t += 1_100_000
        h.send(t, [{"type": "end_of_turn", "payload": {}}])
        quiet_since, limit = t, t + 60_000_000
        while t < limit:
            t += 20_000
            report = h.advance(t)
            for er in report.emit_report.emitted:
                a = er.action
                if a.action_type in (ActionType.SPEAK, ActionType.CLARIFY, ActionType.FINAL):
                    print(f"  AGENT     {a.body.text}")
                    quiet_since = t
                elif a.action_type == ActionType.TOOL_CALL:
                    print(f"            [tool {a.body.tool_name}({dict(a.body.arguments)})]")
                    quiet_since = t
                elif a.action_type == ActionType.CANCEL:
                    print("            [cancelled superseded work]")
            if t - quiet_since > 20_000_000 and not h.store.call_ledger.non_terminal():
                break
        t += 500_000

    print(RULE)
    print(f"bookings made: {toolset.bookings}")
    print(f"tickets opened: {toolset.tickets}")
    print(RULE)


if __name__ == "__main__":
    main()
