"""Keeps the OpenAI key on a trusted machine while a GPU box you don't fully
trust runs the voice agent with hosted speech-to-text (JANUS_STT=openai).

Run this on the trusted machine (it reads OPENAI_API_KEY from the environment
or ./.env), then SSH to the GPU box with a reverse tunnel:

    python scripts/fdb_v3/openai_stt_relay.py            # listens on 127.0.0.1:8787
    ssh -R 8787:127.0.0.1:8787 -p <PORT> -i <KEY> root@<HOST>

On the GPU box, point the agent at the tunnel with a placeholder key:

    OPENAI_BASE_URL=http://127.0.0.1:8787/v1 OPENAI_API_KEY=relay JANUS_STT=openai ...

The relay forwards exactly one thing -- POST /v1/audio/transcriptions for the
allowed model(s) -- adds the real key itself, and refuses everything else
(chat, embeddings, files, other models). Requests are capped in size and rate,
so even someone else on the GPU box can only ever transcribe a little audio
while the tunnel is open. Closing the SSH session closes the tunnel.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UPSTREAM = "https://api.openai.com/v1/audio/transcriptions"
PATH = "/v1/audio/transcriptions"
DEFAULT_MODELS = ("gpt-4o-mini-transcribe-2025-12-15",)
MAX_BODY = 5 * 1024 * 1024  # a 60 s 16 kHz mono WAV is ~1.9 MB
_MODEL_FIELD = re.compile(rb'name="model"\r\n\r\n([^\r\n]+)\r\n')


def _load_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "")
    env = Path(".env")
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("OPENAI_API_KEY not found in the environment or ./.env")
    return key


class _RateLimit:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.times: list[float] = []
        self.lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self.lock:
            self.times = [t for t in self.times if now - t < 60]
            if len(self.times) >= self.per_minute:
                return False
            self.times.append(now)
            return True


def make_handler(key: str, models: tuple[str, ...], limit: _RateLimit, stats: dict):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet default logging; we print our own line
            pass

        def _reply(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._reply(404, {"error": "only POST /v1/audio/transcriptions is relayed"})

        def do_POST(self):
            if self.path.split("?", 1)[0] != PATH:
                stats["refused"] += 1
                return self._reply(404, {"error": "only POST /v1/audio/transcriptions is relayed"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                stats["refused"] += 1
                return self._reply(413, {"error": f"body must be 1..{MAX_BODY} bytes"})
            body = self.rfile.read(length)
            match = _MODEL_FIELD.search(body)
            model = match.group(1).decode(errors="replace") if match else ""
            if model not in models:
                stats["refused"] += 1
                return self._reply(403, {"error": f"model {model!r} is not relayed"})
            if not limit.allow():
                stats["refused"] += 1
                return self._reply(429, {"error": "relay rate limit"})
            req = urllib.request.Request(UPSTREAM, data=body, method="POST")
            req.add_header("Authorization", f"Bearer {key}")
            req.add_header("Content-Type", self.headers.get("Content-Type", ""))
            started = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data, code = resp.read(), resp.status
            except urllib.error.HTTPError as exc:
                data, code = exc.read(), exc.code
            except Exception as exc:  # noqa: BLE001 - report upstream failure to the caller
                stats["errors"] += 1
                return self._reply(502, {"error": f"upstream: {exc}"})
            stats["ok" if code == 200 else "errors"] += 1
            print(f"[relay] {code} {model} {length/1024:.0f} KiB {time.monotonic()-started:.2f}s "
                  f"(ok={stats['ok']} refused={stats['refused']} errors={stats['errors']})", flush=True)
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--per-minute", type=int, default=120, help="max relayed requests per minute")
    ap.add_argument("--model", action="append", help="allowed model (repeatable); default: the pinned snapshot")
    args = ap.parse_args()
    models = tuple(args.model or DEFAULT_MODELS)
    stats = {"ok": 0, "refused": 0, "errors": 0}
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(_load_key(), models, _RateLimit(args.per_minute), stats))
    print(f"[relay] listening on 127.0.0.1:{args.port}, relaying {PATH} for {', '.join(models)}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
