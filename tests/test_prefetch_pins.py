"""scripts/fdb_v3/prefetch_models.py reads the pinned model revisions out of speech.py.

The Docker image build copies only speech.py ahead of the rest of the source (to keep
the multi-GB weights layer cached) and runs the prefetch script on it. speech.py began
importing prism_rt on 23 Oct, so importing it at that point failed and a fresh
`docker build` broke (found on 25 Oct on a new VM; runs with --skip-build hid it). The
script therefore parses the file instead of importing it.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEECH = ROOT / "src" / "prism_rt" / "voice" / "speech.py"


def _load_prefetch(monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=lambda *a, **k: None))
    monkeypatch.setenv("JANUS_SPEECH_PY", str(SPEECH))
    spec = importlib.util.spec_from_file_location("prefetch_models_under_test", ROOT / "scripts" / "fdb_v3" / "prefetch_models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pins_are_read_from_speech_py_without_importing_it(monkeypatch):
    module = _load_prefetch(monkeypatch)
    pins = module.speech
    assert pins.WHISPER_REPO and pins.KOKORO_REPO and pins.KOKORO_VOICE
    for revision in (pins.WHISPER_REVISION, pins.KOKORO_REVISION):
        assert re.fullmatch(r"[0-9a-f]{40}", revision), revision
    # The same values the worker itself loads with.
    text = SPEECH.read_text(encoding="utf-8")
    assert f'WHISPER_REVISION = "{pins.WHISPER_REVISION}"' in text
    assert f'KOKORO_REVISION = "{pins.KOKORO_REVISION}"' in text


def test_a_speech_file_with_imports_that_do_not_exist_still_yields_its_pins(tmp_path, monkeypatch):
    # The image-build condition: speech.py copied alone, prism_rt and livekit not importable.
    fake = tmp_path / "janus_speech.py"
    fake.write_text(
        "from __future__ import annotations\n"
        "from prism_rt.adapters.voice_settings import echoes_prompt\n"
        "from livekit import rtc\n"
        'WHISPER_REPO = "org/whisper"\nWHISPER_REVISION = "a" * 3\nKOKORO_REPO = "org/kokoro"\n'
        'KOKORO_REVISION = "b" * 3\nKOKORO_VOICE: str = "af_heart"\nSAMPLE_RATE = 24_000\nNOT_A_PIN = os.environ["X"]\n',
        encoding="utf-8",
    )
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=lambda *a, **k: None))
    monkeypatch.setenv("JANUS_SPEECH_PY", str(fake))
    spec = importlib.util.spec_from_file_location("prefetch_models_under_test2", ROOT / "scripts" / "fdb_v3" / "prefetch_models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.speech.WHISPER_REPO == "org/whisper"
    assert module.speech.KOKORO_VOICE == "af_heart"
    assert module.speech.SAMPLE_RATE == 24_000
    assert not hasattr(module.speech, "NOT_A_PIN")
