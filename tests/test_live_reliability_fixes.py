"""Regression tests for the live-model reliability fixes made after a real
live demo run (`demo/run_v1_live_demo.py`) showed a destination slot fail
to extract, and a second live run hit persistent 429s during debugging.

Two independent fixes, tested independently, no real network or sleeps:
1. `workers/gateway.py`'s `_call_with_retry`/`_is_transient` -- a
   transient error (429/500/502/503/504, or a connection/timeout issue)
   gets retried a bounded number of times before surfacing; a genuine
   error (400 bad request, a malformed-schema JSONDecodeError) surfaces
   immediately, since retrying it would just waste time reproducing the
   same failure.
2. `workers/interpreter.py`'s `build_prompt` -- when `view["tools"]` is
   present, the prompt now tells a live model exactly which parameter
   names are valid for `slot_deltas[].name`, instead of leaving it to
   guess a synonym that then binds to nothing.
"""

from __future__ import annotations

import urllib.error

import pytest

from prism_rt.workers.gateway import _call_with_retry, _is_transient
from prism_rt.workers.interpreter import build_prompt


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url="http://x", code=code, msg="err", hdrs=None, fp=None)


class _FakeStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("boom")
        self.status_code = status_code


class RateLimitError(Exception):
    """Stands in for `anthropic.RateLimitError` without importing the
    (optional, undeclared) `anthropic` package -- `_is_transient` matches
    on class name, so a same-named class is a faithful test double."""


def test_is_transient_http_status_codes():
    for code in (429, 500, 502, 503, 504):
        assert _is_transient(_http_error(code)) is True
    for code in (400, 401, 403, 404):
        assert _is_transient(_http_error(code)) is False


def test_is_transient_url_error_and_status_code_attr_and_class_name():
    assert _is_transient(urllib.error.URLError("connection reset")) is True
    assert _is_transient(_FakeStatusError(503)) is True
    assert _is_transient(_FakeStatusError(400)) is False
    assert _is_transient(RateLimitError()) is True
    assert _is_transient(ValueError("not an API error at all")) is False


def test_call_with_retry_succeeds_after_transient_failures(monkeypatch):
    monkeypatch.setattr("prism_rt.workers.gateway.time.sleep", lambda _s: None)
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _http_error(503)
        return {"ok": True}

    result = _call_with_retry(flaky, attempts=3, backoff_s=(0, 0))
    assert result == {"ok": True}
    assert attempts["n"] == 3


def test_call_with_retry_gives_up_after_exhausting_attempts(monkeypatch):
    monkeypatch.setattr("prism_rt.workers.gateway.time.sleep", lambda _s: None)
    attempts = {"n": 0}

    def always_503():
        attempts["n"] += 1
        raise _http_error(503)

    with pytest.raises(urllib.error.HTTPError):
        _call_with_retry(always_503, attempts=3, backoff_s=(0, 0))
    assert attempts["n"] == 3  # tried exactly `attempts` times, no more


def test_call_with_retry_does_not_retry_a_genuine_error(monkeypatch):
    monkeypatch.setattr("prism_rt.workers.gateway.time.sleep", lambda _s: None)
    attempts = {"n": 0}

    def bad_request():
        attempts["n"] += 1
        raise _http_error(400)

    with pytest.raises(urllib.error.HTTPError):
        _call_with_retry(bad_request, attempts=3, backoff_s=(0, 0))
    assert attempts["n"] == 1  # never retried -- not transient


def test_interpret_prompt_names_required_slot_params_when_tools_present():
    view = {
        "transcript": "book me a flight to Pune",
        "active_intent": None,
        "active_slots": {},
        "suspended_goals": [],
        "pending_clarification": None,
        "tools": [{"name": "search_flights", "required_params": ["destination"]}],
    }
    prompt = build_prompt(view)
    assert "destination" in prompt
    assert "search_flights" in prompt
    # The transcript's own rendering is unchanged -- ScriptedProvider's
    # substring-match registration (every existing test) still matches.
    assert "transcript: 'book me a flight to Pune'" in prompt


def test_interpret_prompt_omits_tools_block_when_absent():
    """No `tools` key at all (e.g. a test view that predates this fix, or
    a caller that never sets it) must not crash or inject a stray block --
    additive only."""
    view = {"transcript": "hi", "active_intent": None, "active_slots": {}, "suspended_goals": [], "pending_clarification": None}
    prompt = build_prompt(view)
    assert "Available tools" not in prompt
