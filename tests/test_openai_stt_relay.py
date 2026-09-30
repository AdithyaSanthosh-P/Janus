"""scripts/fdb_v3/openai_stt_relay.py keeps the OpenAI key on a trusted machine:
it must forward only transcription requests for the allowed model, add the key
itself, and refuse everything else."""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

# Captured before any test patches the relay's urlopen (the same function object).
_ORIGINAL_URLOPEN = urllib.request.urlopen

_spec = importlib.util.spec_from_file_location(
    "openai_stt_relay", Path(__file__).resolve().parents[1] / "scripts" / "fdb_v3" / "openai_stt_relay.py"
)
relay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(relay)

MODEL = relay.DEFAULT_MODELS[0]


class _FakeUpstream:
    def __init__(self):
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)

        class _Resp:
            status = 200

            def read(self_inner):
                return json.dumps({"text": "track order BOB12"}).encode()

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        return _Resp()


@pytest.fixture
def server(monkeypatch):
    upstream = _FakeUpstream()
    monkeypatch.setattr(relay.urllib.request, "urlopen", upstream)
    stats = {"ok": 0, "refused": 0, "errors": 0}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), relay.make_handler("sk-real", (MODEL,), relay._RateLimit(2), stats))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", upstream
    srv.shutdown()


def _multipart(model: str) -> tuple[bytes, str]:
    boundary = "xyz"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{model}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
        f"Content-Type: audio/wav\r\n\r\nRIFF....\r\n--{boundary}--\r\n"
    ).encode()
    return body, f"multipart/form-data; boundary={boundary}"


def test_transcription_is_forwarded_with_the_real_key(server):
    url, upstream = server
    body, ctype = _multipart(MODEL)
    status, data = _client_post(url, "/v1/audio/transcriptions", body, ctype)
    assert status == 200 and data["text"] == "track order BOB12"
    assert upstream.requests[0].get_header("Authorization") == "Bearer sk-real"


def test_other_endpoints_are_refused(server):
    url, upstream = server
    status, _ = _client_post(url, "/v1/chat/completions", b'{"model": "gpt-4.1"}')
    assert status == 404 and not upstream.requests


def test_other_models_are_refused(server):
    url, upstream = server
    body, ctype = _multipart("whisper-1")
    status, _ = _client_post(url, "/v1/audio/transcriptions", body, ctype)
    assert status == 403 and not upstream.requests


def test_rate_limit_applies(server):
    url, _ = server
    body, ctype = _multipart(MODEL)
    codes = [_client_post(url, "/v1/audio/transcriptions", body, ctype)[0] for _ in range(3)]
    assert codes == [200, 200, 429]


def _client_post(url: str, path: str, body: bytes, ctype: str = "application/json"):
    req = urllib.request.Request(url + path, data=body, method="POST", headers={"Content-Type": ctype, "Authorization": "Bearer placeholder"})
    try:
        with _ORIGINAL_URLOPEN(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_a_model_name_planted_in_the_audio_does_not_pass(server):
    """Review finding: the allowlist was a regex over the whole body, so an
    allowed model string planted in the audio bytes let another model through."""
    url, upstream = server
    boundary = "xyz"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
        f"Content-Type: audio/wav\r\n\r\nRIFF name=\"model\"\r\n\r\n{MODEL}\r\n\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n--{boundary}--\r\n"
    ).encode()
    status, _ = _client_post(url, "/v1/audio/transcriptions", body, f"multipart/form-data; boundary={boundary}")
    assert status == 403 and not upstream.requests
